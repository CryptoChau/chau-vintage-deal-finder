# -*- coding: utf-8 -*-
"""Grenzen fuer den PSA-Lauf. Testdaten sind synthetisch, keine echten Deals."""
import sys
import time
import unittest

import psa_runtime
import psa_vintage_deal_finder as psa


def _card(cid, base="pikachu", eligible=True):
    return {
        "id": cid,
        "mode": "psa",
        "drop": None,
        "base": base,
        "tier": -1 if base == "bulbasaur" else 2,
        "needs_sold_lookup": eligible,
        "title": f"TEST DATA {base} {cid}",
        "reasons": [],
    }


class PsaRuntimeTests(unittest.TestCase):
    def test_large_queue_checks_bulbasaur_first_and_stays_fast(self):
        cards = [_card(f"T{i}") for i in range(4000)]
        cards.append(_card("BISA", base="bulbasaur"))
        calls = []

        def checker(card):
            calls.append(card["base"])
            return dict(card, slab_verified=False, slab_check="visual-label-mismatch")

        started = time.monotonic()
        out, stats = psa_runtime.apply_bounded_slab_checks(
            cards, checker, limit=24, should_continue=lambda: True,
        )
        elapsed = time.monotonic() - started
        self.assertLess(elapsed, 1.0)
        self.assertEqual(calls[0], "bulbasaur")
        self.assertEqual(len(calls), 24)
        self.assertEqual(stats["ocr_attempted"], 24)
        self.assertEqual(stats["ocr_eligible"], 4001)
        self.assertGreater(stats["ocr_skipped"], 0)
        skipped = [c for c in out if c.get("slab_check") == "not-reached"]
        self.assertTrue(all(not c.get("needs_sold_lookup") for c in skipped))
        self.assertTrue(all(c["title"].startswith("TEST DATA") for c in out))

    def test_deadline_stops_a_slow_queue(self):
        cards = [_card(f"T{i}", base="bulbasaur") for i in range(200)]
        calls = {"n": 0}

        def checker(card):
            calls["n"] += 1
            time.sleep(0.02)
            return dict(card, slab_verified=False, slab_check="visual-label-mismatch")

        started = time.monotonic()
        _out, stats = psa_runtime.apply_bounded_slab_checks(
            cards, checker, limit=200, should_continue=lambda: calls["n"] < 4,
        )
        self.assertEqual(calls["n"], 4)
        self.assertTrue(stats["stopped_early"])
        self.assertLess(time.monotonic() - started, 1.0)

    def test_partial_summary_is_not_a_market_wide_zero(self):
        text = psa_runtime.format_run_summary({
            "queries_done": 40,
            "queries_total": 97,
            "queries_skipped_names": ["pokemon japanese pcg psa"],
            "listings": 18748,
            "classified": 1000,
            "ocr_attempted": 10,
            "ocr_matched": 0,
            "ocr_skipped": 50,
            "sold_lookups": 0,
            "qualified": 0,
            "posted": 0,
            "rejected": {"shipping-unresolved": 12},
        })
        self.assertIn("PARTIAL", text)
        self.assertIn("queries 40/97", text)
        self.assertIn("posted 0", text)
        self.assertIn("keine Aussage ueber den gesamten Markt", text)
        self.assertNotIn("keine Deals auf dem gesamten Markt", text)
        self.assertIn("keine 0 Posts", text)
        complete = psa_runtime.format_run_summary({
            "queries_done": 3,
            "queries_total": 3,
            "ocr_skipped": 0,
            "listings": 1,
            "classified": 1,
            "ocr_attempted": 1,
            "ocr_matched": 1,
            "sold_lookups": 1,
            "qualified": 1,
            "posted": 1,
        })
        self.assertIn("COMPLETE", complete)
        self.assertNotIn("PARTIAL", complete)

    def test_delivery_records_only_successful_posts(self):
        state = psa_runtime.record_delivery(
            {"seen": {}, "posted": {}},
            examined_ids=["seen-only", "posted-one"],
            sent_ids=["posted-one"],
            summary={"partial": True, "qualified": 2, "posted": 1},
            day="2026-11-08",
        )
        self.assertIn("seen-only", state["seen"])
        self.assertNotIn("seen-only", state["posted"])
        self.assertEqual(state["posted"]["posted-one"], "2026-11-08")
        self.assertEqual(state["last_run"]["posted_ids"], ["posted-one"])
        self.assertTrue(state["last_run"]["partial"])

    def test_log_flushes_immediately(self):
        class Buf:
            def __init__(self):
                self.writes = []
                self.flushes = 0

            def write(self, text):
                self.writes.append(text)

            def flush(self):
                self.flushes += 1

        buf = Buf()
        old = sys.stdout
        sys.stdout = buf
        try:
            psa.log("TEST DATA fortschritt")
        finally:
            sys.stdout = old
        self.assertGreaterEqual(buf.flushes, 1)
        self.assertTrue(any("TEST DATA fortschritt" in part for part in buf.writes))

    def test_clock_reserves_validation_time(self):
        now = {"t": 0.0}

        def mono():
            return now["t"]

        clock = psa_runtime.RunClock(100, 30, monotonic=mono)
        self.assertTrue(clock.search_open())
        now["t"] = 70
        self.assertFalse(clock.search_open())
        self.assertTrue(clock.validation_open())
        now["t"] = 100
        self.assertFalse(clock.validation_open())


if __name__ == "__main__":
    unittest.main()
