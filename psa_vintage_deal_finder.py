# -*- coding: utf-8 -*-
"""
CHAU PSA-Vintage-Deal-Finder
============================
Durchsucht eBay.ch (Sofort-Kaufen) ausschliesslich nach PSA-gegradeten (PSA 1-10) Vintage-
Pokemon-Einzelkarten (Base Set, Jungle, Fossil, Team Rocket, Gym Heroes, Gym Challenge, Neo
Genesis, Neo Discovery, Neo Revelation, Neo Destiny; englisch + japanisch). Berechnet pro
Karte+Set+Sprache+EXAKTEM PSA-Grade einen Referenzpreis (Median aus aktuellen Angeboten PLUS
Preishistorie fruehere Laeufe - Einzelgrade sind selten, oft nur 1-2 Angebote gleichzeitig
aktiv, siehe Learnings im Booster-Pack-Finder) und meldet Angebote deutlich unter diesem Median
als Deal in Discord (Webhook DISCORD_WEBHOOK_PSA, Kanal #psa-vintage-deals) - mit Foto und Link.

WICHTIG: Nur PSA (kein BGS/CGC/Beckett/ungegradet). Der exakte Grade ist zwingender Teil des
Vergleichsschluessels (Set + Karte + Sprache + Grade) - PSA 9 und PSA 10 derselben Karte werden
NIEMALS im selben Median vermischt (unterschiedliche Wertklassen).

Aufruf:
    python psa_vintage_deal_finder.py              -> normaler Lauf, postet neue Deals
    python psa_vintage_deal_finder.py --dry-run    -> nur Log/Ausgabe, kein Discord, kein State
    python psa_vintage_deal_finder.py --reset      -> vergisst bereits gemeldete Angebote

Technik: eBay blockt Headless-Browser (403). Es wird deshalb ein echtes Chromium-Fenster per
Playwright gestartet (lokal ausserhalb des Bildschirms positioniert, in der Cloud unter xvfb).
"""
import json
import os
import re
import statistics
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime

from playwright.sync_api import sync_playwright

HERE = os.path.dirname(os.path.abspath(__file__))
STATE_FILE = os.path.join(HERE, "psa_state.json")
LOG_FILE = os.path.join(HERE, "psa_finder.log")
LOCK_FILE = os.path.join(HERE, "psa_finder.lock")

DRY_RUN = "--dry-run" in sys.argv
RESET = "--reset" in sys.argv

# ---------------------------------------------------------------- Einstellungen
# PSA-Vintage-Karten reichen preislich von ca. 20 CHF (PSA 1-4 Commons) bis mehrere tausend CHF
# (PSA 10 Charizard etc.) - deutlich breiteres Fenster als bei rohen Karten, da der Grade selbst
# schon die Wertklasse bestimmt und im Vergleichsschluessel exakt getrennt wird.
MIN_TOTAL = 20.0
MAX_TOTAL = 3000.0
DEAL_RATIO = 0.70       # Idee/Kandidat: Preis <= 65 % des Medians der ANDEREN aktuellen Angebote (kein Marktwert!)
MIN_SAMPLES = 4         # mind. so viele andere Vergleichsangebote (ohne das Angebot selbst)
MIN_MEDIAN = 15.0
MAX_POSTS_PER_RUN = 12
PAGE_WAIT_MS = 2500
HISTORY_MAX_DAYS = 120  # so lange bleibt ein nicht mehr gesehenes Angebot als Preisreferenz erhalten

SEARCHES = [
    # englisch
    "pokemon base set psa", "pokemon base set 1st edition psa", "pokemon shadowless psa",
    "pokemon jungle psa", "pokemon fossil psa", "pokemon team rocket psa",
    "pokemon gym heroes psa", "pokemon gym challenge psa",
    "pokemon neo genesis psa", "pokemon neo discovery psa", "pokemon neo revelation psa", "pokemon neo destiny psa",
    # japanisch
    "pokemon japanese base set psa", "pokemon japanese jungle psa", "pokemon japanese fossil psa",
    "pokemon japanese team rocket psa", "pokemon japanese gym psa",
    "pokemon japanese neo genesis psa", "pokemon japanese neo discovery psa",
    "pokemon japanese neo revelation psa", "pokemon japanese neo destiny psa",
    "pokemon japanese no rarity psa", "pokemon japanese old back psa",
]

