# -*- coding: utf-8 -*-
"""
CHAU Booster-Pack-Deal-Finder
==============================
Durchsucht eBay.ch UND Ricardo.ch (beide Sofort-Kaufen/"Sofort kaufen") nach einzelnen,
versiegelten Pokemon-Booster-PACKS (nicht Boxen/Displays!) quer durch beliebte Sets -
Vintage (Base Set, Jungle, Fossil, Team Rocket, Gym, Neo) bis moderne Sets mit gefragten
Hit-Karten (151, Prismatic Evolutions, Paldean Fates, Astral Radiance, Mega Evolution, ...).
Berechnet pro Set+Sprache einen Referenzpreis (Median aller aktuellen Angebote beider
Plattformen zusammen) und meldet Angebote deutlich unter diesem Median in Discord
(Webhook DISCORD_WEBHOOK_BOOSTER, Kanal #booster-pack-deals) - mit Foto und Link.

Cardmarket ist NICHT dabei: hinter Cloudflare-Challenge, die weder mit echtem Headed-
Chromium noch camoufox durchkommt (getestet 2026-09-26, siehe Memory
feedback_cardmarket_nur_brave - nur der echte Brave-Browser des Nutzers mit bestehender
Session schafft das zuverlaessig, nicht automatisierbar).

Aufruf:
    python booster_deal_finder.py              -> normaler Lauf, postet neue Deals
    python booster_deal_finder.py --dry-run    -> nur Log/Ausgabe, kein Discord, kein State
    python booster_deal_finder.py --reset      -> vergisst bereits gemeldete Angebote

Technik: eBay UND Ricardo blocken echten Headless-Chrome (Cloudflare-Turnstile bzw. eBays
eigene Fehlerseite). Beide kommen aber mit einem "echten" (headed) Chromium-Prozess durch -
lokal (Windows) ausserhalb des sichtbaren Desktops positioniert, in der Cloud (GitHub
Actions) unter einem virtuellen Display (xvfb-run).
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
STATE_FILE = os.path.join(HERE, "booster_state.json")
LOG_FILE = os.path.join(HERE, "booster_finder.log")
LOCK_FILE = os.path.join(HERE, "booster_finder.lock")

DRY_RUN = "--dry-run" in sys.argv
RESET = "--reset" in sys.argv

# ---------------------------------------------------------------- Einstellungen
# Booster-Packs reichen preislich von ein paar Franken (moderne Sets) bis mehrere hundert/
# tausend Franken (versiegelte Vintage-Packs) - deutlich breiteres Fenster als bei Einzelkarten.
MIN_TOTAL = 4.0
MAX_TOTAL = 900.0
DEAL_RATIO = 0.72
MIN_SAMPLES = 4
MIN_MEDIAN = 8.0
MAX_POSTS_PER_RUN = 12
PAGE_WAIT_MS = 2500
RICARDO_SHIP_ESTIMATE = 8.0  # CHF - Ricardo-Kartenansicht zeigt keine Versandkosten, grobe Schaetzung

# name, suchbegriff-teil, ist_japanisch
SETS = [
    # ---- Vintage (WOTC-Aera, extrem wertvoll wenn wirklich versiegelt) ----
    ("Base Set", "base set", False),
    ("Base Set (JP)", "japanese base set", True),
    ("Jungle", "jungle", False),
    ("Fossil", "fossil", False),
    ("Team Rocket", "team rocket", False),
    ("Gym Heroes", "gym heroes", False),
    ("Gym Challenge", "gym challenge", False),
    ("Neo Genesis", "neo genesis", False),
    ("Neo Discovery", "neo discovery", False),
    ("Neo Revelation", "neo revelation", False),
    ("Neo Destiny", "neo destiny", False),
    # ---- Moderne, gefragte Sets mit bekannten Hit-Karten ----
    ("151", "151", False),
    ("Paldean Fates", "paldean fates", False),
    ("Prismatic Evolutions", "prismatic evolutions", False),
    ("Destined Rivals", "destined rivals", False),
    ("Journey Together", "journey together", False),
    ("Surging Sparks", "surging sparks", False),
    ("Twilight Masquerade", "twilight masquerade", False),
    ("Temporal Forces", "temporal forces", False),
    ("Paradox Rift", "paradox rift", False),
    ("Obsidian Flames", "obsidian flames", False),
    ("Evolving Skies", "evolving skies", False),
    ("Astral Radiance", "astral radiance", False),
    ("Crown Zenith", "crown zenith", False),
    ("Celebrations", "celebrations", False),
    ("White Flare", "white flare", False),
    ("Black Bolt", "black bolt", False),
    ("Mega Evolution", "mega evolution", False),
]

# Nur fuer schnelle Testlaeufe: BOOSTER_TEST_LIMIT=3 begrenzt SETS auf die ersten N Eintraege,
# damit ein Testlauf nicht jedes Mal 15+ Minuten dauert. In normalen/geplanten Laeufen NICHT
# gesetzt -> volle Liste.
_test_limit = os.environ.get("BOOSTER_TEST_LIMIT")
if _test_limit:
    SETS = SETS[: int(_test_limit)]

# Titel muss "Booster Pack" (oder Kurzform) enthalten, aber NICHT Box/Display/Case/Bulk-Lot.
PACK_PATTERN = re.compile(r"\bbooster\W{0,3}pack\b|\bboosterpack\b", re.I)
EXCLUDE_PACK = re.compile(
    r"\b(box|display|case|karton|bundle|lot\b|sammlung|collection|sealed\s*box|"
    r"\d{2,}\s*x\b|\d{2,}\s*stk|\d{2,}\s*stück|\d{2,}\s*pcs|"
    r"proxy|custom|fake|replica|orica|repro|empty|leer|geöffnet|opened|open\b|pulled|"
    r"einzelkarte|single\s*card|used|gebraucht|"
    r"sleeve\b|binder|toploader|deck\s*box|playmat|"
    # Digitale/virtuelle Codes (Pokemon TCG Live) sind ein komplett anderes Produkt als eine
    # physische Packung und wuerden den Median voellig verzerren (gefunden 2026-09-26: "Digital
    # Booster Pack Codes", "TCG Live Booster Pack Code Cards" landeten faelschlich im Pool).
    r"code\b|codes\b|tcg\s*live|digital|instant\s*delivery|e-?mail\s*delivery|"
    # Zubehoer/Merchandise, das nur zufaellig "Booster Pack" im Text erwaehnt, aber selbst
    # keine Packung ist (Portfolio/Album mit Packungs-Zugabe, unrelated Fanartikel).
    r"portfolio|album|magnet|puzzle|sticker\s*sheet|"
    # Promo-/Sonderausgaben (z.B. KFC-Tie-in) sind ein anderes Produkt als der Standard-
    # Retail-Booster und verzerren den Vergleichspreis ebenso.
    r"\bpromo\b|\bkfc\b)\b"
    r"|\b\d{1,3}\s*/\s*\d{1,3}\b",  # Kartennummer wie "200/197" -> Einzelkarten-Listing, kein Pack
    re.I,
)

# "Base Set" wird umgangssprachlich auch fuer das Starter-/Grundset JEDER Aera verwendet
# (z.B. "Sonne & Mond Base Set", "Sword & Shield Base Set") - das ist NICHT das wertvolle
# Original-Base-Set von 1996/1998. Bei Treffer auf "Base Set" muss sichergestellt sein, dass
# keine dieser modernen Aera-Marker im selben Titel stehen, sonst komplett falscher/verzerrter
# Vergleichspreis (Bug gefunden 2026-09-26: Median CHF 708 durch Vermischung mit ~5-CHF-Packs).
NOT_VINTAGE_ERA = re.compile(
    r"sonne\s*&?\s*mond|sun\s*&?\s*moon|sword\s*&?\s*shield|schwert\s*&?\s*schild|"
    r"scarlet\s*&?\s*violet|scharlachrot\s*&?\s*violett|black\s*&?\s*white(?!\s*promo)|"
    r"schwarz\s*&?\s*wei|diamond\s*&?\s*pearl|diamant\s*&?\s*perle|platinum|platin|"
    r"heartgold|soulsilver|xy\d|\bsm\d|\bswsh\d|\bsv\d\b",
    re.I,
)


def detect_set(t):
    for name, needle, is_jp in SETS:
        pat = re.escape(needle).replace(r"\ ", r"[\s-]*")
        if re.search(pat, t, re.I):
            if name in ("Base Set", "Base Set (JP)") and NOT_VINTAGE_ERA.search(t):
                continue
            return name, is_jp
    return None, False


def is_japanese_listing(t):
    if re.search(r"japan|japanese|japanisch|\bjp\b|\bjpn\b", t, re.I):
        return True
    return False


def parse_chf(s):
    if not s:
        return None
    m = re.search(r"([\d'.]+),(\d{2})\b", s)
    if m:
        return float(m.group(1).replace("'", "").replace(".", "") + "." + m.group(2))
    m2 = re.search(r"([\d'.]+)\.(\d{2})\b", s)
    if m2:
        return float(m2.group(1).replace("'", "").replace(",", ""))
    m3 = re.search(r"([\d'.]+)", s)
    return float(m3.group(1).replace("'", "").replace(",", "")) if m3 else None


def parse_shipping_ebay(s):
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


EBAY_EXTRACT_JS = r"""() => [...document.querySelectorAll('li.s-item, li.s-card')].map(li => {
  const a = li.querySelector('a[href*="/itm/"]');
  const id = a ? (a.href.match(/itm[/](\d+)/) || [])[1] : '';
  const t = (li.querySelector('.s-item__title, .s-card__title') || {}).textContent || '';
  const p = (li.querySelector('.s-item__price, .s-card__price') || {}).textContent || '';
  const s = (li.querySelector('.s-item__shipping, .s-item__logisticsCost, .s-card__shipping, [class*="shipping"], [class*="logistics"]') || {}).textContent || '';
  const img = li.querySelector('img');
  const locEl = (li.querySelector('.s-item__location, .s-item__itemLocation, [class*="location"]') || {}).textContent || '';
  const fromMatch = (li.innerText || '').match(/\baus ([^\n]+)/);
  const country = (locEl.replace(/^aus\s+/i, '').trim()) || (fromMatch ? fromMatch[1].trim() : '');
  return {id, t: t.replace('Wird in neuem Fenster oder Tab geöffnet', '').trim(), p: p.trim(), s: s.trim(),
          img: img ? (img.src || img.getAttribute('data-src') || '') : '', country};
}).filter(x => x.id && x.id !== '123456')"""

RICARDO_EXTRACT_JS = r"""() => [...document.querySelectorAll("a[href*='/a/']")].map(a => {
  const lines = (a.innerText || '').split('\n').map(s => s.trim()).filter(Boolean);
  const img = a.querySelector('img');
  return {href: a.href, lines, img: img ? (img.src || img.getAttribute('data-src') || '') : ''};
}).filter(x => x.lines.length)"""


def fetch_ebay(page):
    out = {}
    for name, needle, _jp in SETS:
        q = f"pokemon {needle} booster pack"
        url = ("https://www.ebay.ch/sch/i.html?_nkw=" + urllib.parse.quote(q) +
               f"&_sacat=183454&LH_BIN=1&_sop=10&_udlo={int(MIN_TOTAL)}&_udhi={int(MAX_TOTAL) + 60}&_ipg=120")
        rows = []
        for attempt in range(3):
            try:
                page.goto(url, timeout=60000, wait_until="domcontentloaded")
                page.wait_for_timeout(PAGE_WAIT_MS * (attempt + 1))
                rows = page.evaluate(EBAY_EXTRACT_JS)
            except Exception as e:
                log(f"eBay Fehler bei '{q}' (Versuch {attempt + 1}): {e}")
            if rows:
                break
            time.sleep(3)
        new = 0
        for r in rows:
            key = "ebay:" + r["id"]
            if key not in out:
                price = parse_chf(r["p"])
                if price is None:
                    continue
                ship = parse_shipping_ebay(r["s"])
                out[key] = dict(id=key, title=r["t"], price=price, ship=ship, total=round(price + ship, 2),
                                img=r["img"], url=f"https://www.ebay.ch/itm/{r['id']}", source="eBay.ch",
                                country=r.get("country") or "unbekannt", query=q)
                new += 1
        log(f"eBay '{q}': {len(rows)} Treffer, {new} neu")
        time.sleep(0.8)
    return out


def _ricardo_price_and_title(lines):
    if "Sofort kaufen" not in lines:
        return None, None
    idx = lines.index("Sofort kaufen")
    if idx == 0:
        return None, None
    price = parse_chf(lines[idx - 1])
    if price is None:
        return None, None
    bad_exact = {"Sofort kaufen", "Pokémon", "Boost", "Beliebt", "Neu", "Top-Artikel", "|"}
    candidates = [
        l for l in lines
        if len(l) >= 10 and l not in bad_exact and "Gebot" not in l
        and not re.match(r"^[\d.,']+$", l) and not re.search(r"^\(.*\)$", l)
        and not re.search(r"Heute|Morgen|Gestern|,\s*\d{1,2}:\d{2}", l)
    ]
    title = candidates[0] if candidates else lines[0]
    return price, title


def fetch_ricardo(browser):
    # Bug gefunden 2026-09-26: bei Wiederverwendung derselben Page ODER desselben Contexts
    # (nur frische Page pro Suche reichte NICHT) lieferte NUR die allererste Ricardo-Suche
    # echte Treffer, alle folgenden 0 - die Cloudflare-Freigabe scheint an den Browser-CONTEXT
    # (Cookies/Storage) gebunden zu sein und wird nach mehreren schnellen Suchen in derselben
    # Sitzung entzogen. Fix: pro Suche ein komplett frischer Context (= frischer "Besucher"),
    # nicht nur eine frische Page, plus deutlich mehr Wartezeit.
    out = {}
    for name, needle, _jp in SETS:
        q = f"pokemon {needle} booster pack"
        url = "https://www.ricardo.ch/de/s/" + urllib.parse.quote(q) + "/"
        rows = []
        for attempt in range(3):
            ctx = browser.new_context(locale="de-CH", viewport={"width": 1280, "height": 900})
            page = ctx.new_page()
            try:
                page.goto(url, timeout=45000, wait_until="domcontentloaded")
                page.wait_for_timeout(PAGE_WAIT_MS * (attempt + 1))
                rows = page.evaluate(RICARDO_EXTRACT_JS)
            except Exception as e:
                log(f"Ricardo Fehler bei '{q}' (Versuch {attempt + 1}): {e}")
            finally:
                ctx.close()
            if rows:
                break
            time.sleep(4)
        new = 0
        for r in rows:
            price, title = _ricardo_price_and_title(r["lines"])
            if price is None or not title:
                continue
            m = re.search(r"-(\d+)/?$", r["href"].rstrip("/"))
            item_id = m.group(1) if m else r["href"]
            key = "ricardo:" + item_id
            if key not in out:
                total = round(price + RICARDO_SHIP_ESTIMATE, 2)
                img = r["img"] or ""
                out[key] = dict(id=key, title=title, price=price, ship=RICARDO_SHIP_ESTIMATE, total=total,
                                img=img, url=r["href"], source="Ricardo.ch", country="Schweiz", query=q)
                new += 1
        log(f"Ricardo '{q}': {len(rows)} Treffer, {new} neu")
        time.sleep(3.0)
    return out


def classify(items):
    out = []
    for r in items.values():
        t = r["title"]
        if not PACK_PATTERN.search(t) or EXCLUDE_PACK.search(t):
            continue
        set_name, _ = detect_set(t)
        if not set_name:
            continue
        lang = "JP" if is_japanese_listing(t) else "EN"
        key = f"{lang}|{set_name}"
        img = r["img"]
        img = re.sub(r"/s-l\d+\.(webp|jpg)", "/s-l1600.jpg", img)
        img = re.sub(r"/t_\d+x\d+/", "/t_600x600/", img)
        out.append(dict(r, set=set_name, lang=lang, key=key, img=img))
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
    deals.sort(key=lambda d: d["ratio"])
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
        ship_note = "" if d["source"] == "eBay.ch" else " (Versand geschaetzt)"
        desc = (f"**CHF {d['total']:.2f}** inkl. Versand{ship_note} (Preis {d['price']:.2f} + Versand {d['ship']:.2f})\n"
                f"Set: **{d['set']}** ({d['lang']}) - Quelle: **{d['source']}** - Herkunft: {d.get('country', 'unbekannt')}\n"
                f"**{pct} % unter Median** (Median CHF {d['median']:.2f} aus {d['n']} aktuellen Angeboten eBay+Ricardo)\n"
                f"[Zum Angebot]({d['url']})")
        color = 0x2ECC71 if pct >= 40 else 0xF1C40F
        embeds.append({
            "title": d["title"][:240],
            "url": d["url"],
            "description": desc,
            "color": color,
            "image": {"url": d["img"]} if d["img"] else None,
            "footer": {"text": f"{d['source']} Sofort-Kaufen - gefunden {datetime.now():%d.%m.%Y %H:%M}"},
        })
    for e in embeds:
        if e.get("image") is None:
            e.pop("image", None)
    for i in range(0, len(embeds), 8):
        payload = {"username": "CHAU Booster-Deals", "embeds": embeds[i:i + 8]}
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(webhook, data=data, headers={"Content-Type": "application/json", "User-Agent": "chau-booster-finder/1.0"})
        with urllib.request.urlopen(req) as r:
            log(f"Discord {r.status} ({len(embeds[i:i + 8])} Embeds)")
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
        webhook = os.environ.get("DISCORD_WEBHOOK_BOOSTER") or ""
        if not webhook and not DRY_RUN and os.name == "nt":
            import subprocess
            webhook = subprocess.run(["powershell", "-NoProfile", "-Command", "[Environment]::GetEnvironmentVariable('DISCORD_WEBHOOK_BOOSTER','User')"],
                                     capture_output=True, text=True).stdout.strip()
        if not webhook and not DRY_RUN:
            log("Webhook DISCORD_WEBHOOK_BOOSTER fehlt.")
            return

        st = load_state()
        launch_args = ["--window-size=1280,900"]
        if os.name == "nt":
            launch_args.insert(0, "--window-position=-2400,-2400")
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=False, args=launch_args)
            ctx = browser.new_context(locale="de-CH", viewport={"width": 1280, "height": 900})
            page = ctx.new_page()
            items = {}
            items.update(fetch_ebay(page))
            items.update(fetch_ricardo(browser))
            browser.close()

        cards = classify(items)
        deals = find_deals(cards)
        log(f"{len(items)} Angebote geladen (eBay+Ricardo), {len(cards)} klassifiziert, {len(deals)} Deals gesamt")
        new = [d for d in deals if d["id"] not in st["seen"]][:MAX_POSTS_PER_RUN]
        for d in deals[:25]:
            log(f"  {'NEU ' if d['id'] not in st['seen'] else '    '}{d['ratio']:.2f}x CHF {d['total']:.2f} (Median {d['median']:.2f}, n={d['n']}) {d['key']} [{d['source']}] | {d['title'][:65]} | {d['url']}")

        if DRY_RUN:
            log(f"Dry-Run: {len(new)} neue Deals wuerden gepostet.")
            return
        if new:
            post_discord(webhook, new)
        for d in new:
            st["seen"][d["id"]] = datetime.now().strftime("%Y-%m-%d")
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
