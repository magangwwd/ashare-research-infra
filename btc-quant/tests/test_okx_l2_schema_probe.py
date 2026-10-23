"""Offline schema-probe tests, no historical data exposure."""
import hashlib
import importlib.util
import io
from pathlib import Path
import tarfile
import tempfile
import unittest

p=Path(__file__).resolve().parents[1]/"data"/"okx_l2_schema_probe.py"
spec=importlib.util.spec_from_file_location("okx_private_l2_probe",p)
mod=importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


def fixture(folder, data):
    archive=Path(folder)/"input.tar.gz"
    with tarfile.open(archive,"w:gz") as tf:
        info=tarfile.TarInfo("sample.txt")
        info.size=len(data)
        tf.addfile(info,io.BytesIO(data))
    return archive,hashlib.sha256(archive.read_bytes()).hexdigest()


class SchemaTests(unittest.TestCase):
    def test_jsonl_schema_only(self):
        with tempfile.TemporaryDirectory() as d:
            path,digest=fixture(d,b'{"ts":"123","bids":[1]}\n{"ts":"124","bids":[2]}\n')
            r=mod.inspect_archive(path,digest)
            self.assertEqual(r["format"],"JSON_LINES")
            self.assertEqual(r["json_record_keys"],["bids","ts"])
            # Hashes are opaque: a digest can coincidentally contain the digits "123".
            # Check the report structure instead of searching arbitrary hash text.
            self.assertNotIn("ts", r)
            self.assertNotIn("bids", r)
            self.assertTrue(all(
                isinstance(value, (str, int, float, bool))
                for key, value in r.items() if key != "json_record_keys"
            ))
            self.assertFalse(r["raw_market_events_in_report"])

    def test_csv_header_only(self):
        with tempfile.TemporaryDirectory() as d:
            path,digest=fixture(d,b'timestamp,price,size\n123,120,0.2\n124,122,0.1\n')
            r=mod.inspect_archive(path,digest)
            self.assertEqual(r["format"],"DELIMITED_TEXT")
            self.assertEqual(r["field_count_mode"],3)
            self.assertEqual(r["header_names"],["timestamp","price","size"])

    def test_wrong_sha_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            path,digest=fixture(d,b'hello\n')
            with self.assertRaises(ValueError):
                mod.inspect_archive(path,"0"*64)


if __name__=="__main__":
    unittest.main()
