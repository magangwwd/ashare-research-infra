"""Healthcare data quality: traceable specialties, doctors, dated booking sources and patient safety."""
import json
from pathlib import Path
import sys
import unittest
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"scripts"))
from refresh_care_guide import validate,safe_url
class PatientCareEvidenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data=json.loads((ROOT/"data"/"care-guide.json").read_text("utf-8"))
        cls.html=(ROOT/"index.html").read_text("utf-8")
        cls.script=(ROOT/"care.js").read_text("utf-8")
        cls.workflow=(ROOT.parent/".github"/"workflows"/"bci-pages.yml").read_text("utf-8")
    def test_v2_guide_and_source_allowlist(self):
        self.assertIs(validate(self.data),self.data)
        self.assertEqual(self.data["schema_version"],2)
        self.assertEqual(self.data["review_status"],"editorial_reference_only_not_medically_reviewed")
        self.assertGreaterEqual(len(self.data["hospitals"]),12)
        self.assertGreaterEqual(len(self.data["journeys"]),4)
        self.assertGreaterEqual(len(self.data["frontiers"]),4)
        self.assertGreaterEqual(len(self.data["glossary"]),12)
    def test_hospital_military_and_official_sources_not_blurred(self):
        military=[h for h in self.data["hospitals"] if h["type"]=="军队医院"]
        self.assertGreaterEqual(len(military),5)
        official=[h for h in self.data["hospitals"] if "医院官网" in h["verified"]]
        self.assertGreaterEqual(len(official),6)
        for h in self.data["hospitals"]:
            self.assertTrue(safe_url(h["url"]))
            self.assertTrue(h["caution"])
            self.assertTrue(h["match"])
            self.assertTrue(h["appointment"]["summary"])
            self.assertTrue(h["appointment"]["checked_at"])
            self.assertTrue(safe_url(h["appointment"]["url"]))
            self.assertTrue(h["sources"])
            self.assertTrue(h["checked_at"])
            self.assertIsNone(h["actual_patient_count"])
            self.assertIsNone(h["clinical_outcome_comparison"])
        for h in military:
            self.assertNotEqual(h["verified"],"医院官网已核实")
            self.assertTrue(any(w in h["caution"] for w in ("待核实","尚缺少","尚未","须再次确认","未取得")))
    def test_doctors_are_not_live_appointments(self):
        self.assertGreaterEqual(sum(len(h["doctors"]) for h in self.data["hospitals"]),10)
        for h in self.data["hospitals"]:
            for doctor in h["doctors"]:
                self.assertTrue(safe_url(doctor["url"]))
                self.assertTrue(doctor["checked_at"])
                self.assertTrue(doctor["source"])
    def test_user_page_is_local_only_and_interactive(self):
        for field in ("care-stage-btn","care-hospitals","care-hospital-list","care-type",
                      "care-compare","care-glossary","care-glossary-list","care-frontiers",
                      "cloud-data","care-copy-text"):
            self.assertIn(field,self.html)
        for key in ("care-patient-fit","care-doctors","care-booking","care-sources","renderGlossary"):
            self.assertIn(key,self.script)
        self.assertNotIn("professional.html",self.html)
        self.assertNotIn("innerHTML",self.script)
        self.assertNotIn("github.com",self.script)
    def test_google_drive_is_source_for_all_public_edits(self):
        self.assertIn("refresh_care_guide.py",self.workflow)
        self.assertIn("care-guide.json",self.workflow)
        self.assertIn("bci_public_site/research_cn.js",self.workflow)
        self.assertIn("test ! -e _site/professional.html",self.workflow)
    def test_guard_against_tampering(self):
        self.assertFalse(safe_url("http://example.com"))
        self.assertFalse(safe_url("https://www.shca.org.cn.evil.com/"))
        data=dict(self.data,review_status="clinically_approved")
        with self.assertRaises(ValueError):validate(data)
        data=dict(self.data)
        bad=dict(data["hospitals"][0])
        bad["appointment"]=dict(bad["appointment"],url="https://evil.invalid/book")
        data["hospitals"]=[bad]+data["hospitals"][1:]
        with self.assertRaises(ValueError):validate(data)
if __name__=="__main__":unittest.main()
