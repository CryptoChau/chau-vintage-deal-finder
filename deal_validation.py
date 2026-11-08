# -*- coding: utf-8 -*-
"""Gemeinsame Identitaet und Deal-Bestaetigung fuer Vintage- und PSA-Finder.

Root cause 2026-11-08, Discord #ebay-vintage-deals, Lauf
https://github.com/CryptoChau/chau-vintage-deal-finder/actions/runs/42433564704
(Commit 63e2fad, vor dem Set-Alias "XY Emerald Break"):

Der Titel "M Latios-EX RR 049/078 XY6 Emerald Break" traf das Muster
"emerald" und landete als JP|EX Emerald|Latios|Mega EX|raw ?. Das Wort
"Break" blieb als Variante BREAK im Text haengen, der Schluessel nahm aber
Mega EX. Drei andere Wunschpreise derselben falschen Identitaet (148.01,
181.99, 118.44) ergaben den Median 148.01. Der Post nannte das "66 % unter
Median" bei CHF 50.25, davon CHF 25 pauschal geschaetzter Auslandversand.
Collectr-Richtwert und Angebotspreise sind keine Verkaufsmediane.

Bestaetigt wird nur eine exakte Identitaet (Nummer, Set, Ursprungsjahr,
Sprache, Finish, Edition, Variante, Zustand) mit belegtem CH-Gesamtpreis
und mindestens drei unterschiedlichen oeffentlichen Verkaeufen im 90-Tage-
Fenster, sonst 180 Tage. PSA zusaetzlich nur unter CHF 200 und nur wenn das
Listing-Foto per lokalem OCR zum Label passt. Das ist kein Zertifikatscheck.
"""
from __future__ import annotations

import re
import statistics
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import slab_label

ZURICH = ZoneInfo("Europe/Zurich")

MIN_ALL_IN = 20.0
MAX_RAW = 250.0
MAX_RAW_GOLD = 600.0
# Eigenes Roh-Budget fuer Bulbasaur/Bisasam. Nicht in das PSA-Limit unter
# CHF 200 und nicht in das allgemeine Roh-Limit ziehen.
MAX_RAW_BULBASAUR = 400.0
MAX_PSA = 200.0  # strikt darunter
SOLD_MIN = 3
SOLD_WINDOWS = (90, 180)
SOLD_LOOKUP_CAP = 12

CH_COUNTRIES = {"schweiz", "switzerland", "suisse", "svizzera", "ch"}

# Sets mit einem belegbaren Ursprungsjahr. Fehlende Sprache = kein Jahr,
# dann keine Bestaetigung. Bewusst keine Schaetzung fuer Serien, die mehrere
# Jahre umfassen (die stehen in UNEXACT_SETS).
EXACT_YEARS = {
    "Base Set": {"EN": 1999, "JP": 1996},
    "Base Set 1st Ed.": {"EN": 1999, "JP": 1996},
    "Base Set Shadowless": {"EN": 1999},
    "Base Set 2": {"EN": 2000},
    "No Rarity (JP)": {"JP": 1996},
    "Team Rocket": {"EN": 2000, "JP": 1997},
    "Gym Heroes": {"EN": 2000, "JP": 1998},
    "Gym Challenge": {"EN": 2000, "JP": 1998},
    "Neo Genesis": {"EN": 2000, "JP": 2000},
    "Neo Discovery": {"EN": 2001, "JP": 2001},
    "Neo Revelation": {"EN": 2001, "JP": 2001},
    "Neo Destiny": {"EN": 2002, "JP": 2001},
    "Legendary Collection": {"EN": 2002},
    "Expedition": {"EN": 2002},
    "Aquapolis": {"EN": 2003},
    "Skyridge": {"EN": 2003},
    "Jungle": {"EN": 1999, "JP": 1997},
    "Fossil": {"EN": 1999, "JP": 1997},
    "EX Ruby & Sapphire": {"EN": 2003},
    "EX Sandstorm": {"EN": 2003},
    "EX Dragon": {"EN": 2003},
    "EX Team Magma vs Team Aqua": {"EN": 2004},
    "EX Hidden Legends": {"EN": 2004},
    "EX FireRed & LeafGreen": {"EN": 2004},
    "EX Team Rocket Returns": {"EN": 2004},
    "EX Deoxys": {"EN": 2005},
    "EX Emerald": {"EN": 2005},
    "XY Emerald Break": {"JP": 2015},
    "EX Unseen Forces": {"EN": 2005},
    "EX Delta Species": {"EN": 2005},
    "EX Legend Maker": {"EN": 2006},
    "EX Holon Phantoms": {"EN": 2006},
    "EX Crystal Guardians": {"EN": 2006},
    "EX Dragon Frontiers": {"EN": 2006},
    "EX Power Keepers": {"EN": 2007},
    "Diamond & Pearl": {"EN": 2007},
    "DP Mysterious Treasures": {"EN": 2007},
    "DP Secret Wonders": {"EN": 2007},
    "DP Great Encounters": {"EN": 2008},
    "DP Majestic Dawn": {"EN": 2008},
    "DP Legends Awakened": {"EN": 2008},
    "DP Stormfront": {"EN": 2008},
    "Platinum": {"EN": 2009},
    "Platinum Rising Rivals": {"EN": 2009},
    "Platinum Supreme Victors": {"EN": 2009},
    "HeartGold SoulSilver": {"EN": 2010},
    "HGSS Unleashed": {"EN": 2010},
    "HGSS Undaunted": {"EN": 2010},
    "HGSS Triumphant": {"EN": 2010},
    "HGSS Call of Legends": {"EN": 2011},
    "Black & White": {"EN": 2011},
    "BW Emerging Powers": {"EN": 2011},
    "BW Noble Victories": {"EN": 2011},
    "BW Next Destinies": {"EN": 2012},
    "BW Dark Explorers": {"EN": 2012},
    "BW Dragons Exalted": {"EN": 2012},
    "BW Dragon Vault": {"EN": 2012},
    "BW Boundaries Crossed": {"EN": 2012},
    "BW Plasma Storm": {"EN": 2013},
    "BW Plasma Freeze": {"EN": 2013},
    "BW Plasma Blast": {"EN": 2013},
    "BW Legendary Treasures": {"EN": 2013},
    "BW Radiant Collection": {"EN": 2013},
    "XY": {"EN": 2014},
    "XY Flashfire": {"EN": 2014},
    "XY Furious Fists": {"EN": 2014},
    "XY Phantom Forces": {"EN": 2014},
    "XY Primal Clash": {"EN": 2015},
    "XY Double Crisis": {"EN": 2015},
    "XY Roaring Skies": {"EN": 2015},
    "XY Ancient Origins": {"EN": 2015},
    "XY BREAKthrough": {"EN": 2015},
    "XY BREAKpoint": {"EN": 2016},
    "XY Fates Collide": {"EN": 2016},
    "XY Steam Siege": {"EN": 2016},
    "XY Generations": {"EN": 2016},
    "XY Evolutions": {"EN": 2016},
    "Neo Premium File": {"JP": 2000},
}

