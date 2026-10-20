"""Contract tests for source evidence, deadlines and finance-specific details."""
import json,sys,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"scripts"))
from update_sources import safe_url,validate_curated
from read_drive import choose
class CareersContract(unittest.TestCase):
 @classmethod
 def setUpClass(cls):
  cls.doc=json.loads((ROOT/"data/opportunities.json").read_text("utf-8"))
  cls.html=(ROOT/"index.html").read_text("utf-8")
  cls.js=(ROOT/"app.js").read_text("utf-8")
  cls.css=(ROOT/"styles.css").read_text("utf-8")
 def test_validated_evidence(self):
  self.assertIs(validate_curated(self.doc),self.doc)
  self.assertGreaterEqual(len(self.doc["listings"]),18)
  self.assertGreaterEqual(len(self.doc["sources"]),10)
  self.assertTrue(any("sgcc.com.cn/sgcchr/static/unitPart.html?" in x["source_url"] for x in self.doc["listings"]))
 def test_finance_posting_details_are_explicit(self):
  fields=("id","title","company","type","regions","roles","graduate_batch","min_degree",
          "majors_exact","work_city","vacancies","application_open","application_deadline_time",
          "application_process","assessment","documents","overseas_details","qs_details",
          "deadline","critical","priority_tier","posting_level","role_confirmed","checked_at",
          "source_url","apply_url","published")
  for item in self.doc["listings"]:
   for f in fields:self.assertIn(f,item,(item["id"],f))
   self.assertTrue(item["source_url"].startswith("https://"))
   self.assertTrue(item["apply_url"].startswith("https://"))
   self.assertTrue(item["critical"])
   self.assertTrue(item["application_process"])
   self.assertTrue(item["qs_details"])
   self.assertTrue(item["roles"])
 def test_rare_confirmed_actual_job_and_rankings(self):
  confirmed=[j for j in self.doc["listings"] if j["role_confirmed"]]
  self.assertTrue(confirmed)
  self.assertTrue(all(j["verified_from"] for j in confirmed))
  self.assertTrue(all(j["qs_threshold"] is None for j in self.doc["listings"]))
  sgcc=next(j for j in self.doc["listings"] if j["id"]=="sgcc-2027-general")
  self.assertFalse(sgcc["role_confirmed"])
  self.assertIn("财务会计",sgcc["critical"])
 def test_deadlines_and_distinct_parent_companies(self):
  post=next(j for j in self.doc["listings"] if j["id"]=="chinapost-2027")
  savings=next(j for j in self.doc["listings"] if j["id"]=="psbc-2027")
  self.assertEqual(post["deadline"],"2026-10-31")
  self.assertEqual(savings["deadline"],"2026-10-07")
  self.assertEqual(next(x for x in self.doc["listings"] if x["id"]=="boc-2027")["deadline"],"2026-10-09")
  self.assertEqual(next(x for x in self.doc["listings"] if x["id"]=="sh-2026-institution-second")["deadline"],"2026-10-23")
 def test_single_page_accessibility_and_urgent(self):
  for ident in ("urgent","jobs","focus","guide","urgent-list","jobs-list","filter-text",
                "filter-type","filter-region","filter-overseas","filter-qs","jobs-more","leads-list"):
   self.assertIn('id="'+ident+'"',self.html)
  for tag in ("daysBetween","jobCard","renderUrgent","renderJobs","details-inner","qs_details"):
   self.assertIn(tag,self.js)
  self.assertIn("near-deadline",self.css)
  self.assertIn("prefers-reduced-motion",self.css)
  self.assertIn("aria-live",self.html)
  self.assertNotIn("乳腺癌",self.html)
  self.assertNotIn("innerHTML",self.js)
 def test_exact_https_host_whitelist(self):
  self.assertFalse(safe_url("https://zhaopin.sgcc.com.cn.bad.example/path"))
  self.assertFalse(safe_url("http://zhaopin.sgcc.com.cn/"))
  self.assertFalse(safe_url("https://malicious.example/"))
  self.assertTrue(safe_url("https://zhaopin.sgcc.com.cn"))
 def test_drive_snapshot_choose_new_capture_not_reuploaded_old(self):
  files=[
   {"name":"finance_20261017T010000Z_1.json","mimeType":"application/json","modifiedTime":"2026-10-20"},
   {"name":"finance_20261020T010000Z_2.json","mimeType":"application/json","modifiedTime":"2026-10-20"}]
  self.assertEqual(choose(files)["name"],"finance_20261020T010000Z_2.json")
  self.assertIsNone(choose([{"name":"../finance_20261020T010000Z_2.json","mimeType":"application/json"}]))
if __name__=="__main__":unittest.main()
