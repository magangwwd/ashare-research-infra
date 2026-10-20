"""Keep hospital "strengths" factual and separate unverified military medicine leads."""
import json
from pathlib import Path
import sys
import unittest
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"scripts"))
from refresh_care_guide import safe_url,validate
class SourcedHospitalGuideTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.guide=json.loads((ROOT/"data/care-guide.json").read_text("utf-8"))
        cls.js=(ROOT/"guide.js").read_text("utf-8")
        cls.html=(ROOT/"index.html").read_text("utf-8")
        cls.workflow=(ROOT.parent/".github/workflows/bci-pages.yml").read_text("utf-8")
    def test_editorial_structure_and_patient_content(self):
        self.assertIs(validate(self.guide),self.guide)
        self.assertEqual(self.guide["schema_version"],2)
        self.assertEqual(self.guide["review_status"],"editorial_reference_only_not_medically_reviewed")
        self.assertGreaterEqual(len(self.guide["hospitals"]),12)
        self.assertGreaterEqual(len(self.guide["journeys"]),4)
        self.assertGreaterEqual(len(self.guide["frontiers"]),4)
        self.assertGreaterEqual(len(self.guide["glossary"]),12)
    def test_hospital_claims_have_dated_sources_and_no_fake_stats(self):
        for h in self.guide["hospitals"]:
            self.assertTrue(safe_url(h["url"]))
            self.assertTrue(h["checked_at"] and h["sources"])
            self.assertTrue(h["match"] and h["caution"])
            self.assertTrue(h["appointment"]["checked_at"])
            self.assertTrue(safe_url(h["appointment"]["url"]))
            self.assertIsNone(h["actual_patient_count"])
            self.assertIsNone(h["clinical_outcome_comparison"])
            for person in h["doctors"]:
                self.assertTrue(person["checked_at"])
                self.assertTrue(safe_url(person["url"]))
            for source in h["sources"]:
                self.assertTrue(source["checked_at"])
                self.assertTrue(safe_url(source["url"]))
    def test_military_uncertainty_is_not_ranked_as_advantage(self):
        military=[h for h in self.guide["hospitals"] if h["type"]=="军队医院"]
        self.assertGreaterEqual(len(military),5)
        self.assertTrue(all("待核实" in h["verified"] or "历史" in h["verified"] or "研究" in h["verified"] for h in military))
        self.assertIn("现行服务待核实",self.js)
        self.assertIn("数据来源和更新日期",self.js)
        self.assertIn("不等于推荐或疗效排名",self.html)
    def test_simple_hospital_ui_still_has_real_sources(self):
        self.assertIn("hospitalMatch",self.js)
        self.assertIn("comparisonRender",self.js)
        self.assertIn("h.appointment?.summary",self.js)
        self.assertIn("h.doctors?.length",self.js)
        self.assertIn("h.sources||[]",self.js)
        self.assertIn("refresh_care_guide.py",self.workflow)
    def test_source_host_allowlist(self):
        self.assertFalse(safe_url("http://www.shca.org.cn/path"))
        self.assertFalse(safe_url("https://www.shca.org.cn.attacker.invalid"))
        self.assertFalse(safe_url("https://evil.invalid/booking"))
        data=dict(self.guide,review_status="approved_by_doctors")
        with self.assertRaises(ValueError):validate(data)
if __name__=="__main__":unittest.main()
