import pathlib
import sys
import unittest
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]))
from collect_trials import build_url,normalize
class TrialTests(unittest.TestCase):
    def test_query(self):
        self.assertIn("query.cond=Breast+Cancer",build_url())
    def test_normalize(self):
        obj={"protocolSection":{"identificationModule":{"nctId":"NCT12345678","briefTitle":"Test"},"statusModule":{"overallStatus":"RECRUITING"}}}
        row=normalize(obj)
        self.assertEqual(row["nct_id"],"NCT12345678")
        self.assertEqual(row["overall_status"],"RECRUITING")
