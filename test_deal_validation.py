# -*- coding: utf-8 -*-
"""Regressionen fuer den Fehlalarm vom 08.11.2026 und die gemeinsamen Tore.

Die positive Karte ist TEST DATA: ein synthetisches Angebot mit example.invalid-URL.
Ihre Preise sind keine Marktdaten. Nummern in den Nah-Verwechslungs-Titeln
(99/106, 5/106, 13/101, 15/109) sind ebenfalls nur Testtexte, keine Katalogaussage.
"""
import unittest
from datetime import date, datetime, timezone
from unittest.mock import patch
from zoneinfo import ZoneInfo

import deal_validation as dv
import vintage_deal_finder as v

NOW = datetime(2026, 11, 8, 2, 40, tzinfo=ZoneInfo("Europe/Zurich"))
PHOTO = "https://i.ebayimg.com/images/g/TEST-NOT-A-FETCH/s-l1600.jpg"
# Angebots-Pool aus deal_state.json, Schluessel JP|EX Emerald|Latios|Mega EX|raw ?,
# Lauf 2026-11-08. Das sind Wunschpreise, keine Verkaeufe.
ASKING = {
    "366780675900": [148.01, "261108"],
    "800809385065": [181.99, "261108"],
    "158189652586": [118.44, "261108"],
}


def item(title, **kw):
    raw = {
        "id": kw.get("id", "100000000001"),
        "t": title,
        "p": kw.get("p", "CHF 30,00"),
        "s": kw.get("s", "Kostenlos"),
        "img": kw.get("img", PHOTO),
        "sub": kw.get("sub", ""),
        "full": kw.get("full", title),
        "country": kw.get("country", "Schweiz"),
        "query": kw.get("query", ""),
        "ended": kw.get("ended", ""),
        "url": kw.get("url"),
    }
    return raw


SCREENSHOT = item(
    "M Latios-EX RR 049/078 XY6 Emerald Break – Pokemon Karte Japanisch",
    id="186769723941",
    p="CHF 25,25",
    s="",
    country="Japan",
    query="pokemon breakpoint holo",
    sub="",
)


