"""Bounded, deterministic structural replay of private OKX 400-level L2 events.

This is a research integrity diagnostic, not proof of exchange event continuity,
queue position, executable fills, or any trading signal. Raw levels stay private.
"""
from __future__ import annotations

import argparse
from collections import Counter
import datetime as dt
from decimal import Decimal, InvalidOperation
import hashlib
import heapq
import json
from pathlib import Path
import tarfile

INSTRUMENT = "BTC-USDT-SWAP"
APPROVED_DAY = dt.date(2026, 10, 10)
APPROVED_ARCHIVE_SHA256 = "829b1393dcf7b9072383092f620039ba15b8be0d7e0fd422e9b66a8afd8b5a7e"
MAX_EVENTS = 15_000_000
MAX_LINE_BYTES = 16 * 1024 * 1024
MAX_LEVELS_PER_SIDE = 10_000  # Includes temporarily retained levels beyond top 400.
MAX_SNAPSHOT_LEVELS = 400


def _decimal(value, *, positive):
    if not isinstance(value, (str, int, float)) or isinstance(value, bool):
        raise ValueError("Invalid L2 numeric type")
    token = str(value)
    if len(token) > 40:
        raise ValueError("Excessive L2 numeric width")
    try:
        number = Decimal(token)
    except InvalidOperation as exc:
        raise ValueError("Invalid L2 numeric value") from exc
    if not number.is_finite() or number < 0 or (positive and number == 0):
        raise ValueError("Nonfinite or negative L2 numeric value")
    return number


def _levels(rows, *, side, snapshot):
    if not isinstance(rows, list):
        raise ValueError("L2 side is not an array")
    if snapshot and (not rows or len(rows) > MAX_SNAPSHOT_LEVELS):
        raise ValueError("Missing or oversized 400-level snapshot side")
    result = []
    seen = set()
    previous = None
    for row in rows:
        if not isinstance(row, list) or len(row) < 2:
            raise ValueError("Malformed L2 level")
        price = _decimal(row[0], positive=True)
        size = _decimal(row[1], positive=False)
        if price in seen:
            raise ValueError("Duplicate price within one L2 message")
        if snapshot and (size == 0 or (previous is not None and
                        ((side == "bids" and price >= previous) or
                         (side == "asks" and price <= previous)))):
            raise ValueError("Invalid snapshot size or side ordering")
        seen.add(price)
        previous = price
        result.append((price, size))
    return result


class Book:
    def __init__(self, max_levels=MAX_LEVELS_PER_SIDE):
        if not 1 <= max_levels <= MAX_LEVELS_PER_SIDE:
            raise ValueError("Invalid bounded replay level cap")
        self.max_levels = max_levels
        self.bids = {}
        self.asks = {}
        self.bid_heap = []
        self.ask_heap = []

    def _rebuild(self, side):
        data = self.bids if side == "bids" else self.asks
        heap = [(-p if side == "bids" else p) for p in data]
        heapq.heapify(heap)
        if side == "bids":
            self.bid_heap = heap
        else:
            self.ask_heap = heap

    def replace(self, bids, asks):
        if len(bids) > self.max_levels or len(asks) > self.max_levels:
            raise ValueError("L2 snapshot exceeds bounded memory cap")
        self.bids = dict(bids)
        self.asks = dict(asks)
        self._rebuild("bids")
        self._rebuild("asks")
        self.check()

    def update(self, bids, asks):
        for side, rows in (("bids", bids), ("asks", asks)):
            data = self.bids if side == "bids" else self.asks
            heap = self.bid_heap if side == "bids" else self.ask_heap
            # An update is a batch: delete levels first, then insert/change.
            # Enforce the bound BEFORE each new allocation, not only afterward.
            # This also permits a valid full-capacity delete+insert batch.
            for price, size in rows:
                if size == 0:
                    data.pop(price, None)
            for price, size in rows:
                if size != 0:
                    if price not in data:
                        if len(data) >= self.max_levels:
                            raise ValueError("L2 side exceeds bounded memory cap")
                        heapq.heappush(heap, -price if side == "bids" else price)
                    data[price] = size
            if len(heap) > max(4096, 4 * len(data)):
                self._rebuild(side)
        self.check()

    def _best(self, side):
        data = self.bids if side == "bids" else self.asks
        heap = self.bid_heap if side == "bids" else self.ask_heap
        while heap:
            price = -heap[0] if side == "bids" else heap[0]
            if price in data:
                return price
            heapq.heappop(heap)
        return None

    def check(self):
        bid, ask = self._best("bids"), self._best("asks")
        if bid is not None and ask is not None and bid >= ask:
            raise ValueError("Crossed or locked reconstructed order book")



