import unittest,sys
from pathlib import Path
import numpy as np,pandas as pd
sys.path.insert(0,str(Path(__file__).parent))
from stock_personality import features,episode_indices,load_panel,analyze
class TestStockPersonality(unittest.TestCase):
 def setUp(self):
  dates=pd.bdate_range('2025-11-01',periods=60)
  self.x=pd.DataFrame({'ts_code':'600031.SH','trade_date':dates,'open':np.full(60,10.),'high':np.full(60,10.5),'low':np.full(60,9.5),'close':np.full(60,10.),'vol':np.full(60,100.)})
 def test_episode_gap(self):self.assertEqual(episode_indices([1,2,3,7,10,11,20]),[1,7,20])
 def test_future_not_used_for_feature(self):
  x=features(self.x);y=self.x.copy();y.loc[40,'close']=10.4
  z=features(y)
  for col in ['A_volume_down','B_breakdown','D_breakout','F_tight_lowvol']:
   self.assertEqual(bool(x.loc[39,col]),bool(z.loc[39,col]))
 def test_no_cross_year_labels(self):
  r=analyze(self.x)
  self.assertLess(r['stocks']['600031.SH']['periods']['2025']['baseline']['n'],len(self.x))
 def test_reject_duplicate(self):
  from tempfile import NamedTemporaryFile
  with NamedTemporaryFile(suffix='.csv') as f:
   pd.concat([self.x,self.x.iloc[:1]]).to_csv(f.name,index=False)
   with self.assertRaises(ValueError):load_panel(f.name)
if __name__=='__main__':unittest.main()