# Ein Set-Name, der mehrere Ursprungsjahre mischt, ist keine exakte Identitaet.
UNEXACT_SETS = {
    "Neo", "Gym", "ADV (JP)", "PCG (JP)", "e-Card (JP)", "Vending (JP)",
    "Vending/Promo (JP)", "JP Promo", "Black Star Promo", "POP Series",
}

# Nur diese Set-Namen legen die Sprache fest, weil es das Produkt nur so gibt.
# XY6 Emerald Break ist ein japanisches Set; EX Emerald (2005) ist das
# englische Set. Ein Widerspruch im Titel ist ein Konflikt, keine Vermutung.
LANG_LOCK = {
    "XY Emerald Break": "JP",
    "No Rarity (JP)": "JP",
    "Vending (JP)": "JP",
    "ADV (JP)": "JP",
    "PCG (JP)": "JP",
    "e-Card (JP)": "JP",
    "Neo Premium File": "JP",
    "EX Emerald": "EN",
    "Base Set Shadowless": "EN",
    "Base Set 2": "EN",
}

FOIL_VARIANTS = {
    "Mega EX", "EX", "Gold Star", "Full Art", "Secret Rare", "LV.X",
    "Prime", "LEGEND", "BREAK", "Shining",
}

MONTHS = {
    "jan": 1, "januar": 1, "january": 1,
    "feb": 2, "februar": 2, "february": 2,
    "mar": 3, "mär": 3, "maerz": 3, "märz": 3, "march": 3,
    "apr": 4, "april": 4,
    "mai": 5, "may": 5,
    "jun": 6, "juni": 6, "june": 6,
    "jul": 7, "juli": 7, "july": 7,
    "aug": 8, "august": 8,
    "sep": 9, "sept": 9, "september": 9,
    "okt": 10, "oct": 10, "october": 10, "oktober": 10,
    "nov": 11, "november": 11,
    "dez": 12, "dec": 12, "dezember": 12, "december": 12,
}

NUMBER_RE = re.compile(r"(?<!\d)(\d{1,3})\s*/\s*(\d{1,3})(?!\d)")
RARITY_RE = re.compile(r"(?<![A-Za-z])(SAR|CSR|SSR|CHR|HR|UR|SR|RR|AR|PR)(?![A-Za-z])", re.I)
JP_RE = re.compile(r"japan|japanese|japanisch|\bjp\b|\bjpn\b|日本", re.I)
EN_RE = re.compile(r"\benglish\b|\benglisch\b|\(en\)", re.I)
UNLIMITED_RE = re.compile(r"\bunlimited\b|unlimitiert", re.I)
IMPORT_INCLUDED_RE = re.compile(
    r"inkl\.?\s*(?:zoll|mwst|einfuhr|vat)|zoll\s*(?:bezahlt|inkl)|duty\s*paid|"
    r"\bddp\b|delivered\s*duty|import\s*(?:included|inkl)|keine\s*einfuhr|"
    r"versand\s*inkl\.?\s*zoll|inklusive\s*zoll",
    re.I,
)
IMPORT_FEE_RE = re.compile(
    r"(?:zoll|einfuhr|handling|proxy)(?:\s*gebuehr|\s*gebühr|\s*fee)?\s*:?\s*CHF\s*([\d'.,]+)",
    re.I,
)
FREE_SHIP_RE = re.compile(r"kostenlos|kostenfrei|gratis|\bfree\b", re.I)
LOT_RE = re.compile(r"\blots?\b|\bbundle\b|sammlung|konvolut", re.I)
HIDDEN_OFFER_RE = re.compile(
    r"best\s*offer\s*accepted|angebot\s*angenommen|preisvorschlag\s*angenommen|accepted\s*offer",
    re.I,
)
SOLD_WORD_RE = re.compile(r"verkauft|sold|ended|beendet", re.I)
MODERN_EXTRA = re.compile(
    r"glory\s*of\s*team\s*rocket|team\s*rocket'?s\s+\w+(?:\s+\w+)?\s+ex\b|"
    r"(?<![a-z0-9])m\d[a-z]?(?![a-z0-9])|mega\s*dream|inferno\s*x|nihil\s*zero|battle\s*partners|terastal|"
    r"super\s*electric|heat\s*wave|wild\s*force|cyber\s*judge|shiny\s*treasure|snow\s*hazard|clay\s*burst",
    re.I,
)
PSA_GRADE_RE = re.compile(r"\bpsa\s*-?\s*(10|[1-9])(?!\.?\d)\b", re.I)