# Gleiche Set-Erkennung wie im rohen Vintage-Finder (feinere Aufloesung als nur "Base Set" etc.
# hilft der Genauigkeit des Vergleichsschluessels, z.B. 1st Edition vs. Unlimited/Shadowless).
SETS = [
    ("Base Set 1st Ed.", r"\b1st\s*ed(ition)?\b.*\bbase\b|\bbase\b.*\b1st\s*ed(ition)?\b"),
    ("Base Set Shadowless", r"shadowless"),
    ("No Rarity (JP)", r"no\s*rarit"),
    ("Team Rocket", r"team\s*rocket|\brocket\b"),
    ("Gym Heroes", r"gym\s*heroes"),
    ("Gym Challenge", r"gym\s*challenge"),
    ("Gym", r"\bgym\b|leaders' stadium|challenge from the darkness"),
    ("Neo Genesis", r"neo\s*genesis|gold,? silver,? to a new world"),
    ("Neo Discovery", r"neo\s*discovery|crossing the ruins"),
    ("Neo Revelation", r"neo\s*revelation|awakening legends"),
    ("Neo Destiny", r"neo\s*destiny|darkness and to light"),
    ("Neo Premium File", r"premium\s*file"),
    ("Neo", r"\bneo\b"),
    ("Jungle", r"\bjungle\b"),
    ("Fossil", r"\bfossil\b|mystery of the fossils"),
    ("Base Set 2", r"base\s*set\s*2"),
    ("Base Set", r"\bbase\s*set\b|\bbase\b|expansion pack|\bbasic\b"),
    ("Vending/Promo (JP)", r"vending|\bpromo\b|corocoro"),
]

HOLO_NAMES = [
    # Base
    "Alakazam", "Blastoise", "Chansey", "Charizard", "Clefairy", "Gyarados", "Hitmonchan", "Machamp", "Magneton",
    "Mewtwo", "Nidoking", "Ninetales", "Poliwrath", "Raichu", "Venusaur", "Zapdos",
    # Jungle
    "Clefable", "Electrode", "Flareon", "Jolteon", "Kangaskhan", "Mr. Mime", "Mr Mime", "Nidoqueen", "Pidgeot", "Pinsir",
    "Scyther", "Snorlax", "Vaporeon", "Venomoth", "Victreebel", "Vileplume", "Wigglytuff",
    # Fossil
    "Aerodactyl", "Articuno", "Ditto", "Dragonite", "Gengar", "Haunter", "Hitmonlee", "Hypno", "Kabutops", "Lapras",
    "Moltres", "Muk",
    # Team Rocket
    "Dark Alakazam", "Dark Arbok", "Dark Blastoise", "Dark Charizard", "Dark Dragonite", "Dark Dugtrio", "Dark Golbat",
    "Dark Gyarados", "Dark Hypno", "Dark Machamp", "Dark Magneton", "Dark Slowbro", "Dark Vileplume", "Dark Weezing",
    "Dark Raichu", "Here Comes Team Rocket", "Rocket's Sneak Attack", "Rainbow Energy",
    # Gym
    "Blaine's Moltres", "Brock's Rhydon", "Erika's Dragonair", "Erika's Vileplume", "Lt. Surge's Electabuzz",
    "Lt. Surge's Fearow", "Lt. Surge's Magneton", "Rocket's Hitmonchan", "Rocket's Moltres", "Rocket's Scyther",
    "Sabrina's Gengar", "Blaine's Arcanine", "Blaine's Charizard", "Brock's Ninetales", "Erika's Venusaur",
    "Giovanni's Gyarados", "Giovanni's Machamp", "Giovanni's Nidoking", "Giovanni's Persian", "Koga's Beedrill",
    "Koga's Ditto", "Lt. Surge's Raichu", "Misty's Golduck", "Misty's Gyarados", "Rocket's Mewtwo", "Rocket's Zapdos",
    "Sabrina's Alakazam", "Misty's Seadra", "Misty's Tentacruel",
    # Neo
    "Ampharos", "Azumarill", "Bellossom", "Feraligatr", "Heracross", "Jumpluff", "Kingdra", "Lugia", "Meganium",
    "Pichu", "Skarmory", "Slowking", "Steelix", "Togetic", "Typhlosion",
    "Espeon", "Forretress", "Hitmontop", "Houndoom", "Politoed", "Scizor", "Smeargle", "Tyranitar", "Umbreon",
    "Unown", "Ursaring", "Wobbuffet", "Yanma",
    "Blissey", "Celebi", "Crobat", "Entei", "Ho-oh", "Ho-Oh", "Misdreavus", "Porygon2", "Raikou", "Suicune",
    "Shining Gyarados", "Shining Magikarp", "Shining Charizard", "Shining Mewtwo", "Shining Kabutops", "Shining Celebi",
    "Shining Noctowl", "Shining Raichu", "Shining Steelix", "Shining Tyranitar",
    "Dark Ampharos", "Dark Crobat", "Dark Donphan", "Dark Espeon", "Dark Feraligatr", "Dark Gengar", "Dark Houndoom",
    "Dark Porygon2", "Dark Scizor", "Dark Typhlosion", "Dark Tyranitar", "Light Arcanine", "Light Azumarill",
    "Light Dragonite", "Light Togetic",
    # Trainer/sonstige gefragte
    "Pikachu", "Eevee", "Mew", "Lucky Stadium",
]
# laengere Namen zuerst, damit "Dark Charizard" vor "Charizard" matcht
HOLO_NAMES_SORTED = sorted(set(HOLO_NAMES), key=len, reverse=True)

