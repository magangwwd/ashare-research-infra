"""Verify care source records, public privacy boundary and interactive website deliverables."""
import json
from pathlib import Path
import sys
import unittest
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"scripts"))
from refresh_care_guide import validate, safe_url

class CareGuideTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data=json.loads((ROOT/"data"/"care-guide.json").read_text(encoding="utf-8"))
        cls.html=(ROOT/"index.html").read_text(encoding="utf-8")
        cls.script=(ROOT/"care.js").read_text(encoding="utf-8")
        cls.workflow=(ROOT.parent/".github"/"workflows"/"bci-pages.yml").read_text(encoding="utf-8")
    def test_drive_source_editorial_guide(self):
        validate(self.data)
        self.assertGreaterEqual(len(self.data["hospitals"]),3)
        self.assertGreaterEqual(len(self.data["journeys"]),4)
        self.assertGreaterEqual(len(self.data["frontiers"]),3)
        self.assertEqual(self.data["review_status"],"editorial_reference_only_not_medically_reviewed")
    def test_hospital_references_and_limitations(self):
        for h in self.data["hospitals"]:
            self.assertTrue(safe_url(h["url"]))
            self.assertTrue(h["caution"])
            self.assertTrue(h["highlight"])
            self.assertIn("verified",h)
            self.assertNotIn("survival_rate",h)
            self.assertNotIn("patient_data",h)
        military=[h for h in self.data["hospitals"] if h["type"]=="军队医院"]
        self.assertTrue(military)
        self.assertTrue(all("尚缺少" in h["caution"] or "待核实" in h["caution"] for h in military))
    def test_public_html_interactive_components(self):
        for elem in ["care-hub","care-stage-name","care-questions","care-hospitals","care-region",
                     "care-hospital-list","care-compare","care-frontiers","care-frontier-list",
                     "care-faq","care-research-list","care-copy-text","cloud-data"]:
            self.assertIn('id="'+elem+'"',self.html)
        self.assertIn("./care.js",self.html)
        self.assertIn("./care.css",self.html)
        self.assertIn("./professional.html",self.html)
    def test_no_sensitive_form_or_external_developer_links(self):
        for content in [self.html,self.script,json.dumps(self.data,ensure_ascii=False)]:
            self.assertNotIn("github.com",content.lower())
            self.assertNotIn("refresh_token",content.lower())
            self.assertNotIn("client_secret",content.lower())
            self.assertNotIn("patient_name",content.lower())
        self.assertNotIn("innerHTML",self.script)
        self.assertIn("noopener noreferrer",self.script)
    def test_latest_data_and_drive_editorial_are_separate(self):
        self.assertIn("./data/care-guide.json",self.script)
        self.assertIn("./data/site-data.json",self.script)
        self.assertIn("refresh_care_guide.py",self.workflow)
        self.assertIn("care-guide.json",self.workflow)
        self.assertIn("bci_public_site/care.js",self.workflow)
        self.assertIn("bci_public_site/care.css",self.workflow)
    def test_reject_untrusted_urls_and_tampering(self):
        self.assertFalse(safe_url("http://example.com"))
        self.assertFalse(safe_url("https://example.com/malicious"))
        self.assertFalse(safe_url("https://www.shca.org.cn.evil.com/"))
        modified=dict(self.data,review_status="clinically_approved")
        with self.assertRaises(ValueError):validate(modified)

if __name__=="__main__":unittest.main()