def zurich_now(now=None):
    if now is None:
        return datetime.now(ZURICH)
    if now.tzinfo is None:
        return now.replace(tzinfo=ZURICH)
    return now.astimezone(ZURICH)


def zurich_stamp(now=None):
    """Nutzerzeit Europe/Zurich, mit Zonenname, damit UTC nicht als Lokalzeit wirkt."""
    return zurich_now(now).strftime("%d.%m.%Y %H:%M Europe/Zurich")


def release_allowed(year):
    return year is not None and 1996 <= int(year) <= 2016


def _v():
    import vintage_deal_finder as v
    return v


def parse_number_token(raw):
    s = (raw or "").strip().replace("'", "").replace(" ", "")
    if not s:
        return None
    if "," in s and "." in s:
        if s.rfind(",") > s.rfind("."):
            s = s.replace(".", "").replace(",", ".")
        else:
            s = s.replace(",", "")
    elif "," in s:
        s = s.replace(",", ".") if re.search(r",\d{1,2}$", s) else s.replace(",", "")
    elif "." in s and not re.search(r"\.\d{1,2}$", s):
        s = s.replace(".", "")
    try:
        return float(s)
    except ValueError:
        return None


def chf_amounts(text):
    if not text:
        return []
    vals = []
    for raw in re.findall(r"CHF\s*([\d'.,]+)", text, re.I):
        n = parse_number_token(raw)
        if n is not None:
            vals.append(round(n, 2))
    return vals


def one_chf_amount(text):
    """Ein CHF-Betrag, oder (None, Grund) wenn der Betrag fehlt oder widerspricht."""
    vals = chf_amounts(text)
    uniq = sorted(set(vals))
    if not uniq:
        return None, "missing"
    if len(uniq) > 1:
        return None, "inconsistent"
    return uniq[0], None


def is_ch_country(country):
    return (country or "").strip().lower() in CH_COUNTRIES


def parse_cost(price_text, ship_text, country, blob, fx_date):
    """Belegter CH-Gesamtpreis. Geschaetzter Versand (früher pauschal 8/25 CHF)
    ist kein Gesamtpreis. Auslands-Einfuhr ohne ausgewiesene Gebühr auch nicht."""
    price, price_flag = one_chf_amount(price_text)
    reasons = []
    if price_flag == "inconsistent":
        reasons.append("price-inconsistent")
    elif price is None:
        reasons.append("price-missing")
    ship = None
    ship_known = False
    ship_text = ship_text or ""
    if FREE_SHIP_RE.search(ship_text):
        ship = 0.0
        ship_known = True
    else:
        amount, flag = one_chf_amount(ship_text)
        if flag == "inconsistent":
            reasons.append("shipping-inconsistent")
        elif amount is not None:
            ship = amount
            ship_known = True
        else:
            reasons.append("shipping-unresolved")
    import_chf = None
    foreign = not is_ch_country(country)
    if not (country or "").strip() or (country or "").strip().lower() in {"unbekannt", "unknown"}:
        reasons.append("country-unknown")
    elif not foreign:
        import_chf = 0.0
    elif IMPORT_INCLUDED_RE.search(blob or ""):
        import_chf = 0.0
    else:
        fee_m = IMPORT_FEE_RE.search(blob or "")
        if fee_m:
            import_chf = parse_number_token(fee_m.group(1))
            if import_chf is None:
                reasons.append("import-unresolved")
        else:
            reasons.append("import-unresolved")
    confirmed = price is not None and ship_known and import_chf is not None and "price-inconsistent" not in reasons and "shipping-inconsistent" not in reasons
    all_in = round(price + ship + import_chf, 2) if confirmed else None
    return {
        "price": price,
        "currency": "CHF" if price is not None else None,
        "ship": ship,
        "ship_est": not ship_known,
        "import_chf": import_chf,
        "all_in_chf": all_in,
        "all_in_confirmed": confirmed,
        "fx_source": "eBay.ch Preis in CHF" if price is not None else None,
        "fx_date": fx_date,
        "cost_reasons": reasons,
    }


def within_budget(mode, base, variant, all_in):
    if all_in is None:
        return False
    if all_in < MIN_ALL_IN:
        return False
    if mode == "psa":
        return all_in < MAX_PSA
    if base == "bulbasaur":
        return all_in <= MAX_RAW_BULBASAUR
    if variant == "Gold Star":
        return all_in <= MAX_RAW_GOLD
    return all_in <= MAX_RAW


