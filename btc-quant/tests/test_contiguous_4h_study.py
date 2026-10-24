"""Research segment must exclude missing dates rather than bridge source outages."""
import csv
import io
import json
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "data"))
from audit_full_4h_continuity import expected_month
from build_contiguous_4h_study import build_segment


class SegmentStudyTests(unittest.TestCase):
    def make_months(self, directory):
        from datetime import datetime, timezone
        for month in range(1, 10):
            filename = f"BTCUSDT-4h-2020-{month:02d}.zip"
            times = expected_month(2020, month)
            if month == 1:
                # Single missing bar means the entire first UTC day is excluded.
                times = times[1:]
            buf = io.BytesIO()
            with zipfile.ZipFile(buf, "w") as z:
                rows = "".join(
                    f"{int(t.timestamp()*1000)},100,110,90,105,3,0,0,0,0,0,0\n"
                    for t in times
                )
                z.writestr(filename.replace(".zip", ".csv"), rows)
            (directory / filename).write_bytes(buf.getvalue())

    def test_approved_contiguous_window_drops_invalid_day(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            src, out = base / "raw", base / "output"
            src.mkdir()
            self.make_months(src)
            result = build_segment(sorted(src.glob("*.zip")), out, min_days=200)
            self.assertEqual(result["start_utc"], "2020-01-02")
            self.assertEqual(result["end_utc"], "2020-09-30")
            self.assertEqual(result["global_4h_bars_missing"], 1)
            self.assertFalse((out / "btc_daily_utc.csv").read_text().find("2020-01-01,") >= 0)
            with (out / "btc_daily_utc.csv").open() as f:
                rows = list(csv.DictReader(f))
            self.assertEqual(len(rows), result["days"])
            self.assertTrue(result["not_issue_26_repaired"])
            self.assertTrue(result["no_missing_bars_filled"])
            self.assertTrue(json.loads((out / "source_boundary.json").read_text())["no_multiple_segments_spliced"])

    def test_rejects_insufficient_uninterrupted_sample(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            src = base / "raw"
            src.mkdir()
            self.make_months(src)
            with self.assertRaisesRegex(ValueError, "No complete contiguous"):
                build_segment(sorted(src.glob("*.zip")), base / "out", min_days=500)


if __name__ == "__main__":
    unittest.main()
