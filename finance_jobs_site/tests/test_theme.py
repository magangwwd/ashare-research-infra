"""Visual theme contract: calm neutral palette and legible UI without external assets."""
import re
import unittest
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
CSS=(ROOT/"styles.css").read_text(encoding="utf-8")
HTML=(ROOT/"index.html").read_text(encoding="utf-8")

def luminance(color):
    values=[int(color[i:i+2],16)/255 for i in (1,3,5)]
    def channel(v):
        return v/12.92 if v<=0.04045 else ((v+0.055)/1.055)**2.4
    return sum(weight*channel(v) for weight,v in zip((.2126,.7152,.0722),values))

def ratio(a,b):
    x,y=sorted((luminance(a),luminance(b)),reverse=True)
    return (x+.05)/(y+.05)

class VisualQuality(unittest.TestCase):
    def test_neutral_canvas_dark_primary_and_sufficient_contrast(self):
        self.assertIn("--bg:#f8f8f6",CSS)
        self.assertIn("--ink:#202b33",CSS)
        self.assertIn("--blue:#28596f",CSS)
        self.assertGreaterEqual(ratio("#28596f","#ffffff"),4.5)
        self.assertGreaterEqual(ratio("#62736b","#ffffff"),4.5)
        self.assertGreaterEqual(ratio("#f6f8f3","#243943"),4.5)
    def test_calm_homepage_is_text_first(self):
        self.assertIn('content="#f8f8f6"',HTML)
        self.assertIn('class="aside-steps"',HTML)
        for label in ("寻找匹配的岗位","核对申请条件","从官方渠道报名"):
            self.assertIn(label,HTML)
        self.assertNotIn('class="hero-illustration"',HTML)
        self.assertNotIn("<img",HTML.lower())
        self.assertNotIn("@import",CSS)
    def test_all_original_functional_regions_stay_visible_and_responsive(self):
        for key in ("hero-search","hero-region","jobs-list","job-detail-pane",
                    "detail-backdrop","urgent-list","sources-list","changes-list"):
            self.assertIn('id="'+key+'"',HTML)
        self.assertIn("grid-template-columns:minmax(0,1fr) minmax(0,1.07fr)",CSS)
        self.assertIn("@media(max-width:860px)",CSS)
        self.assertIn("@media(max-width:620px)",CSS)
        self.assertIn("prefers-reduced-motion",CSS)
        self.assertIn("focus-visible",CSS)
    def test_no_data_or_health_project_interference(self):
        self.assertNotIn("乳腺癌",HTML)
        self.assertNotIn("乳腺疾病",HTML)
        self.assertIn('src="./app.js"',HTML)
        self.assertIn('href="./styles.css"',HTML)
        self.assertNotIn("cdn.",HTML)
if __name__=="__main__":
    unittest.main()
