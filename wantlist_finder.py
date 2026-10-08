# -*- coding: utf-8 -*-
"""
CHAU Wunschlisten-Finder
=========================
Sucht nach ganz bestimmten Karten aus `wantlist.json` (Nutzerwunsch 2026-10-08) auf
eBay.ch, eBay.de, eBay.com und Ricardo.ch und postet JEDES neue Angebot mit dem
GESAMTPREIS IN CHF (Preis + Versand + geschaetzter Zoll/MWST bei Auslandskaeufen) in Discord
(Webhook DISCORD_WEBHOOK_WANTLIST, Kanal #wunschliste-deals). Keine harte Preisgrenze - der
Nutzer entscheidet selbst; Posts erscheinen nur bis `post_up_to_ratio` x Cardmarket-Trend
(Rauschen vermeiden), mit Zustand (aus dem Titel) und Vergleich zum Cardmarket-Trend.

Nicht automatisch moeglich (Cloudflare): Cardmarket. Japan-Maerkte (Mercari JP, Yahoo
Auctions, Suruga-ya) und TCGplayer: Phase 2, erst nach Einzeltest.

Aufruf:
    python wantlist_finder.py             -> normaler Lauf
    python wantlist_finder.py --dry-run   -> nur Log, kein Discord, kein State
    python wantlist_finder.py --reset     -> vergisst bereits gemeldete Angebote

Technik wie bei den anderen Findern: echter Headed-Chromium (eBay/Ricardo blocken Headless);
in GitHub Actions unter xvfb-run.
"""
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime

from playwright.sync_api import sync_playwright

HERE = os.path.dirname(os.path.abspath(__file__))
STATE_FILE = os.path.join(HERE, "wantlist_state.json")
LOG_FILE = os.path.join(HERE, "wantlist_finder.log")
LOCK_FILE = os.path.join(HERE, "wantlist_finder.lock")
WANTLIST_FILE = os.path.join(HERE, "wantlist.json")

DRY_RUN = "--dry-run" in sys.argv
RESET = "--reset" in sys.argv

PAGE_WAIT_MS = 2500
MAX_POSTS_PER_RUN = 40
MAX_POSTS_PER_CARD = 10   # damit jede Wunschkarte pro Lauf drankommt (guenstigste zuerst)
RICARDO_SHIP_ESTIMATE = 8.0    # CHF, Ricardo-Trefferliste zeigt keinen Versand
FOREIGN_SHIP_DEFAULT = {"EUR": 12.0, "USD": 20.0}   # falls eBay keinen Versand nennt
# Schweizer Einfuhr: MWST 8.1 % + Post-Verzollungsgebuehr, erst ab ca. CHF 62 Warenwert
# (darunter Steuerbetrag < CHF 5, wird nicht erhoben). Zoll auf Sammelkarten: 0 %. Schaetzung!
VAT_RATE = 0.081
VAT_FREE_BELOW = 62.0
CUSTOMS_HANDLING_FEE = 13.0
FX_FALLBACK = {"CHF": 1.0, "EUR": 0.93, "USD": 0.80, "JPY": 0.0053}   # CHF je 1 Einheit

_EXCL_COMMON = (
    r"proxy|custom|fake|replica|orica|repro|reprint|digital|code|codes|tcg\s*live|"
    r"sticker|magnet|poster|puzzle|plush|"
    r"sleeve|binder|playmat|extended\s*art|altered|art\s*card|"
    r"f(ü|u|ue)r\s*(psa|cgc|bgs)|for\s*(psa|cgc|bgs)|toploader|ultra\s*pro"
)
_LOT_WORDS = r"lot|bulk|konvolut|sammlung"
EXCLUDE_LOT = re.compile(r"\b(" + _EXCL_COMMON + r"|booster|display|case|empty)\b", re.I)
# Einzelkarten: Sealed-Begriffe ausschliessen. Sealed-Karten ("sealed": true): erlaubt.
EXCLUDE = re.compile(r"\b(" + _EXCL_COMMON + r"|" + _LOT_WORDS + r"|bundle|collection|booster|display|case)\b", re.I)
EXCLUDE_SEALED = re.compile(r"\b(" + _EXCL_COMMON + r"|" + _LOT_WORDS + r"|empty|leer|opened|geöffnet|geoeffnet|open\s*box|resealed|"
                            r"single\s*cards?|einzelkarte|pack\s*only|nur\s*pack|ohne\s*(box|display)|"
                            r"without\s*box|(1|one|einzel)\s*(booster\s*)?pack)\b", re.I)