def budget_cap(mode, base, variant):
    if mode == "psa":
        return MAX_PSA
    if base == "bulbasaur":
        return MAX_RAW_BULBASAUR
    if variant == "Gold Star":
        return MAX_RAW_GOLD
    return MAX_RAW


def parse_collector_number(title):
    found = []
    printed = []
    for a, b in NUMBER_RE.findall(title or ""):
        key = f"{int(a)}/{int(b)}"
        show = f"{a}/{b}"
        if key not in found:
            found.append(key)
            printed.append(show)
    if not found:
        return None, None, "number-missing"
    if len(found) > 1:
        return None, None, "number-ambiguous"
    return found[0], printed[0], None


def parse_rarity(title):
    tokens = []
    for m in RARITY_RE.findall(title or ""):
        tok = m.upper()
        if tok not in tokens:
            tokens.append(tok)
    if not tokens:
        return "unstated", None
    if len(tokens) > 1:
        return None, "rarity-ambiguous"
    return tokens[0], None


def parse_sold_date(text):
    """Nur ein Datum direkt bei Verkauft/Sold/Ended. Eine Jahreszahl im
    Kartentitel (Base Set 1999) ist kein Verkaufsdatum."""
    if not text:
        return None
    m = SOLD_WORD_RE.search(text)
    if not m:
        return None
    window = text[m.start():m.start() + 80]
    dmy = re.search(r"(\d{1,2})\.\s*([A-Za-zäÄ]+)\.?\s*(\d{4})", window)
    if dmy:
        mon = MONTHS.get(dmy.group(2).lower().rstrip("."))
        if mon:
            return _safe_date(int(dmy.group(3)), mon, int(dmy.group(1)))
    mdy = re.search(r"([A-Za-zäÄ]+)\s+(\d{1,2}),?\s*(\d{4})", window)
    if mdy:
        mon = MONTHS.get(mdy.group(1).lower())
        if mon:
            return _safe_date(int(mdy.group(3)), mon, int(mdy.group(2)))
    num = re.search(r"(\d{1,2})\.(\d{1,2})\.(\d{4})", window)
    if num:
        return _safe_date(int(num.group(3)), int(num.group(2)), int(num.group(1)))
    return None


def _safe_date(year, month, day):
    try:
        return datetime(year, month, day).date()
    except ValueError:
        return None


def normalize_raw_condition(grade):
    if not grade or not str(grade).startswith("raw"):
        return None
    if grade == "raw ?":
        return None
    if "MP/HP" in grade:
        return None
    if "Damaged" in grade:
        return "DMG"
    if re.search(r"\bHP\b", grade):
        return "HP"
    if re.search(r"\bMP\b", grade):
        return "MP"
    if re.search(r"\bLP\b", grade):
        return "LP"
    if "NM" in grade:
        return "NM"
    return None


def photo_ok(item_id, img):
    img = img or ""
    if str(item_id).startswith("TESTDATA") and "example.invalid" in img:
        return True
    return "ebayimg.com" in img or "ebaystatic.com" in img


def make_key(card):
    return "|".join([
        card.get("mode") or "?",
        card.get("lang") or "?",
        card.get("set") or "?",
        str(card.get("year") or "?"),
        card.get("base") or "?",
        card.get("number") or "?",
        card.get("rarity") or "?",
        card.get("finish") or "?",
        card.get("edition") or "?",
        card.get("variant") or "?",
        card.get("condition") or "?",
    ])


def _language(title, set_name):
    jp = bool(JP_RE.search(title or ""))
    en = bool(EN_RE.search(title or ""))
    if jp and en:
        return None, "set-language-conflict"
    lock = LANG_LOCK.get(set_name or "")
    if lock == "JP":
        if en:
            return None, "set-language-conflict"
        return "JP", None
    if lock == "EN":
        if jp:
            return None, "set-language-conflict"
        return "EN", None
    if jp:
        return "JP", None
    if en:
        return "EN", None
    return None, "language-unknown"


def _year_for(set_name, lang, title):
    if set_name in EXACT_YEARS:
        row = EXACT_YEARS[set_name]
        if lang and lang in row:
            return row[lang], None
        return None, "year-unknown"
    if set_name in UNEXACT_SETS:
        years = [int(y) for y in re.findall(r"\b(19\d{2}|20\d{2})\b", title or "")]
        years = sorted({y for y in years if 1996 <= y <= 2035})
        if len(years) == 1:
            return years[0], None
        return None, "year-unknown"
    return None, "year-unknown"


def _edition(title, era, set_name, v):
    if set_name == "Base Set 1st Ed." or v.FIRST_ED_RE.search(v.norm(title)):
        return "1st"
    if UNLIMITED_RE.search(title or ""):
        return "unlimited"
    if era == "WOTC":
        return None
    return "n/a"


def _finish(title, variant, v):
    tn = v.norm(title)
    if re.search(r"reverse|rev\.?\s*holo", tn):
        return "reverse"
    if v.NONHOLO.search(title or "") and not re.search(r"\bholo", tn):
        return "nonholo"
    if v.HOLO_RE.search(tn):
        return "holo"
    if variant in FOIL_VARIANTS:
        return "holo"
    return None


def _display(name, variant):
    if not name:
        return None
    if variant == "Mega EX":
        return f"M {name}-EX"
    if variant == "EX":
        return f"{name}-EX"
    if variant == "Gold Star":
        return f"{name} Gold Star"
    return name


