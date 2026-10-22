"""
Intraday Trading System - FIXED VERSION

Fixes from original:
1. Entry at actual close price (not breakout level)
2. Added slippage (0.05%)
3. Stop-loss based on ATR, not arbitrary range
4. Realistic position sizing
"""

import pandas as pd
import numpy as np
import yfinance as yf
from datetime import datetime, timedelta
from typing import Dict, List, Tuple
import warnings
warnings.filterwarnings('ignore')

from backtest_results_tracker import log_result


class ORBStrategyFixed:
    """
    Opening Range Breakout - FIXED VERSION

    Key fixes:
    - Entry at CLOSE price when breakout confirmed (not at breakout level)
    - Stop-loss using ATR (more realistic)
    - Slippage included
    """

    def __init__(self, range_hours: int = 1, volume_factor: float = 1.2,
                 atr_stop_multiplier: float = 1.5):
        self.range_hours = range_hours
        self.volume_factor = volume_factor
        self.atr_stop_multiplier = atr_stop_multiplier
        self.name = f"ORB_Fixed ({range_hours}h)"

    def generate_signals(self, df: pd.DataFrame) -> pd.DataFrame:
        """Generate ORB signals with REALISTIC entry prices."""
        df = df.copy()

        # Calculate ATR for stop-loss
        tr1 = df['High'] - df['Low']
        tr2 = abs(df['High'] - df['Close'].shift(1))
        tr3 = abs(df['Low'] - df['Close'].shift(1))
        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        df['ATR'] = tr.rolling(window=14).mean()

        # Calculate opening range
        df['Date'] = df.index.date

        range_high = pd.Series(index=df.index, dtype=float)
        range_low = pd.Series(index=df.index, dtype=float)

        for date in df['Date'].unique():
            day_data = df[df['Date'] == date]
            if len(day_data) >= self.range_hours:
                or_high = day_data['High'].iloc[:self.range_hours].max()
                or_low = day_data['Low'].iloc[:self.range_hours].min()
                range_high.loc[day_data.index] = or_high
                range_low.loc[day_data.index] = or_low

        df['OR_High'] = range_high
        df['OR_Low'] = range_low

        # Volume condition
        df['Vol_MA'] = df['Volume'].rolling(20).mean()
        df['High_Volume'] = df['Volume'] > df['Vol_MA'] * self.volume_factor

        # Initialize signals
        df['Signal'] = 0
        df['Entry'] = np.nan
        df['Stop_Loss'] = np.nan
        df['Signal_Type'] = ''

        for date in df['Date'].unique():
            day_data = df[df['Date'] == date]

            if len(day_data) <= self.range_hours:
                continue

            post_or = day_data.iloc[self.range_hours:]
            or_high = day_data['OR_High'].iloc[0]
            or_low = day_data['OR_Low'].iloc[0]

            if pd.isna(or_high) or pd.isna(or_low):
                continue

            breakout_found = False

            for idx in post_or.index:
                if breakout_found:
                    break

                row = df.loc[idx]
                atr = row['ATR'] if not pd.isna(row['ATR']) else (or_high - or_low)

                # Bullish breakout
                if row['Close'] > or_high and row['High_Volume']:
                    # FIX: Entry at CLOSE price (where we actually can execute)
                    entry_price = row['Close']
                    # FIX: Stop-loss using ATR from entry, not arbitrary range
                    stop_loss = entry_price - self.atr_stop_multiplier * atr

                    df.loc[idx, 'Signal'] = 1
                    df.loc[idx, 'Entry'] = entry_price
                    df.loc[idx, 'Stop_Loss'] = stop_loss
                    df.loc[idx, 'Signal_Type'] = 'ORB_Long'
                    breakout_found = True

                # Bearish breakdown
                elif row['Close'] < or_low and row['High_Volume']:
                    entry_price = row['Close']
                    stop_loss = entry_price + self.atr_stop_multiplier * atr

                    df.loc[idx, 'Signal'] = -1
                    df.loc[idx, 'Entry'] = entry_price
                    df.loc[idx, 'Stop_Loss'] = stop_loss
                    df.loc[idx, 'Signal_Type'] = 'ORB_Short'
                    breakout_found = True

        return df


