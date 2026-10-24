import pathlib
import sys
import tempfile
import unittest

ROOT=pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from publish_drive import verify_file

class DrivePublisherTests(unittest.TestCase):
    def test_rejects_non_zip(self):
        with tempfile.TemporaryDirectory() as d:
            p=pathlib.Path(d)/"test.zip"
            p.write_bytes(b"not a zip")
            with self.assertRaises(ValueError):
                verify_file(p)
    def test_fingerprint(self):
        with tempfile.TemporaryDirectory() as d:
            p=pathlib.Path(d)/"test.zip"
            p.write_bytes(b"PK"+b"example")
            raw,digest=verify_file(p)
            self.assertEqual(raw,p.read_bytes())
            self.assertEqual(len(digest),64)
