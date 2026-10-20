"""Historical audit journal must separate official-source changes from verified job facts."""
import datetime
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"scripts"))
from create_snapshot import change_ledger, make
from extract_public import export_snapshot
from update_sources import validate_curated

class FinanceAuditChangeTest(unittest.TestCase):
 @classmethod
 def setUpClass(cls):
  cls.raw=json.loads((ROOT/"data/opportunities.json").read_text("utf-8"))
 def test_monitor_sources_remain_exact_and_are_dated(self):
  validate_curated(self.raw)
  self.assertGreaterEqual(len(self.raw["listings"]),50)
  self.assertGreaterEqual(len(self.raw["sources"]),25)
  self.assertGreaterEqual(sum(j["type"]=="国家电网" for j in self.raw["listings"]),4)
  self.assertTrue(any(j["id"]=="sgcc-digital-2027-finance-xiongan" and j["deadline"]=="2026-10-30" for j in self.raw["listings"]))
  for job in self.raw["listings"]:
   self.assertTrue(job["critical"] and job["source_url"] and job["checked_at"])
   self.assertIn("role_confirmed",job)
 def test_new_record_is_not_misrepresented_as_newly_published(self):
  t=datetime.datetime(2026,10,20,tzinfo=datetime.timezone.utc)
  old={"collected_at":"2026-10-19T08:00:00Z","listings":[{"id":"j1","title":"旧","source_url":"https://zhaopin.sgcc.com.cn","deadline":None}]}
  current=[{"id":"j1","title":"旧","source_url":"https://zhaopin.sgcc.com.cn","deadline":None},
           {"id":"j2","title":"新入库","source_url":"https://zhaopin.sgcc.com.cn","deadline":None,"role_confirmed":False}]
  log=change_ledger(current,old,{"sources":[],"potential_new_announcements":[]},t)
  self.assertEqual(log["counts"]["new_curated_record"],1)
  self.assertIn("不代表",log["events"][0]["note"])
  self.assertEqual(log["previous_collected_at"],"2026-10-19T08:00:00Z")
 def test_deadline_change_is_a_fact_change_but_source_hash_is_only_a_lead(self):
  t=datetime.datetime(2026,10,20,tzinfo=datetime.timezone.utc)
  old={"collected_at":"2026-10-19T08:00:00Z",
       "listings":[{"id":"j1","title":"岗位","source_url":"https://zhaopin.sgcc.com.cn","deadline":"2026-10-31"}],
       "source_monitor":{"sources":[{"id":"bank","http_status":200,"digest":"abcd"}],
        "potential_new_announcements":[]}}
  newer=[{"id":"j1","title":"岗位","source_url":"https://zhaopin.sgcc.com.cn","deadline":"2026-11-01"}]
  scan={"sources":[{"id":"bank","name":"银行","url":"https://zhaopin.sgcc.com.cn","http_status":200,"digest":"dcba"}],
       "potential_new_announcements":[{"source_id":"bank","title":"招生公告","url":"https://zhaopin.sgcc.com.cn/new"}]}
  log=change_ledger(newer,old,scan,t)
  kinds={e["kind"] for e in log["events"]}
  self.assertEqual(kinds,{"curated_field_change","official_page_changed_review_needed","unreviewed_link_discovered"})
  self.assertIn("deadline",next(e["changed_fields"] for e in log["events"] if e["kind"]=="curated_field_change"))
  self.assertIn("不是岗位变更",next(e["note"] for e in log["events"] if e["kind"]=="official_page_changed_review_needed"))
  self.assertEqual(log["total_events"],3)
 def test_unchanged_day_does_not_invent_announcements(self):
  t=datetime.datetime(2026,10,20,tzinfo=datetime.timezone.utc)
  records=[{"id":"j1","title":"职位","source_url":"https://zhaopin.sgcc.com.cn","deadline":None}]
  prev={"listings":records,"source_monitor":{"sources":[{"id":"s","digest":"abcd","http_status":200}],"potential_new_announcements":[]}}
  scan={"sources":[{"id":"s","digest":"abcd","http_status":200}],"potential_new_announcements":[]}
  log=change_ledger(records,prev,scan,t)
  self.assertEqual(log["total_events"],0)
 def test_public_extraction_preserves_verified_audit_without_creds(self):
  today="2026-10-20T08:00:00Z"
  report=dict(self.raw,collected_at=today,data_as_of="2026-10-20",
     source_monitor={"schema":1,"checked_at":today,"sources":[],"potential_new_announcements":[]},
     change_log={"events":[],"total_events":0,"counts":{},"previous_collected_at":"2026-10-19T08:00:00Z"})
  with tempfile.TemporaryDirectory() as td:
   p=Path(td)/"source.json";p.write_text(json.dumps(report,ensure_ascii=False),encoding="utf-8")
   dest=Path(td)/"public"
   stat=export_snapshot(p,dest)
   self.assertEqual(stat["records"],len(report["listings"]))
   for name in ("latest.json","monitor.json","changes.json"):
    self.assertTrue((dest/name).exists())
   public=json.loads((dest/"latest.json").read_text("utf-8"))
   self.assertNotIn("source_monitor",public)
   self.assertIn("change_log",public)
if __name__=="__main__":unittest.main()