CONDITIONS = [
    (r"\b(gem\s*mint|psa\s*10|bgs\s*10|cgc\s*10)\b", "Gem Mint / Graded 10"),
    (r"\b(psa|bgs|cgc|ags)\s*-?\s*\d{1,2}(\.\d)?\b|graded|slab", "Gegradet"),
    (r"\b(near\s*mint|nm|mint|nm/m|m/nm|nm-mt)\b", "NM"),
    (r"\b(excellent|ex-?mt|lightly\s*played|lp|light\s*play|leicht\s*bespielt|lp/ex)\b", "EX/LP"),
    (r"\b(moderately\s*played|moderate[d]?\s*play(ed)?|mp|good|gut\s*bespielt|played|bespielt|gespielt)\b", "MP/Played"),
    (r"\b(heavily\s*played|hp|poor|dmg|damaged|beschädigt|beschaedigt|stark\s*bespielt)\b", "HP/Damaged"),
]


def log(msg):
    line = f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {msg}"
    print(line)
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except OSError:
        pass


def get_fx():
    fx = dict(FX_FALLBACK)
    try:
        req = urllib.request.Request("https://open.er-api.com/v6/latest/CHF", headers={"User-Agent": "chau-wantlist/1.0"})
        with urllib.request.urlopen(req, timeout=15) as r:
            rates = json.load(r)["rates"]
        for cur in ("EUR", "USD", "JPY"):
            if rates.get(cur):
                fx[cur] = 1.0 / rates[cur]
        log("Wechselkurse geladen: " + ", ".join(f"{c}={fx[c]:.4f}" for c in fx))
    except Exception as e:
        log(f"Wechselkurse nicht ladbar ({e}), Fallback.")
    return fx


def parse_price(s):
    """Gibt (Betrag, Waehrung) zurueck. Nimmt bei Bereichen ('X bis Y') den ersten Betrag."""
    if not s:
        return None, None
    cur = "CHF"
    if re.search(r"EUR|€", s):
        cur = "EUR"
    elif re.search(r"US\s*\$|USD|\$", s):
        cur = "USD"
    elif re.search(r"JPY|¥|円", s):
        cur = "JPY"
    m = re.search(r"(\d[\d'.,]*)", s)
    if not m:
        return None, cur
    num = m.group(1).replace("'", "")
    if "," in num and "." in num:
        # letztes Trennzeichen = Dezimaltrenner
        if num.rfind(",") > num.rfind("."):
            num = num.replace(".", "").replace(",", ".")
        else:
            num = num.replace(",", "")
    elif "," in num:
        num = num.replace(",", ".") if re.search(r",\d{1,2}$", num) else num.replace(",", "")
    elif "." in num and not re.search(r"\.\d{1,2}$", num):
        num = num.replace(".", "")
    try:
        return float(num), cur
    except ValueError:
        return None, cur


def parse_shipping(s, cur):
    if not s:
        return None
    if re.search(r"kostenlos|gratis|free", s, re.I):
        return 0.0
    v, c = parse_price(s)
    return v if v is not None else None


def detect_condition(t):
    for pat, label in CONDITIONS:
        if re.search(pat, t, re.I):
            return label
    return "k. A."


def detect_lang(t):
    if re.search(r"japan|japanese|japanisch|\bjp\b|\bjpn\b|日本", t, re.I):
        return "JP"
    return "EN"


def landed_chf(price, ship, cur, country, fx):
    """Gesamtpreis in CHF inkl. Versand und geschaetzter Einfuhrabgaben."""
    rate = fx.get(cur, 1.0)
    base = (price + ship) * rate
    foreign = (cur != "CHF") or (country and country.lower() not in ("schweiz", "switzerland", "suisse", "svizzera", ""))
    duty = 0.0
    if foreign and base >= VAT_FREE_BELOW:
        duty = base * VAT_RATE + CUSTOMS_HANDLING_FEE
    return round(base + duty, 2), round(duty, 2), bool(foreign)


