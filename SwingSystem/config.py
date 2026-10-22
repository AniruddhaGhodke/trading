"""
Central configuration for the swing signal system.

Every number that affects results lives here, so backtests and live scans
always use the same assumptions.
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
CACHE_DIR = DATA_DIR / "cache"
UNIVERSE_FILE = DATA_DIR / "nifty100.csv"
JOURNAL_DB = DATA_DIR / "journal.db"

# Market index used for regime detection and relative strength
INDEX_SYMBOL = "^NSEI"

# History to download (enough for 200-day / 52-week indicators + backtest years)
HISTORY_START = "2015-01-01"

# ---------------------------------------------------------------------------
# Costs (per side, as a fraction of trade value)
# Indian delivery trades: STT 0.1% each side + exchange/GST/stamp + slippage.
# 0.15% per side = 0.30% round trip. Deliberately conservative.
# ---------------------------------------------------------------------------
COST_PER_SIDE = 0.0015

# ---------------------------------------------------------------------------
# Execution model (shared by backtest AND live tracker)
# Signal is generated on day T's close. Entry is day T+1's OPEN.
# If T+1 opens beyond MAX_ENTRY_GAP_ATR from T's close, the call is skipped
# (chasing gaps destroys the planned risk/reward).
# ---------------------------------------------------------------------------
MAX_ENTRY_GAP_ATR = 1.0

# ---------------------------------------------------------------------------
# Backtest verdict (`main.py backtest`). A strategy is labelled an EDGE only if ALL hold:
#   monthly t of (strategy - random control) > EDGE_MIN_T
#   per-trade edge over control >= MIN_EDGE_PER_TRADE (percentage points)
#   net average return per trade > 0 (after COST_PER_SIDE)
# 0.10% = one third of the 0.30% round-trip cost: a smaller edge is inside the error of the
# cost/slippage assumption itself, so it would not survive real fills.
# ---------------------------------------------------------------------------
EDGE_MIN_T = 2.5
MIN_EDGE_PER_TRADE = 0.10

# ---------------------------------------------------------------------------
# Scanner output
# ---------------------------------------------------------------------------
TOP_N_CALLS = 10

# ---------------------------------------------------------------------------
# Research lab (python lab.py ...). Fixed up front - never change these after
# looking at results, or the holdouts stop being out-of-sample.
# ---------------------------------------------------------------------------
LAB_DIR = DATA_DIR / "lab"
LAB_PRICES_DIR = LAB_DIR / "prices"
# 10 tuning stocks, one per sector, mix of low and high volatility
LAB_STOCKS = ["RELIANCE.NS", "HDFCBANK.NS", "INFY.NS", "ITC.NS", "LT.NS",
              "SUNPHARMA.NS", "MARUTI.NS", "TATASTEEL.NS", "BAJFINANCE.NS", "BHARTIARTL.NS"]
TRAIN_START = "2016-01-01"
TRAIN_END = "2023-12-31"     # train = LAB_STOCKS, TRAIN_START..TRAIN_END (data cut here)
HOLDOUT_START = "2024-01-01" # holdout A = LAB_STOCKS from here; holdout B = the other 90 stocks
MIN_TRAIN_TRADES = 30

# ---------------------------------------------------------------------------
# Position sizing shown by the scanner (edit to your account)
# ---------------------------------------------------------------------------
CAPITAL = 500_000            # Rs
RISK_PER_TRADE = 0.01        # lose at most 1% of capital if the stop is hit
MAX_POSITION_PCT = 0.20      # never put more than 20% of capital in one stock
MAX_NEW_POSITIONS = 3        # dip signals cluster in selloffs - cap new entries per day

# ---------------------------------------------------------------------------
# Intraday lab (python lab_intraday.py ...). Fixed before looking at results.
# Intraday equity costs (India): brokerage ~0.03%/Rs20, STT 0.025% on SELL only, exchange+GST+stamp
# ~0.005% - roughly 0.03%/side + slippage. Results are shown at two levels.
# ---------------------------------------------------------------------------
INTRADAY_COST_PER_SIDE = 0.0005        # 0.05%/side = 0.10% round trip (optimistic but realistic, liquid large caps)
INTRADAY_COST_STRESS = 0.0010          # 0.10%/side = 0.20% round trip (bad fills, opening-bar slippage)
INTRADAY_TRAIN_START = "2023-12-01"    # hourly data starts 2023-11-01; 1 month warm-up for daily features
INTRADAY_TRAIN_END = "2025-06-30"      # train = LAB_STOCKS to here | A = LAB_STOCKS after | B = other 90 stocks
INTRADAY_HOLDOUT_START = "2025-07-01"
# 5-minute intraday lab (Angel One history from 2018)
INTRADAY5_TRAIN_START = "2018-03-01"
INTRADAY5_TRAIN_END = "2023-12-31"     # train = LAB_STOCKS 2018-2023 | A = LAB_STOCKS 2024+ | B = other 90 | B24 = other 90 2024+
INTRADAY5_HOLDOUT_START = "2024-01-01"
