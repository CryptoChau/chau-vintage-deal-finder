# -*- coding: utf-8 -*-
"""Echtes Tesseract auf TEST-DATA-Bildern. Kein gesetztes True ohne OCR."""
import os
import unittest
from datetime import date

import deal_validation as dv
from test_deal_validation import NOW, item

FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")


def _load(name):
    with open(os.path.join(FIXTURES, name), "rb") as fh:
        return fh.read()


def _psa_card():
    return dv.parse_listing(item(
        "TEST DATA SYNTHETIC ONLY Bulbasaur Holo 15/102 Base Set 1999 English Unlimited PSA 8",
        id="TESTDATA-PSA-001",
        p="CHF 40,00",
        s="Kostenlos",
        country="Schweiz",
        img="https://i.ebayimg.com/images/g/TESTDATA/s-l1600.jpg",
        url="https://example.invalid/testdata/synthetic-psa",
        sub="Graded - PSA 8",
    ), mode="psa", now=NOW)


class SlabLabelOcrTests(unittest.TestCase):
    def test_photo_ocr_can_verify_and_still_is_not_a_certificate(self):
        card = _psa_card()
        self.assertFalse(card["slab_verified"])
        image = _load("TESTDATA-psa-slab-bulbasaur.png")
        checked = dv.apply_visual_slab_check(card, image_bytes=image)
        self.assertTrue(checked["slab_verified"])
        self.assertEqual(checked["slab_check"], "visual-label-ocr")
        self.assertFalse(checked["slab_cert_authenticated"])
        excerpt = (checked.get("slab_ocr_excerpt") or "").upper()
        self.assertIn("PSA", excerpt)
        self.assertIn("BULBASAUR", excerpt)
        self.assertIn("15/102", excerpt.replace(" ", ""))
        solds = [
            self._sold(checked, "TESTDATA-PSA-S1", 80.0, date(2026, 10, 2)),
            self._sold(checked, "TESTDATA-PSA-S2", 100.0, date(2026, 9, 20)),
            self._sold(checked, "TESTDATA-PSA-S3", 120.0, date(2026, 8, 30)),
        ]
        decision = dv.decide(checked, solds, now=NOW)
        self.assertEqual(decision["status"], "confirmed")
        self.assertTrue(decision["title"].startswith("TEST DATA"))
        text = dv.discord_description(decision, now=NOW)
        self.assertIn("Kein PSA-Zertifikatscheck", text)
        self.assertNotIn("authentisch", text.lower())
        self.assertNotIn("zertifiziert", text.lower())

    def test_wrong_grade_variant_and_non_tcg_fail_closed(self):
        card = _psa_card()
        wrong = dv.apply_visual_slab_check(card, image_bytes=_load("TESTDATA-psa-slab-wrong-grade.png"))
        self.assertFalse(wrong["slab_verified"])
        self.assertIn("slab-grade-mismatch", wrong["reasons"])
        bandai = dv.apply_visual_slab_check(card, image_bytes=_load("TESTDATA-psa-slab-carddass.png"))
        self.assertFalse(bandai["slab_verified"])
        self.assertIn("slab-non-tcg", bandai["reasons"])
        vmax = dv.apply_visual_slab_check(card, image_bytes=_load("TESTDATA-psa-slab-vmax.png"))
        self.assertFalse(vmax["slab_verified"])
        self.assertIn("slab-variant-mismatch", vmax["reasons"])

    def test_unreadable_missing_dependency_and_download_fail(self):
        card = _psa_card()
        unread = dv.apply_visual_slab_check(card, image_bytes=_load("TESTDATA-psa-slab-unreadable.png"))
        self.assertFalse(unread["slab_verified"])
        self.assertIn("slab-ocr-unreadable", unread["reasons"])
        import pytesseract
        old = pytesseract.pytesseract.tesseract_cmd
        pytesseract.pytesseract.tesseract_cmd = "/no/such/tesseract-binary"
        try:
            missing = dv.apply_visual_slab_check(card, image_bytes=_load("TESTDATA-psa-slab-bulbasaur.png"))
        finally:
            pytesseract.pytesseract.tesseract_cmd = old
        self.assertFalse(missing["slab_verified"])
        self.assertIn("slab-ocr-missing-dependency", missing["reasons"])
        hosted = dict(card)
        hosted["img"] = "https://example.invalid/testdata/not-a-photo.jpg"
        failed = dv.apply_visual_slab_check(hosted)
        self.assertFalse(failed["slab_verified"])
        self.assertIn("slab-photo-host-rejected", failed["reasons"])
        self.assertNotEqual(dv.decide(failed, [], now=NOW)["status"], "confirmed")

    def test_title_and_url_alone_do_not_verify_and_raw_path_still_confirms(self):
        card = _psa_card()
        self.assertTrue(card["photo_ok"])
        self.assertFalse(dv.visual_slab_ok(card))
        raw = dv.parse_listing(item(
            "TEST DATA SYNTHETIC ONLY Bulbasaur Holo 15/102 Base Set 1999 English Unlimited Near Mint",
            id="TESTDATA-SYNTH-001",
            p="CHF 90,00",
            s="Kostenlos",
            country="Schweiz",
            img="https://example.invalid/testdata/synthetic-bulbasaur.jpg",
            url="https://example.invalid/testdata/synthetic-bulbasaur",
        ), mode="raw", now=NOW)
        checked = dv.apply_visual_slab_check(raw, image_bytes=_load("TESTDATA-psa-slab-bulbasaur.png"))
        self.assertFalse(checked["slab_verified"])
        solds = [
            self._sold(raw, "TESTDATA-SOLD-1", 180.0, date(2026, 10, 2)),
            self._sold(raw, "TESTDATA-SOLD-2", 200.0, date(2026, 9, 20)),
            self._sold(raw, "TESTDATA-SOLD-3", 220.0, date(2026, 8, 30)),
        ]
        self.assertEqual(dv.decide(raw, solds, now=NOW)["status"], "confirmed")

    def _sold(self, card, sid, price, when):
        row = dict(card)
        row.update({
            "id": sid,
            "url": f"https://example.invalid/testdata/{sid}",
            "title": card["title"],
            "price": price,
            "ship": 0.0,
            "ship_est": False,
            "import_chf": 0.0,
            "all_in_chf": price,
            "all_in_confirmed": True,
            "sold_date": when,
            "hidden_offer": False,
            "price_hidden": False,
            "drop": None,
            "key": card["key"],
        })
        return row


if __name__ == "__main__":
    unittest.main()
