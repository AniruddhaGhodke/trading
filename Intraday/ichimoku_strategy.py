"""
Ichimoku Cloud + EMA Strategy for Indian Stocks (NSE)

Converted from Ichimoku.ipynb for testing on NSE stocks.

Strategy Logic:
1. Ichimoku Cloud pierce signals (price breaks through cloud)
2. EMA trend confirmation (price above/below EMA-100)
3. ATR-based SL (2x ATR) and TP (4x ATR = 2R reward-risk)

Note: NSE stocks via yfinance only support daily data, not 4h.
For intraday, we use 1h data instead.

Key differences from forex:
- Higher transaction costs (0.1% slippage + 0.1% commission)
- Different volatility patterns
- Market hours (9:15 AM - 3:30 PM IST)
"""

from __future__ import annotations
import warnings
warnings.filterwarnings("ignore", category=FutureWarning)

import numpy as np
import pandas as pd
import yfinance as yf
from typing import Dict, List, Tuple
from datetime import datetime, timedelta

# Try to import pandas_ta, fallback to manual if not available
try:
    import pandas_ta as ta
    HAS_PANDAS_TA = True
except ImportError:
    HAS_PANDAS_TA = False
    print("pandas_ta not found, using manual indicator calculations")

# =====================================================================
# ICHIMOKU PARAMETERS
# =====================================================================
TENKAN = 9      # Conversion line period
KIJUN = 26      # Base line period
SENKOU_B = 52   # Span B period
ATR_LEN = 14    # ATR period
EMA_LEN = 100   # EMA period

# Risk settings
ATR_MULT_SL = 2.0   # Stop loss = ATR * this
ATR_MULT_TP = 4.0   # Take profit = ATR * this (2:1 reward-risk)


# =====================================================================
# DATA FUNCTIONS
# =====================================================================

def fetch_data(symbol: str, start: str, end: str, interval: str = "1d") -> pd.DataFrame:
    """Download data from Yahoo Finance."""
    df = yf.download(symbol, start=start, end=end, interval=interval,
                     auto_adjust=True, progress=False, threads=False)
    
    if df.empty:
        raise ValueError(f"No data returned for {symbol} @ {interval}.")
    
    # Handle new yfinance MultiIndex format
    if isinstance(df.columns, pd.MultiIndex):
        try:
            df = df.xs(symbol, axis=1, level=1)
        except KeyError:
            # Try first level if symbol not found
            df.columns = df.columns.get_level_values(0)
    
    # Standardize column names
    df.columns = [c.title() for c in df.columns]
    return df.dropna()


def calculate_atr(df: pd.DataFrame, length: int = 14) -> pd.Series:
    """Calculate ATR manually."""
    h, l, c = df['High'], df['Low'], df['Close']
    tr1 = h - l
    tr2 = abs(h - c.shift(1))
    tr3 = abs(l - c.shift(1))
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    return tr.rolling(window=length).mean()


def calculate_ema(series: pd.Series, length: int) -> pd.Series:
    """Calculate EMA manually."""
    return series.ewm(span=length, adjust=False).mean()


# =====================================================================
# ICHIMOKU INDICATOR
# =====================================================================

def add_ichimoku(df: pd.DataFrame,
                 tenkan: int = TENKAN,
                 kijun: int = KIJUN,
                 senkou_b: int = SENKOU_B) -> pd.DataFrame:
    """
    Build bias-safe Ichimoku columns for SIGNAL logic.
    - SpanA/SpanB are UNshifted (no lookahead bias)
    - Chikou confirmation uses past data only
    """
    out = df.copy()
    h, l, c = out["High"], out["Low"], out["Close"]
    
    # Tenkan-sen (Conversion Line)
    tenkan_line = (h.rolling(tenkan).max() + l.rolling(tenkan).min()) / 2.0
    
    # Kijun-sen (Base Line)  
    kijun_line = (h.rolling(kijun).max() + l.rolling(kijun).min()) / 2.0
    
    # Senkou Span A (raw, no forward shift)
    span_a_raw = (tenkan_line + kijun_line) / 2.0
    
    # Senkou Span B (raw, no forward shift)
    span_b_raw = (h.rolling(senkou_b).max() + l.rolling(senkou_b).min()) / 2.0
    
    out["ich_tenkan"] = tenkan_line
    out["ich_kijun"] = kijun_line
    out["ich_spanA"] = span_a_raw
    out["ich_spanB"] = span_b_raw
    
    # Cloud boundaries
    cloud_top = out[["ich_spanA", "ich_spanB"]].max(axis=1)
    cloud_bot = out[["ich_spanA", "ich_spanB"]].min(axis=1)
    
    # Bias-free chikou confirmations for logic
    # At time t, check what was true kijun bars ago
    out["chik_ok_long"] = c.shift(kijun) > cloud_top.shift(kijun)
    out["chik_ok_short"] = c.shift(kijun) < cloud_bot.shift(kijun)
    
    # ATR
    if HAS_PANDAS_TA:
        out["ATR"] = ta.atr(out["High"], out["Low"], out["Close"], length=ATR_LEN)
    else:
        out["ATR"] = calculate_atr(out, ATR_LEN)
    
    # Drop warmup NaNs
    cols_needed = ["ich_tenkan", "ich_kijun", "ich_spanA", "ich_spanB", "ATR"]
    out = out.dropna(subset=cols_needed)
    
    return out


