"""The public site must lead with verified cloud data; no patient-identifying details."""
import json
from pathlib import Path
import sys
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"scripts"))
from refresh_drive import choose, eligible_name

class DataFirstWebsiteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.patient=(ROOT/"index.html").read_text(encoding="utf-8")
        cls.patient_js=(ROOT/"patient.js").read_text(encoding="utf-8")
        cls.workflow=(ROOT.parent/".github"/"workflows"/"bci-pages.yml").read_text(encoding="utf-8")
        cls.collector_workflow=(ROOT.parent/".github"/"workflows"/"bci-drive-collector.yml").read_text(encoding="utf-8")

    def test_cloud_data_is_main_patient_section(self):
        self.assertLess(self.patient.index('id="cloud-data"'),self.patient.index('id="path"'))
        for item in ["cloud-collected","cloud-generated","cloud-snapshot",
                     "cloud-count-articles","cloud-count-trials","cloud-count-fda",
                     "cloud-count-guides","cloud-search","cloud-feed","cloud-qualifier"]:
            self.assertIn('id="'+item+'"',self.patient)
        for filter in ("all","recent","recovery","case","trials","fda","guides"):
            self.assertIn('data-cloud-filter="'+filter+'"',self.patient)
        self.assertIn("data/site-data.json",self.patient_js)
        self.assertIn("cache:'no-store'",self.patient_js)

    def test_patient_care_guide_has_more_priority_than_raw_data(self):
        self.assertLess(self.patient.index('id="care-hub"'),self.patient.index('id="cloud-data"'))
        self.assertLess(self.patient.index('id="care-hospitals"'),self.patient.index('id="cloud-data"'))
        self.assertLess(self.patient.index('id="care-glossary"'),self.patient.index('id="cloud-data"'))

    def test_no_public_github_or_unlicensed_abstract(self):
        for text in [self.patient,self.patient_js,(ROOT/"care.js").read_text(encoding="utf-8")]:
            self.assertNotIn("github.com",text.lower())
            self.assertNotIn("github actions",text.lower())
            self.assertNotIn("innerHTML",self.patient_js)
        data=json.loads((ROOT/"data"/"site-data.json").read_text(encoding="utf-8"))
        raw=json.dumps(data,ensure_ascii=False)
        self.assertNotIn("github.com",raw)
        self.assertNotIn("GDRIVE_OAUTH_JSON",raw)
        self.assertNotIn("abstract_for_internal_research_only",raw)
        self.assertEqual(sum(data["counts"][k] for k in ("recent","case","recovery")),len(data["articles"]))

    def test_cloud_refresh_on_protected_main_and_collector_complete(self):
        self.assertIn("workflows: ['BCI collect verified source to Drive']",self.workflow)
        self.assertIn("github.event_name != 'pull_request'",self.workflow)
        self.assertIn("github.event.workflow_run.head_branch == 'main'",self.workflow)
        self.assertIn("secrets.GDRIVE_OAUTH_JSON",self.collector_workflow)
        self.assertIn("github.ref == 'refs/heads/main'",self.collector_workflow)

    def test_reuploaded_old_snapshot_does_not_outrank_newer(self):
        files=[
           {"name":"BCI_20261017_complete_public_snapshot_101.zip",
            "mimeType":"application/zip","modifiedTime":"2026-10-19T22:00:00Z"},
           {"name":"20261018T010101Z_202.zip",
            "mimeType":"application/zip","modifiedTime":"2026-10-18T01:04:00Z"}
        ]
        self.assertEqual(choose(files)["name"],"20261018T010101Z_202.zip")
        self.assertFalse(eligible_name("../BCI_20261018_complete_public_snapshot_202.zip"))