EXCLUDE = re.compile(
    r"\b(proxy|custom|fake|replica|orica|repro|lot\b|bundle|sammlung|collection of|x\s?\d{1,2}\b|\d{1,2}\s?x\b|"
    r"karten\s?set|complete set|komplett|sleeve|binder|booster|pack\b|box\b|deck\b|tin\b|"
    r"spanish|espa[nñ]ol|italian|italiano|french|fran[cç]ais|german|deutsch|portugu|korean|chinese|"
    r"\bde\b|\bfr\b|\bit\b|\bes\b|coin|m[uü]nze|sticker|topps|carddass|bandai|meiji|amada|ensky)\b",
    re.I,
)
MODERN = re.compile(
    r"\b(ex|gx|v|vmax|vstar|tag team|sv\d*|swsh|sm\d*|xy\d*|bw\d*|dp\d*|scarlet|violet|sword|shield|sun|moon|"
    r"destined rivals|evolving skies|obsidian|paldea|151|celebrations|crown zenith|prismatic|surging|stellar|"
    r"journey together|temporal|paradox|twilight|mega evolution|illustration rare|secret rare|full art|alt art|"
    r"20(0[3-9]|1\d|2\d))\b|\b\d{3}/\d{3}\b",
    re.I,
)

# NUR PSA, exakter Grade 1-10 zwingend Teil des Vergleichsschluessels. BGS/CGC/Beckett/ungegradet
# werden bewusst ignoriert (Nutzerwunsch: ausschliesslich PSA-gegradete Karten).
PSA_GRADE = re.compile(r"\bpsa\s*-?\s*(10|[1-9])(?!\.?\d)\b", re.I)
OTHER_GRADER = re.compile(r"\b(bgs|cgc|beckett|ace|sgc|csg)\s*-?\s*(10|9\.5|9|8\.5|8|7\.5|7|6\.5|6|5\.5|5|4|3|2|1)\b", re.I)


def detect_set(t):
    for name, pat in SETS:
        if re.search(pat, t, re.I):
            return name
    return None


