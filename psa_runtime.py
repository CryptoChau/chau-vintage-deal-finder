# -*- coding: utf-8 -*-
"""Zeit- und Mengengrenzen fuer den PSA-Lauf.

Die Suchliste bleibt vollstaendig. Ein Lauf, der nicht alles schafft, ist
PARTIAL und sagt nichts ueber den gesamten Markt.
"""
from __future__ import annotations

import time


class RunClock:
    def __init__(self, budget_s, reserve_s, monotonic=None):
        self._mono = monotonic or time.monotonic
        self.t0 = self._mono()
        self.budget_s = budget_s
        self.reserve_s = reserve_s

    def elapsed(self):
        return self._mono() - self.t0

    def search_open(self):
        return self.elapsed() < max(0, self.budget_s - self.reserve_s)

    def validation_open(self):
        return self.elapsed() < self.budget_s


def is_bulbasaur(card):
    return (card.get("base") or "").lower() == "bulbasaur"


def slab_queue(cards, limit):
    """Nur Karten, die die billigen Tore schon geschafft haben. Bisasam zuerst."""
    eligible = [
        c for c in cards
        if c.get("mode") == "psa" and not c.get("drop") and c.get("needs_sold_lookup")
    ]
    eligible.sort(key=lambda c: (0 if is_bulbasaur(c) else 1, c.get("tier", 9), str(c.get("id") or "")))
    limit = max(0, int(limit))
    return eligible[:limit], eligible[limit:]


def _skipped_card(card):
    card = dict(card)
    card["needs_sold_lookup"] = False
    card["slab_verified"] = False
    card["slab_check"] = "not-reached"
    card["slab_cert_authenticated"] = False
    reasons = list(card.get("reasons") or [])
    if "slab-check-skipped-partial" not in reasons:
        reasons.append("slab-check-skipped-partial")
    card["reasons"] = reasons
    return card


def apply_bounded_slab_checks(cards, checker, limit, should_continue, on_progress=None):
    """OCR nur fuer die ausgewaehlte Schlange. Der Rest wird nicht bestaetigt."""
    chosen, skipped = slab_queue(cards, limit)
    checked = {}
    attempted = 0
    matched = 0
    stopped = False
    for index, card in enumerate(chosen, start=1):
        if not should_continue():
            stopped = True
            break
        result = checker(card)
        attempted += 1
        if result.get("slab_verified"):
            matched += 1
        checked[card.get("id")] = result
        if on_progress:
            on_progress(attempted, len(chosen))
    skip_ids = {c.get("id") for c in skipped}
    skip_ids.update(c.get("id") for c in chosen if c.get("id") not in checked)
    out = []
    for card in cards:
        cid = card.get("id")
        if cid in checked:
            out.append(checked[cid])
        elif cid in skip_ids:
            out.append(_skipped_card(card))
        else:
            out.append(card)
    return out, {
        "ocr_attempted": attempted,
        "ocr_matched": matched,
        "ocr_skipped": len(skip_ids),
        "ocr_eligible": len(chosen) + len(skipped),
        "stopped_early": stopped,
    }


def reason_counts(decisions):
    counts = {}
    for decision in decisions:
        if decision.get("status") == "confirmed":
            continue
        reasons = decision.get("reasons") or ["unspecified"]
        for reason in reasons:
            counts[reason] = counts.get(reason, 0) + 1
    return counts


def format_run_summary(stats):
    partial = bool(
        stats.get("queries_done", 0) < stats.get("queries_total", 0)
        or stats.get("ocr_skipped", 0) > 0
        or stats.get("stopped_early")
    )
    lines = []
    if partial:
        lines.append("PARTIAL")
        lines.append("keine Aussage ueber den gesamten Markt")
    else:
        lines.append("COMPLETE")
    lines.append(
        f"queries {stats.get('queries_done', 0)}/{stats.get('queries_total', 0)}"
    )
    lines.append(f"listings {stats.get('listings', 0)}")
    lines.append(f"classified {stats.get('classified', 0)}")
    lines.append(f"ocr_attempted {stats.get('ocr_attempted', 0)}")
    lines.append(f"ocr_matched {stats.get('ocr_matched', 0)}")
    lines.append(f"ocr_skipped {stats.get('ocr_skipped', 0)}")
    lines.append(f"sold_lookups {stats.get('sold_lookups', 0)}")
    lines.append(f"qualified {stats.get('qualified', 0)}")
    lines.append(f"posted {stats.get('posted', 0)}")
    rejected = stats.get("rejected") or {}
    if rejected:
        lines.append("rejected " + ",".join(f"{k}={v}" for k, v in sorted(rejected.items())))
    names = stats.get("queries_skipped_names") or []
    if names:
        lines.append("not_searched " + " | ".join(names[:12]))
    lines.append(
        "Fruehere abgebrochene Laeufe ohne State-Commit beweisen keine 0 Posts. "
        "Gezaehlt wird nur HTTP 200/204."
    )
    return "\n".join(lines)


def record_delivery(state, examined_ids, sent_ids, summary, day):
    """examined -> seen. Nur tatsaechlich gesendete Ids -> posted."""
    state = dict(state or {})
    seen = dict(state.get("seen") or {})
    posted = dict(state.get("posted") or {})
    for item_id in examined_ids:
        seen[str(item_id)] = day
    for item_id in sent_ids:
        posted[str(item_id)] = day
    state["seen"] = seen
    state["posted"] = posted
    state["last_run"] = dict(summary)
    state["last_run"]["posted_ids"] = [str(i) for i in sent_ids]
    return state
