"""Public two-audience site: strict separation, medical disclaimers, no technical interfaces."""
import pathlib
import re
import unittest

BASE = pathlib.Path(__file__).resolve().parents[1]

class AudienceSplitTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.patient = (BASE / "index.html").read_text(encoding="utf-8")
        cls.professional = (BASE / "professional.html").read_text(encoding="utf-8")
        cls.patient_js = (BASE / "patient.js").read_text(encoding="utf-8")
        cls.pro_js = (BASE / "app.js").read_text(encoding="utf-8")

    def test_patient_is_default_and_accessible(self):
        self.assertIn('lang="zh-CN"', self.patient)
        self.assertIn('href="#main"', self.patient)
        self.assertIn('id="main"', self.patient)
        self.assertIn('aria-current="page"', self.patient)
        for anchor in ["screening", "diagnosis", "treatment", "recovery", "resources", "updates"]:
            self.assertIn('id="'+anchor+'"', self.patient)
        self.assertIn("患者与家属版", self.patient)
        self.assertIn("不代替诊断", self.patient)

    def test_professional_is_separate_and_crosslinked(self):
        self.assertIn('href="./professional.html"', self.patient)
        self.assertIn('href="./index.html"', self.professional)
        self.assertIn("医疗专业人员", self.professional)
        self.assertIn('id="trials-view"', self.professional)
        self.assertIn('id="articles-view"', self.professional)
        self.assertIn('id="fda-view"', self.professional)
        self.assertIn('id="guides-view"', self.professional)

    def test_no_github_code_or_api_tech_links_on_public_pages(self):
        for document in (self.patient, self.professional, self.patient_js, self.pro_js):
            self.assertNotIn("github.com", document.lower())
            self.assertNotIn("github actions", document.lower())
            self.assertNotIn("源代码", document)
            self.assertNotIn("代码接口", document)
            self.assertNotIn("api.github.com", document.lower())
        self.assertNotIn("httpStatus", self.patient)
        self.assertNotIn("SHA-256", self.patient)

    def test_patient_sources_and_data_update(self):
        for url in [
          "https://www.cancer.gov/types/breast/screening",
          "https://www.cancer.gov/types/breast/treatment",
          "https://www.cancer.gov/about-cancer/coping/survivorship/follow-up-care",
          "https://www.china-oncology.com/zh/article/doi/10.19401/j.cnki.1007-3639.2025.12.009/",
        ]:
            self.assertIn(url, self.patient)
        self.assertIn("./data/site-data.json", self.patient_js)
        self.assertIn("cache:'no-store'", self.patient_js)
        self.assertIn("textContent", self.patient_js)
        self.assertNotIn("innerHTML", self.patient_js)

    def test_assets_exist(self):
        for path in ["patient.css", "patient.js", "professional.html", "app.js", "styles.css", "data/site-data.json"]:
            self.assertTrue((BASE/path).is_file(), path)
