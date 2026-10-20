"""The one public homepage should be understandable and usable by ordinary patients."""
from pathlib import Path
import unittest
ROOT=Path(__file__).resolve().parents[1]
class SimplePatientSite(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html=(ROOT/"index.html").read_text("utf-8")
        cls.js=(ROOT/"guide.js").read_text("utf-8")
        cls.css=(ROOT/"guide.css").read_text("utf-8")
        cls.cn=(ROOT/"research_cn.js").read_text("utf-8")
    def test_single_simple_home(self):
        self.assertIn('lang="zh-CN"',self.html)
        self.assertIn('href="#main"',self.html)
        self.assertIn('id="main"',self.html)
        for section in ("where","hospitals","treatments","dictionary","research","faq"):
            self.assertIn('id="'+section+'"',self.html)
        self.assertNotIn("professional.html",self.html)
        self.assertNotIn("医疗专业人员版",self.html)
    def test_interactions_are_real_controls(self):
        for ident in ("hospital-list","region-filter","focus-filter","hospital-search","hospital-count",
                      "comparison","compare-count","show-more-hospitals","stage-title","stage-steps",
                      "stage-ask","copy-questions","term-search","term-list","frontier-list",
                      "research-list","research-update","show-more-research","research-scope"):
            self.assertIn('id="'+ident+'"',self.html)
        for stage in ("screen","diagnosis","plan","recovery"):
            self.assertIn('data-stage="'+stage+'"',self.html)
        for category in ("recent","recovery","trials","fda","guides"):
            self.assertIn('data-research="'+category+'"',self.html)
        for typ in ("地方医院","军队医院"):
            self.assertIn('data-type="'+typ+'"',self.html)
    def test_accessibility_responsive_and_privacy(self):
        self.assertIn("aria-live",self.html)
        self.assertIn("aria-pressed",self.html)
        self.assertIn("@media(max-width:790px)",self.css)
        self.assertIn("prefers-reduced-motion",self.css)
        self.assertIn("noopener noreferrer",self.js)
        self.assertNotIn("innerHTML",self.js)
        self.assertNotIn("localStorage",self.js)
        self.assertNotIn("github.com",self.html)
        self.assertNotIn("github.com",self.js)
        self.assertNotIn("刷新网页就会产生新研究",self.html)
    def test_patient_education_and_chinese_research(self):
        self.assertIn("不要求上传病历",self.html)
        self.assertIn("不代替医生",self.html)
        self.assertIn("新治疗方法",self.html)
        self.assertIn("describeArticle",self.cn)
        self.assertIn("describeTrial",self.cn)
        self.assertIn("英文原标题（核对用）",self.js)
        self.assertLess(self.html.index("research_cn.js"),self.html.index("guide.js"))
    def test_no_unused_professional_assets(self):
        for name in ("professional.html","app.js","styles.css","patient.js","patient.css","care.js","care.css"):
            self.assertFalse((ROOT/name).exists(),name)
        for name in ("guide.js","guide.css","research_cn.js","data/care-guide.json","data/site-data.json"):
            self.assertTrue((ROOT/name).is_file(),name)
if __name__=="__main__":unittest.main()