EBAY_EXTRACT_JS = r"""() => [...document.querySelectorAll('li.s-item, li.s-card')].map(li => {
  const a = li.querySelector('a[href*="/itm/"]');
  const id = a ? (a.href.match(/itm[/](\d+)/) || [])[1] : '';
  const t = (li.querySelector('.s-item__title, .s-card__title') || {}).textContent || '';
  const p = (li.querySelector('.s-item__price, .s-card__price') || {}).textContent || '';
  const s = (li.querySelector('.s-item__shipping, .s-item__logisticsCost, .s-card__shipping, [class*="shipping"], [class*="logistics"]') || {}).textContent || '';
  const img = li.querySelector('img');
  const locEl = (li.querySelector('.s-item__location, .s-item__itemLocation, [class*="location"]') || {}).textContent || '';
  const fromMatch = (li.innerText || '').match(/\b(?:aus|from) ([^\n]+)/);
  const country = (locEl.replace(/^(aus|from)\s+/i, '').trim()) || (fromMatch ? fromMatch[1].trim() : '');
  return {id, t: t.replace('Wird in neuem Fenster oder Tab geöffnet', '').replace('Opens in a new window or tab', '').trim(),
          p: p.trim(), s: s.trim(), img: img ? (img.src || img.getAttribute('data-src') || '') : '', country};
}).filter(x => x.id && x.id !== '123456')"""

RICARDO_EXTRACT_JS = r"""() => [...document.querySelectorAll("a[href*='/a/']")].map(a => {
  const lines = (a.innerText || '').split('\n').map(s => s.trim()).filter(Boolean);
  const img = a.querySelector('img');
  return {href: a.href, lines, img: img ? (img.src || img.getAttribute('data-src') || '') : ''};
}).filter(x => x.lines.length)"""

EBAY_SITES = [
    ("eBay.ch", "https://www.ebay.ch/sch/i.html"),
    ("eBay.de", "https://www.ebay.de/sch/i.html"),
    ("eBay.com", "https://www.ebay.com/sch/i.html"),
]


def fetch_ebay(page, card):
    out = {}
    for site, base in EBAY_SITES:
        if card.get("sources") and site not in card["sources"]:
            continue
        for q in card["queries"]:
            url = f"{base}?_nkw={urllib.parse.quote(q)}&_sacat=183454&LH_BIN=1&_sop=15&_ipg=120"
            rows = []
            for attempt in range(3):
                try:
                    page.goto(url, timeout=60000, wait_until="domcontentloaded")
                    page.wait_for_timeout(PAGE_WAIT_MS * (attempt + 1))
                    rows = page.evaluate(EBAY_EXTRACT_JS)
                except Exception as e:
                    log(f"{site} Fehler bei '{q}' (Versuch {attempt + 1}): {e}")
                if rows:
                    break
                time.sleep(3)
            new = 0
            for r in rows:
                price, cur = parse_price(r["p"])
                if price is None:
                    continue
                ship = parse_shipping(r["s"], cur)
                ship_est = ship is None
                if ship_est:
                    ship = RICARDO_SHIP_ESTIMATE if cur == "CHF" else FOREIGN_SHIP_DEFAULT.get(cur, 15.0)
                key = f"ebay:{r['id']}"
                if key not in out:
                    host = base.split("/")[2].replace("www.", "")
                    out[key] = dict(id=key, title=r["t"], price=price, cur=cur, ship=ship, ship_est=ship_est,
                                    img=r["img"], url=f"https://www.{host}/itm/{r['id']}", source=site,
                                    country=r.get("country") or "")
                    new += 1
            log(f"{site} '{q}': {len(rows)} Treffer, {new} neu")
            time.sleep(0.8)
    return out