class DealValidationTests(unittest.TestCase):
    def test_zurich_stamp_is_not_utc(self):
        utc = datetime(2026, 11, 8, 1, 40, tzinfo=timezone.utc)
        self.assertEqual(dv.zurich_stamp(utc), "08.11.2026 02:40 Europe/Zurich")

    def test_chf_parser_accepts_comma_and_dot(self):
        self.assertEqual(dv.parse_number_token("25,25"), 25.25)
        self.assertEqual(dv.parse_number_token("25.25"), 25.25)
        self.assertEqual(dv.parse_number_token("1'234,50"), 1234.50)
        self.assertIsNone(dv.one_chf_amount("")[0])

    def test_empty_shipping_is_not_a_flat_25(self):
        cost = dv.parse_cost("CHF 25,25", "", "Japan", "M Latios-EX", "08.11.2026")
        self.assertIsNone(cost["ship"])
        self.assertTrue(cost["ship_est"])
        self.assertFalse(cost["all_in_confirmed"])
        self.assertIsNone(cost["all_in_chf"])
        self.assertNotEqual(cost["all_in_chf"], 50.25)
        self.assertIn("shipping-unresolved", cost["cost_reasons"])
        self.assertIn("import-unresolved", cost["cost_reasons"])

    def test_swiss_free_shipping_is_confirmed_all_in(self):
        cost = dv.parse_cost("CHF 90,00", "Kostenlos", "Schweiz", "Bulbasaur", "08.11.2026")
        self.assertTrue(cost["all_in_confirmed"])
        self.assertEqual(cost["ship"], 0.0)
        self.assertEqual(cost["import_chf"], 0.0)
        self.assertEqual(cost["all_in_chf"], 90.0)
        self.assertEqual(cost["fx_source"], "eBay.ch Preis in CHF")

    def test_foreign_explicit_ship_without_import_is_not_all_in(self):
        cost = dv.parse_cost("CHF 40,00", "CHF 10,00", "Japan", "card", "08.11.2026")
        self.assertFalse(cost["all_in_confirmed"])
        self.assertIn("import-unresolved", cost["cost_reasons"])

    def test_foreign_with_stated_import_included(self):
        cost = dv.parse_cost("CHF 40,00", "CHF 10,00", "Japan", "Versand inkl. Zoll und MwSt", "08.11.2026")
        self.assertTrue(cost["all_in_confirmed"])
        self.assertEqual(cost["import_chf"], 0.0)
        self.assertEqual(cost["all_in_chf"], 50.0)

    def test_inconsistent_price_is_not_a_single_live_price(self):
        cost = dv.parse_cost("CHF 10,00 bis CHF 20,00", "Kostenlos", "Schweiz", "", "08.11.2026")
        self.assertFalse(cost["all_in_confirmed"])
        self.assertIn("price-inconsistent", cost["cost_reasons"])

    def test_budgets_stay_separate(self):
        self.assertEqual(dv.MAX_RAW_BULBASAUR, 400.0)
        self.assertTrue(dv.within_budget("raw", "bulbasaur", "Std", 400))
        self.assertFalse(dv.within_budget("raw", "bulbasaur", "Std", 400.01))
        self.assertTrue(dv.within_budget("psa", "bulbasaur", "Std", 199.99))
        self.assertFalse(dv.within_budget("psa", "bulbasaur", "Std", 200))
        self.assertTrue(dv.within_budget("raw", "latios", "Mega EX", 250))
        self.assertFalse(dv.within_budget("raw", "latios", "Mega EX", 251))
        self.assertTrue(dv.within_budget("raw", "rayquaza", "Gold Star", 600))
        self.assertFalse(dv.within_budget("raw", "rayquaza", "Gold Star", 600.01))
        self.assertTrue(dv.release_allowed(2016))
        self.assertFalse(dv.release_allowed(2017))

    def test_sold_date_ignores_the_set_year(self):
        self.assertIsNone(dv.parse_sold_date("Bulbasaur Holo Base Set 1999 English"))
        self.assertEqual(dv.parse_sold_date("Verkauft 2. Okt. 2026"), date(2026, 10, 2))
        self.assertEqual(dv.parse_sold_date("Sold Nov 8, 2026"), date(2026, 11, 8))

    def test_screenshot_false_positive_is_not_a_deal(self):
        card = dv.parse_listing(SCREENSHOT, mode="raw", now=NOW)
        self.assertEqual(card["set"], "XY Emerald Break")
        self.assertNotEqual(card["set"], "EX Emerald")
        self.assertEqual(card["era"], "XY")
        self.assertEqual(card["year"], 2015)
        self.assertEqual(card["lang"], "JP")
        self.assertEqual(card["number"], "49/78")
        self.assertEqual(card["number_display"], "049/078")
        self.assertEqual(card["rarity"], "RR")
        self.assertEqual(card["variant"], "Mega EX")
        self.assertEqual(card["display"], "M Latios-EX")
        self.assertNotIn("BREAK", card["tags"])
        self.assertEqual(card["finish"], "holo")
        self.assertIsNone(card["condition"])
        self.assertIn("unknown-condition", card["reasons"])
        self.assertFalse(card["all_in_confirmed"])
        self.assertIsNone(card["ship"])
        self.assertNotEqual(card.get("all_in_chf"), 50.25)
        self.assertEqual(card["query"], "pokemon breakpoint holo")
        self.assertNotEqual(card["set"], "XY BREAKpoint")
        history = {card["key"]: dict(ASKING)}
        decisions, confirmed = v.find_deals([card], history=history, solds_by_key={}, now=NOW)
        self.assertEqual(confirmed, [])
        decision = decisions[0]
        self.assertNotEqual(decision["status"], "confirmed")
        self.assertIsNone(decision["percent_below"])
        self.assertIsNone(decision["median"])
        self.assertNotEqual(decision["median"], 148.01)
        self.assertNotEqual(decision["percent_below"], 66)
        self.assertIn("shipping-unresolved", decision["reasons"])
        self.assertIn("import-unresolved", decision["reasons"])
        record = dv.artifact_record(decision)
        self.assertEqual(record["identity"]["set"], "XY Emerald Break")
        self.assertEqual(record["identity"]["year"], 2015)
        self.assertEqual(record["listing_url"], "https://www.ebay.ch/itm/186769723941")
        self.assertTrue(record["photo_url"])
        self.assertIsNone(record["percent_below"])
        self.assertIsNone(record["median_chf"])
        self.assertEqual(record["solds"], [])
        self.assertFalse(record["price"]["all_in_confirmed"])
        with self.assertRaises(RuntimeError):
            dv.discord_description(decision)

    def test_xy6_and_emerald_break_do_not_fall_through_to_ex_emerald(self):
        xy6 = dv.parse_listing(item(
            "M Latios-EX RR 049/078 XY6 Japanisch Near Mint",
            s="CHF 5,00", country="Schweiz",
        ), mode="raw", now=NOW)
        words = dv.parse_listing(item(
            "M Latios-EX RR 049/078 Emerald Break Japanisch Near Mint",
            s="CHF 5,00", country="Schweiz",
        ), mode="raw", now=NOW)
        plain = dv.parse_listing(item(
            "Latios ex 99/106 EX Emerald English Near Mint Holo",
        ), mode="raw", now=NOW)
        self.assertEqual(xy6["set"], "XY Emerald Break")
        self.assertEqual(words["set"], "XY Emerald Break")
        self.assertNotIn("BREAK", xy6["tags"])
        self.assertNotIn("BREAK", words["tags"])
        self.assertEqual(plain["set"], "EX Emerald")
        self.assertEqual(plain["year"], 2005)
        self.assertEqual(plain["lang"], "EN")
        self.assertEqual(plain["variant"], "EX")
        self.assertNotEqual(plain["key"], xy6["key"])
        ex_pat = next(p for n, p, _e in v.SETS if n == "EX Emerald")
        self.assertIsNone(__import__("re").search(ex_pat, "xy6 emerald break", __import__("re").I))

    def test_near_set_rarity_and_grade_keys_differ(self):
        point = dv.parse_listing(item("Rayquaza BREAK 85/122 XY BREAKpoint English Near Mint Holo"), mode="raw", now=NOW)
        through = dv.parse_listing(item("Rayquaza BREAK 85/122 XY BREAKthrough English Near Mint Holo"), mode="raw", now=NOW)
        frontiers = dv.parse_listing(item("Rayquaza ex 13/101 EX Dragon Frontiers English Near Mint Holo"), mode="raw", now=NOW)
        dragon = dv.parse_listing(item("Rayquaza ex 13/97 EX Dragon English Near Mint Holo"), mode="raw", now=NOW)
        returns = dv.parse_listing(item("Dark Charizard 15/109 Team Rocket Returns English Near Mint Holo"), mode="raw", now=NOW)
        rocket = dv.parse_listing(item("Dark Charizard 21/82 Team Rocket English Near Mint Holo"), mode="raw", now=NOW)
        sceptile = dv.parse_listing(item("Sceptile 5/106 EX Emerald English Near Mint Holo"), mode="raw", now=NOW)
        self.assertEqual(point["set"], "XY BREAKpoint")
        self.assertEqual(through["set"], "XY BREAKthrough")
        self.assertEqual(point["variant"], "BREAK")
        self.assertNotEqual(point["key"], through["key"])
        self.assertEqual(frontiers["set"], "EX Dragon Frontiers")
        self.assertEqual(dragon["set"], "EX Dragon")
        self.assertEqual(frontiers["variant"], "EX")
        self.assertNotEqual(frontiers["key"], dragon["key"])
        self.assertEqual(returns["set"], "EX Team Rocket Returns")
        self.assertEqual(rocket["set"], "Team Rocket")
        self.assertEqual(sceptile["variant"], "Std")
        rr = dv.parse_listing(item("M Latios-EX RR 049/078 XY6 Emerald Break Japanisch Near Mint", s="CHF 4,00"), mode="raw", now=NOW)
        sr = dv.parse_listing(item("M Latios-EX SR 080/078 XY6 Emerald Break Japanisch Near Mint", s="CHF 4,00"), mode="raw", now=NOW)
        other = dv.parse_listing(item("M Latios-EX RR 050/078 XY6 Emerald Break Japanisch Near Mint", s="CHF 4,00"), mode="raw", now=NOW)
        self.assertNotEqual(rr["key"], sr["key"])
        self.assertNotEqual(rr["key"], other["key"])
        self.assertEqual(rr["rarity"], "RR")
        self.assertEqual(sr["number"], "80/78")
        psa9 = dv.parse_listing(item(
            "M Latios-EX RR 049/078 XY6 Emerald Break Japanisch PSA 9",
            sub="Graded - PSA 9",
        ), mode="psa", now=NOW)
        psa10 = dv.parse_listing(item(
            "M Latios-EX RR 049/078 XY6 Emerald Break Japanisch PSA 10",
            sub="Graded - PSA 10",
        ), mode="psa", now=NOW)
        self.assertEqual(psa9["condition"], "PSA 9")
        self.assertEqual(psa10["condition"], "PSA 10")
        self.assertNotEqual(psa9["key"], psa10["key"])
        self.assertNotEqual(psa9["key"], rr["key"])
        conflict = dv.parse_listing(item(
            "M Latios-EX RR 049/078 XY6 Emerald Break Japanisch PSA 9",
            sub="Graded - PSA 10",
        ), mode="psa", now=NOW)
        self.assertIn("psa-grade-conflict", conflict["reasons"])
        self.assertNotEqual(dv.decide(conflict, [], now=NOW)["status"], "confirmed")
        buried = dv.parse_listing(item(
            "M Latios-EX RR 049/078 XY6 Emerald Break Japanisch Near Mint",
            full="seller also has PSA 8 copies",
        ), mode="psa", now=NOW)
        self.assertIn("psa-label-missing", buried["reasons"])
        self.assertNotEqual(dv.decide(buried, [], now=NOW)["status"], "confirmed")

    def test_vending_tcg_is_not_carddass_bandai_or_meiji(self):
        vending = dv.parse_listing(item("Pokemon Vending Series Bulbasaur Holo Japanese"), mode="raw", now=NOW)
        self.assertIsNone(vending["drop"])
        self.assertEqual(vending["set"], "Vending (JP)")
        self.assertNotEqual(dv.decide(vending, [], now=NOW)["status"], "confirmed")
        self.assertIn("year-unknown", vending["reasons"])
        for title in ("Pokemon Carddass Bulbasaur", "Pokemon Bandai Bulbasaur Holo", "Pokemon Meiji Bulbasaur"):
            card = dv.parse_listing(item(title), mode="raw", now=NOW)
            self.assertEqual(card["drop"], "ausschluss-wort", title)

    def test_bisasam_alias_and_german_wording_stays_excluded(self):
        jp = dv.parse_listing(item("Bisasam Holo Base Set Japanisch Unlimited Near Mint 1/102"), mode="raw", now=NOW)
        self.assertEqual(jp["base"], "bulbasaur")
        self.assertEqual(jp["lang"], "JP")
        self.assertEqual(jp["year"], 1996)
        kana = dv.parse_listing(item("フシギダネ Holo Base Set Japanisch Unlimited Near Mint"), mode="raw", now=NOW)
        self.assertEqual(kana["base"], "bulbasaur")
        de = dv.parse_listing(item("Bisasam Holo Base Set deutsch Near Mint"), mode="raw", now=NOW)
        self.assertEqual(de["drop"], "ausschluss-wort")
        bare = dv.parse_listing(item("Bisasam Holo Base Set Unlimited Near Mint 1/102"), mode="raw", now=NOW)
        self.assertIn("language-unknown", bare["reasons"])
        self.assertNotEqual(dv.decide(bare, [], now=NOW)["status"], "confirmed")

    def test_modern_team_rocket_ex_is_not_wotc(self):
        card = dv.parse_listing(item("Team Rocket's Mewtwo ex Glory of Team Rocket PSA 10"), mode="raw", now=NOW)
        self.assertEqual(card["drop"], "modern")
        self.assertNotEqual(dv.decide(card, [], now=NOW)["status"], "confirmed")

    def test_positive_synthetic_raw_deal(self):
        raw = item(
            "TEST DATA SYNTHETIC ONLY Bulbasaur Holo 15/102 Base Set 1999 English Unlimited Near Mint",
            id="TESTDATA-SYNTH-001",
            p="CHF 90,00",
            s="Kostenlos",
            country="Schweiz",
            img="https://example.invalid/testdata/synthetic-bulbasaur.jpg",
            url="https://example.invalid/testdata/synthetic-bulbasaur",
        )
        card = dv.parse_listing(raw, mode="raw", now=NOW)
        self.assertTrue(card["title"].startswith("TEST DATA"))
        self.assertNotIn("ebay.ch/itm", card["url"])
        self.assertTrue(card["identity_complete"])
        self.assertEqual(card["base"], "bulbasaur")
        self.assertEqual(card["year"], 1999)
        self.assertEqual(card["edition"], "unlimited")
        self.assertEqual(card["condition"], "NM")
        self.assertEqual(card["all_in_chf"], 90.0)
        self.assertTrue(card["needs_sold_lookup"])
        solds = [
            self._sold(card, "TESTDATA-SOLD-1", 180.0, date(2026, 10, 2)),
            self._sold(card, "TESTDATA-SOLD-2", 200.0, date(2026, 9, 20)),
            self._sold(card, "TESTDATA-SOLD-3", 220.0, date(2026, 8, 30)),
        ]
        decision = dv.decide(card, solds, now=NOW)
        self.assertEqual(decision["status"], "confirmed")
        self.assertEqual(decision["median_kind"], "sold")
        self.assertEqual(decision["n"], 3)
        self.assertEqual(decision["window_days"], 90)
        self.assertEqual(decision["median"], 200.0)
        self.assertEqual(decision["percent_below"], 55)
        text = dv.discord_description(decision, now=NOW)
        self.assertIn("verkauftem Median", text)
        self.assertIn("Europe/Zurich", text)
        self.assertIn("https://example.invalid/testdata/TESTDATA-SOLD-1", text)
        for banned in ("GESCHAETZT", "Preishistorie", "anderen Angeboten", "Collectr"):
            self.assertNotIn(banned.lower(), text.lower())
        record = dv.artifact_record(decision)
        self.assertEqual(record["n"], 3)
        self.assertEqual(len(record["solds"]), 3)
        self.assertTrue(all("example.invalid" in s["url"] for s in record["solds"]))

    def test_equal_to_median_and_thin_windows_are_not_deals(self):
        card = self._synthetic()
        three = [
            self._sold(card, "TESTDATA-SOLD-A", 180.0, date(2026, 10, 1)),
            self._sold(card, "TESTDATA-SOLD-B", 200.0, date(2026, 9, 1)),
            self._sold(card, "TESTDATA-SOLD-C", 220.0, date(2026, 6, 15)),
        ]
        widened = dv.decide(card, three, now=NOW)
        self.assertEqual(widened["status"], "confirmed")
        self.assertEqual(widened["window_days"], 180)
        only_two = three[:2]
        thin = dv.decide(card, only_two, now=NOW)
        self.assertEqual(thin["status"], "unverified")
        self.assertIn("insufficient-solds", thin["reasons"])
        self.assertIsNone(thin["percent_below"])
        old = three[:2] + [self._sold(card, "TESTDATA-SOLD-OLD", 300.0, date(2026, 4, 1))]
        self.assertEqual(dv.decide(card, old, now=NOW)["status"], "unverified")
        priced = dict(card)
        priced["price"] = 200.0
        priced["all_in_chf"] = 200.0
        tied = [
            self._sold(card, "TESTDATA-SOLD-A", 180.0, date(2026, 10, 2)),
            self._sold(card, "TESTDATA-SOLD-B", 200.0, date(2026, 9, 20)),
            self._sold(card, "TESTDATA-SOLD-C", 220.0, date(2026, 8, 30)),
        ]
        not_below = dv.decide(priced, tied, now=NOW)
        self.assertEqual(not_below["status"], "nonqualified")
        self.assertIn("not-below-sold-median", not_below["reasons"])
        self.assertIsNone(not_below["percent_below"])
        self.assertIsNone(not_below["median"])

    def test_hidden_offers_lots_duplicates_and_wrong_keys_do_not_count(self):
        card = self._synthetic()
        good = [
            self._sold(card, "TESTDATA-SOLD-1", 180.0, date(2026, 10, 2)),
            self._sold(card, "TESTDATA-SOLD-2", 200.0, date(2026, 9, 20)),
        ]
        hidden = self._sold(card, "TESTDATA-HIDDEN", None, date(2026, 8, 30))
        hidden["price_hidden"] = True
        hidden["hidden_offer"] = True
        hidden["all_in_confirmed"] = False
        hidden["all_in_chf"] = None
        lot = self._sold(card, "TESTDATA-LOT", 50.0, date(2026, 8, 30))
        lot["title"] = "TEST DATA Bulbasaur lot"
        dup = self._sold(card, "TESTDATA-SOLD-1", 1.0, date(2026, 8, 1))
        wrong = self._sold(card, "TESTDATA-WRONG", 30.0, date(2026, 8, 30))
        wrong["key"] = card["key"] + "|other"
        foreign = self._sold(card, "TESTDATA-JP", 40.0, date(2026, 8, 30))
        foreign["import_chf"] = None
        foreign["all_in_confirmed"] = False
        decision = dv.decide(card, good + [hidden, lot, dup, wrong, foreign], now=NOW)
        self.assertEqual(decision["status"], "unverified")
        self.assertIn("insufficient-solds", decision["reasons"])
        full = good + [self._sold(card, "TESTDATA-SOLD-3", 220.0, date(2026, 8, 30)), dup]
        confirmed = dv.decide(card, full, now=NOW)
        self.assertEqual(confirmed["status"], "confirmed")
        self.assertEqual(confirmed["n"], 3)
        self.assertEqual(confirmed["median"], 200.0)

    def test_psa_is_not_confirmed_without_a_read_slab(self):
        card = dv.parse_listing(item(
            "TEST DATA SYNTHETIC ONLY Bulbasaur Holo 15/102 Base Set 1999 English Unlimited PSA 8",
            id="TESTDATA-PSA-001",
            p="CHF 40,00",
            s="Kostenlos",
            country="Schweiz",
            img="https://example.invalid/testdata/synthetic-psa.jpg",
            url="https://example.invalid/testdata/synthetic-psa",
            sub="Graded - PSA 8",
        ), mode="psa", now=NOW)
        self.assertFalse(card["slab_verified"])
        self.assertEqual(card["condition"], "PSA 8")
        self.assertTrue(card["all_in_chf"] < 200)
        solds = [
            self._sold(card, "TESTDATA-PSA-S1", 80.0, date(2026, 10, 2)),
            self._sold(card, "TESTDATA-PSA-S2", 100.0, date(2026, 9, 20)),
            self._sold(card, "TESTDATA-PSA-S3", 120.0, date(2026, 8, 30)),
        ]
        blocked = dv.decide(card, solds, now=NOW)
        self.assertEqual(blocked["status"], "unverified")
        self.assertIn("psa-slab-label-not-verified", blocked["reasons"])
        self.assertIsNone(blocked["percent_below"])
        self.assertEqual(len(blocked["solds_observed"]), 3)
        opted = dict(card)
        opted["slab_verified"] = True
        confirmed = dv.decide(opted, solds, now=NOW)
        self.assertEqual(confirmed["status"], "confirmed")
        self.assertTrue(confirmed["title"].startswith("TEST DATA"))
        self.assertNotIn("ebay.ch/itm", confirmed["url"])
        other_grade = dict(solds[0])
        other_grade["id"] = "TESTDATA-PSA-9"
        other_grade["condition"] = "PSA 9"
        other_grade["key"] = dv.make_key(other_grade)
        only_nines = [dict(s, id=f"TESTDATA-NINE-{i}", condition="PSA 9", key=other_grade["key"]) for i, s in enumerate(solds)]
        self.assertNotEqual(dv.decide(opted, only_nines, now=NOW)["status"], "confirmed")
        no_photo = dv.parse_listing(item(
            "Bulbasaur Holo 15/102 Base Set English Unlimited PSA 8",
            img="",
            sub="Graded - PSA 8",
        ), mode="psa", now=NOW)
        self.assertIn("photo-missing", no_photo["reasons"])
        self.assertNotEqual(dv.decide(no_photo, [], now=NOW)["status"], "confirmed")

    def test_seen_and_posted_are_separate_and_history_is_not_extended(self):
        state = dv.normalize_state({"seen": {"1": "2026-11-01"}, "history": {"old": {}}})
        self.assertEqual(state["posted"], {"1": "2026-11-01"})
        self.assertEqual(state["seen"], {"1": "2026-11-01"})
        again = dv.normalize_state({"seen": {"2": "2026-11-02"}, "posted": {"1": "2026-10-01"}, "history": {}})
        self.assertEqual(again["posted"], {"1": "2026-10-01"})
        card = self._synthetic()
        confirmed = dv.decide(card, [
            self._sold(card, "TESTDATA-SOLD-1", 180.0, date(2026, 10, 2)),
            self._sold(card, "TESTDATA-SOLD-2", 200.0, date(2026, 9, 20)),
            self._sold(card, "TESTDATA-SOLD-3", 220.0, date(2026, 8, 30)),
        ], now=NOW)
        picked = dv.select_new([confirmed], posted_ids=set(), limit=15)
        self.assertEqual([d["id"] for d in picked], [confirmed["id"]])
        self.assertEqual(dv.select_new([confirmed], posted_ids={confirmed["id"]}), [])
        history = {}
        v.update_history(history, [confirmed])
        self.assertEqual(history, {})

    def test_discord_is_quiet_and_refuses_unconfirmed_or_dry_run(self):
        card = dv.parse_listing(SCREENSHOT, mode="raw", now=NOW)
        decision = dv.decide(card, [], now=NOW)
        with patch("urllib.request.urlopen") as opened:
            with self.assertRaises(RuntimeError):
                v.post_discord("https://example.invalid/hook", [decision])
            opened.assert_not_called()
            v.post_discord("https://example.invalid/hook", [])
            opened.assert_not_called()
        synthetic = dv.decide(self._synthetic(), [
            self._sold(self._synthetic(), "TESTDATA-SOLD-1", 180.0, date(2026, 10, 2)),
            self._sold(self._synthetic(), "TESTDATA-SOLD-2", 200.0, date(2026, 9, 20)),
            self._sold(self._synthetic(), "TESTDATA-SOLD-3", 220.0, date(2026, 8, 30)),
        ], now=NOW)
        old = v.DRY_RUN
        v.DRY_RUN = True
        try:
            with patch("urllib.request.urlopen") as opened:
                with self.assertRaises(RuntimeError):
                    v.post_discord("https://example.invalid/hook", [synthetic])
                opened.assert_not_called()
        finally:
            v.DRY_RUN = old
        self.assertIn("bulbasaur", v.SEARCHES[0])
        self.assertIn("Europe/Zurich", dv.discord_footer(synthetic, now=NOW))

    def _synthetic(self):
        raw = item(
            "TEST DATA SYNTHETIC ONLY Bulbasaur Holo 15/102 Base Set 1999 English Unlimited Near Mint",
            id="TESTDATA-SYNTH-001",
            p="CHF 90,00",
            s="Kostenlos",
            country="Schweiz",
            img="https://example.invalid/testdata/synthetic-bulbasaur.jpg",
            url="https://example.invalid/testdata/synthetic-bulbasaur",
        )
        return dv.parse_listing(raw, mode="raw", now=NOW)

    def _sold(self, card, sid, price, when):
        row = dict(card)
        row.update({
            "id": sid,
            "url": f"https://example.invalid/testdata/{sid}",
            "img": "https://example.invalid/testdata/sold.jpg",
            "title": card["title"],
            "price": price,
            "ship": 0.0,
            "ship_est": False,
            "import_chf": 0.0,
            "all_in_chf": price,
            "all_in_confirmed": price is not None,
            "currency": "CHF",
            "fx_source": "eBay.ch Preis in CHF",
            "fx_date": "08.11.2026",
            "sold_date": when,
            "hidden_offer": False,
            "price_hidden": False,
            "drop": None,
            "key": card["key"],
        })
        return row


if __name__ == "__main__":
    unittest.main()