def _bounded_lines(lines, *, max_line_bytes=MAX_LINE_BYTES):
    """Bound each physical JSONL line *during* reading from a file-like stream.

    Archive extraction uses a file-like object with readline(size). Generic
    pre-materialized iterables are still checked, but cannot undo allocations
    made by their producer before yielding an element.
    """
    if not 0 < max_line_bytes <= MAX_LINE_BYTES:
        raise ValueError("Invalid L2 line cap")
    if hasattr(lines, "readline"):
        while True:
            raw = lines.readline(max_line_bytes + 1)
            if not raw:
                return
            if len(raw) > max_line_bytes:
                raise ValueError("L2 replay event/line bound exceeded")
            # A final unterminated line exactly at the cap is allowed only
            # when EOF follows; never split a longer physical line into events.
            if len(raw) == max_line_bytes and not raw.endswith((b"\\n", "\\n")):
                if lines.read(1):
                    raise ValueError("L2 replay event/line bound exceeded")
            yield raw
    else:
        for raw in lines:
            if len(raw) > max_line_bytes:
                raise ValueError("L2 replay event/line bound exceeded")
            yield raw


def replay_stream(lines, *, utc_day=APPROVED_DAY, max_events=MAX_EVENTS,
                  max_levels=MAX_LEVELS_PER_SIDE):
    """Replay events in archive order, reset on snapshots; emit aggregates only."""
    start = int(dt.datetime.combine(utc_day, dt.time(), tzinfo=dt.timezone.utc).timestamp() * 1000)
    end = start + 86_400_000
    book = Book(max_levels)
    counts = Counter()
    mismatch = Counter()
    boundary_timing = Counter()
    previous_ts = None
    for raw in _bounded_lines(lines):
        if not raw.strip():
            continue
        counts["events"] += 1
        if counts["events"] > max_events or len(raw) > MAX_LINE_BYTES:
            raise ValueError("L2 replay event/line bound exceeded")
        try:
            event = json.loads(raw)
        except (ValueError, UnicodeError) as exc:
            raise ValueError("Malformed JSON L2 event") from exc
        if not isinstance(event, dict) or event.get("instId") != INSTRUMENT:
            raise ValueError("Unknown instrument or event object")
        action = event.get("action")
        if action not in ("snapshot", "partial", "update"):
            raise ValueError("Unknown L2 action")
        try:
            ts = int(event["ts"])
        except (KeyError, TypeError, ValueError, OverflowError) as exc:
            raise ValueError("Invalid L2 timestamp") from exc
        if ts < start or ts >= end:
            raise ValueError("L2 event outside expected UTC day")
        if previous_ts is not None and ts < previous_ts:
            raise ValueError("Out-of-order L2 timestamp")
        prior_event_ts = previous_ts
        previous_ts = ts
        snapshot = action in ("snapshot", "partial")
        bids = _levels(event.get("bids"), side="bids", snapshot=snapshot)
        asks = _levels(event.get("asks"), side="asks", snapshot=snapshot)
        if snapshot:
            if counts["snapshots"]:
                counts["snapshot_comparisons"] += 1
                # Diagnostic only: absence of sequence IDs makes equality inconclusive.
                # Compare only the visible depth of the incoming snapshot.
                # The replay intentionally retains older, deeper price levels;
                # comparing the entire book creates false mismatch reports.
                old_bids = {p: book.bids[p] for p in heapq.nlargest(len(bids), book.bids)}
                old_asks = {p: book.asks[p] for p in heapq.nsmallest(len(asks), book.asks)}
                new_bids, new_asks = dict(bids), dict(asks)
                different = old_bids != new_bids or old_asks != new_asks
                # The preceding event is not necessarily synchronized to the
                # exchange snapshot; timing is diagnostic, not gap evidence.
                gap_ms = ts - prior_event_ts
                boundary_timing["max_prior_event_gap_ms"] = max(
                    boundary_timing["max_prior_event_gap_ms"], gap_ms)
                boundary_timing["mismatched" if different else "matched"] += 1
                boundary_timing[("mismatched" if different else "matched") +
                                ("_same_timestamp" if gap_ms == 0 else "_positive_gap")] += 1
                if different:
                    mismatch["snapshots_differ_from_prior_replay"] += 1
                    for side, before, after, visible in (
                            ("bid", old_bids, new_bids, bids),
                            ("ask", old_asks, new_asks, asks)):
                        mismatch[f"{side}_price_levels_different"] += sum(
                            before.get(p) != after.get(p)
                            for p in before.keys() | after.keys())
                        mismatch[f"{side}_new_price_not_in_replay"] += sum(
                            p not in before for p in after)
                        mismatch[f"{side}_old_price_not_in_snapshot"] += sum(
                            p not in after for p in before)
                        mismatch[f"{side}_same_price_size_difference"] += sum(
                            before[p] != after[p] for p in before.keys() & after.keys())
                        old_visible = sorted(before.items(), reverse=(side == "bid"))
                        for depth in (1, 10, 50, 100, 400):
                            if old_visible[:depth] != visible[:depth]:
                                mismatch[f"{side}_top{depth}_boundaries_different"] += 1
            book.replace(bids, asks)
            counts["snapshots"] += 1
        else:
            if not counts["snapshots"]:
                raise ValueError("Update before first snapshot")
            book.update(bids, asks)
            counts["updates"] += 1
    if not counts["snapshots"]:
        raise ValueError("No starting L2 snapshot")
    return {
        "utc_day": utc_day.isoformat(),
        "instrument": INSTRUMENT,
        "status": "STRUCTURAL_REPLAY_PASS_NOT_EXCHANGE_CONTINUITY_PROOF",
        "events_parsed": counts["events"],
        "snapshots": counts["snapshots"],
        "updates": counts["updates"],
        "snapshot_comparisons": counts["snapshot_comparisons"],
        "snapshot_comparison_scope": "VISIBLE_DEPTH_ONLY_DIAGNOSTIC_NOT_SEQUENCE_PROOF",
        "comparison_diagnostics": dict(sorted(mismatch.items())),
        "boundary_timing_diagnostics": dict(sorted(boundary_timing.items())),
        "last_book_level_counts": {"bids": len(book.bids), "asks": len(book.asks)},
        "exchange_sequence_continuity": "NOT_VERIFIED_NO_SEQUENCE_IN_EXPOSED_JSON_FIELDS",
        "perfect_tick_reconstruction": "NOT_CLAIMED",
        "execution_or_fill_accuracy": "NOT_VERIFIED",
        "raw_market_events_in_report": False,
    }


def replay_archive(path, *, expected_sha=APPROVED_ARCHIVE_SHA256):
    digest = hashlib.sha256()
    with Path(path).open("rb") as source:
        for block in iter(lambda: source.read(8 * 1024 * 1024), b""):
            digest.update(block)
    if digest.hexdigest() != expected_sha:
        raise ValueError("Approved compressed L2 archive SHA256 mismatch")
    with tarfile.open(path, "r:gz") as archive:
        members = archive.getmembers()
        if len(members) != 1 or not members[0].isfile():
            raise ValueError("Unexpected L2 archive members")
        member = members[0]
        if Path(member.name).is_absolute() or ".." in Path(member.name).parts:
            raise ValueError("Unsafe archive member path")
        with archive.extractfile(member) as stream:
            result = replay_stream(stream)
    result["approved_compressed_sha256"] = expected_sha
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--archive", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    result = replay_archive(Path(args.archive))
    target = Path(args.out)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