def parse_listing(raw, mode="raw", now=None):
    """Rohdaten einer Trefferkarte in eine Identitaet uebersetzen.

    `drop` bedeutet: kein Karten-Kandidat (Lot, Non-TCG, modern, Non-Holo im
    Roh-Bot). Alles andere bleibt ein Kandidat und kann hoechstens unverified
    oder nonqualified sein, nie ein bestaetigter Deal ohne die weiteren Tore.
    """
    v = _v()
    title = (raw.get("t") or "").replace("Wird in neuem Fenster oder Tab geöffnet", "").strip()
    item_id = str(raw.get("id") or "").strip()
    if not item_id or not title:
        return None
    now = zurich_now(now)
    fx_date = now.strftime("%d.%m.%Y")
    img = raw.get("img") or ""
    img = re.sub(r"/s-l\d+\.(webp|jpg)", "/s-l1600.jpg", img)
    card = {
        "id": item_id,
        "title": title,
        "url": raw.get("url") or f"https://www.ebay.ch/itm/{item_id}",
        "img": img,
        "query": raw.get("query") or "",
        "country": (raw.get("country") or "").strip(),
        "sub": raw.get("sub") or "",
        "mode": mode,
        "slab_verified": False,
        "reasons": [],
        "drop": None,
        "tags": [],
        "is_jp": v.is_japan_origin(raw.get("country") or ""),
    }
    blob = " ".join([title, card["sub"], raw.get("full") or "", raw.get("ended") or ""])
    if v.EXCLUDE.search(title) or LOT_RE.search(title):
        card["drop"] = "ausschluss-wort"
        card["reasons"].append("excluded-product")
        card["key"] = make_key(card)
        return card
    if v.MODERN.search(title) or MODERN_EXTRA.search(v.norm(title)):
        card["drop"] = "modern"
        card["reasons"].append("release-after-2016")
        card["key"] = make_key(card)
        return card

    tn = v.norm(title)
    sset, era, sm = v.detect_set(tn)
    card["set"] = sset
    card["era"] = era
    if not sset:
        card["drop"] = "kein-set"
        card["reasons"].append("set-unknown")
        card["key"] = make_key(card)
        return card
    tn_wo = v.strip_set_tokens(tn, sset, sm)
    disp_name, base = v.detect_name(tn_wo)
    if not disp_name:
        disp_name, base = v.detect_name(tn)
    card["name"] = disp_name
    card["base"] = base
    if not disp_name:
        card["drop"] = "kein-name"
        card["reasons"].append("name-unknown")
        card["key"] = make_key(card)
        return card

    variant, tags = v.detect_variant(v.COND_STRIP.sub(" ", tn_wo), base, era)
    card["variant"] = variant or "Std"
    card["tags"] = list(tags)
    card["display"] = _display(disp_name, card["variant"])
    card["finish"] = _finish(title, card["variant"], v)
    if mode == "raw" and card["finish"] == "reverse":
        card["drop"] = "reverse-nicht-rohbot"
        card["reasons"].append("finish-reverse")
        card["key"] = make_key(card)
        return card
    if card["finish"] == "nonholo":
        card["drop"] = "non-holo"
        card["reasons"].append("finish-nonholo")
        card["key"] = make_key(card)
        return card
    if card["finish"] is None:
        card["reasons"].append("finish-unknown")

    number, printed, number_reason = parse_collector_number(title)
    card["number"] = number
    card["number_display"] = printed
    if number_reason:
        card["reasons"].append(number_reason)
    rarity, rarity_reason = parse_rarity(title)
    card["rarity"] = rarity
    if rarity_reason:
        card["reasons"].append(rarity_reason)

    lang, lang_reason = _language(title, sset)
    card["lang"] = lang
    if lang_reason:
        card["reasons"].append(lang_reason)
    year, year_reason = _year_for(sset, lang, title)
    card["year"] = year
    if year_reason:
        card["reasons"].append(year_reason)
    elif year is not None and not release_allowed(year):
        card["drop"] = "modern"
        card["reasons"].append("release-after-2016")

    edition = _edition(title, era, sset, v)
    card["edition"] = edition
    if edition is None:
        card["reasons"].append("edition-unknown")

    label = f"{title} {card['sub']}"
    label_grades = [g for g in PSA_GRADE_RE.findall(label)]
    full_grades = [g for g in PSA_GRADE_RE.findall(blob)]
    raw_grade = v.detect_grade(blob)
    if mode == "psa":
        if len(set(full_grades)) != 1:
            card["condition"] = None
            card["reasons"].append("psa-grade-conflict" if full_grades else "psa-grade-missing")
        elif len(set(label_grades)) != 1:
            card["condition"] = None
            card["reasons"].append("psa-label-missing")
        else:
            card["condition"] = f"PSA {label_grades[0]}"
        card["reasons"].append("psa-slab-label-not-verified")
        if not str(card.get("condition") or "").startswith("PSA"):
            card["drop"] = "kein-psa"
    else:
        if v.PSA_GRADED.search(label) or (v.PSA_WORD.search(label) and not v.PSA_NEG.search(label)):
            card["drop"] = "psa-eigener-bot"
            card["reasons"].append("psa-goes-to-psa-bot")
            card["condition"] = None
        else:
            card["condition"] = normalize_raw_condition(raw_grade)
            if card["condition"] is None:
                card["reasons"].append("unknown-condition")
    card["grade"] = card["condition"]

    cost = parse_cost(raw.get("p") or "", raw.get("s") or "", card["country"], blob, fx_date)
    card.update(cost)
    card["reasons"].extend(cost["cost_reasons"])
    card["photo_ok"] = photo_ok(item_id, img)
    if not card["photo_ok"]:
        card["reasons"].append("photo-missing")

    hidden = bool(HIDDEN_OFFER_RE.search(blob))
    price_hidden = hidden and card["price"] is None
    card["hidden_offer"] = hidden
    card["price_hidden"] = price_hidden
    card["sold_date"] = parse_sold_date(raw.get("ended") or "") or parse_sold_date(raw.get("full") or "")

    card["identity_complete"] = _identity_complete(card)
    card["in_budget"] = bool(card["all_in_confirmed"] and within_budget(mode, card.get("base"), card.get("variant"), card.get("all_in_chf")))
    if card["all_in_confirmed"] and card["all_in_chf"] is not None and not card["in_budget"]:
        cap = budget_cap(mode, card.get("base"), card.get("variant"))
        if mode == "psa" and card["all_in_chf"] >= MAX_PSA:
            card["reasons"].append("over-budget")
        elif card["all_in_chf"] < MIN_ALL_IN:
            card["reasons"].append("under-budget")
        elif card["all_in_chf"] > cap:
            card["reasons"].append("over-budget")
    card["needs_sold_lookup"] = bool(
        not card["drop"]
        and card["identity_complete"]
        and card["all_in_confirmed"]
        and card["in_budget"]
        and card["photo_ok"]
        and card.get("condition")
    )
    popular = card["base"] in v.POPULAR
    rare = card["variant"] != "Std" or card["finish"] == "holo"
    if card["base"] == "bulbasaur" and (card["finish"] == "holo" or card["variant"] != "Std"):
        card["tier"] = -1
    elif card["variant"] == "Gold Star":
        card["tier"] = 0
    elif popular and rare:
        card["tier"] = 1
    elif popular or rare:
        card["tier"] = 2
    else:
        card["tier"] = 3
    card["key"] = make_key(card)
    card["reasons"] = _uniq(card["reasons"])
    return card