def detect_name(t):
    tl = t.lower()
    for n in HOLO_NAMES_SORTED:
        if n.lower() in tl:
            return n
    return None


def detect_psa_grade(t):
    """Gibt 'PSA <n>' zurueck, oder None wenn kein eindeutiger PSA-Grade gefunden wurde (dann
    wird das Angebot komplett verworfen - roh/ungegradet und andere Grader sind hier fehl am
    Platz)."""
    m = PSA_GRADE.search(t)
    if not m:
        return None
    return f"PSA {m.group(1)}"


def detect_edition(t):
    if re.search(r"1st\s*ed|first\s*edition|(?<![0-9a-z])1st(?![0-9a-z])", t, re.I):
        return "1st Ed"
    if re.search(r"shadowless", t, re.I):
        return "Shadowless"
    return "Unlimited"


def detect_variant(t):
    if re.search(r"promo|premium\s*file|\bfile\b|folder|vending|carddass|prerelease|pre-release|error|misprint", t, re.I):
        return "Promo/Special"
    return "Standard"


def reference_price(vals):
    """Robuster Referenzpreis: Median, nach Entfernen von Ausreissern (>2.5x oder <0.4x des
    Roh-Medians) nochmals Median. Gibt None zurueck, wenn danach zu wenig Angebote uebrig sind."""
    if len(vals) < MIN_SAMPLES:
        return None, 0
    raw = statistics.median(vals)
    kept = [v for v in vals if 0.4 * raw <= v <= 2.5 * raw]
    if len(kept) < MIN_SAMPLES:
        return None, len(kept)
    return statistics.median(kept), len(kept)


def is_japanese(t):
    return bool(re.search(r"japan|japanese|japanisch|\bjp\b|\bjpn\b|old back|no rarity|vending|carddass", t, re.I))


def parse_chf(s):
    if not s:
        return None
    m = re.search(r"CHF\s*([\d'.]+),(\d{2})", s)
    if not m:
        m2 = re.search(r"CHF\s*([\d'.]+)", s)
        return float(m2.group(1).replace("'", "").replace(".", "")) if m2 else None
    return float(m.group(1).replace("'", "").replace(".", "") + "." + m.group(2))


def parse_shipping(s):
    if not s:
        return 0.0
    if re.search(r"kostenlos|gratis|free", s, re.I):
        return 0.0
    v = parse_chf(s)
    return v if v is not None else 0.0


def log(msg):
    line = f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {msg}"
    print(line)
    with open(LOG_FILE, "a", encoding="utf-8") as fh:
        fh.write(line + "\n")


# Kein Laender-Ausschluss (gleiche Begruendung wie im rohen Vintage-Finder) - Japan bleibt
# Top-Praeferenz und wird beim Posten nach vorne gezogen.
def is_japan_origin(country_raw):
    return (country_raw or "").strip().lower() == "japan"


EXTRACT_JS = """() => [...document.querySelectorAll('li.s-item, li.s-card')].map(li => {
  const a = li.querySelector('a[href*="/itm/"]');
  const id = a ? (a.href.match(/itm[/](\\d+)/) || [])[1] : '';
  const t = (li.querySelector('.s-item__title, .s-card__title') || {}).textContent || '';
  const p = (li.querySelector('.s-item__price, .s-card__price') || {}).textContent || '';
  const s = (li.querySelector('.s-item__shipping, .s-item__logisticsCost, .s-card__shipping, [class*="shipping"], [class*="logistics"]') || {}).textContent || '';
  const img = li.querySelector('img');
  const sub = [...li.querySelectorAll('.SECONDARY_INFO, .s-item__subtitle, .s-card__subtitle, [class*="condition"], [class*="Condition"]')].map(x => x.textContent).join(' ');
  const full = (li.innerText || '').replace(/\\s+/g, ' ').trim();
  const locEl = (li.querySelector('.s-item__location, .s-item__itemLocation, [class*="location"]') || {}).textContent || '';
  const fromMatch = (li.innerText || '').match(/\\baus ([^\\n]+)/);
  const country = (locEl.replace(/^aus\\s+/i, '').trim()) || (fromMatch ? fromMatch[1].trim() : '');
  return {id, t: t.replace('Wird in neuem Fenster oder Tab geöffnet', '').trim(), p: p.trim(), s: s.trim(),
          img: img ? (img.src || img.getAttribute('data-src') || '') : '', sub: sub.trim(), full, country};
}).filter(x => x.id && x.id !== '123456')"""