class RealisticBacktester:
    """
    Realistic backtester with:
    - Slippage
    - Transaction costs
    - Proper position sizing
    """

    def __init__(self, capital: float = 100000, max_risk_pct: float = 0.01,
                 slippage_pct: float = 0.0005,  # 0.05% slippage
                 commission_pct: float = 0.0003,  # 0.03% commission
                 max_trades_per_day: int = 2):
        self.capital = capital
        self.max_risk_pct = max_risk_pct
        self.slippage_pct = slippage_pct
        self.commission_pct = commission_pct
        self.max_trades_per_day = max_trades_per_day

    def run(self, df: pd.DataFrame, exit_eod: bool = True) -> Dict:
        """Run realistic backtest."""
        df = df.copy()

        capital = self.capital
        position = 0
        entry_price = 0
        stop_loss = 0
        entry_date = None
        trades = []
        daily_trades = {}

        for i, (idx, row) in enumerate(df.iterrows()):
            current_date = idx.date() if hasattr(idx, 'date') else idx

            if current_date not in daily_trades:
                daily_trades[current_date] = 0

            # Check stop-loss (with slippage on exit)
            if position > 0 and row['Low'] <= stop_loss:
                # Stop-loss hit - add slippage (worse exit)
                exit_price = stop_loss * (1 - self.slippage_pct)
                commission = position * exit_price * self.commission_pct
                pnl = position * (exit_price - entry_price) - commission
                capital += pnl  # FIXED: just add P&L, not double count

                trades.append({
                    'entry_date': entry_date,
                    'exit_date': idx,
                    'type': 'long',
                    'entry_price': entry_price,
                    'exit_price': exit_price,
                    'exit_reason': 'stop_loss',
                    'pnl': pnl,
                    'return_pct': (exit_price / entry_price - 1) * 100
                })
                position = 0

            elif position < 0 and row['High'] >= stop_loss:
                exit_price = stop_loss * (1 + self.slippage_pct)
                commission = abs(position) * exit_price * self.commission_pct
                pnl = abs(position) * (entry_price - exit_price) - commission
                capital += pnl

                trades.append({
                    'entry_date': entry_date,
                    'exit_date': idx,
                    'type': 'short',
                    'entry_price': entry_price,
                    'exit_price': exit_price,
                    'exit_reason': 'stop_loss',
                    'pnl': pnl,
                    'return_pct': (entry_price / exit_price - 1) * 100
                })
                position = 0

            # End of day exit
            if exit_eod and position != 0:
                next_idx = i + 1
                if next_idx < len(df):
                    next_date = df.index[next_idx].date() if hasattr(df.index[next_idx], 'date') else None
                    if next_date and next_date != current_date:
                        exit_price = row['Close']
                        commission = abs(position) * exit_price * self.commission_pct

                        if position > 0:
                            pnl = position * (exit_price - entry_price) - commission
                            ret_pct = (exit_price / entry_price - 1) * 100
                        else:
                            pnl = abs(position) * (entry_price - exit_price) - commission
                            ret_pct = (entry_price / exit_price - 1) * 100

                        capital += pnl  # FIXED: always just add P&L
                        trades.append({
                            'entry_date': entry_date,
                            'exit_date': idx,
                            'type': 'long' if position > 0 else 'short',
                            'entry_price': entry_price,
                            'exit_price': exit_price,
                            'exit_reason': 'eod',
                            'pnl': pnl,
                            'return_pct': ret_pct
                        })
                        position = 0

            # New signal - with slippage on entry
            if position == 0 and row['Signal'] != 0:
                if daily_trades[current_date] >= self.max_trades_per_day:
                    continue

                if pd.isna(row['Entry']) or pd.isna(row['Stop_Loss']):
                    continue

                # Add slippage to entry (worse entry)
                if row['Signal'] == 1:
                    entry_price = row['Entry'] * (1 + self.slippage_pct)
                    stop_loss = row['Stop_Loss']
                else:
                    entry_price = row['Entry'] * (1 - self.slippage_pct)
                    stop_loss = row['Stop_Loss']

                # Calculate position size based on risk
                risk_per_share = abs(entry_price - stop_loss)
                if risk_per_share <= 0:
                    continue

                max_risk_amount = capital * self.max_risk_pct
                max_shares = max_risk_amount / risk_per_share

                # Also limit to 20% of capital
                max_position = capital * 0.20
                max_shares_by_capital = max_position / entry_price

                shares = int(min(max_shares, max_shares_by_capital))

                if shares <= 0:
                    continue

                # Commission on entry
                commission = shares * entry_price * self.commission_pct

                # FIXED: Track capital correctly for both long and short
                # For both long and short, we track the initial capital and position separately
                # The capital change happens on exit, not on entry
                if row['Signal'] == 1:
                    position = shares
                else:
                    position = -shares
                # Note: We don't deduct capital on entry - P&L is calculated on exit

                entry_date = idx
                daily_trades[current_date] += 1

        # Calculate results
        trades_df = pd.DataFrame(trades) if trades else pd.DataFrame()
        total_return = (capital / self.capital - 1) * 100 if trades else 0

        if len(trades_df) > 0:
            win_rate = (trades_df['pnl'] > 0).mean()

            wins = trades_df[trades_df['pnl'] > 0]['pnl']
            losses = trades_df[trades_df['pnl'] < 0]['pnl']

            avg_win = wins.mean() if len(wins) > 0 else 0
            avg_loss = abs(losses.mean()) if len(losses) > 0 else 0

            profit_factor = (wins.sum() / abs(losses.sum())) if len(losses) > 0 and losses.sum() != 0 else 0
        else:
            win_rate = 0
            profit_factor = 0

        return {
            'total_return_pct': total_return,
            'num_trades': len(trades_df),
            'win_rate': win_rate,
            'profit_factor': profit_factor,
            'final_capital': capital,
            'trades': trades_df
        }