def fetch_ricardo(browser, card):
    out = {}
    for q in card["queries"]:
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
            lines = r["lines"]
            if "Sofort kaufen" not in lines:
                continue
            idx = lines.index("Sofort kaufen")
            if idx == 0:
                continue
            price, _ = parse_price(lines[idx - 1])
            if price is None:
                continue
            cands = [l for l in lines if len(l) >= 10 and l != "Sofort kaufen" and "Gebot" not in l
                     and not re.match(r"^[\d.,']+$", l) and not re.search(r"Heute|Morgen|Gestern|,\s*\d{1,2}:\d{2}", l)]
            title = cands[0] if cands else lines[0]
            m = re.search(r"-(\d+)/?$", r["href"].rstrip("/"))
            key = "ricardo:" + (m.group(1) if m else r["href"])
            if key not in out:
                out[key] = dict(id=key, title=title, price=price, cur="CHF", ship=RICARDO_SHIP_ESTIMATE, ship_est=True,
                                img=r["img"], url=r["href"], source="Ricardo.ch", country="Schweiz")
                new += 1
        log(f"Ricardo '{q}': {len(rows)} Treffer, {new} neu")
        time.sleep(3.0)
    return out


def parse_qty(t):
    """Stueckzahl aus Titel ('10x', 'x10', '25 Stk', 'Playset', '4er Set'); 1 wenn keine Angabe."""
    m = re.search(r"\b(\d{1,3})\s*[x×]\b|\b[x×]\s*(\d{1,3})\b|\b(\d{1,3})\s*(?:stk|stück|stueck|pcs|pieces|cards|karten|st\.)|\b(\d{1,3})er\s*(?:set|pack|lot)|\b(?:lot|set|bundle)\s*(?:of|von)\s*(\d{1,3})\b", t, re.I)
    if m:
        for g in m.groups():
            if g:
                return int(g)
    if re.search(r"play\s*-?set", t, re.I):
        return 4
    return 1


def matches_card(card, title):
    t = title
    excl = EXCLUDE_SEALED if card.get("sealed") else (EXCLUDE_LOT if card.get("lot") else EXCLUDE)
    if excl.search(t):
        return False
    if not re.search(card["match"], t, re.I):
        return False
    if card.get("require") and not re.search(card["require"], t, re.I):
        return False
    if card.get("reject") and re.search(card["reject"], t, re.I):
        return False
    other = card.get("other_number_regex")
    if other and re.search(other, t):
        return False
    nums = card.get("number", [])
    words = card.get("number_words", [])
    if nums or words:
        ok = any(n.lower() in t.lower() for n in nums) or any(w.lower() in t.lower() for w in words)
        if not ok:
            return False
    return True


def evaluate(card, items, fx):
    ref_chf = card["ref_eur"] * fx["EUR"] if card.get("ref_eur") else None
    results = []
    for it in items.values():
        if not matches_card(card, it["title"]):
            continue
        if card.get("swiss_only") and it["country"] and it["country"].lower() not in (
                "schweiz", "switzerland", "suisse", "svizzera"):
            continue   # nur Verkaeufer aus der Schweiz (unbekannter Standort bleibt drin)
        lang = detect_lang(it["title"])
        if lang not in card.get("langs", ["EN", "JP"]):
            continue
        total, duty, foreign = landed_chf(it["price"], it["ship"], it["cur"], it["country"], fx)
        qty = parse_qty(it["title"]) if card.get("lot") else 1
        if card.get("lot") and qty < card.get("min_qty", 2):
            continue
        per_piece = total / qty
        if total < card.get("min_chf", 0):
            continue
        if card.get("max_chf") and total > card["max_chf"]:
            continue
        ratio = per_piece / ref_chf if ref_chf else None
        if ratio is not None and ratio > card.get("post_up_to_ratio", 1.15):
            continue
        img = re.sub(r"/s-l\d+\.(webp|jpg)", "/s-l1600.jpg", it["img"] or "")
        results.append(dict(it, total=total, duty=duty, foreign=foreign, lang=lang,
                            condition=detect_condition(it["title"]), ref_chf=ref_chf, ratio=ratio, img=img,
                            qty=qty, per_piece=round(per_piece, 2),
                            card_id=card["id"], card_name=card["name"], cardmarket=card.get("cardmarket", "")))
    results.sort(key=lambda r: r["total"])
    return results


