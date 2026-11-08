# -*- coding: utf-8 -*-
"""Visueller Abgleich eines Slab-Labels im Listing-Foto.

Lokales Tesseract liest das heruntergeladene Bild. Ein Treffer heisst nur:
der lesbare Labeltext stuetzt Name, Nummer, Set und die PSA-Note.
Das ist kein PSA-Zertifikatscheck und keine Echtheitsaussage.
Titeltext oder eine vorhandene Bild-URL allein setzen die Pruefung nicht.
"""
from __future__ import annotations

import io
import re
import urllib.error
import urllib.parse
import urllib.request

NON_TCG = re.compile(r"\b(carddass|bandai|meiji|topps|amada|ensky)\b", re.I)
MODERN_ON_LABEL = re.compile(r"\b(vmax|vstar|v-star|gx)\b", re.I)
PSA_GRADE = re.compile(r"\bPSA\s*[:#-]?\s*(10|[1-9])\b", re.I)
SLASH_NUMBER = re.compile(r"(?<!\d)(\d{1,3})\s*/\s*(\d{1,3})(?!\d)")
HASH_NUMBER = re.compile(r"(?:#|no\.?|nr\.?)\s*(\d{1,3})\b", re.I)
YEAR = re.compile(r"\b(19\d{2}|20\d{2})\b")
JP_WORD = re.compile(r"\b(japanese|japanisch|japan)\b", re.I)
EN_WORD = re.compile(r"\b(english|englisch)\b", re.I)
FIRST_ED = re.compile(r"\b1st\b|first\s+edition", re.I)
MEGA_WORD = re.compile(r"\bmega\b", re.I)
EX_WORD = re.compile(r"\bex\b", re.I)
REVERSE_WORD = re.compile(r"\breverse\b", re.I)
SET_STOP = {"ex", "xy", "the", "and", "jp", "set", "series", "pokemon", "card"}


class OcrDependencyError(Exception):
    pass


class OcrUnreadable(Exception):
    pass


class PhotoDownloadError(Exception):
    pass


