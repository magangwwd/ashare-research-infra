"""One public site: Chinese patient-friendly medical navigation without developer portals."""
import pathlib
import unittest
ROOT=pathlib.Path(__file__).resolve().parents[1]

class ChinesePatientOnlySite(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html=(ROOT/"index.html").read_text(encoding="utf-8")
        cls.care=(ROOT/"care.js").read_text(encoding="utf-8")
        cls.patient=(ROOT/"patient.js").read_text(encoding="utf-8")
        cls.explanation=(ROOT/"research_cn.js").read_text(encoding="utf-8")
    def test_homepage_single_entry(self):
        for token in ('lang="zh-CN"','href="#main"','id="main"','id="care-hub"','id="care-hospitals"',
                      'id="care-frontiers"','id="care-glossary"','id="cloud-data"','id="care-region"','id="care-type"'):
            self.assertIn(token,self.html)
        self.assertNotIn("professional.html",self.html)
        self.assertNotIn("医疗专业人员版",self.html)
        self.assertNotIn("expert-ranking",self.html)
    def test_no_separate_professional_files(self):
        for file in ("professional.html","app.js","styles.css"):
            self.assertFalse((ROOT/file).exists(),file)
    def test_privacy_and_non_diagnosis(self):
        for content in (self.html,self.care,self.patient,self.explanation):
            self.assertNotIn("github.com",content.lower())
            self.assertNotIn("refresh_token",content)
            self.assertNotIn("innerHTML",content)
        self.assertIn("不代替诊断",self.html)
        self.assertIn("noopener noreferrer",self.care)
    def test_educational_data_and_scripts(self):
        for f in ("patient.css","patient.js","care.css","care.js","research_cn.js",
                  "data/care-guide.json","data/site-data.json"):
            self.assertTrue((ROOT/f).is_file(),f)
        self.assertLess(self.html.index("research_cn.js"),self.html.index("patient.js"))
        self.assertLess(self.html.index("research_cn.js"),self.html.index("care.js"))
        self.assertIn("./data/care-guide.json",self.care)
        self.assertIn("./data/site-data.json",self.patient)
        self.assertIn("cache:'no-store'",self.patient)
    def test_chinese_explainer_keeps_original_for_verification(self):
        self.assertIn("describeArticle",self.explanation)
        self.assertIn("describeTrial",self.explanation)
        self.assertIn("describeFDA",self.explanation)
        self.assertIn("中文主题提示",self.explanation)
        self.assertIn("英文原标题（仅供核对）",self.patient)
        self.assertIn("不能直接认为",self.explanation)

if __name__=="__main__":unittest.main()