def _identity_complete(card):
    if card.get("drop"):
        return False
    if card.get("rarity") is None:
        return False
    fields = (
        card.get("lang"), card.get("set"), card.get("year"), card.get("base"),
        card.get("number"), card.get("finish"), card.get("edition"),
        card.get("variant"), card.get("condition"),
    )
    if any(x in (None, "", "?") for x in fields):
        return False
    if not release_allowed(card.get("year")):
        return False
    return True


def _uniq(items):
    out = []
    for x in items:
        if x and x not in out:
            out.append(x)
    return out


def _sold_record_usable(candidate, sold):
    if not sold or sold.get("id") == candidate.get("id"):
        return False, "sold-is-listing"
    if sold.get("drop"):
        return False, "sold-excluded"
    if LOT_RE.search(sold.get("title") or ""):
        return False, "sold-lot"
    if sold.get("price_hidden") or (sold.get("hidden_offer") and not sold.get("all_in_confirmed")):
        return False, "sold-hidden-offer"
    if sold.get("key") != candidate.get("key"):
        return False, "sold-identity-mismatch"
    if not sold.get("sold_date"):
        return False, "sold-date-missing"
    if not sold.get("all_in_confirmed"):
        return False, "sold-cost-unconfirmed"
    if candidate.get("import_chf") == 0 and sold.get("import_chf") not in (0, 0.0):
        return False, "sold-cost-basis-mismatch"
    if (candidate.get("import_chf") or 0) > 0 and sold.get("import_chf") is None:
        return False, "sold-cost-basis-mismatch"
    return True, None


def select_solds(candidate, solds, today):
    usable = []
    seen = set()
    rejected = []
    for sold in solds or []:
        ok, why = _sold_record_usable(candidate, sold)
        if not ok:
            rejected.append({"id": sold.get("id"), "reason": why, "title": (sold.get("title") or "")[:120]})
            continue
        if sold["id"] in seen:
            rejected.append({"id": sold["id"], "reason": "sold-duplicate", "title": (sold.get("title") or "")[:120]})
            continue
        seen.add(sold["id"])
        age = (today - sold["sold_date"]).days
        if age < 0:
            rejected.append({"id": sold["id"], "reason": "sold-date-future", "title": (sold.get("title") or "")[:120]})
            continue
        row = dict(sold)
        row["age_days"] = age
        usable.append(row)

    def within(days):
        return [s for s in usable if s["age_days"] <= days]

    for days in SOLD_WINDOWS:
        got = within(days)
        if len(got) >= SOLD_MIN:
            return got, days, rejected
    return within(SOLD_WINDOWS[-1]), None, rejected