def download_data(symbol: str, period: str = '60d') -> pd.DataFrame:
    """Download hourly data."""
    data = yf.download(symbol, period=period, interval='1h', progress=False)
    if isinstance(data.columns, pd.MultiIndex):
        data.columns = data.columns.get_level_values(0)
    return data


def compare_old_vs_fixed(symbol: str = 'RELIANCE.NS'):
    """Compare buggy vs fixed backtests."""
    from intraday_system import OpeningRangeBreakout, IntradayBacktester

    print("="*70)
    print(f"COMPARISON: Buggy vs Fixed ORB - {symbol}")
    print("="*70)

    df = download_data(symbol, '60d')
    if df.empty:
        print("No data")
        return

    print(f"Period: {df.index[0]} to {df.index[-1]}")
    print(f"Candles: {len(df)}")

    # Buggy version
    print("\n--- BUGGY VERSION (original) ---")
    buggy_strategy = OpeningRangeBreakout(range_hours=1)
    buggy_backtester = IntradayBacktester(capital=100000, max_risk_pct=0.01)

    buggy_signals = buggy_strategy.generate_signals(df)
    buggy_result = buggy_backtester.run(buggy_signals)

    print(f"Return: {buggy_result['total_return_pct']:.2f}%")
    print(f"Trades: {buggy_result['num_trades']}")
    print(f"Win Rate: {buggy_result['win_rate']:.1%}")
    print(f"Profit Factor: {buggy_result['profit_factor']:.2f}")

    # Fixed version
    print("\n--- FIXED VERSION (realistic) ---")
    fixed_strategy = ORBStrategyFixed(range_hours=1, atr_stop_multiplier=1.5)
    fixed_backtester = RealisticBacktester(
        capital=100000,
        max_risk_pct=0.01,
        slippage_pct=0.0005,
        commission_pct=0.0003
    )

    fixed_signals = fixed_strategy.generate_signals(df)
    fixed_result = fixed_backtester.run(fixed_signals)

    print(f"Return: {fixed_result['total_return_pct']:.2f}%")
    print(f"Trades: {fixed_result['num_trades']}")
    print(f"Win Rate: {fixed_result['win_rate']:.1%}")
    print(f"Profit Factor: {fixed_result['profit_factor']:.2f}")

    # Show sample trades comparison
    if len(buggy_result['trades']) > 0 and len(fixed_result['trades']) > 0:
        print("\n--- Sample Trade Comparison ---")
        print("\nBuggy (first 3 trades):")
        for _, t in buggy_result['trades'].head(3).iterrows():
            print(f"  Entry: {t['entry_price']:.2f}, Exit: {t['exit_price']:.2f}, Return: {t['return_pct']:.2f}%")

        print("\nFixed (first 3 trades):")
        for _, t in fixed_result['trades'].head(3).iterrows():
            print(f"  Entry: {t['entry_price']:.2f}, Exit: {t['exit_price']:.2f}, Return: {t['return_pct']:.2f}%")

    return buggy_result, fixed_result


