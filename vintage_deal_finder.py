# -*- coding: utf-8 -*-
"""
CHAU Vintage-Deal-Finder
========================
Durchsucht eBay.ch (Sofort-Kaufen) nach Vintage-Pokemon-Karten (Base Set, Jungle, Fossil,
Team Rocket, Gym, Neo; englisch + japanisch; roh oder PSA/BGS/CGC), berechnet pro Karte+Zustand
einen Referenzpreis (Median aller aktuellen Angebote) und meldet Angebote, die deutlich unter
diesem Median liegen, als Deal in Discord (Webhook DISCORD_WEBHOOK_HOLO) - mit Foto und Link.

Zielgruppe: Flohmarkt-Kunde, der Vintage-Holos fuer 100-300 CHF pro Karte kauft.
Einkaufsfenster daher standardmaessig 30-250 CHF inkl. Versand (MIN_TOTAL / MAX_TOTAL).

Haendler-Herkunft: kein Land wird ausgeschlossen, auch USA nicht (Stand 2026-09-26). Zu hohe
Versandkosten fangen MIN_TOTAL/MAX_TOTAL (Preis + Versand) ohnehin ab. Japanische Verkaeufer
werden bevorzugt: in Discord mit Flagge markiert und im Ranking nach vorne gezogen.

Aufruf:
    python vintage_deal_finder.py              -> normaler Lauf, postet neue Deals
    python vintage_deal_finder.py --dry-run    -> nur Log/Ausgabe, kein Discord, kein State
    python vintage_deal_finder.py --reset      -> vergisst bereits gemeldete Angebote

Technik: eBay blockt Headless-Browser (403). Es wird deshalb ein echtes Chromium-Fenster
per Playwright gestartet, das ausserhalb des Bildschirms positioniert wird.
"""
import json
import os
import re
import statistics
import sys
import time
import urllib.request
from datetime import datetime

from playwright.sync_api import sync_playwright

HERE = os.path.dirname(os.path.abspath(__file__))
STATE_FILE = os.path.join(HERE, "deal_state.json")
LOG_FILE = os.path.join(HERE, "deal_finder.log")
LOCK_FILE = os.path.join(HERE, "deal_finder.lock")

DRY_RUN = "--dry-run" in sys.argv
RESET = "--reset" in sys.argv

# ---------------------------------------------------------------- Einstellungen
MIN_TOTAL = 30.0        # CHF inkl. Versand
MAX_TOTAL = 250.0       # CHF inkl. Versand (Kunde zahlt 100-300, Marge muss drin sein)
DEAL_RATIO = 0.72       # Preis <= 72 % des Medians gleicher Karte/Zustand
MIN_SAMPLES = 4         # Mindestens so viele Vergleichsangebote fuer einen Median
MIN_MEDIAN = 80.0       # Marktwert (Median) mind. so hoch, sonst fuer den 100-300-CHF-Kunden uninteressant
MAX_POSTS_PER_RUN = 12
PAGE_WAIT_MS = 2500

SEARCHES = [
    # englisch
    "pokemon base set holo", "pokemon base set 1st edition holo", "pokemon shadowless holo",
    "pokemon jungle holo", "pokemon fossil holo", "pokemon team rocket holo",
    "pokemon gym heroes holo", "pokemon gym challenge holo",
    "pokemon neo genesis holo", "pokemon neo discovery holo", "pokemon neo revelation holo", "pokemon neo destiny holo",
    "pokemon wotc holo psa", "pokemon base set psa", "pokemon shining psa",
    # japanisch
    "pokemon japanese base set holo", "pokemon japanese jungle holo", "pokemon japanese fossil holo",
    "pokemon japanese team rocket holo", "pokemon japanese gym holo", "pokemon japanese neo holo",
    "pokemon japanese no rarity holo", "pokemon japanese old back psa",
]

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
NONHOLO = re.compile(r"non[\s-]?holo|no[\s-]?holo|nicht[\s-]?holo|\bregular\b|\bcommon\b|uncommon|reverse", re.I)


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


