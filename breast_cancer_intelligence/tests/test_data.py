import json
import pathlib
import sys
import unittest
ROOT=pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from collect_pubmed import build_url

class PilotTests(unittest.TestCase):
    def test_pubmed_url(self):
        self.assertIn("db=pubmed",build_url())
        self.assertIn("retmode=json",build_url())
    def test_hospital_identity(self):
        hospitals=json.loads((ROOT/"config"/"hospitals.json").read_text(encoding="utf-8"))["hospitals"]
        ids=[h["id"] for h in hospitals]
        self.assertEqual(len(ids),len(set(ids)))
        for h in hospitals:
            if "parent_id" in h:
                self.assertIn(h["parent_id"],ids)
