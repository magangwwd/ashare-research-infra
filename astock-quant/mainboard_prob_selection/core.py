"""Shared selection + execution core (used by replay backtests AND the forward script).

Fixes from audit_20261107:
  F01  quarterly frozen models: train_quarter() reproduces 02/08b exactly and can save/load
  F04  event order per day: OPEN exits -> OPEN entries -> CLOSE exits. A position that is
       still held at the open blocks a new buy that day even if it is sold at the close.
  F09  candidates are ranked on the signal date (no Tuesday information); the gate uses the
       rank-1 stock; Tuesday-open buyability is checked afterwards, in fixed rank order.
  F08  weekly statistics use Monday-start trading weeks.
  F10  cost scenarios name exactly which items are scaled.
"""
from __future__ import annotations

import dataclasses
import datetime as dt
import glob
import hashlib
import json
import os

import lightgbm as lgb
import numpy as np
import polars as pl

HERE = os.path.dirname(os.path.abspath(__file__))
GOLD = os.environ.get("PROB_GOLD_ROOT", r"G:\我的云端硬盘\project\down_data\data\gold\main_board")
CAPITAL = 100_000.0
STAMP_CUT = np.datetime64("2023-08-28")
SEED = 20261107
LGB_PARAMS = dict(objective="binary", learning_rate=0.03, num_leaves=31, min_child_samples=2000,
                  colsample_bytree=0.7, subsample=0.7, subsample_freq=1, reg_lambda=10.0,
                  n_estimators=300, n_jobs=8, verbose=-1)
TRAIN_FROM = dt.date(2015, 1, 1)
POOL_ATR_MAX = 0.08          # protection pool: 2*ATR14 <= 8%
SEEDS = (SEED, SEED + 1, SEED + 2)


# ------------------------------------------------------------------ calendar
AQ = os.environ.get("PROB_AQ_ROOT", r"G:\我的云端硬盘\Ashare_Quant")


def trading_days() -> list[dt.date]:
    """Open SSE days: gold calendar (history) + Tushare trade_cal from the current
    Ashare_Quant version for future dates; weekdays as a last-resort extrapolation."""
    g = (pl.read_parquet(os.path.join(GOLD, "trading_calendar.parquet"))
           .filter((pl.col("exchange") == "SSE") & pl.col("is_open"))["calendar_date"].to_list())
    end = max(g)
    fut = []
    try:
        cur = json.load(open(os.path.join(AQ, "current.json"), encoding="utf-8"))["version"]
        for f in glob.glob(os.path.join(AQ, "versions", cur, "data", "clean", "trade_cal", "year=*", "*.parquet")):
            t = pl.read_parquet(f).filter((pl.col("exchange") == "SSE") & (pl.col("is_open") == 1) &
                                          (pl.col("calendar_date") > end))
            fut += t["calendar_date"].to_list()
    except Exception as e:  # noqa: BLE001
        print(f"WARN future calendar unavailable: {e}")
    days = sorted(set(g) | set(fut))
    last = days[-1]
    if last < end + dt.timedelta(days=14):
        print("WARN calendar extrapolated with weekdays beyond", last)
        if os.environ.get("PROB_REQUIRE_OFFICIAL_CALENDAR") == "1":
            raise RuntimeError("Missing official future trade calendar; refuse prediction rather than extrapolate weekdays")
        d = last
        while d < end + dt.timedelta(days=14):
            d += dt.timedelta(days=1)
            if d.weekday() < 5:
                days.append(d)
    return days


# ------------------------------------------------------------------ models (F01)
def features(panel: pl.DataFrame) -> list[str]:
    return [c for c in panel.columns if c.startswith(("rk_", "mkt_"))]


def train_quarter(panel: pl.DataFrame, cutoff: dt.date, save_dir: str | None = None):
    """Train the V7 model set with labels settled before `cutoff` (= first signal date of quarter)."""
    feats = features(panel)
    tr = panel.filter((pl.col("signal_date") >= TRAIN_FROM) & (pl.col("exit_date") < cutoff) &
                      pl.col("buyable") & pl.col("y_rel").is_not_null())
    X = tr.select(feats).to_numpy().astype(np.float32)
    models = {f"rel_s{k}": lgb.LGBMClassifier(**LGB_PARAMS, random_state=s).fit(X, tr["y_rel"].to_numpy())
              for k, s in enumerate(SEEDS)}
    models["abs"] = lgb.LGBMClassifier(**LGB_PARAMS, random_state=SEED).fit(X, tr["y_abs"].to_numpy())
    meta = dict(cutoff=str(cutoff), train_rows=tr.height, features=feats, params=LGB_PARAMS,
                seeds=list(SEEDS), lightgbm=lgb.__version__, train_from=str(TRAIN_FROM))
    if save_dir:
        os.makedirs(save_dir, exist_ok=True)
        hashes = {}
        for k, m in models.items():
            f = os.path.join(save_dir, f"{k}.txt")
            m.booster_.save_model(f)
            hashes[k] = sha256(f)
        meta["sha256"] = hashes
        atomic_write(os.path.join(save_dir, "meta.json"), json.dumps(meta, ensure_ascii=False, indent=1))
    return {k: m.booster_ for k, m in models.items()}, meta


