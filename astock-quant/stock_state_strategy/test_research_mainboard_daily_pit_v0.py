"""Offline invariants for the new, independently versioned main-board research."""
import unittest
from collections import defaultdict
import pandas as pd
import numpy as np
from research_mainboard_daily_pit_v0 import history_features, run_portfolio, START


class MainBoardCausalTests(unittest.TestCase):
    def sample(self, end=290):
        dates=pd.bdate_range("2023-01-02", periods=end)
        prices=10+np.linspace(0,4,len(dates))
        return pd.DataFrame({
            "vendor_symbol":"600000.SH","trade_date":dates,
            "open":prices,"high":prices+0.1,"low":prices-0.1,"close":prices,
            "volume_shares":np.full(len(dates),1000000.),
            "turnover_cny":np.full(len(dates),110000000.),
            "p_open":prices,"p_high":prices+0.1,"p_low":prices-0.1,"p_close":prices,
        })

    def test_appending_future_prices_never_changes_past_signals(self):
        df=self.sample()
        past=history_features(df.iloc[:260].copy())
        modified=df.copy()
        modified.loc[260:,"p_close"]*=100
        modified.loc[260:,"p_high"]*=100
        modified.loc[260:,"p_low"]*=100
        after=history_features(modified)
        self.assertEqual(past["signal"].tolist(),after.iloc[:260]["signal"].tolist())
        self.assertTrue(np.allclose(
            past["atr14"].iloc[20:].to_numpy(),
            after["atr14"].iloc[20:260].to_numpy(),equal_nan=True
        ))

    def test_entry_day_stop_cannot_exit_until_next_session(self):
        dates=pd.to_datetime(["2024-01-02","2024-01-03","2024-01-04"])
        # Entry at 10.00, risk 0.40, stop at 9.60; on entry day low 9.50.
        bars=pd.DataFrame({
            "open":[10,9.8,9.5],"high":[10.5,10,9.8],
            "low":[9.5,9.3,9.4],"close":[9.7,9.5,9.7],
            "p_open":[10,9.8,9.5],"p_high":[10.5,10,9.8],
            "p_low":[9.5,9.3,9.4],"p_close":[9.7,9.5,9.7],
            "volume_shares":[100000,100000,100000],
            "adjustment_factor":[1,1,1],
        },index=dates)
        candidate={"symbol":"600000.SH","signal_date":pd.Timestamp("2023-12-29"),
                   "pattern":"BREAKOUT","strength":3,
                   "signal_close":10,"atr":0.2}
        trades,nav,_,_ = run_portfolio(
            list(dates),{"600000.SH":bars},
            {dates[0]:[candidate]}
        )
        self.assertEqual(len(trades),1)
        self.assertEqual(trades.iloc[0]["entry_date"],"2024-01-02")
        self.assertEqual(trades.iloc[0]["exit_date"],"2024-01-03")
        self.assertEqual(trades.iloc[0]["exit_reason"],"ENTRY_DAY_STOP_TPLUS1")


if __name__=="__main__":
    unittest.main()