def fetch_all():
    items = {}
    # eBay blockt echten Headless-Chrome - nur ein "echter" (headed) Chromium-Prozess funktioniert
    # (siehe vintage_deal_finder.py / booster_deal_finder.py, identisch getestet 2026-09-26).
    launch_args = ["--window-size=1280,900"]
    if os.name == "nt":
        launch_args.insert(0, "--window-position=-2400,-2400")
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False, args=launch_args)
        ctx = browser.new_context(locale="de-CH", viewport={"width": 1280, "height": 900})
        page = ctx.new_page()
        for q in SEARCHES:
            url = ("https://www.ebay.ch/sch/i.html?_nkw=" + urllib.parse.quote(q) +
                   f"&_sacat=183454&LH_BIN=1&_sop=10&_udlo={int(MIN_TOTAL)}&_udhi={int(MAX_TOTAL) + 60}&_ipg=240")
            rows = []
            for attempt in range(3):
                try:
                    page.goto(url, timeout=60000, wait_until="domcontentloaded")
                    page.wait_for_timeout(PAGE_WAIT_MS * (attempt + 1))
                    rows = page.evaluate(EXTRACT_JS)
                except Exception as e:
                    log(f"Fehler bei '{q}' (Versuch {attempt + 1}): {e}")
                if rows:
                    break
                time.sleep(3)
            new = 0
            for r in rows:
                if r["id"] not in items:
                    r["query"] = q
                    items[r["id"]] = r
                    new += 1
            log(f"'{q}': {len(rows)} Treffer, {new} neu")
            time.sleep(1.0)
        browser.close()
    return items


def classify(items):
    out = []
    for r in items.values():
        t = r["t"]
        full_text = " ".join([t, r.get("sub", ""), r.get("full", "")])
        if EXCLUDE.search(t) or MODERN.search(t):
            continue
        # Nur PSA - und wenn ein ANDERER Grader (BGS/CGC/...) im Titel erwaehnt wird, ohne
        # eindeutig auch PSA zu sein, verwerfen (kein Vermischen unterschiedlicher Grading-Skalen).
        if OTHER_GRADER.search(full_text) and not PSA_GRADE.search(full_text):
            continue
        grade = detect_psa_grade(full_text)
        if not grade:
            continue
        # Titel und eBay-"Artikelzustand" muessen denselben PSA-Grade nennen (Bug 2026-10-06: Titel
        # "PSA 9", Artikelzustand "Graded - PSA 8").
        if len({g for g in PSA_GRADE.findall(full_text)}) > 1:
            continue
        price = parse_chf(r["p"])
        if price is None:
            continue
        is_jp = is_japan_origin(r.get("country", ""))
        ship = parse_shipping(r["s"])
        total = round(price + ship, 2)
        name = detect_name(t)
        sset = detect_set(t)
        if not name or not sset:
            continue
        lang = "JP" if is_japanese(t) else "EN"
        edition = detect_edition(t)
        variant = detect_variant(t)
        key = f"{lang}|{sset}|{edition}|{variant}|{name}|{grade}"
        img = r["img"] or ""
        img = re.sub(r"/s-l\d+\.(webp|jpg)", "/s-l1600.jpg", img)
        out.append(dict(id=r["id"], title=t, price=price, ship=ship, total=total, name=name, set=sset, grade=grade,
                        lang=lang, edition=edition, key=key, img=img, url=f"https://www.ebay.ch/itm/{r['id']}", sub=r.get("sub", ""),
                        query=r.get("query", ""), country=r.get("country", "") or "unbekannt", is_jp=is_jp))
    return out


