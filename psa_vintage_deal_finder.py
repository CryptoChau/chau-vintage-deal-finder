# -*- coding: utf-8 -*-
"""
CHAU PSA-Vintage-Ideen (Karten bis 2016)
========================================
Durchsucht eBay.ch (Sofort-Kaufen) nach PSA-gegradeten Pokemon-Einzelkarten bis 2016.
Bestaetigt wird nur ein belegter Gesamtpreis unter CHF 200 und unter dem Median echter
Verkaeufe derselben Identitaet. Angebotspreise und Preishistorie sind kein Median.

Die Erkennung und die Bestaetigung kommen aus vintage_deal_finder.py und
deal_validation.py. Dieselbe Identitaet (Nummer, Set, Jahr, Sprache, Finish,
Edition, Variante, exakter PSA-Grade) gilt fuer beide Bots.

WICHTIG
- Nur PSA 1-10. PSA 9 und PSA 10 werden nie gemischt. Ohne gelesenes Slab-Label
  (diese Codebasis oeffnet das Foto nicht) bleibt die Karte unverified.
- Angebotspreise und Preisguides sind kein Median. Bestaetigt wird nur ein
  belegter Gesamtpreis unter CHF 200, inklusive bekanntem Versand und bekannter
  Einfuhr, und nur unter dem Median echter Verkaeufe (mindestens 3, 90 sonst 180 Tage).
- Kein Post, wenn nichts bestaetigt ist.

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

import deal_validation as dv
import vintage_deal_finder as v  # gemeinsame Erkennung und Verkaufspruefung

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
    rare = [q for q in out if any(s in q for s in ("bulbasaur", "bisasam", "fushigidane"))]
    gold = [q for q in out if "gold star" in q and q not in rare]
    rest = [q for q in out if q not in rare and q not in gold]
    return rare + gold + rest


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
    line = f"[{dv.zurich_stamp()}] {msg}"
    print(line)
    with open(LOG_FILE, "a", encoding="utf-8") as fh:
        fh.write(line + "\n")


def fetch_all():
    from playwright.sync_api import sync_playwright
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
    """Dieselbe Identitaet wie der Roh-Bot, Modus PSA. Kein Pauschalversand."""
    return v.classify(items, mode="psa")


def find_deals(cards, history=None, solds_by_key=None, now=None, notes_by_key=None):
    """Angebots-Historie ist kein Verkaufsmedian. Siehe deal_validation.decide."""
    if history:
        log("Hinweis: gespeicherte Angebotspreise werden nicht als Verkaufsmedian verwendet.")
    return v.find_deals(cards, history=None, solds_by_key=solds_by_key, now=now, notes_by_key=notes_by_key)


def update_history(history, cards):
    """Keine neuen Angebotspreise als Referenz. Bestehende psa_state-Historie
    wird hier nicht fortgeschrieben und nicht geloescht."""
    return history


def load_state():
    if RESET or not os.path.exists(STATE_FILE):
        return dv.normalize_state({"seen": {}, "history": {}})
    try:
        st = json.load(open(STATE_FILE, encoding="utf-8"))
        return dv.normalize_state(st)
    except Exception:
        return dv.normalize_state({"seen": {}, "history": {}})


def save_state(st):
    if DRY_RUN or os.environ.get("DEAL_FINDER_NEVER_POST") == "1":
        raise RuntimeError("Dry-Run darf psa_state.json nicht schreiben")
    json.dump(st, open(STATE_FILE, "w", encoding="utf-8"), ensure_ascii=False, indent=0)


def post_discord(webhook, deals, now=None):
    if DRY_RUN or os.environ.get("DEAL_FINDER_NEVER_POST") == "1":
        raise RuntimeError("Dry-Run: kein Discord")
    if not deals:
        return []
    dv.assert_postable(deals)
    embeds = []
    for d in deals:
        pct = d["percent_below"]
        flag = "\U0001F1EF\U0001F1F5 " if d.get("is_jp") else ""
        star = "⭐ GOLD STAR - " if d.get("variant") == "Gold Star" else ""
        desc = dv.discord_description(d, now=now)
        color = 0xF1C40F if d.get("variant") == "Gold Star" else (0x2ECC71 if pct >= 40 else 0x95A5A6)
        embeds.append({
            "title": (flag + star + d["title"])[:240],
            "url": d["url"],
            "description": desc,
            "color": color,
            "image": {"url": d["img"]} if d["img"] else None,
            "footer": {"text": dv.discord_footer(d, now=now)},
            "_deal": d,
        })
    sent = []
    for i in range(0, len(embeds), 6):
        batch = embeds[i:i + 6]
        payload_embeds = []
        for e in batch:
            item = {k: v for k, v in e.items() if k != "_deal"}
            if item.get("image") is None:
                item.pop("image", None)
            payload_embeds.append(item)
        payload = {"username": "CHAU PSA-Vintage-Ideen", "embeds": payload_embeds}
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(webhook, data=data, headers={"Content-Type": "application/json", "User-Agent": "chau-psa-deal-finder/1.0"})
        try:
            with urllib.request.urlopen(req) as r:
                status = getattr(r, "status", None)
                log(f"Discord {status} ({len(batch)} Embeds)")
                if status not in (200, 204):
                    raise RuntimeError(f"Discord antwortete mit Status {status}")
        except Exception as e:
            e.sent = list(sent)
            raise
        sent.extend(e["_deal"] for e in batch)
        time.sleep(1.5)
    return sent


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
        now = dv.zurich_now()
        items = fetch_all()
        cards = classify(items)
        decisions, confirmed = v.resolve_deals(cards, now=now)
        log(f"{len(items)} Angebote geladen, {len(cards)} PSA-klassifiziert, {len(confirmed)} bestaetigte Deals")
        new = dv.select_new(confirmed, set(st.get("posted") or {}), per_key_cap=MAX_PER_KEY_PER_RUN, limit=MAX_POSTS_PER_RUN)
        for d in decisions:
            if d.get("status") == "confirmed":
                log(f"  CONFIRMED {d.get('percent_below')}% CHF {d.get('all_in_chf'):.2f} n={d.get('n')} {d.get('key')} | {d.get('title','')[:70]}")
            else:
                log(f"  {d.get('status')} {'; '.join((d.get('reasons') or [])[:4])} | {(d.get('title') or '')[:70]}")
        if DRY_RUN:
            log(f"Dry-Run: {len(new)} bestaetigte Deals wuerden gepostet. Kein Discord, kein State.")
            return
        sent = []
        post_error = None
        if new:
            try:
                sent = post_discord(webhook, new, now=now) or []
            except Exception as e:
                post_error = e
                sent = list(getattr(e, "sent", None) or [])
        else:
            log("Keine qualifizierten Deals. Kein Discord-Post.")
        today = now.strftime("%Y-%m-%d")
        for c in cards:
            st["seen"][c["id"]] = today
        for d in sent:
            st["posted"][d["id"]] = today
        cutoff = now.timestamp() - 60 * 86400
        def _fresh(mapping):
            kept = {}
            for k, val in (mapping or {}).items():
                try:
                    ts = datetime.strptime(val, "%Y-%m-%d").replace(tzinfo=dv.ZURICH).timestamp()
                except Exception:
                    continue
                if ts > cutoff:
                    kept[k] = val
            return kept
        st["seen"] = _fresh(st.get("seen"))
        st["posted"] = _fresh(st.get("posted"))
        update_history(st.get("history") or {}, cards)
        save_state(st)
        log(f"Geprueft {len(cards)}, qualifiziert {len(confirmed)}, gepostet {len(sent)}.")
        if post_error is not None:
            log(f"Discord-Fehler nach {len(sent)} erfolgreichen Posts, Rest nicht als posted markiert: {post_error}")
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