def run_fixed_backtest(symbols: List[str] = None, log_results: bool = True):
    """Run fixed backtest on multiple stocks."""
    if symbols is None:
        symbols = [
            'RELIANCE.NS', 'TCS.NS', 'HDFCBANK.NS', 'INFY.NS', 'ICICIBANK.NS',
            'SBIN.NS', 'BHARTIARTL.NS', 'ITC.NS', 'KOTAKBANK.NS', 'AXISBANK.NS'
        ]

    print("="*70)
    print("ORB FIXED BACKTEST - Realistic Version")
    print("="*70)

    strategy = ORBStrategyFixed(range_hours=1, atr_stop_multiplier=1.5)
    backtester = RealisticBacktester(capital=100000, max_risk_pct=0.01)

    all_results = []

    for symbol in symbols:
        try:
            print(f"Testing {symbol}...", end=" ")
            df = download_data(symbol, '60d')

            if df.empty or len(df) < 20:
                print("Insufficient data")
                continue

            signals_df = strategy.generate_signals(df)
            result = backtester.run(signals_df)

            all_results.append({
                'symbol': symbol,
                'total_return_pct': result['total_return_pct'],
                'num_trades': result['num_trades'],
                'win_rate': result['win_rate'],
                'profit_factor': result['profit_factor']
            })

            if log_results:
                log_result(
                    strategy="ORB_Fixed (1h)",
                    symbol=symbol,
                    period="60d hourly",
                    total_return_pct=result['total_return_pct'],
                    num_trades=result['num_trades'],
                    win_rate=result['win_rate'],
                    profit_factor=result['profit_factor'],
                    notes="Fixed: realistic entry, slippage, ATR stop"
                )

            print(f"Return: {result['total_return_pct']:.2f}%, WR: {result['win_rate']:.1%}")

        except Exception as e:
            print(f"Error: {e}")

    if all_results:
        results_df = pd.DataFrame(all_results)

        print("\n" + "="*70)
        print("FIXED ORB SUMMARY (Realistic)")
        print("="*70)
        print(f"Avg Return: {results_df['total_return_pct'].mean():.2f}%")
        print(f"Median Return: {results_df['total_return_pct'].median():.2f}%")
        print(f"Avg Win Rate: {results_df['win_rate'].mean():.1%}")
        print(f"Profitable Stocks: {(results_df['total_return_pct'] > 0).sum()}/{len(results_df)}")

        return results_df

    return None


if __name__ == "__main__":
    # Compare buggy vs fixed
    compare_old_vs_fixed('RELIANCE.NS')

    print("\n\n")

    # Run fixed backtest on multiple stocks
    run_fixed_backtest()