def decide(card, solds=None, now=None, sold_note=None):
    """Niemals einen Prozentsatz unter einem Angebots- oder Richtwertmedian.

    `median` und `percent_below` bleiben leer, solange status != confirmed.
    """
    now = zurich_now(now)
    today = now.date()
    reasons = [r for r in (card.get("reasons") or []) if not (card.get("slab_verified") and r == "psa-slab-label-not-verified")]
    status = "rejected" if card.get("drop") else "unverified"
    median = None
    window = None
    used = []
    ratio = None
    percent = None
    searched = True if sold_note is None else bool(sold_note.get("searched"))
    note_detail = (sold_note or {}).get("detail")
    if card.get("needs_sold_lookup") and not searched:
        reasons.append(note_detail or "sold-lookup-cap")
    elif card.get("needs_sold_lookup"):
        used, window, rejected = select_solds(card, solds or [], today)
        if note_detail:
            reasons.append(note_detail)
        if window is None:
            reasons.append("insufficient-solds")
        else:
            prices = [s["all_in_chf"] for s in used]
            med = float(statistics.median(prices))
            if card["all_in_chf"] < med:
                median = round(med, 2)
                ratio = round(card["all_in_chf"] / med, 4)
                percent = int(round((1 - card["all_in_chf"] / med) * 100))
            else:
                reasons.append("not-below-sold-median")
                median = None
    elif not card.get("drop"):
        reasons.append("sold-lookup-skipped")

    if card.get("drop") or "release-after-2016" in reasons and card.get("drop"):
        status = "rejected"
    elif not card.get("identity_complete") or not card.get("photo_ok"):
        status = "unverified"
    elif card.get("mode") == "psa" and not visual_slab_ok(card):
        status = "unverified"
        if "psa-slab-label-not-verified" not in reasons:
            reasons.append("psa-slab-label-not-verified")
    elif not card.get("all_in_confirmed") or not card.get("in_budget"):
        status = "nonqualified"
    elif "sold-lookup-cap" in reasons or (sold_note and not searched):
        status = "unverified"
    elif window is None:
        status = "unverified"
    elif "not-below-sold-median" in reasons:
        status = "nonqualified"
    elif median is not None and card.get("all_in_confirmed") and card["all_in_chf"] < median and len(used) >= SOLD_MIN:
        if card.get("mode") == "psa" and not visual_slab_ok(card):
            status = "unverified"
        else:
            status = "confirmed"
    else:
        status = "unverified"

    if status != "confirmed":
        median = None
        ratio = None
        percent = None
        window_out = window if status == "confirmed" else None
    else:
        window_out = window
    if status != "confirmed":
        window_out = None

    dates = [s["sold_date"] for s in used] if status == "confirmed" else []
    out = dict(card)
    out.update({
        "status": status,
        "reasons": _uniq(reasons),
        "median": median,
        "median_kind": "sold" if status == "confirmed" else None,
        "n": len(used) if status == "confirmed" else 0,
        "window_days": window_out,
        "window_start": min(dates).isoformat() if dates else None,
        "window_end": max(dates).isoformat() if dates else None,
        "ratio": ratio,
        "percent_below": percent,
        "solds_used": [_public_sold(s) for s in used] if status == "confirmed" else [],
        "solds_observed": [_public_sold(s) for s in (used if status != "confirmed" else used)],
        "qualified": status == "confirmed",
    })
    if status != "confirmed":
        # Beobachtete Verkaeufe duerfen im Artefakt stehen, aber nicht als Median.
        observed, _, _ = select_solds(card, solds or [], today) if card.get("identity_complete") else ([], None, [])
        out["solds_observed"] = [_public_sold(s) for s in observed]
        out["solds_used"] = []
        out["n"] = 0
        out["median"] = None
        out["percent_below"] = None
        out["median_kind"] = None
    return out


def _public_sold(s):
    return {
        "item_id": s.get("id"),
        "title": s.get("title"),
        "url": s.get("url"),
        "photo_url": s.get("img"),
        "date": s.get("sold_date").isoformat() if s.get("sold_date") else None,
        "price_chf": s.get("price"),
        "shipping_chf": s.get("ship"),
        "import_chf": s.get("import_chf"),
        "all_in_chf": s.get("all_in_chf"),
        "currency": s.get("currency"),
        "fx_source": s.get("fx_source"),
        "fx_date": s.get("fx_date"),
    }


def evaluate_cards(cards, solds_by_key=None, now=None, notes_by_key=None):
    solds_by_key = solds_by_key or {}
    notes_by_key = notes_by_key or {}
    decisions = []
    for card in cards:
        decisions.append(decide(
            card,
            solds_by_key.get(card.get("key"), []),
            now=now,
            sold_note=notes_by_key.get(card.get("key")),
        ))
    confirmed = [d for d in decisions if d["status"] == "confirmed"]
    confirmed.sort(key=lambda d: (d.get("tier", 9), 0 if d.get("is_jp") else 1, d.get("ratio") or 1))
    return decisions, confirmed


def select_new(confirmed, posted_ids, per_key_cap=2, limit=15):
    """`posted` unterdrueckt Wiederholungen. `seen` allein nicht."""
    new = []
    per = {}
    posted_ids = posted_ids or set()
    for d in confirmed:
        if d["id"] in posted_ids or d.get("status") != "confirmed":
            continue
        if per.get(d["key"], 0) >= per_key_cap:
            continue
        per[d["key"]] = per.get(d["key"], 0) + 1
        new.append(d)
        if len(new) >= limit:
            break
    return new


def normalize_state(st):
    """Historisches `seen` waren veroeffentlichte Posts. `posted` bleibt
    davon getrennt, sobald der Schluessel existiert. Nichts wird geloescht."""
    st = st or {}
    st.setdefault("seen", {})
    st.setdefault("history", {})
    if "posted" not in st:
        st["posted"] = dict(st.get("seen") or {})
    return st