def find_deals(cards, history):
    by_key = {}
    for c in cards:
        by_key.setdefault(c["key"], []).append(c["total"])
    # Preishistorie fruehere Laeufe dazumischen - bei einem exakten Grade (z.B. "PSA 8" einer
    # bestimmten Karte) sind fast nie 3+ Angebote GLEICHZEITIG aktiv (gleiches Problem/gleiche
    # Loesung wie im Booster-Pack-Finder, siehe dortige Learnings 2026-09-26).
    for key, bucket in history.items():
        vals = [v["total"] for v in bucket.values()]
        by_key.setdefault(key, [])
        by_key[key] = by_key[key] + vals
    deals = []
    for c in cards:
        # Referenz ohne das Angebot selbst (sonst zieht der eigene Preis den Median nach unten)
        others = list(by_key[c["key"]])
        if c["total"] in others:
            others.remove(c["total"])
        med, n = reference_price(others)
        if med is None:
            continue
        if not (MIN_TOTAL <= c["total"] <= MAX_TOTAL) or med < MIN_MEDIAN:
            continue
        if c["total"] <= DEAL_RATIO * med:
            c = dict(c, median=round(med, 2), n=n, ratio=round(c["total"] / med, 2))
            deals.append(c)
    # Japan-Verkaeufer sind Top-Praeferenz -> nach vorne ziehen, danach nach Deal-Faktor sortieren.
    deals.sort(key=lambda d: (0 if d["is_jp"] else 1, d["ratio"]))
    return deals


SOLD_RATIO = 0.80       # Preis muss <= 80 % des mittleren VERKAUFSPREISES (eBay "Verkaufte Artikel") liegen
def update_history(history, cards):
    """Traegt die aktuell klassifizierten Angebote in die Preishistorie ein (pro Set+Karte+
    Sprache+Grade, dedupliziert nach Angebots-ID) und entfernt Eintraege, die seit
    HISTORY_MAX_DAYS nicht mehr gesehen wurden (vermutlich verkauft/entfernt)."""
    today = datetime.now().strftime("%Y-%m-%d")
    for c in cards:
        bucket = history.setdefault(c["key"], {})
        bucket[c["id"]] = {"total": c["total"], "last_seen": today}
    cutoff = datetime.now().timestamp() - HISTORY_MAX_DAYS * 86400
    for key in list(history.keys()):
        bucket = history[key]
        for lid in list(bucket.keys()):
            try:
                ts = datetime.strptime(bucket[lid]["last_seen"], "%Y-%m-%d").timestamp()
            except Exception:
                ts = 0
            if ts < cutoff:
                del bucket[lid]
        if not bucket:
            del history[key]


def load_state():
    if RESET or not os.path.exists(STATE_FILE):
        return {"seen": {}, "history": {}}
    try:
        st = json.load(open(STATE_FILE, encoding="utf-8"))
        st.setdefault("seen", {})
        st.setdefault("history", {})
        return st
    except Exception:
        return {"seen": {}, "history": {}}


def save_state(st):
    json.dump(st, open(STATE_FILE, "w", encoding="utf-8"), ensure_ascii=False, indent=0)