def add_ema_signal(df: pd.DataFrame, ema_len: int = EMA_LEN, back_candles: int = 5) -> pd.DataFrame:
    """
    Add EMA and trend signal.
    
    EMA_signal:
      +1 (uptrend): All last back_candles bars have Open > EMA and Close > EMA
      -1 (downtrend): All last back_candles bars have Open < EMA and Close < EMA
       0 otherwise
    """
    out = df.copy()
    
    # Calculate EMA
    if HAS_PANDAS_TA:
        out["EMA"] = ta.ema(close=out["Close"], length=ema_len)
    else:
        out["EMA"] = calculate_ema(out["Close"], ema_len)
    
    # Window size
    w = int(back_candles) + 1
    
    # Booleans per-bar relative to EMA
    above = (out["Open"] > out["EMA"]) & (out["Close"] > out["EMA"])
    below = (out["Open"] < out["EMA"]) & (out["Close"] < out["EMA"])
    
    # "All true in last w bars" via rolling sum == w
    above_all = (above.rolling(w, min_periods=w).sum() == w)
    below_all = (below.rolling(w, min_periods=w).sum() == w)
    
    # Single signal column
    signal = np.where(above_all, 1, np.where(below_all, -1, 0)).astype(int)
    out["EMA_signal"] = signal
    
    return out


def create_signals(df: pd.DataFrame,
                   lookback_window: int = 10,
                   min_confirm: int = 5) -> pd.DataFrame:
    """
    Produce a single 'signal' column:
      +1 (long): Ichimoku 'pierce-up' + enough prior bars above cloud + EMA_signal == +1
      -1 (short): Ichimoku 'pierce-down' + enough prior bars below cloud + EMA_signal == -1
       0 (none): otherwise
    """
    out = df.copy()
    
    # Cloud boundaries
    cloud_top = out[["ich_spanA", "ich_spanB"]].max(axis=1)
    cloud_bot = out[["ich_spanA", "ich_spanB"]].min(axis=1)
    
    # Candles entirely above/below cloud
    above_cloud = (out["Open"] > cloud_top) & (out["Close"] > cloud_top)
    below_cloud = (out["Open"] < cloud_bot) & (out["Close"] < cloud_bot)
    
    above_count = above_cloud.rolling(lookback_window, min_periods=lookback_window).sum()
    below_count = below_cloud.rolling(lookback_window, min_periods=lookback_window).sum()
    
    # Current-bar pierce conditions
    pierce_up = (out["Open"] < cloud_top) & (out["Close"] > cloud_top)
    pierce_down = (out["Open"] > cloud_bot) & (out["Close"] < cloud_bot)
    
    # Trend confirmations
    up_trend_ok = above_count >= min_confirm
    down_trend_ok = below_count >= min_confirm
    
    # EMA alignment
    ema_up = (out["EMA_signal"] == 1)
    ema_down = (out["EMA_signal"] == -1)
    
    # Final conditions (Ichimoku + EMA alignment)
    long_cond = up_trend_ok & pierce_up & ema_up
    short_cond = down_trend_ok & pierce_down & ema_down
    
    # Single signal column
    signal = np.where(long_cond & ~short_cond, 1,
                      np.where(short_cond & ~long_cond, -1, 0)).astype(int)
    
    out["signal"] = signal
    return out


# =====================================================================
# BACKTEST ENGINE
# =====================================================================

