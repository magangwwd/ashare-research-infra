"""No third-party dependencies: validate public data and safe Drive refresh selection."""
import json
import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from refresh_drive import eligible_name, choose

class PublicSiteTests(unittest.TestCase):
    def setUp(self):
        self.data=json.loads((ROOT/"data"/"site-data.json").read_text(encoding="utf-8"))
    def test_counts(self):
        d=self.data
        self.assertEqual(len(d["trials"]), d["counts"]["trials"])
        self.assertEqual(len(d["fda"]), d["counts"]["fda"])
        self.assertEqual(len(d["guidelines"]), d["counts"]["guidelines"])
        self.assertEqual(len(d["sources"]), d["counts"]["sources"])
        for category in ("recent","case","recovery"):
            self.assertEqual(sum(a["category"]==category for a in d["articles"]),d["counts"][category])
    def test_only_public_research_metadata(self):
        d=self.data
        self.assertEqual(d["schemaVersion"],1)
        self.assertNotIn("refresh_token", json.dumps(d))
        self.assertNotIn("client_secret", json.dumps(d))
        for record in d["articles"]:
            self.assertTrue(record["url"].startswith("https://pubmed.ncbi.nlm.nih.gov/"))
            self.assertNotIn("abstract_for_internal_research_only",record)
        for record in d["trials"]:
            self.assertTrue(record["url"].startswith("https://clinicaltrials.gov/study/"))
            self.assertNotIn("patient_name", record)
    def test_restrict_drive_archive_name(self):
        self.assertTrue(eligible_name("BCI_20261018_complete_public_snapshot_39088628933.zip"))
        self.assertTrue(eligible_name("20261018T020217Z_39088628933.zip"))
        self.assertFalse(eligible_name("BCI_storage_validation_2026-10-18.zip"))
        self.assertFalse(eligible_name("../bad.zip"))
        self.assertFalse(eligible_name("BCI_20261018_real_data_run_39088423343.zip"))
    def test_drive_selection(self):
        rows=[{"name":"BCI_20261018_complete_public_snapshot_100.zip","modifiedTime":"2026-10-18T01:00:00Z","mimeType":"application/zip"},
              {"name":"BCI_20261019_complete_public_snapshot_101.zip","modifiedTime":"2026-10-19T02:00:00Z","mimeType":"application/zip"},
              {"name":"bad.zip","modifiedTime":"2030-01-01T00:00:00Z","mimeType":"application/zip"}]
        self.assertEqual(choose(rows)["name"],"BCI_20261019_complete_public_snapshot_101.zip")

if __name__=="__main__":unittest.main()