def post_discord(webhook, deals):
    embeds = []
    for d in deals:
        parts = [f"**CHF {d['total']:.2f}** Gesamtpreis inkl. Versand" + (" + Zoll/MWST (geschaetzt)" if d["duty"] else "")]
        orig = f"{d['price']:.2f} {d['cur']}"
        ship_note = " (geschaetzt)" if d["ship_est"] else ""
        parts.append(f"Angebot: {orig} + Versand {d['ship']:.2f} {d['cur']}{ship_note}"
                     + (f" + Einfuhr ca. CHF {d['duty']:.2f}" if d["duty"] else ""))
        parts.append(f"Karte: **{d['card_name']}** - Sprache: {d['lang']} - Zustand: **{d['condition']}** - Quelle: **{d['source']}**"
                     + (f" - aus {d['country']}" if d["country"] else ""))
        if d.get("qty", 1) > 1:
            parts.insert(1, f"**{d['qty']} Stueck -> CHF {d['per_piece']:.2f} pro Stueck** (Stueckzahl aus dem Titel, bitte pruefen)")
        if d["ref_chf"]:
            diff = int(round((d["per_piece"] / d["ref_chf"] - 1) * 100))
            word = "ueber" if diff > 0 else "unter"
            parts.append(f"Cardmarket-Trend ca. CHF {d['ref_chf']:.2f} -> dieses Angebot (pro Stueck) **{abs(diff)} % {word} Trend**")
        parts.append(f"[Zum Angebot]({d['url']})" + (f" - [Cardmarket]({d['cardmarket']})" if d["cardmarket"] else ""))
        color = 0x2ECC71 if d["ratio"] and d["ratio"] <= 0.9 else (0xF1C40F if d["ratio"] and d["ratio"] <= 1.0 else 0xE67E22)
        e = {"title": d["title"][:240], "url": d["url"], "description": "\n".join(parts), "color": color,
             "footer": {"text": f"{d['source']} Sofort-Kaufen - gefunden {datetime.now():%d.%m.%Y %H:%M}"}}
        if d["img"]:
            e["image"] = {"url": d["img"]}
        embeds.append(e)
    for i in range(0, len(embeds), 8):
        payload = {"username": "CHAU Wunschliste", "embeds": embeds[i:i + 8]}
        req = urllib.request.Request(webhook, data=json.dumps(payload).encode("utf-8"),
                                     headers={"Content-Type": "application/json", "User-Agent": "chau-wantlist/1.0"})
        with urllib.request.urlopen(req) as r:
            log(f"Discord {r.status} ({len(embeds[i:i + 8])} Embeds)")
        time.sleep(1.5)


def diagnose(card, items, fx):
    """Gibt alle LOSE passenden Angebote mit Ablehnungsgrund aus (WANTLIST_DIAG=<card id>)."""
    loose = re.compile(card["match"], re.I)
    rows = []
    for it in items.values():
        if not loose.search(it["title"]):
            continue
        t = it["title"]
        why = []
        excl = EXCLUDE_SEALED if card.get("sealed") else (EXCLUDE_LOT if card.get("lot") else EXCLUDE)
        m = excl.search(t)
        if m:
            why.append(f"excl:{m.group(0)}")
        if card.get("require") and not re.search(card["require"], t, re.I):
            why.append("require")
        m = card.get("reject") and re.search(card["reject"], t, re.I)
        if m:
            why.append(f"reject:{m.group(0)}")
        nums, words = card.get("number", []), card.get("number_words", [])
        if (nums or words) and not (any(n.lower() in t.lower() for n in nums) or any(w.lower() in t.lower() for w in words)):
            why.append("number")
        total, duty, foreign = landed_chf(it["price"], it["ship"], it["cur"], it["country"], fx)
        qty = parse_qty(t)
        ref = card["ref_eur"] * fx["EUR"] if card.get("ref_eur") else None
        pp = total / qty
        if card.get("lot") and qty < card.get("min_qty", 2):
            why.append(f"qty{qty}")
        if card.get("swiss_only") and it["country"] and it["country"].lower() not in ("schweiz", "switzerland", "suisse", "svizzera"):
            why.append(f"land:{it['country']}")
        if ref and pp / ref > card.get("post_up_to_ratio", 1.15):
            why.append(f"ratio{pp / ref:.2f}")
        if card.get("min_chf") and total < card["min_chf"]:
            why.append("min_chf")
        rows.append((total, f"DIAG CHF {total:7.2f} q{qty} [{it['source']}|{it['country'][:12]}] {','.join(why) or 'OK'} | {t[:85]} | {it['url']}"))
    for _, line in sorted(rows)[:60]:
        log(line)
    log(f"DIAG {card['id']}: {len(rows)} lose Treffer")


