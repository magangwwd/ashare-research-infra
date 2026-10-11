"""Offline regression tests for stock-universe refresh; no API access."""
import unittest
from universe_audit import audit_universe


def item(symbol, status="LISTED", day="2026-10-09"):
    return {"instrument_id": "CN.XSHG." + symbol.split(".")[0],
            "vendor_symbol": symbol, "exchange": "SSE", "listing_status": status,
            "list_date": "2020-01-01",
            "delist_date": "2026-10-09" if status == "DELISTED" else "",
            "as_of_date": day}


def raw(row):
    return {"ts_code": row["vendor_symbol"], "exchange": row["exchange"],
            "market": "主板",
            "list_status": {"LISTED":"L", "DELISTED":"D", "PAUSED_LISTING":"P"}[row["listing_status"]],
            "query_trade_date": row["as_of_date"].replace("-", "")}


class TestUniverseRefresh(unittest.TestCase):
    def test_add_new_stock_and_keep_delisted_history(self):
        old = [item("600000.SH"), item("600001.SH")]
        new = [item("600000.SH"), item("600001.SH", "DELISTED"), item("600002.SH")]
        report = audit_universe(old, new, [raw(x) for x in new])
        self.assertEqual(report["new_symbols"], ["600002.SH"])
        self.assertEqual(report["delisted_count"], 1)
        self.assertEqual(report["status_changes"],
                         [{"symbol":"600001.SH","before":"LISTED","after":"DELISTED"}])

    def test_disappearing_delisted_symbol_is_error(self):
        old = [item("600000.SH"), item("600001.SH", "DELISTED")]
        new = [item("600000.SH")]
        with self.assertRaisesRegex(ValueError, "vanished"):
            audit_universe(old, new, [raw(x) for x in new])

    def test_non_mainboard_is_error(self):
        new = [item("600000.SH")]
        with self.assertRaisesRegex(ValueError, "non-mainboard"):
            audit_universe(None, new, [dict(raw(new[0]), market="科创板")])

    def test_stale_snapshot_is_error(self):
        old, new = [item("600000.SH")], [item("600000.SH", day="2026-10-06")]
        with self.assertRaisesRegex(ValueError, "older"):
            audit_universe(old, new, [raw(x) for x in new])

    def test_identity_change_is_error(self):
        old = [item("600000.SH")]
        new = [dict(item("600000.SH"), instrument_id="changed")]
        with self.assertRaisesRegex(ValueError, "identities changed"):
            audit_universe(old, new, [raw(x) for x in new])

    def test_source_status_mismatch_is_error(self):
        new = [item("600000.SH")]
        with self.assertRaisesRegex(ValueError, "metadata mismatch"):
            audit_universe(None, new, [dict(raw(new[0]), list_status="D")])

    def test_first_snapshot_validates(self):
        new = [item("600000.SH")]
        report = audit_universe(None, new, [raw(x) for x in new])
        self.assertEqual(report["status"], "VALIDATED")
        self.assertEqual(report["current_count"], 1)


if __name__ == "__main__":
    unittest.main()