class IchimokuBacktest:
    """
    Custom backtester for Ichimoku strategy with realistic costs.
    
    Uses ATR-based stops and takes profit.
    Includes slippage and commission.
    """
    
    def __init__(self,
                 atr_mult_sl: float = ATR_MULT_SL,
                 atr_mult_tp: float = ATR_MULT_TP,
                 slippage: float = 0.001,      # 0.1%
                 commission: float = 0.001,     # 0.1%
                 risk_per_trade: float = 0.02): # 2% risk
        self.atr_mult_sl = atr_mult_sl
        self.atr_mult_tp = atr_mult_tp
        self.slippage = slippage
        self.commission = commission
        self.risk_per_trade = risk_per_trade
        self.name = f"Ichimoku (SL={atr_mult_sl}xATR, TP={atr_mult_tp}xATR)"
    
    def backtest(self, df: pd.DataFrame, capital: float = 100000) -> Dict:
        """Run backtest on prepared DataFrame with signals."""
        df = df.copy().reset_index()
        
        trades = []
        current_capital = capital
        position = None  # None, 'long', or 'short'
        
        for i in range(len(df)):
            row = df.iloc[i]
            
            # Check for exit if in position
            if position is not None:
                exit_triggered = False
                exit_price = None
                exit_reason = None
                
                if position == 'long':
                    # Check stop loss
                    if row['Low'] <= stop_loss:
                        exit_price = stop_loss * (1 - self.slippage)
                        exit_reason = 'stop_loss'
                        exit_triggered = True
                    # Check take profit
                    elif row['High'] >= take_profit:
                        exit_price = take_profit * (1 - self.slippage)
                        exit_reason = 'take_profit'
                        exit_triggered = True
                        
                elif position == 'short':
                    # Check stop loss
                    if row['High'] >= stop_loss:
                        exit_price = stop_loss * (1 + self.slippage)
                        exit_reason = 'stop_loss'
                        exit_triggered = True
                    # Check take profit
                    elif row['Low'] <= take_profit:
                        exit_price = take_profit * (1 + self.slippage)
                        exit_reason = 'take_profit'
                        exit_triggered = True
                
                if exit_triggered:
                    # Calculate P&L
                    if position == 'long':
                        pnl = shares * (exit_price - entry_price)
                    else:
                        pnl = shares * (entry_price - exit_price)
                    
                    # Commission on exit
                    pnl -= shares * exit_price * self.commission
                    
                    current_capital += pnl
                    
                    trades.append({
                        'entry_date': entry_date,
                        'exit_date': row.get('Datetime', row.get('Date', i)),
                        'direction': position.upper(),
                        'entry_price': entry_price,
                        'exit_price': exit_price,
                        'stop_loss': stop_loss,
                        'take_profit': take_profit,
                        'shares': shares,
                        'pnl': pnl,
                        'return_pct': (exit_price / entry_price - 1) * 100 if position == 'long' 
                                      else (entry_price / exit_price - 1) * 100,
                        'exit_reason': exit_reason,
                        'capital': current_capital
                    })
                    
                    position = None
            
            # Check for new entry if not in position
            if position is None:
                signal = row.get('signal', 0)
                atr = row.get('ATR', 0)
                
                if signal != 0 and atr > 0 and not pd.isna(atr):
                    close = row['Close']
                    
                    # Calculate SL and TP distances
                    sl_dist = atr * self.atr_mult_sl
                    tp_dist = atr * self.atr_mult_tp
                    
                    if signal == 1:  # Long entry
                        entry_price = close * (1 + self.slippage)
                        stop_loss = entry_price - sl_dist
                        take_profit = entry_price + tp_dist
                        position = 'long'
                    else:  # Short entry
                        entry_price = close * (1 - self.slippage)
                        stop_loss = entry_price + sl_dist
                        take_profit = entry_price - tp_dist
                        position = 'short'
                    
                    # Position sizing based on risk
                    risk_amount = current_capital * self.risk_per_trade
                    risk_per_share = sl_dist
                    shares = int(risk_amount / risk_per_share) if risk_per_share > 0 else 0
                    
                    # Cap at 25% of capital
                    max_shares = int(current_capital * 0.25 / entry_price)
                    shares = min(shares, max_shares)
                    
                    if shares <= 0:
                        position = None
                        continue
                    
                    # Commission on entry
                    current_capital -= shares * entry_price * self.commission
                    
                    entry_date = row.get('Datetime', row.get('Date', i))
        
        return self._calculate_results(trades, capital, current_capital)
    
    def _calculate_results(self, trades: List[Dict], 
                          initial_capital: float, 
                          final_capital: float) -> Dict:
        """Calculate backtest statistics."""
        trades_df = pd.DataFrame(trades) if trades else pd.DataFrame()
        total_return = (final_capital / initial_capital - 1) * 100
        
        if len(trades_df) > 0:
            win_rate = (trades_df['pnl'] > 0).mean()
            
            wins = trades_df[trades_df['pnl'] > 0]
            losses = trades_df[trades_df['pnl'] < 0]
            
            avg_win = wins['return_pct'].mean() if len(wins) > 0 else 0
            avg_loss = abs(losses['return_pct'].mean()) if len(losses) > 0 else 0
            
            if len(losses) > 0 and losses['pnl'].sum() != 0:
                profit_factor = abs(wins['pnl'].sum() / losses['pnl'].sum())
            else:
                profit_factor = float('inf') if len(wins) > 0 else 0
            
            best_trade = trades_df['return_pct'].max()
            worst_trade = trades_df['return_pct'].min()
            
            # Count by exit reason
            sl_exits = (trades_df['exit_reason'] == 'stop_loss').sum()
            tp_exits = (trades_df['exit_reason'] == 'take_profit').sum()
        else:
            win_rate = 0
            profit_factor = 0
            avg_win = 0
            avg_loss = 0
            best_trade = 0
            worst_trade = 0
            sl_exits = 0
            tp_exits = 0
        
        return {
            'total_return_pct': total_return,
            'num_trades': len(trades_df),
            'win_rate': win_rate,
            'profit_factor': profit_factor,
            'avg_win': avg_win,
            'avg_loss': avg_loss,
            'best_trade': best_trade,
            'worst_trade': worst_trade,
            'sl_exits': sl_exits,
            'tp_exits': tp_exits,
            'final_capital': final_capital,
            'trades': trades_df
        }