def detect_grade(t):
    m = re.search(r"\b(psa|bgs|cgc|beckett|ace)\s*-?\s*(10|9\.5|9|8\.5|8|7\.5|7|6\.5|6|5\.5|5|4|3|2|1)\b", t, re.I)
    if m:
        return f"{m.group(1).upper()} {m.group(2)}"
    # eBays offizielle "Artikelzustand"-Stufen fuer ungegradete Sammelkarten (das Feld, das der
    # Nutzer 2026-09-26 als massgeblich markiert hat, NICHT irgendein Titel-Slang):
    # Near Mint or Better / Lightly Played (Excellent) / Moderately Played (Very Good) /
    # Heavily Played (Poor) / Damaged - DE etwa: Neuwertig oder besser / Leicht bespielt
    # (Ausgezeichnet) / Mäßig bespielt (Sehr gut) / Stark bespielt (Schlecht) / Beschädigt.
    # WICHTIG: "(Very Good)"/"(Sehr gut)" gehoert bei eBay zur MITTLEREN Stufe (Moderately
    # Played), nicht zu Lightly Played - deshalb zuerst und getrennt von "Excellent" pruefen.
    if re.search(r"near\s*mint\s*or\s*better|neuwertig\s*oder\s*besser", t, re.I):
        return "raw NM (eBay: Near Mint or Better)"
    if re.search(r"moderately\s*played|m[aä]{1,2}s{1,2}ig\s*bespielt|very\s*good|sehr\s*gut", t, re.I):
        return "raw MP (eBay: Moderately Played/Very Good)"
    if re.search(r"lightly\s*played|leicht\s*bespielt|\bexcellent\b|ausgezeichnet", t, re.I):
        return "raw LP (eBay: Lightly Played/Excellent)"
    if re.search(r"heavily\s*played|stark\s*bespielt|\bpoor\b|\bschlecht\b", t, re.I):
        return "raw HP (eBay: Heavily Played/Poor)"
    if re.search(r"\bdamaged\b|beschädigt", t, re.I):
        return "raw Damaged"
    # Fallback: generischer Titel-Slang, falls kein offizielles eBay-Zustandsfeld gefunden wurde.
    if re.search(r"\b(nm|mint|exc?)\b", t, re.I):
        return "raw NM/EX (Titel)"
    if re.search(r"\b(lp|pl|vg\+?)\b", t, re.I):
        return "raw LP (Titel)"
    # "mp"/"hp" NUR als eigenstaendiges Wort matchen (kein \w*-Suffix!), sonst matcht z.B. die
    # Pokemon-Statzeile "HP70"/"HP100" im Titel faelschlich als Zustand "HP" (Heavily Played) -
    # \bhp\b schlaegt bei "HP70" korrekt fehl (keine Wortgrenze zwischen "P" und "70").
    if re.search(r"\b(mp|hp)\b", t, re.I) or re.search(
        r"\b(moderat|played|heavy|heavily|damaged|dmg|poor|beschädigt|bespielt|bend|bent|"
        r"crease|creased|knick|geknickt|inked|ink|written|scratch|kratzer|whitening|riss|torn)\w*\b",
        t, re.I,
    ):
        return "raw MP/HP (Titel)"
    return "raw ?"


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


