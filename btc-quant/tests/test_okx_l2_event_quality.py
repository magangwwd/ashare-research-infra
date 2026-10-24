"""Strict event/day/timestamp tests using synthetic orderbook only."""
import datetime as dt
import importlib.util
import io
from pathlib import Path
import json
import tarfile
import tempfile
import hashlib
import unittest

src=Path(__file__).resolve().parents[1]/"data"/"okx_l2_event_quality.py"
spec=importlib.util.spec_from_file_location("okx_l2_quality_test",src)
mod=importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
DAY=dt.date(2026,10,10)
START=int(dt.datetime(2026,10,10,tzinfo=dt.timezone.utc).timestamp()*1000)


def event(sec,action="update",inst="BTC-USDT-SWAP",bids=None,asks=None):
    return json.dumps({"ts":str(START+sec*1000),"instId":inst,"action":action,
                       "bids":[["100","1"]] if bids is None else bids,
                       "asks":[["101","1"]] if asks is None else asks})+"\n"


class FullDayQualityTests(unittest.TestCase):
    def test_actions_minutes_and_gaps(self):
        rows=[event(0,"snapshot"),event(1),event(2),event(100),event(99)]
        x=mod.audit_stream([x.encode() for x in rows],DAY)
        self.assertEqual(x["events_parsed"],5)
        self.assertEqual(x["event_actions"],{"snapshot":1,"update":4})
        self.assertEqual(x["minutes_with_at_least_one_event"],2)
        self.assertEqual(x["counts"]["out_of_order_timestamp_events"],1)
        self.assertEqual(x["counts"]["gaps_over_60s"],1)
        self.assertFalse(x["raw_market_events_in_report"])

    def test_instrument_rejected(self):
        with self.assertRaises(ValueError):
            mod.audit_stream([event(0,inst="OTHER").encode()])

    def test_malformed_price_rejected(self):
        with self.assertRaises(ValueError):
            mod.audit_stream([event(0,bids=[["-12","1"]]).encode()])

    def test_invalid_middle_levels_rejected(self):
        # Old audit checked only first and last, missing corrupted middle levels.
        valid = [["100", "1"], ["99", "2"], ["98", "3"]]
        for middle in (["99", "-2"], ["99"], ["0", "1"],
                       ["99", "NaN"], ["Infinity", "1"], ["99", "inf"],
                       ["oops", "1"]):
            with self.subTest(middle=middle), self.assertRaises(ValueError):
                mod.audit_stream([event(0, bids=[valid[0], middle, valid[-1]]).encode()], DAY)

    def test_zero_size_update_and_full_depth_valid(self):
        # Size zero is a legitimate deletion; do not reject valid L2 updates.
        levels = [["100", "1"], ["99", "0"], ["98", "3"]]
        result = mod.audit_stream([event(0, "update", bids=levels).encode()], DAY)
        self.assertEqual(result["schema_validation"], "PASS")
        self.assertEqual(result["events_parsed"], 1)
        self.assertEqual(result["exchange_sequence_continuity"],
                         "NOT_VERIFIED_NO_SEQUENCE_IN_EXPOSED_JSON_FIELDS")

    def test_invalid_middle_ask_rejected(self):
        with self.assertRaises(ValueError):
            mod.audit_stream([event(0, asks=[["101", "1"], ["102", "-1"], ["103", "1"]]).encode()], DAY)

    def test_outside_day_rejected_over_ten(self):
        records=[event(24*60*60+i).encode() for i in range(11)]
        with self.assertRaises(ValueError):
            mod.audit_stream(records)

    def test_private_tar_hash_and_reader(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/"test.tar.gz"
            data=(event(0,"snapshot")+event(1)).encode()
            with tarfile.open(path,"w:gz") as tf:
                info=tarfile.TarInfo("data.jsonl")
                info.size=len(data)
                tf.addfile(info,io.BytesIO(data))
            digest=hashlib.sha256(path.read_bytes()).hexdigest()
            stats=mod.audit_file(path,digest)
            self.assertEqual(stats["events_parsed"],2)
            with self.assertRaises(ValueError):
                mod.audit_file(path,"0"*64)


if __name__=="__main__":
    unittest.main()