# =====================================================================
# MAIN BACKTEST RUNNER
# =====================================================================

def run_ichimoku_backtest(symbols: List[str] = None,
                          start: str = "2023-01-01",
                          end: str = "2025-01-01",
                          interval: str = "1d"):
    """
    Run Ichimoku strategy on multiple NSE stocks.
    """
    if symbols is None:
        symbols = [
            'RELIANCE.NS', 'TCS.NS', 'HDFCBANK.NS', 'INFY.NS', 'SBIN.NS',
            'BHARTIARTL.NS', 'ITC.NS', 'KOTAKBANK.NS', 'AXISBANK.NS', 'LT.NS',
            'MARUTI.NS', 'SUNPHARMA.NS', 'TITAN.NS', 'WIPRO.NS', 'IDEA.NS'
        ]
    
    print("=" * 85)
    print("ICHIMOKU CLOUD + EMA STRATEGY - NSE Stocks")
    print("=" * 85)
    print(f"\nPeriod: {start} to {end}")
    print(f"Interval: {interval}")
    print(f"Parameters: Tenkan={TENKAN}, Kijun={KIJUN}, SenkouB={SENKOU_B}, EMA={EMA_LEN}")
    print(f"Risk: SL={ATR_MULT_SL}x ATR, TP={ATR_MULT_TP}x ATR")
    print(f"Costs: 0.1% slippage + 0.1% commission")
    
    backtest = IchimokuBacktest(
        atr_mult_sl=ATR_MULT_SL,
        atr_mult_tp=ATR_MULT_TP,
        slippage=0.001,
        commission=0.001
    )
    
    all_results = []
    
    for symbol in symbols:
        try:
            # Fetch and prepare data
            df = fetch_data(symbol, start, end, interval)
            
            if df.empty or len(df) < SENKOU_B + EMA_LEN:
                print(f"Skipping {symbol}: Insufficient data")
                continue
            
            # Add indicators
            df = add_ichimoku(df, TENKAN, KIJUN, SENKOU_B)
            df = add_ema_signal(df, EMA_LEN, back_candles=5)
            df = create_signals(df, lookback_window=10, min_confirm=5)
            df = df.dropna()
            
            if len(df) < 50:
                print(f"Skipping {symbol}: Not enough data after indicators")
                continue
            
            # Count signals
            long_signals = (df['signal'] == 1).sum()
            short_signals = (df['signal'] == -1).sum()
            
            # Run backtest
            result = backtest.backtest(df)
            
            all_results.append({
                'symbol': symbol,
                'return': result['total_return_pct'],
                'trades': result['num_trades'],
                'win_rate': result['win_rate'],
                'profit_factor': result['profit_factor'],
                'avg_win': result['avg_win'],
                'avg_loss': result['avg_loss'],
                'best_trade': result['best_trade'],
                'worst_trade': result['worst_trade'],
                'sl_exits': result['sl_exits'],
                'tp_exits': result['tp_exits'],
                'long_signals': long_signals,
                'short_signals': short_signals,
                'trades_df': result['trades']
            })
            
        except Exception as e:
            print(f"Error with {symbol}: {e}")
    
    # Print results table
    print("\n" + "=" * 85)
    print("RESULTS BY STOCK")
    print("=" * 85)
    
    if all_results:
        results_df = pd.DataFrame(all_results)
        
        print(f"\n{'Symbol':<15} | {'Return':<8} | {'Trades':<6} | {'WR':<6} | {'PF':<6} | {'TP/SL':<7} | {'Best':<8} | {'Worst':<8}")
        print("-" * 85)
        
        for _, r in results_df.iterrows():
            status = "OK" if r['return'] > 0 else "--"
            tp_sl = f"{int(r['tp_exits'])}/{int(r['sl_exits'])}"
            print(f"{r['symbol']:<15} | {r['return']:>6.2f}% | {r['trades']:>6} | {r['win_rate']:>5.1%} | {r['profit_factor']:>5.2f} | {tp_sl:>7} | {r['best_trade']:>7.2f}% | {r['worst_trade']:>7.2f}% {status}")
        
        # Summary
        print("-" * 85)
        avg_return = results_df['return'].mean()
        total_trades = results_df['trades'].sum()
        avg_wr = results_df['win_rate'].mean()
        profitable = (results_df['return'] > 0).sum()
        
        print(f"\n{'AVERAGE':<15} | {avg_return:>6.2f}% | {total_trades:>6} | {avg_wr:>5.1%} | {'--':>5} | {'--':>7} | {'--':>8} | {'--':>8}")
        print(f"\nProfitable stocks: {profitable}/{len(results_df)}")
        
        # Show sample trades
        print("\n" + "=" * 85)
        print("SAMPLE TRADES (first 3 per stock):")
        print("=" * 85)
        
        for r in all_results[:5]:  # First 5 stocks
            if len(r['trades_df']) > 0:
                print(f"\n{r['symbol']}:")
                trades = r['trades_df'].head(3)
                for _, t in trades.iterrows():
                    status = "WIN" if t['pnl'] > 0 else "LOSS"
                    print(f"  {t['direction']} | Entry: {t['entry_price']:.2f} | Exit: {t['exit_price']:.2f} | "
                          f"Return: {t['return_pct']:+.2f}% | {t['exit_reason']} | {status}")
    else:
        print("\nNo results to display.")
    
    return all_results