def visual_slab_ok(card):
    """PSA ist nur bestaetigt, wenn das Foto per OCR zum Label passt.

    Ein gesetztes slab_verified ohne slab_check reicht nicht. OCR ist kein
    Nachweis, dass PSA die Karte zertifiziert hat.
    """
    if card.get("mode") != "psa":
        return True
    return (
        card.get("slab_verified") is True
        and card.get("slab_check") == "visual-label-ocr"
        and card.get("slab_cert_authenticated") is False
    )


def apply_visual_slab_check(card, image_bytes=None):
    return slab_label.apply_visual_slab_check(card, image_bytes=image_bytes)


def discord_description(d, now=None):
    if d.get("status") != "confirmed" or d.get("median_kind") != "sold":
        raise RuntimeError("nur bestaetigte Verkaeufe duerfen als Deal formuliert werden")
    if d.get("ship_est") or not d.get("all_in_confirmed"):
        raise RuntimeError("geschaetzter Versand ist kein bestaetigter Gesamtpreis")
    if d.get("percent_below") is None or d.get("n", 0) < SOLD_MIN or not d.get("median"):
        raise RuntimeError("Median ohne genug Verkaeufe")
    if d.get("mode") == "psa" and not visual_slab_ok(d):
        raise RuntimeError("PSA ohne gelesenes Slab-Label")
    import_bit = ""
    if d.get("import_chf"):
        import_bit = f" + Einfuhr/Handling/Proxy {d['import_chf']:.2f}"
    rarity = d.get("rarity") if d.get("rarity") not in (None, "unstated") else "Rarity im Titel nicht genannt"
    lines = [
        f"**CHF {d['all_in_chf']:.2f}** bestaetigt inkl. Versand "
        f"(Preis {d['price']:.2f} + Versand {d['ship']:.2f}{import_bit})",
        f"Waehrung {d.get('currency') or 'CHF'}, FX: {d.get('fx_source')} ({d.get('fx_date')})",
        f"Karte: **{d.get('display')}** — {d.get('set')} ({d.get('year')}, {d.get('lang')}) "
        f"Nr. {d.get('number_display')} {rarity}",
        f"Finish: {d.get('finish')}, Edition: {d.get('edition')}, Variante: {d.get('variant')}, "
        f"Zustand: {d.get('condition')}",
    ]
    if d.get("mode") == "psa":
        lines.append("Slab-Label im Listing-Foto visuell gelesen (OCR). Kein PSA-Zertifikatscheck.")
    lines.extend([
        f"**{d['percent_below']} % unter verkauftem Median** "
        f"(Median CHF {d['median']:.2f}, n={d['n']}, Fenster {d['window_days']} Tage, "
        f"{d.get('window_start')}–{d.get('window_end')} Europe/Zurich)",
        "Verkaeufe (oeffentliche eBay-Verkaufspreise, keine Angebotspreise):",
    ])
    for s in d.get("solds_used") or []:
        lines.append(f"- {s['date']} CHF {s['all_in_chf']:.2f} {s['url']}")
    lines.append(f"[Zum Angebot]({d['url']})")
    text = "\n".join(lines)
    banned = ("GESCHAETZT", "Preishistorie", "anderen Angeboten", "Collectr", "Richtwert")
    for word in banned:
        if word.lower() in text.lower():
            raise RuntimeError(f"Deal-Text enthaelt unzulaessig: {word}")
    return text


def discord_footer(d, now=None):
    return f"eBay.ch Sofort-Kaufen — gefunden {zurich_stamp(now)} — Suche: {d.get('query') or ''}"[:2000]


def artifact_record(d):
    identity = {
        "name": d.get("base"),
        "display": d.get("display"),
        "variant": d.get("variant"),
        "number": d.get("number_display") or d.get("number"),
        "number_key": d.get("number"),
        "rarity": d.get("rarity"),
        "set": d.get("set"),
        "era": d.get("era"),
        "year": d.get("year"),
        "language": d.get("lang"),
        "finish": d.get("finish"),
        "edition": d.get("edition"),
        "condition": d.get("condition"),
        "mode": d.get("mode"),
    }
    confirmed = d.get("status") == "confirmed" and d.get("median_kind") == "sold"
    return {
        "listing_url": d.get("url"),
        "photo_url": d.get("img"),
        "search_query": d.get("query"),
        "country": d.get("country"),
        "identity": identity,
        "identity_key": d.get("key"),
        "price": {
            "item": d.get("price"),
            "currency": d.get("currency"),
            "shipping_chf": d.get("ship"),
            "shipping_estimated": bool(d.get("ship_est")),
            "import_handling_proxy_chf": d.get("import_chf"),
            "all_in_chf": d.get("all_in_chf") if d.get("all_in_confirmed") else None,
            "all_in_confirmed": bool(d.get("all_in_confirmed")),
            "fx_source": d.get("fx_source"),
            "fx_date": d.get("fx_date"),
        },
        "solds": d.get("solds_observed") or [],
        "n": d.get("n") if confirmed else 0,
        "window_days": d.get("window_days") if confirmed else None,
        "median_chf": d.get("median") if confirmed else None,
        "median_kind": "sold" if confirmed else None,
        "percent_below": d.get("percent_below") if confirmed else None,
        "status": d.get("status"),
        "reasons": d.get("reasons") or [],
        "slab_check": d.get("slab_check"),
        "slab_verified": bool(d.get("slab_verified")),
        "slab_cert_authenticated": False,
    }


def assert_postable(deals):
    for d in deals:
        discord_description(d)