def download_listing_photo(url, timeout=15):
    """Oeffentliches Listing-Foto. Nur eBay-Bildhosts, kein API-Schluessel."""
    parsed = urllib.parse.urlparse(url or "")
    host = (parsed.hostname or "").lower()
    if parsed.scheme not in ("http", "https") or not host.endswith("ebayimg.com"):
        raise PhotoDownloadError("slab-photo-host-rejected")
    req = urllib.request.Request(url, headers={"User-Agent": "chau-deal-finder/slab-ocr"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            status = getattr(response, "status", 200)
            data = response.read(8_000_000)
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise PhotoDownloadError("slab-photo-download-fail") from exc
    if status != 200 or len(data) < 400:
        raise PhotoDownloadError("slab-photo-download-fail")
    return data


def ocr_image_bytes(data):
    try:
        import pytesseract
    except ImportError as exc:
        raise OcrDependencyError("pytesseract fehlt") from exc
    try:
        from PIL import Image
    except ImportError as exc:
        raise OcrDependencyError("Pillow fehlt") from exc
    try:
        image = Image.open(io.BytesIO(data)).convert("RGB")
    except Exception as exc:
        raise OcrUnreadable("slab-ocr-unreadable") from exc
    width, height = image.size
    if width < 40 or height < 40:
        raise OcrUnreadable("slab-ocr-unreadable")
    if width < 1400:
        image = image.resize((width * 2, height * 2))
    try:
        text = pytesseract.image_to_string(image)
    except pytesseract.TesseractNotFoundError as exc:
        raise OcrDependencyError("tesseract fehlt") from exc
    except Exception as exc:
        raise OcrUnreadable("slab-ocr-unreadable") from exc
    if len(re.sub(r"\s+", "", text or "")) < 8:
        raise OcrUnreadable("slab-ocr-unreadable")
    return text


def _norm(text):
    return re.sub(r"\s+", " ", (text or "").lower())


def _uniq(items):
    out = []
    for item in items:
        if item and item not in out:
            out.append(item)
    return out


def visual_label_matches(ocr_text, card):
    """True nur wenn der gelesene Labeltext die Identitaet und die Note stuetzt."""
    reasons = []
    text = ocr_text or ""
    folded = _norm(text)
    if NON_TCG.search(text):
        reasons.append("slab-non-tcg")
    if MODERN_ON_LABEL.search(text):
        reasons.append("slab-variant-mismatch")

    grades = _uniq(PSA_GRADE.findall(text))
    wanted = str(card.get("condition") or "").replace("PSA", "").strip()
    if len(grades) != 1 or grades[0] != wanted:
        reasons.append("slab-grade-mismatch")

    base = re.sub(r"[^a-z]", "", _norm(card.get("base") or card.get("name") or ""))
    letters = re.sub(r"[^a-z]", "", folded)
    if len(base) < 3 or base not in letters:
        reasons.append("slab-name-mismatch")

    number = card.get("number") or ""
    slash = [f"{int(a)}/{int(b)}" for a, b in SLASH_NUMBER.findall(text)]
    hashes = HASH_NUMBER.findall(text)
    if "/" in number:
        numerator = number.split("/")[0]
        if slash:
            if number not in slash:
                reasons.append("slab-number-mismatch")
        elif numerator and numerator in {str(int(h)) for h in hashes}:
            pass
        else:
            reasons.append("slab-number-not-on-label")

    set_tokens = [
        tok for tok in re.findall(r"[a-z0-9]+", _norm(card.get("set") or ""))
        if tok not in SET_STOP and len(tok) >= 4
    ]
    if not set_tokens or not all(tok in folded for tok in set_tokens):
        reasons.append("slab-set-not-on-label")

    years = [int(y) for y in YEAR.findall(text)]
    card_year = card.get("year")
    if years and card_year and int(card_year) not in years:
        reasons.append("slab-year-mismatch")

    jp = bool(JP_WORD.search(text))
    en = bool(EN_WORD.search(text))
    if jp and en:
        reasons.append("slab-language-mismatch")
    elif card.get("lang") == "JP" and not jp:
        reasons.append("slab-language-not-on-label")
    elif card.get("lang") == "EN" and jp:
        reasons.append("slab-language-mismatch")

    edition = card.get("edition")
    if edition == "1st" and not FIRST_ED.search(text):
        reasons.append("slab-edition-not-on-label")
    if edition in ("unlimited", "n/a") and FIRST_ED.search(text):
        reasons.append("slab-edition-mismatch")

    variant = card.get("variant") or "Std"
    if variant == "Mega EX":
        if not MEGA_WORD.search(text):
            reasons.append("slab-variant-not-on-label")
    elif MEGA_WORD.search(text):
        reasons.append("slab-variant-mismatch")
    if variant == "EX" and not EX_WORD.search(text):
        reasons.append("slab-variant-not-on-label")
    if variant == "Std" and EX_WORD.search(text):
        reasons.append("slab-variant-mismatch")
    if variant == "Gold Star" and not ("gold" in folded and "star" in folded):
        reasons.append("slab-variant-not-on-label")

    finish = card.get("finish")
    if finish == "reverse" and not REVERSE_WORD.search(text):
        reasons.append("slab-finish-not-on-label")
    if finish == "holo" and REVERSE_WORD.search(text):
        reasons.append("slab-finish-mismatch")

    reasons = _uniq(reasons)
    return not reasons, reasons


def apply_visual_slab_check(card, image_bytes=None, timeout=15):
    """Setzt slab_verified nur nach lesbarem, passendem Label im Foto.

    slab_cert_authenticated bleibt falsch. OCR belegt nicht, dass PSA
    die Karte zertifiziert hat.
    """
    card = dict(card)
    reasons = list(card.get("reasons") or [])
    card["slab_cert_authenticated"] = False
    if card.get("mode") != "psa" or card.get("drop"):
        card["slab_verified"] = False
        return card
    card["slab_verified"] = False
    card["slab_check"] = "not-read"
    if image_bytes is None:
        try:
            image_bytes = download_listing_photo(card.get("img") or "", timeout=timeout)
        except PhotoDownloadError as exc:
            reason = str(exc) if str(exc).startswith("slab-") else "slab-photo-download-fail"
            card["reasons"] = _uniq(reasons + [reason])
            return card
    try:
        text = ocr_image_bytes(image_bytes)
    except OcrDependencyError:
        card["reasons"] = _uniq(reasons + ["slab-ocr-missing-dependency"])
        return card
    except OcrUnreadable:
        card["reasons"] = _uniq(reasons + ["slab-ocr-unreadable"])
        return card
    ok, why = visual_label_matches(text, card)
    card["slab_ocr_excerpt"] = " ".join(text.split())[:240]
    if ok:
        card["slab_verified"] = True
        card["slab_check"] = "visual-label-ocr"
        card["reasons"] = [r for r in reasons if r != "psa-slab-label-not-verified"]
    else:
        card["slab_check"] = "visual-label-mismatch"
        card["reasons"] = _uniq(reasons + why)
    return card
