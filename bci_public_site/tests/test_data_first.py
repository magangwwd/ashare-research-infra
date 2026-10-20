"""Cloud snapshot must remain trustworthy even on the simple page."""
import json
from pathlib import Path
import sys
import unittest
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"scripts"))
from refresh_drive import choose,eligible_name
class CloudBackedLaypersonGuide(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html=(ROOT/"index.html").read_text("utf-8")
        cls.app=(ROOT/"guide.js").read_text("utf-8")
        cls.workflow=(ROOT.parent/".github/workflows/bci-pages.yml").read_text("utf-8")
        cls.collector=(ROOT.parent/".github/workflows/bci-drive-collector.yml").read_text("utf-8")
        cls.data=json.loads((ROOT/"data/site-data.json").read_text("utf-8"))
    def test_main_navigation_before_dense_research(self):
        self.assertLess(self.html.index('id="where"'),self.html.index('id="research"'))
        self.assertLess(self.html.index('id="hospitals"'),self.html.index('id="research"'))
        self.assertLess(self.html.index('id="dictionary"'),self.html.index('id="research"'))
        self.assertIn("Promise.allSettled",self.app)
        self.assertIn("./data/care-guide.json",self.app)
        self.assertIn("./data/site-data.json",self.app)
        self.assertIn("cache:'no-store'",self.app)
        self.assertIn("医学资料实际采集于",self.app)
    def test_data_is_public_metadata_not_patient_records(self):
        self.assertEqual(self.data["schemaVersion"],1)
        self.assertEqual(len(self.data["trials"]),self.data["counts"]["trials"])
        self.assertNotIn("refresh_token",json.dumps(self.data))
        self.assertNotIn("github.com",json.dumps(self.data))
        for a in self.data["articles"]:
            self.assertNotIn("abstract_for_internal_research_only",a)
    def test_actions_refresh_from_cloud(self):
        self.assertIn("bci_public_site/scripts/refresh_drive.py",self.workflow)
        self.assertIn("bci_public_site/scripts/refresh_care_guide.py",self.workflow)
        self.assertIn("bci_public_site/guide.js",self.workflow)
        self.assertIn("bci_public_site/guide.css",self.workflow)
        self.assertIn("github.event.workflow_run.head_branch == 'main'",self.workflow)
        self.assertIn("secrets.GDRIVE_OAUTH_JSON",self.collector)
    def test_reupload_does_not_make_old_content_new(self):
        rows=[{"name":"BCI_20261017_complete_public_snapshot_1.zip","mimeType":"application/zip","modifiedTime":"2026-10-20T12:00:00Z"},
              {"name":"20261018T010101Z_2.zip","mimeType":"application/zip","modifiedTime":"2026-10-18T01:00:00Z"}]
        self.assertEqual(choose(rows)["name"],"20261018T010101Z_2.zip")
        self.assertFalse(eligible_name("../bad.zip"))
if __name__=="__main__":unittest.main()