def post_discord(webhook, deals):
    embeds = []
    for d in deals:
        pct = int(round((1 - d["ratio"]) * 100))
        flag = "\U0001F1EF\U0001F1F5 " if d.get("is_jp") else ""
        desc = (f"**CHF {d['total']:.2f}** inkl. Versand (Preis {d['price']:.2f} + Versand {d['ship']:.2f})\n"
                f"Karte: **{d['name']}** - {d['set']} ({d['lang']}, {d['edition']}) - Grade: **{d['grade']}**\n"
                f"Herkunft: **{d.get('country', 'unbekannt')}**\n"
                f"**{pct} % unter den anderen Angeboten** (Median CHF {d['median']:.2f} der {d['n']} anderen aktuellen eBay-Angebote)\nIdee mit Potenzial, KEIN Marktwert: Angebotspreise sind Wunschpreise - bitte in Collectr (Sold/Graded) gegenpruefen.\n"
                f"[Zum Angebot]({d['url']})")
        color = 0x2ECC71 if pct >= 40 else 0xF1C40F
        embeds.append({
            "title": (flag + d["title"])[:240],
            "url": d["url"],
            "description": desc,
            "color": color,
            "image": {"url": d["img"]} if d["img"] else None,
            "footer": {"text": f"eBay.ch Sofort-Kaufen - gefunden {datetime.now():%d.%m.%Y %H:%M} - Suche: {d.get('query', '')}"},
        })
    for e in embeds:
        if e.get("image") is None:
            e.pop("image", None)
    for i in range(0, len(embeds), 6):
        payload = {"username": "CHAU PSA-Vintage-Ideen", "embeds": embeds[i:i + 6]}
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(webhook, data=data, headers={"Content-Type": "application/json", "User-Agent": "chau-psa-deal-finder/1.0"})
        with urllib.request.urlopen(req) as r:
            log(f"Discord {r.status} ({len(embeds[i:i + 6])} Embeds)")
        time.sleep(1.5)


def acquire_lock():
    if os.path.exists(LOCK_FILE):
        if time.time() - os.path.getmtime(LOCK_FILE) < 900:
            return False
    open(LOCK_FILE, "w").write(str(os.getpid()))
    return True


def main():
    if not acquire_lock():
        log("Ein anderer Lauf ist bereits aktiv, abgebrochen.")
        return
    try:
        webhook = os.environ.get("DISCORD_WEBHOOK_PSA") or ""
        if not webhook and not DRY_RUN and os.name == "nt":
            # Lokal (Windows) steht der Webhook als persistente User-Umgebungsvariable, die ein
            # laufender Prozess nicht automatisch sieht, wenn sie erst NACH dem Start gesetzt
            # wurde. In der Cloud (Linux) kommt der Webhook ausschliesslich ueber os.environ
            # (GitHub Actions Secret).
            import subprocess
            webhook = subprocess.run(["powershell", "-NoProfile", "-Command", "[Environment]::GetEnvironmentVariable('DISCORD_WEBHOOK_PSA','User')"],
                                     capture_output=True, text=True).stdout.strip()
        if not webhook and not DRY_RUN:
            log("Webhook DISCORD_WEBHOOK_PSA fehlt.")
            return
        st = load_state()
        items = fetch_all()
        cards = classify(items)
        # find_deals() nutzt die BISHERIGE Historie (Stand vor diesem Lauf) - erst danach wird
        # der aktuelle Lauf selbst in die Historie eingetragen, sonst wuerden die eigenen
        # Live-Angebote doppelt gezaehlt.
        deals = find_deals(cards, st["history"])
        log(f"{len(items)} Angebote geladen, {len(cards)} PSA-klassifiziert, {len(deals)} Deals gesamt")
        new = [d for d in deals if d["id"] not in st["seen"]][:MAX_POSTS_PER_RUN]
        for d in deals[:25]:
            log(f"  {'NEU ' if d['id'] not in st['seen'] else '    '}{d['ratio']:.2f}x CHF {d['total']:.2f} (Median {d['median']:.2f}, n={d['n']}) {d['key']} | {d['title'][:70]} | {d['url']}")
        if DRY_RUN:
            log(f"Dry-Run: {len(new)} neue Deals wuerden gepostet.")
            return
        if new:
            post_discord(webhook, new)
        update_history(st["history"], cards)
        for d in new:
            st["seen"][d["id"]] = datetime.now().strftime("%Y-%m-%d")
        # alte Eintraege nach 60 Tagen vergessen
        cutoff = datetime.now().timestamp() - 60 * 86400
        st["seen"] = {k: v for k, v in st["seen"].items() if datetime.strptime(v, "%Y-%m-%d").timestamp() > cutoff}
        save_state(st)
        log(f"{len(new)} neue Deals gepostet.")
    finally:
        try:
            os.remove(LOCK_FILE)
        except OSError:
            pass


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    main()