def compare_with_other_strategies():
    """Compare Ichimoku results with other tested strategies."""
    print("\n" + "=" * 85)
    print("COMPARISON WITH OTHER STRATEGIES")
    print("=" * 85)
    
    print("""
| Strategy               | Avg Return | Win Rate | Verdict      |
|------------------------|------------|----------|--------------|
| ORB (Correct)          | -1.06%     | 25.8%    | LOSING       |
| Mean Reversion         | -0.22%     | 49.2%    | LOSING       |
| Trend Pullback         | -0.76%     | 37.8%    | LOSING       |
| First Candle           | -3.44%     | 32.2%    | LOSING       |
| Gap Continuation       | -0.85%     | 24.4%    | LOSING       |
| Extreme Moves (2%+)    | 0.00%      | 13.3%    | BREAK-EVEN   |
| VWAP Bounce            | +0.07%     | 55.6%    | MARGINAL     |
| Smart SIP (Long-term)  | +19.89%    | N/A      | RECOMMENDED  |
|------------------------|------------|----------|--------------|
| Ichimoku (this test)   | ???        | ???      | SEE ABOVE    |

Note: Previous strategies used 60-day intraday (1h) data.
Ichimoku uses daily data over 2 years, which may show different results.
""")


# =====================================================================
# MAIN
# =====================================================================

if __name__ == "__main__":
    # Run backtest on NSE stocks
    results = run_ichimoku_backtest(
        start="2023-01-01",
        end="2025-01-15",
        interval="1d"  # Daily data (NSE doesn't support 4h via yfinance)
    )
    
    # Compare with other strategies
    compare_with_other_strategies()
