"""Synthetic-only regression tests; no real market ticks or network."""
import datetime as dt
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import tarfile
import tempfile
import unittest

src = Path(__file__).resolve().parents[1] / "data" / "okx_l2_replay.py"
spec = importlib.util.spec_from_file_location("okx_l2_replay_test", src)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
START = int(dt.datetime(2026, 10, 10, tzinfo=dt.timezone.utc).timestamp() * 1000)


def event(sec, action, bids=None, asks=None):
    return (json.dumps({"ts": str(START + sec * 1000), "instId": "BTC-USDT-SWAP",
                        "action": action,
                        "bids": [["100", "1"], ["99", "2"]] if bids is None else bids,
                        "asks": [["101", "1"], ["102", "2"]] if asks is None else asks}) + "\n").encode()


class ReplayTests(unittest.TestCase):
    def test_reset_update_delete_and_comparison(self):
        rows = [event(0, "snapshot"),
                event(1, "update", [["100", "0"], ["98", "4"]], [["101", "3"]]),
                event(2, "snapshot", [["99", "2"], ["98", "4"]], [["101", "3"], ["102", "2"]]),
                event(3, "update", [["99", "0"]], [])]
        result = mod.replay_stream(rows)
        self.assertEqual((result["snapshots"], result["updates"]), (2, 2))
        self.assertEqual(result["comparison_diagnostics"], {})
        self.assertEqual(result["last_book_level_counts"], {"bids": 1, "asks": 2})
        self.assertEqual(result["exchange_sequence_continuity"], "NOT_VERIFIED_NO_SEQUENCE_IN_EXPOSED_JSON_FIELDS")
        self.assertFalse(result["raw_market_events_in_report"])

    def test_snapshot_mismatch_is_diagnostic_not_false_continuity_proof(self):
        x = mod.replay_stream([event(0, "snapshot"), event(1, "snapshot", [["100", "3"]], [["101", "2"]])])
        self.assertEqual(x["comparison_diagnostics"]["snapshots_differ_from_prior_replay"], 1)
        self.assertIn("NOT_EXCHANGE_CONTINUITY_PROOF", x["status"])

    def test_boundary_mismatch_rank_price_size_and_timing_are_aggregate_only(self):
        rows = [
            event(0, "snapshot"),
            event(1, "snapshot", [["100", "3"], ["99", "2"]]),
            event(2, "snapshot", [["100", "3"], ["98", "2"]]),
        ]
        result = mod.replay_stream(rows)
        diag = result["comparison_diagnostics"]
        self.assertEqual(diag["snapshots_differ_from_prior_replay"], 2)
        self.assertEqual(diag["bid_top1_boundaries_different"], 1)
        self.assertEqual(diag["bid_top10_boundaries_different"], 2)
        self.assertEqual(diag["bid_same_price_size_difference"], 1)
        self.assertEqual(diag["bid_new_price_not_in_replay"], 1)
        self.assertEqual(diag["bid_old_price_not_in_snapshot"], 1)
        self.assertNotIn("ask_top1_boundaries_different", diag)
        timing = result["boundary_timing_diagnostics"]
        self.assertEqual(timing["mismatched_positive_gap"], 2)
        self.assertEqual(timing["max_prior_event_gap_ms"], 1000)
        self.assertFalse(result["raw_market_events_in_report"])

    def test_snapshot_visible_depth_ignores_retained_deeper_levels(self):
        rows = [event(0, "snapshot"),
                event(1, "update", [["98", "1"]], [["103", "1"]]),
                event(2, "snapshot")]
        result = mod.replay_stream(rows)
        self.assertEqual(result["snapshot_comparisons"], 1)
        self.assertEqual(result["comparison_diagnostics"], {})
        self.assertIn("VISIBLE_DEPTH_ONLY", result["snapshot_comparison_scope"])

    def test_initial_snapshot_respects_memory_cap(self):
        with self.assertRaisesRegex(ValueError, "memory cap"):
            mod.replay_stream([event(0, "snapshot")], max_levels=1)

    def test_update_batch_never_transiently_exceeds_cap(self):
        # A delete and insert in one message must never temporarily allocate
        # a third level when the two-level side is already full.
        class GuardDict(dict):
            def __setitem__(self, key, value):
                if key not in self and len(self) >= 2:
                    raise AssertionError("transient level-cap breach")
                super().__setitem__(key, value)

        book = mod.Book(max_levels=2)
        book.replace([(100, 1), (99, 2)], [(101, 1), (102, 2)])
        book.bids = GuardDict(book.bids)
        # Insert intentionally precedes delete in the incoming update.
        book.update([(98, 3), (100, 0)], [])
        self.assertEqual(set(book.bids), {99, 98})

    def test_update_over_capacity_rejected_before_new_allocation(self):
        class GuardDict(dict):
            def __setitem__(self, key, value):
                if key not in self and len(self) >= 2:
                    raise AssertionError("transient level-cap breach")
                super().__setitem__(key, value)

        book = mod.Book(max_levels=2)
        book.replace([(100, 1), (99, 2)], [(101, 1), (102, 2)])
        book.bids = GuardDict(book.bids)
        with self.assertRaisesRegex(ValueError, "memory cap"):
            book.update([(98, 3)], [])

    def test_update_before_snapshot_fails_closed(self):
        with self.assertRaisesRegex(ValueError, "before first"):
            mod.replay_stream([event(0, "update")])

    def test_stale_update_timestamp_fails_closed(self):
        with self.assertRaisesRegex(ValueError, "Out-of-order"):
            mod.replay_stream([event(2, "snapshot"), event(1, "update")])

    def test_crossing_after_update_rejected(self):
        with self.assertRaisesRegex(ValueError, "Crossed"):
            mod.replay_stream([event(0, "snapshot"), event(1, "update", [["103", "1"]], [])])

    def test_snapshot_order_duplicate_and_zero_size_rejected(self):
        bad = [([["99", "1"], ["100", "1"]], None),
               ([["100", "1"], ["100", "2"]], None),
               ([["100", "0"]], None),
               (None, [["102", "1"], ["101", "1"]])]
        for bids, asks in bad:
            with self.subTest(bids=bids, asks=asks), self.assertRaises(ValueError):
                mod.replay_stream([event(0, "snapshot", bids, asks)])

    def test_memory_event_and_line_bounds(self):
        with self.assertRaisesRegex(ValueError, "memory cap"):
            mod.replay_stream([event(0, "snapshot"), event(1, "update", [["98", "1"]], [])], max_levels=2)
        with self.assertRaisesRegex(ValueError, "bound exceeded"):
            mod.replay_stream([event(0, "snapshot"), event(1, "update")], max_events=1)

    def test_unknown_action_invalid_numeric_and_out_of_day(self):
        for rows in ([event(0, "weird")], [event(0, "snapshot"), event(1, "update", [["98", "NaN"]], [])],
                     [event(86400, "snapshot")]):
            with self.subTest(rows=rows), self.assertRaises(ValueError):
                mod.replay_stream(rows)

    def test_verified_archive_and_sha_rejection(self):
        with tempfile.TemporaryDirectory() as d:
            archive = Path(d) / "day.tar.gz"
            payload = event(0, "snapshot") + event(1, "update", [], [["101", "0"]])
            with tarfile.open(archive, "w:gz") as tar:
                info = tarfile.TarInfo("l2.jsonl")
                info.size = len(payload)
                tar.addfile(info, io.BytesIO(payload))
            sha = hashlib.sha256(archive.read_bytes()).hexdigest()
            self.assertEqual(mod.replay_archive(archive, expected_sha=sha)["events_parsed"], 2)
            with self.assertRaisesRegex(ValueError, "SHA256"):
                mod.replay_archive(archive, expected_sha="0" * 64)


if __name__ == "__main__":
    unittest.main()
