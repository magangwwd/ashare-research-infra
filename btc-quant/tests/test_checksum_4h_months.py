"""Offline source-integrity checks; never assume downloaded ZIP is authentic."""
import hashlib
import io
import sys
import tempfile
from pathlib import Path
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"data"))
from checksum_4h_months import verify_one


class ChecksumTests(unittest.TestCase):
    def test_matches_official_but_only_exact_name(self):
        with tempfile.TemporaryDirectory() as tmp:
            f=Path(tmp)/"BTCUSDT-4h-2018-02.zip"
            f.write_bytes(b"PKexample")
            digest=hashlib.sha256(f.read_bytes()).hexdigest()
            received=[]
            def fake(req,timeout):
                received.append(req.full_url)
                return io.BytesIO(f"{digest}  {f.name}\n".encode())
            proof=verify_one(f,fake)
            self.assertTrue(proof["official_match"])
            self.assertEqual(len(received),1)
            self.assertTrue(received[0].endswith(f.name+".CHECKSUM"))

    def test_hash_mismatch_and_wrong_file_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            f=Path(tmp)/"BTCUSDT-4h-2018-02.zip"
            f.write_bytes(b"PKexample")
            bad=hashlib.sha256(b"other").hexdigest()
            with self.assertRaisesRegex(ValueError,"Mismatch"):
                verify_one(f,lambda req,timeout:io.BytesIO(f"{bad}  {f.name}".encode()))
            good=hashlib.sha256(f.read_bytes()).hexdigest()
            with self.assertRaisesRegex(ValueError,"Malformed"):
                verify_one(f,lambda req,timeout:io.BytesIO(f"{good}  OTHER.zip".encode()))

if __name__=="__main__":
    unittest.main()
