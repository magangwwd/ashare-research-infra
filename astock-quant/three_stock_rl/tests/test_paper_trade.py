import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from paper_trade import simulate

class SingleTradeTests(unittest.TestCase):
    def test_reference_path(self):
        result = simulate(44.92, 49.41, 36.39, .0003, .001)
        self.assertLess(result["net_reference_return"], result["gross_reference_return"])
        self.assertAlmostEqual(result["gross_reference_return"], 36.39/49.41-1, places=8)
        self.assertFalse(result["fill_confirmed"])
        self.assertFalse(result["rl_trained"])

    def test_reject_bad_inputs(self):
        with self.assertRaises(ValueError):
            simulate(44.92,0,36.39,.0003,.001)
        with self.assertRaises(ValueError):
            simulate(44.92,49.41,36.39,-.1,.001)

if __name__ == "__main__":
    unittest.main()
