"""Deterministic month ledger and gap quarantine tests."""
import datetime as dt
import io
import sys
import unittest
import zipfile
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "data"))
from audit_full_4h_continuity import (
    audit_months, contiguous_daily_ranges, expected_month, parse_month
)


def archive_for(month, times):
    name = f"BTCUSDT-4h-{month}.zip"
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, mode="w") as z:
        rows = "".join(
            f"{int(t.timestamp() * 1000)},100,110,90,105,3,0,0,0,0,0,0\n"
            for t in times
        )
        z.writestr(name.removesuffix(".zip")+".csv", rows)
    return name, buf.getvalue()


class ContinuityTests(unittest.TestCase):
    def test_expected_month_steps_are_exact_utc(self):
        actual = expected_month(2018, 2)
        self.assertEqual(len(actual), 28*6)
        self.assertEqual(actual[0].isoformat(), "2018-02-01T00:00:00+00:00")
        self.assertEqual(actual[-1].isoformat(), "2018-02-28T20:00:00+00:00")

    def test_one_interior_candle_missing_disqualifies_only_its_day(self):
        times = expected_month(2018, 2)
        missing = times[6*9+2]
        name, data = archive_for("2018-02", [t for t in times if t != missing])
        out = audit_months([(name, data)])
        self.assertEqual(out["missing_4h_total"], 1)
        self.assertEqual(out["full_utc_days"], 27)
        self.assertEqual(out["longest_complete_utc_day_span"]["days"], 18)
        self.assertIn(missing.isoformat(), out["month_evidence"][0]["missing_4h_preview"])
        self.assertNotEqual(out["status"], "TIME_CONTINUITY_OK_REQUIRES_OTHER_VALIDATION")

    def test_split_days_and_truly_contiguous_segment(self):
        days = [dt.date(2018, 2, 1), dt.date(2018, 2, 2),
                dt.date(2018, 2, 5)]
        spans = contiguous_daily_ranges(days)
        self.assertEqual([x["days"] for x in spans], [2, 1])

    def test_duplicate_4h_open_is_reported_not_silently_accepted(self):
        stamp = expected_month(2018, 2)[0]
        name, data = archive_for("2018-02", [stamp,stamp])
        bars, anomalies = parse_month(name, data)
        self.assertEqual(len(bars), 1)
        self.assertEqual(anomalies[0]["reason"], "duplicate_open")

    def test_missing_archive_month_is_explicit(self):
        jan = archive_for("2018-01", [])
        mar = archive_for("2018-03", [])
        out = audit_months([jan, mar])
        self.assertEqual(out["missing_archive_months"], ["2018-02"])
        self.assertGreater(out["missing_4h_total"], 28*6)


if __name__ == "__main__":
    unittest.main()