def load_state():
    if RESET or not os.path.exists(STATE_FILE):
        return {"seen": {}}
    try:
        st = json.load(open(STATE_FILE, encoding="utf-8"))
        st.setdefault("seen", {})
        return st
    except Exception:
        return {"seen": {}}


def acquire_lock():
    if os.path.exists(LOCK_FILE) and time.time() - os.path.getmtime(LOCK_FILE) < 900:
        return False
    open(LOCK_FILE, "w").write(str(os.getpid()))
    return True


def main():
    if not acquire_lock():
        log("Ein anderer Lauf ist bereits aktiv, abgebrochen.")
        return
    try:
        webhook = (os.environ.get("DISCORD_WEBHOOK_WANTLIST") or "").strip().lstrip("﻿")
        if not webhook and not DRY_RUN and os.name == "nt":
            import subprocess
            webhook = subprocess.run(["powershell", "-NoProfile", "-Command",
                                      "[Environment]::GetEnvironmentVariable('DISCORD_WEBHOOK_WANTLIST','User')"],
                                     capture_output=True, text=True).stdout.strip()
        if not webhook and not DRY_RUN:
            log("Webhook DISCORD_WEBHOOK_WANTLIST fehlt.")
            return

        cards = json.load(open(WANTLIST_FILE, encoding="utf-8"))["cards"]
        fx = get_fx()
        st = load_state()
        launch_args = ["--window-size=1280,900"]
        if os.name == "nt":
            launch_args.insert(0, "--window-position=-2400,-2400")
        all_new = []
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=False, args=launch_args)
            ctx = browser.new_context(locale="de-CH", viewport={"width": 1280, "height": 900})
            page = ctx.new_page()
            only = os.environ.get("WANTLIST_ONLY", "").strip()
            diag_id = os.environ.get("WANTLIST_DIAG", "").strip()
            for card in cards:
                if only and card["id"] not in only.split(","):
                    continue
                log(f"=== {card['name']} ===")
                fc = dict(card, sources=None) if (diag_id and card["id"] in diag_id.split(",")) else card  # Diagnose: alle Maerkte
                items = {}
                items.update(fetch_ebay(page, fc))
                if not fc.get("sources") or "Ricardo.ch" in fc["sources"]:
                    items.update(fetch_ricardo(browser, fc))
                res = evaluate(card, items, fx)
                if diag_id and card["id"] in diag_id.split(","):
                    diagnose(card, items, fx)
                log(f"{len(items)} Angebote geladen, {len(res)} passend")
                for r in res[:15]:
                    flag = "NEU " if r["id"] not in st["seen"] else "    "
                    log(f"  {flag}CHF {r['total']:.2f} ({r['condition']}, {r['lang']}) [{r['source']}] {r['title'][:70]} | {r['url']}")
                all_new += [r for r in res if r["id"] not in st["seen"]][:MAX_POSTS_PER_CARD]
            browser.close()

        all_new = all_new[:MAX_POSTS_PER_RUN]
        if DRY_RUN:
            log(f"Dry-Run: {len(all_new)} neue Treffer wuerden gepostet.")
            return
        if all_new:
            post_discord(webhook, all_new)
        for d in all_new:
            st["seen"][d["id"]] = datetime.now().strftime("%Y-%m-%d")
        cutoff = datetime.now().timestamp() - 90 * 86400
        st["seen"] = {k: v for k, v in st["seen"].items() if datetime.strptime(v, "%Y-%m-%d").timestamp() > cutoff}
        json.dump(st, open(STATE_FILE, "w", encoding="utf-8"), ensure_ascii=False, indent=0)
        log(f"{len(all_new)} neue Treffer gepostet.")
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
