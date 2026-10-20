"""Authorized QQ Docs export ingestion, conservative screening and privacy contracts."""
from __future__ import annotations
import datetime,io,json,sys,tempfile,unittest,zipfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"scripts"))
from qq_sheet_sync import SOURCE,DRIVE_FOLDER,parse_leads,csv_rows,xlsx_rows,safe_http,normalized_date
from create_snapshot import make
from extract_public import export_snapshot

class QQSheetTests(unittest.TestCase):
 @classmethod
 def setUpClass(cls):
  cls.doc=json.loads((ROOT/"data/opportunities.json").read_text("utf-8"))
  cls.html=(ROOT/"index.html").read_text("utf-8")
  cls.app=(ROOT/"app.js").read_text("utf-8")
 def test_source_is_prioritized_and_export_is_not_falsely_claimed(self):
  self.assertEqual(self.doc["sources"][0]["id"],"qq_2027_master")
  self.assertEqual(self.doc["sources"][0]["url"],SOURCE)
  self.assertTrue(self.doc["sources"][0]["external_spreadsheet"])
  self.assertEqual(self.doc["featured_source"]["spreadsheet_rows_downloaded"],False)
  self.assertIn('id="qq-focus"',self.html)
  self.assertIn(SOURCE,self.html)
  self.assertIn('id="qq-feed-status"',self.html)
  self.assertIn('id="qq-shortlist"',self.html)
 def test_privacy_no_resume_original_or_contact_details_exposed(self):
  for content in (self.html,self.app):
   for secret in ("吴语彤","18055544679","yutongw1106@163.com",DRIVE_FOLDER):
    self.assertNotIn(secret,content)
  self.assertIn("renderQQ",self.app)
  self.assertIn("resumeDirectionScore",self.app)
 def test_candidate_filter_keeps_finance_and_ignores_expired(self):
  data=(
   "公司名称,职位名称,工作地点,报名截止时间,招聘链接,招聘要求\n"
   "华能财务,财务管理岗,上海,2026-10-31,https://zhaopin.chng.com.cn/test,财务会计硕士\n"
   "旧公司,会计岗,南京,2026-10-01,https://zhaopin.chng.com.cn/old,审计\n"
   "电网工厂,电气自动化,深圳,2026-10-31,https://zhaopin.sgcc.com.cn/tech,仅电气\n"
   "其他公司,金融分析师,成都,2026-11-20,https://zhaopin.chng.com.cn/cd,金融\n"
   "报名某银行,银行综合管理岗,苏州,2026-10-25,https://zp.czbank.com.cn/cz,财经专业\n"
  ).encode("utf-8-sig")
  leads,stats=parse_leads(csv_rows(data),datetime.date(2026,10,20))
  self.assertEqual(len(leads),2,leads)
  self.assertEqual({x["company"] for x in leads},{"华能财务","报名某银行"})
  self.assertTrue(all(not x["role_confirmed"] for x in leads))
  self.assertTrue(all(x["review_state"]=="未核实官方招聘资格与开放状态" for x in leads))
  self.assertIn("expired",stats["skipped"])
  self.assertIn("outside_target_regions",stats["skipped"])
 def test_valid_and_suspicious_dates(self):
  self.assertEqual(normalized_date("截至2026年10月31日"),"2026-10-31")
  self.assertIsNone(normalized_date("2026年13月31日"))
  self.assertFalse(safe_http("javascript:alert(1)"))
  self.assertFalse(safe_http("http://example.com"))
 def test_xlsx_export_extracts_hyperlinks_without_executing_formulas(self):
  wb='<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="总表" sheetId="1" r:id="rId1"/></sheets></workbook>'.encode('utf-8')
  reldoc=b'<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Target="worksheets/sheet1.xml" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet"/></Relationships>'
  sh='<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheetData><row r="1"><c r="A1" t="inlineStr"><is><t>公司名称</t></is></c><c r="B1" t="inlineStr"><is><t>职位</t></is></c><c r="C1" t="inlineStr"><is><t>工作地点</t></is></c></row><row r="2"><c r="A2" t="inlineStr"><is><t>某公司</t></is></c><c r="B2" t="inlineStr"><is><t>审计岗</t></is></c><c r="C2" t="inlineStr"><is><t>上海</t></is></c></row></sheetData><hyperlinks><hyperlink ref="B2" r:id="rId2"/></hyperlinks></worksheet>'.encode('utf-8')
  shrels=b'<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink" Target="https://www.chinapost.com.cn/test" TargetMode="External"/></Relationships>'
  buf=io.BytesIO()
  with zipfile.ZipFile(buf,'w') as z:
   for name,raw in [("xl/workbook.xml",wb),("xl/_rels/workbook.xml.rels",reldoc),("xl/worksheets/sheet1.xml",sh),("xl/worksheets/_rels/sheet1.xml.rels",shrels)]:
    z.writestr(name,raw)
  shs=xlsx_rows(buf.getvalue())
  self.assertEqual(shs[0][0],"总表")
  self.assertEqual(shs[0][2]["B2"],"https://www.chinapost.com.cn/test")
  leads,stats=parse_leads(shs,datetime.date(2026,10,20))
  self.assertEqual(len(leads),1)
  self.assertEqual(leads[0]["original_link"],"https://www.chinapost.com.cn/test")
 def test_public_export_excludes_private_google_drive_folder(self):
  sample=dict(self.doc,collected_at="2026-10-20T12:00:00Z",data_as_of="2026-10-20",
   source_monitor={"schema":1,"checked_at":"2026-10-20T12:00:00Z","sources":[],"potential_new_announcements":[]},
   change_log={"total_events":0,"events":[],"counts":{},"previous_collected_at":"2026-10-19T12:00:00Z"},
   qq_sheet={"schema":1,"source_id":"qq_2027_master","source_url":SOURCE,
    "private_export_folder_id":DRIVE_FOLDER,"status":"export_required",
    "qualified_count":0,"rows_examined":0,"leads":[]})
  with tempfile.TemporaryDirectory() as td:
   p=Path(td)/"archive.json";p.write_text(json.dumps(sample,ensure_ascii=False),encoding="utf-8")
   out=Path(td)/"publish"
   export_snapshot(p,out)
   for item in ("latest.json","qq_feed.json"):
    self.assertNotIn(DRIVE_FOLDER,(out/item).read_text("utf-8"))
   pub=json.loads((out/"qq_feed.json").read_text("utf-8"))
   self.assertEqual(pub["status"],"export_required")
   self.assertEqual(pub["leads"],[])
if __name__=="__main__":
 unittest.main()