# ------------------------------------------------------- Haendler-Herkunft
# Nutzerwunsch 2026-09-26: KEIN Laender-Ausschluss (auch US-Haendler wieder erlaubt, Stand
# 2026-09-26 spaeter am Tag zurueckgenommen) - zu hohe Versandkosten werden ohnehin schon
# ueber MIN_TOTAL/MAX_TOTAL (Preis + Versand) abgefangen. Japan bleibt Top-Praeferenz und
# wird beim Posten nach vorne gezogen und mit einer Flagge markiert. eBay.ch zeigt bei jedem
# Suchtreffer "aus <Land>" (deutsche Bezeichnung) im Kartentext - daraus wird das Land gelesen.
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
    # eBay blockt echten Headless-Chrome (403/Stub-Seite, getestet 2026-09-26: urllib, curl,
    # Playwright headless=True, Chrome "--headless=new" und sogar camoufox headless liefern
    # alle nur eine leere Fehlerseite). Nur ein "echter" (headed) Chromium-Prozess funktioniert.
    # Lokal (Windows) wird das Fenster ausserhalb des sichtbaren Desktops platziert, damit es
    # den Nutzer nicht stoert. In der Cloud (GitHub Actions, Linux) laeuft der Lauf unter einem
    # virtuellen Display (xvfb-run) - dort ist niemand da, den man stoeren koennte, also normale
    # Fensterposition.
    launch_args = ["--window-size=1280,900"]
    if os.name == "nt":
        launch_args.insert(0, "--window-position=-2400,-2400")
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False, args=launch_args)
        ctx = browser.new_context(locale="de-CH", viewport={"width": 1280, "height": 900})
        page = ctx.new_page()
        for q in SEARCHES:
            url = ("https://www.ebay.ch/sch/i.html?_nkw=" + urllib.request.quote(q) +
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
        if EXCLUDE.search(t) or NONHOLO.search(t) or MODERN.search(t):
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
        # Zustand steht meistens NICHT im Titel, sondern im separaten "Artikelzustand"-Feld
        # der Trefferkarte - deshalb Titel + Zustands-Badge + kompletter sichtbarer Kartentext
        # zusammen durchsuchen (eBay wechselt haeufig die CSS-Klassen dafuer).
        grade = detect_grade(" ".join([t, r.get("sub", ""), r.get("full", "")]))
        lang = "JP" if is_japanese(t) else "EN"
        key = f"{lang}|{sset}|{name}|{grade}"
        img = r["img"] or ""
        img = re.sub(r"/s-l\d+\.(webp|jpg)", "/s-l1600.jpg", img)
        out.append(dict(id=r["id"], title=t, price=price, ship=ship, total=total, name=name, set=sset, grade=grade,
                        lang=lang, key=key, img=img, url=f"https://www.ebay.ch/itm/{r['id']}", sub=r.get("sub", ""),
                        query=r.get("query", ""), country=r.get("country", "") or "unbekannt", is_jp=is_jp))
    return out


def find_deals(cards):
    by_key = {}
    for c in cards:
        by_key.setdefault(c["key"], []).append(c["total"])
    deals = []
    for c in cards:
        vals = by_key[c["key"]]
        if len(vals) < MIN_SAMPLES:
            continue
        med = statistics.median(vals)
        if not (MIN_TOTAL <= c["total"] <= MAX_TOTAL) or med < MIN_MEDIAN:
            continue
        if c["total"] <= DEAL_RATIO * med:
            c = dict(c, median=round(med, 2), n=len(vals), ratio=round(c["total"] / med, 2))
            deals.append(c)
    # Japan-Verkaeufer sind Top-Praeferenz -> in der Reihenfolge (und damit beim Posten
    # mit MAX_POSTS_PER_RUN) nach vorne ziehen, danach nach Deal-Faktor sortieren.
    deals.sort(key=lambda d: (0 if d["is_jp"] else 1, d["ratio"]))
    return deals


def load_state():
    if RESET or not os.path.exists(STATE_FILE):
        return {"seen": {}}
    try:
        return json.load(open(STATE_FILE, encoding="utf-8"))
    except Exception:
        return {"seen": {}}


def save_state(st):
    json.dump(st, open(STATE_FILE, "w", encoding="utf-8"), ensure_ascii=False, indent=0)


def post_discord(webhook, deals):
    embeds = []
    for d in deals:
        pct = int(round((1 - d["ratio"]) * 100))
        flag = "\U0001F1EF\U0001F1F5 " if d.get("is_jp") else ""
        desc = (f"**CHF {d['total']:.2f}** inkl. Versand (Preis {d['price']:.2f} + Versand {d['ship']:.2f})\n"
                f"Karte: **{d['name']}** - {d['set']} ({d['lang']}) - Zustand/Grade: **{d['grade']}**\n"
                f"Herkunft: **{d.get('country', 'unbekannt')}**\n"
                f"**{pct} % unter Median** (Median CHF {d['median']:.2f} aus {d['n']} aktuellen Angeboten)\n"
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
        if e["image"] is None:
            del e["image"]
    for i in range(0, len(embeds), 6):
        payload = {"username": "CHAU Vintage-Deal-Finder", "embeds": embeds[i:i + 6]}
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(webhook, data=data, headers={"Content-Type": "application/json", "User-Agent": "chau-deal-finder/1.0"})
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
        webhook = os.environ.get("DISCORD_WEBHOOK_HOLO") or ""
        if not webhook and not DRY_RUN and os.name == "nt":
            # Lokal (Windows) steht der Webhook als persistente User-Umgebungsvariable, die ein
            # laufender Prozess (z.B. der Windows-Scheduled-Task) nicht automatisch sieht, wenn
            # sie erst NACH dem Start des Prozesses gesetzt wurde. In der Cloud (Linux) kommt der
            # Webhook ausschliesslich ueber das echte os.environ (GitHub Actions Secret).
            import subprocess
            webhook = subprocess.run(["powershell", "-NoProfile", "-Command", "[Environment]::GetEnvironmentVariable('DISCORD_WEBHOOK_HOLO','User')"],
                                     capture_output=True, text=True).stdout.strip()
        if not webhook and not DRY_RUN:
            log("Webhook DISCORD_WEBHOOK_HOLO fehlt.")
            return
        st = load_state()
        items = fetch_all()
        cards = classify(items)
        deals = find_deals(cards)
        log(f"{len(items)} Angebote geladen, {len(cards)} klassifiziert, {len(deals)} Deals gesamt")
        new = [d for d in deals if d["id"] not in st["seen"]][:MAX_POSTS_PER_RUN]
        for d in deals[:25]:
            log(f"  {'NEU ' if d['id'] not in st['seen'] else '    '}{d['ratio']:.2f}x CHF {d['total']:.2f} (Median {d['median']:.2f}, n={d['n']}) {d['key']} | {d['title'][:70]} | {d['url']}")
        if DRY_RUN:
            log(f"Dry-Run: {len(new)} neue Deals wuerden gepostet.")
            return
        if new:
            post_discord(webhook, new)
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
