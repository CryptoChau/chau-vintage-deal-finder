# -*- coding: utf-8 -*-
"""
CHAU PSA-Vintage-Ideen (Karten bis 2016)
========================================
Durchsucht eBay.ch (Sofort-Kaufen) ausschliesslich nach PSA-gegradeten (PSA 1-10) Pokemon-
Einzelkarten aus ALLEN Aeren von 1996 bis 2016 (WOTC, e-Card, EX-Aera inkl. Gold Stars, Diamond &
Pearl, Platinum, HGSS, Black & White, XY; englisch + japanisch; Holos, Reverse Holos, Gold Stars,
beliebte und seltene Karten) und schlaegt Angebote vor, die deutlich UNTER dem Median der anderen
aktuellen Angebote derselben Karte liegen (plus Preishistorie fruehere Laeufe).

Die Erkennung (Sets mit Aera, Pokemon-Namen, Varianten wie Gold Star/EX/LV.X/Prime/BREAK) kommt
aus vintage_deal_finder.py - beide Bots nutzen dieselbe Logik.

WICHTIG
- Nur PSA (kein BGS/CGC/ungegradet). Der EXAKTE Grade ist Teil des Vergleichsschluessels
  (Set + Karte + Variante + Sprache + Grade), PSA 9 und PSA 10 werden nie gemischt.
- Das sind VORSCHLAEGE, kein Marktwert: Sofort-Kaufen-Preise sind Wunschpreise der Verkaeufer.
  eBay-Verkaufspreise brauchen einen Login (nicht automatisierbar) - bitte in Collectr pruefen.
- Preisfenster 20-200 CHF inkl. Versand (Nutzerwunsch 2026-11-08, gilt fuer alle Aeren).

Aufruf:
    python psa_vintage_deal_finder.py              -> normaler Lauf, postet neue Ideen
    python psa_vintage_deal_finder.py --dry-run    -> nur Log/Ausgabe, kein Discord, kein State
    python psa_vintage_deal_finder.py --reset      -> vergisst bereits gemeldete Angebote
    PSA_QUERY_FILTER="gold star,xy"                -> nur Suchen mit diesen Teilstrings (Tests)

Technik: eBay blockt Headless-Browser (403). Es wird ein echtes Chromium-Fenster per Playwright
gestartet (lokal ausserhalb des Bildschirms, in der Cloud unter xvfb).
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

import vintage_deal_finder as v  # gemeinsame Erkennung (Sets/Aeren, Namen, Varianten)

HERE = os.path.dirname(os.path.abspath(__file__))
STATE_FILE = os.path.join(HERE, "psa_state.json")
LOG_FILE = os.path.join(HERE, "psa_finder.log")
LOCK_FILE = os.path.join(HERE, "psa_finder.lock")

DRY_RUN = "--dry-run" in sys.argv
RESET = "--reset" in sys.argv

# ---------------------------------------------------------------- Einstellungen
MIN_TOTAL = 20.0
MAX_TOTAL = 200.0       # CHF inkl. Versand, fuer ALLE Aeren (Nutzerwunsch 2026-11-08)
DEAL_RATIO = 0.70       # Preis <= 70 % des Medians der ANDEREN aktuellen Angebote (kein Marktwert!)
MIN_SAMPLES = 4         # mind. so viele andere Vergleichsangebote (inkl. Historie, ohne das Angebot selbst)
MIN_MEDIAN = 15.0
MAX_POSTS_PER_RUN = 12
PAGE_WAIT_MS = 2500
HISTORY_MAX_DAYS = 120  # so lange bleibt ein nicht mehr gesehenes Angebot als Preisreferenz erhalten
FETCH_BUDGET_S = 1200   # Zeitbudget fuer alle eBay-Suchen (Rest wird im naechsten Lauf wieder erreicht)

# Suchen: die Suchbegriffe des rohen Bots, aber "holo" raus (PSA-Titel nennen es oft nicht) und
# "psa" dazu. Gold Stars zuerst (hoechste Prioritaet), danach in Reihenfolge der Aeren.
_EXTRA = [
    "pokemon reverse holo psa", "pokemon ex reverse holo psa", "pokemon gold star psa 8", "pokemon gold star psa 9",
]


def _build_searches():
    out = []
    for q in list(v.SEARCHES) + _EXTRA:
        neg = ""
        if v.EXCL_MODERN_QUERY in q:
            q, neg = q.replace(v.EXCL_MODERN_QUERY, ""), v.EXCL_MODERN_QUERY
        if q not in _EXTRA:
            q = re.sub(r"\bholo\b", " ", q)
        q = re.sub(r"\s+", " ", q).strip()
        if not re.search(r"\bpsa\b", q):
            q += " psa"
        q += neg
        if q not in out:
            out.append(q)
    gold = [q for q in out if "gold star" in q]
    return gold + [q for q in out if q not in gold]


SEARCHES = _build_searches()
_qf = [s.strip().lower() for s in os.environ.get("PSA_QUERY_FILTER", "").split(",") if s.strip()]
if _qf:
    SEARCHES = [q for q in SEARCHES if any(f in q.lower() for f in _qf)]

# NUR PSA, exakter Grade 1-10. BGS/CGC/Beckett/SGC/ungegradet werden bewusst ignoriert.
PSA_GRADE = re.compile(r"\bpsa\s*-?\s*(10|[1-9])(?!\.?\d)\b", re.I)
OTHER_GRADER = re.compile(r"\b(bgs|cgc|beckett|ace|sgc|csg)\s*-?\s*(10|9\.5|9|8\.5|8|7\.5|7|6\.5|6|5\.5|5|4|3|2|1)\b", re.I)
# Reverse Holo ist hier ERWUENSCHT (eigene Variante im Schluessel), Non-Holo/Common nicht.
NONHOLO_ONLY = re.compile(r"non[\s-]?holo|no[\s-]?holo|nicht[\s-]?holo|\bregular\b|\bcommon\b|uncommon", re.I)
REVERSE_RE = re.compile(r"reverse|rev\.?\s*holo")
# Karten ab 2017, die v.MODERN nicht erkennt (Bug 2026-11-08: "Team Rocket's Mewtwo ex" / "Glory of
# Team Rocket" von 2025 wurde als WOTC-Set "Team Rocket" einsortiert; japanische Set-Codes M2a etc.).
MODERN_EXTRA = re.compile(
    r"glory\s*of\s*team\s*rocket|team\s*rocket'?s\s+\w+(?:\s+\w+)?\s+ex\b|"
    r"(?<![a-z0-9])m\d[a-z]?(?![a-z0-9])|mega\s*dream|inferno\s*x|nihil\s*zero|battle\s*partners|terastal|"
    r"super\s*electric|heat\s*wave|wild\s*force|cyber\s*judge|shiny\s*treasure|snow\s*hazard|clay\s*burst",
    re.I,
)
MAX_PER_KEY_PER_RUN = 2  # gleiche Karte (gleicher Schluessel) hoechstens 2x pro Lauf posten (kein Spam)


def log(msg):
    line = f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {msg}"
    print(line)
    with open(LOG_FILE, "a", encoding="utf-8") as fh:
        fh.write(line + "\n")


def fetch_all():
    items = {}
    launch_args = ["--window-size=1280,900"]
    if os.name == "nt":
        launch_args.insert(0, "--window-position=-2400,-2400")
    t0 = time.time()
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False, args=launch_args)
        page = browser.new_context(locale="de-CH", viewport={"width": 1280, "height": 900}).new_page()
        for q in SEARCHES:
            if time.time() - t0 > FETCH_BUDGET_S:
                log("Zeitbudget fuer eBay-Suchen aufgebraucht, restliche Suchen im naechsten Lauf")
                break
            url = ("https://www.ebay.ch/sch/i.html?_nkw=" + urllib.parse.quote(q) +
                   f"&_sacat=183454&LH_BIN=1&_sop=10&_udlo={int(MIN_TOTAL)}&_udhi={int(MAX_TOTAL) + 60}&_ipg=240")
            rows = []
            for attempt in range(3):
                try:
                    page.goto(url, timeout=60000, wait_until="domcontentloaded")
                    page.wait_for_timeout(PAGE_WAIT_MS * (attempt + 1))
                    rows = page.evaluate(v.EXTRACT_JS)
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
        if v.EXCLUDE.search(t) or NONHOLO_ONLY.search(t) or v.MODERN.search(t) or MODERN_EXTRA.search(v.norm(t)):
            continue
        # Nur PSA; andere Grader ohne PSA verwerfen.
        if OTHER_GRADER.search(full_text) and not PSA_GRADE.search(full_text):
            continue
        # Titel und eBay-"Artikelzustand" muessen denselben PSA-Grade nennen (Bug 2026-10-06:
        # Titel "PSA 9", Artikelzustand "Graded - PSA 8").
        grades = set(PSA_GRADE.findall(full_text))
        if len(grades) != 1:
            continue
        grade = f"PSA {grades.pop()}"
        price = v.parse_chf(r["p"])
        if price is None:
            continue
        ship = v.parse_shipping(r["s"])
        # Fehlt der Versandpreis in der Trefferliste (v.a. Auslandsverkaeufer), Pauschale einrechnen.
        ship_est = not re.search(r"CHF|kostenlos|gratis|free", r["s"] or "", re.I)
        if ship_est:
            ship = 8.0 if (r.get("country") or "").strip().lower() == "schweiz" else 25.0
        total = round(price + ship, 2)
        tn = v.norm(t)
        sset, era, sm = v.detect_set(tn)
        if not sset:
            continue
        tn_wo_set = (tn[:sm.start()] + " " + tn[sm.end():]) if sm else tn
        disp_name, base = v.detect_name(tn_wo_set)
        if not disp_name:
            disp_name, base = v.detect_name(tn)
        if not disp_name:
            continue
        tt = v.COND_STRIP.sub(" ", tn_wo_set)
        variant, tags = v.detect_variant(tt, base, era)
        rev = bool(REVERSE_RE.search(tn))
        if rev:
            tags = tags + ["Reverse Holo"]
        lang = "JP" if v.is_japanese(t) else "EN"
        key = f"{lang}|{sset}|{disp_name}|{variant}{'|RH' if rev else ''}|{grade}"
        img = re.sub(r"/s-l\d+\.(webp|jpg)", "/s-l1600.jpg", r["img"] or "")
        gold = "Gold Star" in tags
        popular = base in v.POPULAR
        rare = variant != "Std" or "Holo" in tags or rev
        tier = 0 if gold else 1 if (popular and rare) else 2 if (popular or rare) else 3
        out.append(dict(id=r["id"], title=t, price=price, ship=ship, ship_est=ship_est, total=total, name=disp_name,
                        set=sset, era=era, grade=grade, lang=lang, variant=variant, tags=tags, key=key, tier=tier,
                        img=img, url=f"https://www.ebay.ch/itm/{r['id']}", sub=r.get("sub", ""),
                        query=r.get("query", ""), country=r.get("country", "") or "unbekannt",
                        is_jp=v.is_japan_origin(r.get("country", ""))))
    return out


def reference_price(vals):
    """Median, nach Entfernen von Ausreissern (>2.5x oder <0.4x des Roh-Medians) nochmals Median.
    None, wenn danach zu wenig Angebote uebrig sind."""
    if len(vals) < MIN_SAMPLES:
        return None, 0
    raw = statistics.median(vals)
    kept = [x for x in vals if 0.4 * raw <= x <= 2.5 * raw]
    if len(kept) < MIN_SAMPLES:
        return None, len(kept)
    return statistics.median(kept), len(kept)


def find_deals(cards, history):
    # Pro Karte ein Pool {Angebots-ID: Gesamtpreis}: Historie frueherer Laeufe + aktuelle
    # Live-Angebote (live ueberschreibt die Historie derselben ID -> kein Doppelzaehlen).
    pool = {}
    for key, bucket in history.items():
        pool[key] = {lid: e["total"] for lid, e in bucket.items()}
    for c in cards:
        pool.setdefault(c["key"], {})[c["id"]] = c["total"]
    deals = []
    for c in cards:
        if not (MIN_TOTAL <= c["total"] <= MAX_TOTAL):
            continue
        others = [x for lid, x in pool[c["key"]].items() if lid != c["id"]]
        med, n = reference_price(others)
        if med is None or med < MIN_MEDIAN:
            continue
        if c["total"] <= DEAL_RATIO * med:
            deals.append(dict(c, median=round(med, 2), n=n, ratio=round(c["total"] / med, 2)))
    # Gold Stars zuerst, dann beliebte seltene Karten, dann der Rest; innerhalb einer Stufe
    # japanische Verkaeufer zuerst, danach groesster Abstand zum Median.
    deals.sort(key=lambda d: (d["tier"], 0 if d["is_jp"] else 1, d["ratio"]))
    return deals


def update_history(history, cards):
    today = datetime.now().strftime("%Y-%m-%d")
    for c in cards:
        if c["total"] <= MAX_TOTAL * 2:
            history.setdefault(c["key"], {})[c["id"]] = {"total": c["total"], "last_seen": today}
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
        # pro Karte hoechstens 30 Eintraege (neueste zuerst) - haelt psa_state.json klein
        if len(bucket) > 30:
            keep = sorted(bucket.items(), key=lambda kv: kv[1]["last_seen"], reverse=True)[:30]
            history[key] = dict(keep)
        if not history[key]:
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
        star = "⭐ GOLD STAR - " if "Gold Star" in d["tags"] else ""
        tags = ", ".join(t for t in d["tags"] if t != "Gold Star")
        ship_note = " GESCHAETZT" if d.get("ship_est") else ""
        desc = (f"**CHF {d['total']:.2f}** inkl. Versand (Preis {d['price']:.2f} + Versand {d['ship']:.2f}{ship_note})\n"
                f"Karte: **{d['name']}** - {d['set']} ({d['lang']}) - Grade: **{d['grade']}**"
                + (f" - {tags}" if tags else "") + "\n"
                f"Herkunft: **{d.get('country', 'unbekannt')}**\n"
                f"**{pct} % unter den anderen Angeboten** (Median CHF {d['median']:.2f} der {d['n']} anderen aktuellen eBay-Angebote)\n"
                f"Idee mit Potenzial, KEIN Marktwert: Angebotspreise sind Wunschpreise - bitte in Collectr (Sold/Graded) gegenpruefen.\n"
                f"[Zum Angebot]({d['url']})")
        color = 0xF1C40F if "Gold Star" in d["tags"] else (0x2ECC71 if pct >= 40 else 0x95A5A6)
        embeds.append({
            "title": (flag + star + d["title"])[:240],
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
        if time.time() - os.path.getmtime(LOCK_FILE) < 1800:
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
            import subprocess
            webhook = subprocess.run(["powershell", "-NoProfile", "-Command", "[Environment]::GetEnvironmentVariable('DISCORD_WEBHOOK_PSA','User')"],
                                     capture_output=True, text=True).stdout.strip()
        if not webhook and not DRY_RUN:
            log("Webhook DISCORD_WEBHOOK_PSA fehlt.")
            return
        st = load_state()
        items = fetch_all()
        cards = classify(items)
        deals = find_deals(cards, st["history"])
        log(f"{len(items)} Angebote geladen, {len(cards)} PSA-klassifiziert, {len(deals)} Deals gesamt")
        new, per_key = [], {}
        for d in deals:
            if d["id"] in st["seen"] or per_key.get(d["key"], 0) >= MAX_PER_KEY_PER_RUN:
                continue
            per_key[d["key"]] = per_key.get(d["key"], 0) + 1
            new.append(d)
            if len(new) >= MAX_POSTS_PER_RUN:
                break
        for d in deals[:25]:
            log(f"  {'NEU ' if d['id'] not in st['seen'] else '    '}T{d['tier']} {d['ratio']:.2f}x CHF {d['total']:.2f} (Median {d['median']:.2f}, n={d['n']}) {d['key']} | {d['title'][:70]} | {d['url']}")
        if DRY_RUN:
            log(f"Dry-Run: {len(new)} neue Ideen wuerden gepostet.")
            return
        if new:
            post_discord(webhook, new)
        update_history(st["history"], cards)
        for d in new:
            st["seen"][d["id"]] = datetime.now().strftime("%Y-%m-%d")
        cutoff = datetime.now().timestamp() - 60 * 86400
        st["seen"] = {k: x for k, x in st["seen"].items() if datetime.strptime(x, "%Y-%m-%d").timestamp() > cutoff}
        save_state(st)
        log(f"{len(new)} neue Ideen gepostet.")
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