def load_quarter(save_dir: str):
    meta = json.load(open(os.path.join(save_dir, "meta.json"), encoding="utf-8"))
    for k, h in meta["sha256"].items():
        if sha256(os.path.join(save_dir, f"{k}.txt")) != h:
            raise RuntimeError(f"model hash mismatch: {k}")
    return {k: lgb.Booster(model_file=os.path.join(save_dir, f"{k}.txt")) for k in meta["sha256"]}, meta


def score(models, meta, rows: pl.DataFrame) -> pl.DataFrame:
    X = rows.select(meta["features"]).to_numpy().astype(np.float32)
    rel = [models[f"rel_s{k}"].predict(X) for k in range(len(SEEDS))]
    return rows.with_columns(pl.Series("p_rel", rel[0]), pl.Series("p_rel_s1", rel[1]),
                             pl.Series("p_rel_s2", rel[2]),
                             pl.Series("p_rel_ens", np.mean(rel, axis=0)),
                             pl.Series("p_abs", models["abs"].predict(X)))


def quarter_cutoff(panel: pl.DataFrame, signal_date: dt.date) -> dt.date:
    q = (signal_date.year, (signal_date.month - 1) // 3 + 1)
    s = panel.select("signal_date").unique().filter(
        (pl.col("signal_date").dt.year() == q[0]) & (pl.col("signal_date").dt.quarter() == q[1]))
    return s["signal_date"].min()


# --------------------------------------------------------------- selection (F09)
def select(rows: pl.DataFrame, score_col="p_rel_ens", gate_col="p_abs", n=3, gate=0.5):
    """rows = one signal date. Uses only signal-date information."""
    pool = rows.filter(2 * pl.col("atr_raw") <= POOL_ATR_MAX).sort(score_col, descending=True)
    top = pool.head(n)
    g = float(top[gate_col][0]) if top.height else 0.0
    return dict(cands=list(zip(top["instrument_id"].to_list(), top["atr_raw"].to_list())),
                gate_value=g, invest=bool(top.height and g >= gate), pool_size=pool.height,
                ranked=pool)


# ------------------------------------------------------------------- bars
def load_bars(path: str):
    sim = pl.read_parquet(path).sort(["instrument_id", "trade_date"])
    bars = {}
    for (iid,), g in sim.group_by(["instrument_id"], maintain_order=True):
        prev = g["previous_close"].to_numpy(); st = g["is_st"].to_numpy()
        lim = np.where(st, 0.05, 0.10)
        up = g["up_limit"].to_numpy(); dn = g["down_limit"].to_numpy()
        up = np.where(np.isnan(up), np.round(prev * (1 + lim), 2), up)
        dn = np.where(np.isnan(dn), np.round(prev * (1 - lim), 2), dn)
        bars[iid] = (g["trade_date"].to_numpy().astype("datetime64[D]"), g["open"].to_numpy(),
                     g["high"].to_numpy(), g["low"].to_numpy(), g["close"].to_numpy(),
                     g["volume_shares"].to_numpy(), g["adjustment_factor"].to_numpy(), up, dn)
    days = np.array(sorted(sim["trade_date"].unique().to_numpy().astype("datetime64[D]")))
    return bars, days


def bar(bars, iid, day):
    b = bars.get(iid)
    if b is None:
        return None
    i = np.searchsorted(b[0], day)
    if i < len(b[0]) and b[0][i] == day and b[5][i] > 0:
        return dict(o=b[1][i], h=b[2][i], l=b[3][i], c=b[4][i], adj=b[6][i], up=b[7][i], dn=b[8][i])
    return None


def last_close(bars, iid, day):
    b = bars[iid]
    i = np.searchsorted(b[0], day, side="right") - 1
    return b[4][i], b[6][i]


# ------------------------------------------------------------------ costs (F10)
@dataclasses.dataclass
class Costs:
    name: str = "base"
    comm_rate: float = 0.00025
    min_comm: float = 5.0
    transfer: float = 0.00001
    stamp_old: float = 0.001
    stamp_new: float = 0.0005
    slip: float = 0.001

    def fee(self, amount, day, side):
        f = max(self.min_comm, amount * self.comm_rate) + amount * self.transfer
        if side == "sell":
            f += amount * (self.stamp_old if day < STAMP_CUT else self.stamp_new)
        return f


COSTS = {
    "base": Costs(),
    "slip_comm_x2": Costs("slip_comm_x2", comm_rate=0.0005, slip=0.002),
    "all_x2": Costs("all_x2", comm_rate=0.0005, min_comm=10.0, transfer=0.00002,
                    stamp_old=0.002, stamp_new=0.001, slip=0.002),
}


# --------------------------------------------------------------- engine (F04)
@dataclasses.dataclass
class Rules:
    slots: int = 1               # max simultaneous stocks (1 = protocol)
    budget: float = 0.50         # total cost incl. fees <= budget * equity
    risk: float | None = None    # planned risk per account (e.g. 0.01) or None = budget only
    exit: str = "close"          # close | open
    costs: Costs = dataclasses.field(default_factory=Costs)


def simulate(plans: dict, bars, days, rules: Rules):
    """plans: entry_day(np.datetime64) -> dict(exit_day, cands=[(id, atr)], invest=bool)."""
    c = rules.costs
    cash, pos, curve, trades = CAPITAL, [], [], []

    def equity_at_open(day):
        v = cash
        for p in pos:
            lc, la = last_close(bars, p["id"], day - np.timedelta64(1, "D"))
            v += p["shares"] * lc * la / p["adj0"]
        return v

    def sell(p, px, day, why, phase):
        nonlocal cash
        amt = p["shares"] * px * (1 - c.slip)
        cash += amt - c.fee(amt, day, "sell")
        pnl = amt - c.fee(amt, day, "sell") - p["cost"]
        trades.append(dict(id=p["id"], entry=str(p["entry_day"]), exit=str(day), exit_phase=phase,
                           reason=why, shares=p["shares"], cost=p["cost"], pnl=pnl, ret=pnl / p["cost"],
                           equity_before=p["equity_before"], acct_ret=pnl / p["equity_before"]))

    for day in days:
        # ---- OPEN: exits that can happen at the open
        for p in list(pos):
            if day <= p["entry_day"]:
                continue
            b = bar(bars, p["id"], day)
            if b is None:
                continue
            k = b["adj"] / p["adj0"]
            if (p["pending"] or (rules.exit == "open" and day >= p["exit_day"])):
                if b["o"] > b["dn"] + 0.001:
                    sell(p, b["o"] * k, day, "carried" if p["pending"] else "exit_open", "open")
                    pos.remove(p)
                else:
                    p["pending"] = True
        # ---- OPEN: entries (only with slots free at the open)
        plan = plans.get(day)
        if plan is not None and plan["invest"] and len(pos) < rules.slots:
            eq = equity_at_open(day)
            held = {p["id"] for p in pos}
            per_slot_budget = eq * rules.budget / rules.slots
            for iid, atr in plan["cands"]:
                if len(pos) >= rules.slots:
                    break
                if iid in held:
                    continue
                b = bar(bars, iid, day)
                if b is None or not (b["o"] < b["up"] - 0.001):
                    continue
                px = b["o"] * (1 + c.slip)
                n = int(min(per_slot_budget, cash) / (px * (1 + c.comm_rate + c.transfer)) // 100) * 100
                if rules.risk:
                    dist = max(2 * atr, 0.02)
                    rps = px * dist + px * (2 * c.slip + 2 * c.comm_rate + c.stamp_new)
                    n = min(n, int(eq * rules.risk / rules.slots / rps // 100) * 100)
                while n >= 100 and n * px + c.fee(n * px, day, "buy") > min(per_slot_budget, cash):
                    n -= 100
                if n < 100:
                    continue
                amt = n * px
                f = c.fee(amt, day, "buy")
                cash -= amt + f
                pos.append(dict(id=iid, shares=n, adj0=b["adj"], entry_day=day, exit_day=plan["exit_day"],
                                pending=False, cost=amt + f, equity_before=eq))
        # ---- CLOSE: scheduled close exits and pending positions that could not sell at the open
        for p in list(pos):
            if day <= p["entry_day"]:
                continue
            b = bar(bars, p["id"], day)
            if b is None:
                continue
            k = b["adj"] / p["adj0"]
            if p["pending"] or (rules.exit == "close" and day >= p["exit_day"]):
                if b["c"] > b["dn"] + 0.001:
                    sell(p, b["c"] * k, day, "carried" if p["pending"] else "exit_close", "close")
                    pos.remove(p)
                else:
                    p["pending"] = True
        mv = 0.0
        for p in pos:
            lc, la = last_close(bars, p["id"], day)
            mv += p["shares"] * lc * la / p["adj0"]
        curve.append((day, cash + mv))
    return curve, trades


# ------------------------------------------------------------------ stats (F08)
def weekly_returns(curve):
    d = np.array([x[0] for x in curve]); e = np.array([x[1] for x in curve])
    wk = (d - np.datetime64("1970-01-05")).astype(int) // 7          # Monday-start weeks
    last = np.r_[wk[1:] != wk[:-1], True]
    ew = np.r_[CAPITAL, e[last]]
    return ew[1:] / ew[:-1] - 1


def summary(curve, trades):
    d = np.array([x[0] for x in curve]); e = np.array([x[1] for x in curve])
    yrs = (d[-1] - d[0]).astype(int) / 365.25
    yearly = {}
    for y in sorted({int(str(x)[:4]) for x in d}):
        m = (d >= np.datetime64(f"{y}-01-01")) & (d <= np.datetime64(f"{y}-12-31"))
        i0 = int(np.argmax(m))
        yearly[y] = float(e[m][-1] / (e[i0 - 1] if i0 > 0 else CAPITAL) - 1)
    r = np.array([t["ret"] for t in trades]) if trades else np.zeros(1)
    a = np.array([t["acct_ret"] for t in trades]) if trades else np.zeros(1)
    return dict(total_ret=float(e[-1] / CAPITAL - 1), cagr=float((e[-1] / CAPITAL) ** (1 / yrs) - 1),
                max_dd=float((1 - e / np.maximum.accumulate(e)).max()), trades=len(trades),
                win_rate=float((r > 0).mean()), avg_trade=float(r.mean()),
                worst_acct_loss=float(a.min()), yearly=yearly)


# ------------------------------------------------------------------ io helpers
def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def atomic_write(path, text):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(tmp, path)


def build_plans(pred: pl.DataFrame, panel: pl.DataFrame, score_col="p_rel_ens", gate_col="p_abs",
                n=3, gate=0.5):
    """Backtest plans from cached predictions; same select() as the forward script."""
    df = pred.join(panel.select("signal_date", "instrument_id", "entry_date", "exit_date", "atr_raw"),
                   on=["signal_date", "instrument_id"], how="inner")
    plans, picks = {}, []
    for (sd,), g in df.group_by(["signal_date"]):
        s = select(g, score_col, gate_col, n, gate)
        ed = np.datetime64(g["entry_date"][0], "D")
        plans[ed] = dict(exit_day=np.datetime64(g["exit_date"][0], "D"), cands=s["cands"],
                         invest=s["invest"])
        picks.append(dict(signal_date=sd, top1=s["cands"][0][0] if s["cands"] else None,
                          gate_value=s["gate_value"], invest=s["invest"]))
    return plans, pl.DataFrame(picks).sort("signal_date")


# ------------------------------------------------------- calibration (display only)
def calibration(cutoff: dt.date, save_dir: str | None = None, cache_dir: str | None = None) -> dict:
    """Isotonic map p_abs -> realised probability, fitted on OUT-OF-SAMPLE weekly predictions
    (research cache) whose labels settled before `cutoff`, protection pool, buyable rows.
    Two targets: up (ret>0) and net_up (ret>0.3%, ~round-trip cost). Frozen per quarter.
    Information only: it never changes ranking, gate or sizing."""
    f = os.path.join(save_dir, "calib.json") if save_dir else None
    if f and os.path.exists(f):
        return json.load(open(f, encoding="utf-8"))
    from sklearn.isotonic import IsotonicRegression
    cache_dir = cache_dir or os.path.join(HERE, "cache")
    p = pl.read_parquet(os.path.join(cache_dir, "predictions.parquet"), columns=["signal_date", "instrument_id", "p_abs"])
    pn = pl.read_parquet(os.path.join(cache_dir, "weekly_panel.parquet"),
                         columns=["signal_date", "instrument_id", "exit_date", "atr_raw", "buyable", "ret_close"])
    d = p.join(pn, on=["signal_date", "instrument_id"]).filter(
        (pl.col("exit_date") < cutoff) & pl.col("buyable") & pl.col("ret_close").is_not_null() &
        (2 * pl.col("atr_raw") <= POOL_ATR_MAX))
    out = dict(cutoff=str(cutoff), rows=d.height, from_=str(d["signal_date"].min()), to=str(d["signal_date"].max()),
               note="isotonic on out-of-sample predictions; display only")
    x = d["p_abs"].to_numpy()
    for name, thr in (("up", 0.0), ("net_up", 0.003)):
        iso = IsotonicRegression(out_of_bounds="clip", y_min=0, y_max=1).fit(x, (d["ret_close"].to_numpy() > thr).astype(float))
        out[name] = dict(x=iso.X_thresholds_.tolist(), y=iso.y_thresholds_.tolist())
    if f:
        atomic_write(f, json.dumps(out))
    return out


def apply_calibration(cal: dict, p: np.ndarray, name: str) -> np.ndarray:
    return np.interp(p, cal[name]["x"], cal[name]["y"])
