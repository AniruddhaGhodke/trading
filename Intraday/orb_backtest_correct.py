"""
ORB Backtest - CORRECT VERSION

Simple, verifiable backtest with no bugs.
Focus on correctness over features.
"""

import pandas as pd
import numpy as np
import yfinance as yf
from datetime import datetime
from typing import Dict, List
import warnings
warnings.filterwarnings('ignore')

from backtest_results_tracker import log_result


def download_data(symbol: str, period: str = '60d') -> pd.DataFrame:
    """Download hourly data."""
    data = yf.download(symbol, period=period, interval='1h', progress=False)
    if isinstance(data.columns, pd.MultiIndex):
        data.columns = data.columns.get_level_values(0)
    return data


def calculate_atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Calculate ATR."""
    tr1 = df['High'] - df['Low']
    tr2 = abs(df['High'] - df['Close'].shift(1))
    tr3 = abs(df['Low'] - df['Close'].shift(1))
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    return tr.rolling(window=period).mean()


def backtest_orb_simple(
    df: pd.DataFrame,
    capital: float = 100000,
    risk_per_trade: float = 0.01,  # 1% risk
    slippage_pct: float = 0.001,   # 0.1% slippage
    commission_pct: float = 0.001, # 0.1% round-trip commission
    range_hours: int = 1,
    atr_stop_mult: float = 2.0
) -> Dict:
    """
    Simple, correct ORB backtest.

    Rules:
    - Wait for first hour to form opening range (OR)
    - BUY when Close > OR_High (breakout)
    - Entry at CLOSE price (not OR_High!)
    - Stop-loss at Entry - ATR * multiplier
    - Exit at end of day or stop-loss
    - Only 1 trade per day
    """

    df = df.copy()
    df['Date'] = df.index.date
    df['ATR'] = calculate_atr(df)

    trades = []
    portfolio_value = capital

    for date in df['Date'].unique():
        day_data = df[df['Date'] == date]

        if len(day_data) < range_hours + 1:
            continue

        # Calculate opening range
        or_high = day_data['High'].iloc[:range_hours].max()
        or_low = day_data['Low'].iloc[:range_hours].min()

        # Get ATR at start of day
        atr = day_data['ATR'].iloc[range_hours] if not pd.isna(day_data['ATR'].iloc[range_hours]) else (or_high - or_low)
        if pd.isna(atr) or atr <= 0:
            atr = or_high - or_low

        # Look for breakout after opening range
        post_or = day_data.iloc[range_hours:]
        trade_taken = False

        for idx in post_or.index:
            if trade_taken:
                continue

            row = df.loc[idx]

            # LONG breakout
            if row['Close'] > or_high:
                # Entry at CLOSE (realistic!) + slippage
                entry_price = row['Close'] * (1 + slippage_pct)

                # Stop-loss using ATR
                stop_loss = entry_price - atr * atr_stop_mult

                # Position sizing based on risk
                risk_per_share = entry_price - stop_loss
                if risk_per_share <= 0:
                    continue

                max_risk = portfolio_value * risk_per_trade
                shares = int(max_risk / risk_per_share)

                # Cap at 20% of portfolio
                max_shares = int((portfolio_value * 0.2) / entry_price)
                shares = min(shares, max_shares)

                if shares <= 0:
                    continue

                # Find exit
                remaining_day = post_or.loc[idx:]
                exit_price = None
                exit_reason = None

                for exit_idx in remaining_day.index[1:]:  # Start from next candle
                    exit_row = df.loc[exit_idx]

                    # Check stop-loss
                    if exit_row['Low'] <= stop_loss:
                        exit_price = stop_loss * (1 - slippage_pct)  # Slippage on stop
                        exit_reason = 'stop_loss'
                        break

                # If no stop hit, exit at end of day
                if exit_price is None:
                    exit_price = remaining_day['Close'].iloc[-1]
                    exit_reason = 'eod'

                # Calculate P&L
                gross_pnl = shares * (exit_price - entry_price)
                commission = shares * entry_price * commission_pct  # Entry + exit
                net_pnl = gross_pnl - commission

                portfolio_value += net_pnl

                trades.append({
                    'date': date,
                    'entry_price': entry_price,
                    'stop_loss': stop_loss,
                    'exit_price': exit_price,
                    'exit_reason': exit_reason,
                    'shares': shares,
                    'gross_pnl': gross_pnl,
                    'commission': commission,
                    'net_pnl': net_pnl,
                    'return_pct': (exit_price / entry_price - 1) * 100,
                    'portfolio_value': portfolio_value
                })

                trade_taken = True

    # Calculate metrics
    trades_df = pd.DataFrame(trades) if trades else pd.DataFrame()

    if len(trades_df) > 0:
        total_return = (portfolio_value / capital - 1) * 100
        win_rate = (trades_df['net_pnl'] > 0).mean()

        wins = trades_df[trades_df['net_pnl'] > 0]['net_pnl']
        losses = trades_df[trades_df['net_pnl'] < 0]['net_pnl']

        if len(losses) > 0 and losses.sum() != 0:
            profit_factor = abs(wins.sum() / losses.sum())
        else:
            profit_factor = float('inf') if len(wins) > 0 else 0

        avg_win = wins.mean() if len(wins) > 0 else 0
        avg_loss = losses.mean() if len(losses) > 0 else 0
    else:
        total_return = 0
        win_rate = 0
        profit_factor = 0
        avg_win = 0
        avg_loss = 0

    return {
        'total_return_pct': total_return,
        'num_trades': len(trades_df),
        'win_rate': win_rate,
        'profit_factor': profit_factor,
        'avg_win': avg_win,
        'avg_loss': avg_loss,
        'final_capital': portfolio_value,
        'trades': trades_df
    }


def run_backtest_multi(symbols: List[str] = None, log_results: bool = True):
    """Run backtest on multiple stocks."""
    if symbols is None:
        symbols = [
            'RELIANCE.NS', 'TCS.NS', 'HDFCBANK.NS', 'INFY.NS', 'ICICIBANK.NS',
            'SBIN.NS', 'BHARTIARTL.NS', 'ITC.NS', 'KOTAKBANK.NS', 'AXISBANK.NS',
            'LT.NS', 'MARUTI.NS', 'SUNPHARMA.NS', 'TITAN.NS', 'WIPRO.NS'
        ]

    print("="*70)
    print("ORB BACKTEST - CORRECT VERSION")
    print("="*70)
    print("Settings: 1% risk, 0.1% slippage, 0.1% commission, 2x ATR stop")
    print()

    all_results = []

    for symbol in symbols:
        try:
            df = download_data(symbol, '60d')

            if df.empty or len(df) < 30:
                print(f"{symbol}: Insufficient data")
                continue

            result = backtest_orb_simple(
                df,
                capital=100000,
                risk_per_trade=0.01,
                slippage_pct=0.001,
                commission_pct=0.001,
                atr_stop_mult=2.0
            )

            all_results.append({
                'symbol': symbol,
                'return': result['total_return_pct'],
                'trades': result['num_trades'],
                'win_rate': result['win_rate'],
                'profit_factor': result['profit_factor'],
                'avg_win': result['avg_win'],
                'avg_loss': result['avg_loss']
            })

            status = "+" if result['total_return_pct'] > 0 else "-"
            print(f"{symbol:<15} Return: {result['total_return_pct']:>7.2f}%  Trades: {result['num_trades']:>3}  Win Rate: {result['win_rate']:>5.1%}  PF: {result['profit_factor']:>5.2f} {status}")

            if log_results:
                log_result(
                    strategy="ORB_Correct",
                    symbol=symbol,
                    period="60d hourly",
                    total_return_pct=result['total_return_pct'],
                    num_trades=result['num_trades'],
                    win_rate=result['win_rate'],
                    profit_factor=result['profit_factor'],
                    notes="Correct: entry at close, 0.1% slip, 0.1% comm, 2x ATR stop"
                )

        except Exception as e:
            print(f"{symbol}: Error - {e}")

    if all_results:
        results_df = pd.DataFrame(all_results)

        print("\n" + "="*70)
        print("SUMMARY - CORRECT ORB BACKTEST")
        print("="*70)
        print(f"Stocks tested: {len(results_df)}")
        print(f"Avg Return: {results_df['return'].mean():.2f}%")
        print(f"Median Return: {results_df['return'].median():.2f}%")
        print(f"Std Dev: {results_df['return'].std():.2f}%")
        print(f"Avg Win Rate: {results_df['win_rate'].mean():.1%}")
        print(f"Profitable Stocks: {(results_df['return'] > 0).sum()}/{len(results_df)} ({(results_df['return'] > 0).mean():.1%})")
        print(f"Total Trades: {results_df['trades'].sum()}")

        # Log aggregate
        if log_results:
            log_result(
                strategy="ORB_Correct - AGGREGATE",
                symbol=f"{len(results_df)} stocks",
                period="60d hourly",
                total_return_pct=results_df['return'].mean(),
                num_trades=int(results_df['trades'].sum()),
                win_rate=results_df['win_rate'].mean(),
                profit_factor=results_df['profit_factor'].mean(),
                notes="AGGREGATE - Correct version"
            )

        return results_df

    return None


def verify_single_trade(symbol: str = 'RELIANCE.NS'):
    """Verify a single trade step by step."""
    print("="*70)
    print(f"TRADE VERIFICATION - {symbol}")
    print("="*70)

    df = download_data(symbol, '30d')
    df['Date'] = df.index.date
    df['ATR'] = calculate_atr(df)

    # Find first breakout
    for date in df['Date'].unique()[:10]:
        day_data = df[df['Date'] == date]

        if len(day_data) < 3:
            continue

        or_high = day_data['High'].iloc[0]
        or_low = day_data['Low'].iloc[0]
        atr = day_data['ATR'].iloc[1]

        if pd.isna(atr):
            atr = or_high - or_low

        for idx in day_data.index[1:]:
            row = df.loc[idx]
            if row['Close'] > or_high:
                print(f"\nDate: {date}")
                print(f"Opening Range: High={or_high:.2f}, Low={or_low:.2f}")
                print(f"ATR: {atr:.2f}")
                print()
                print(f"Breakout candle Close: {row['Close']:.2f}")
                print(f"Entry (with 0.1% slippage): {row['Close'] * 1.001:.2f}")
                print(f"Stop-loss (2x ATR): {row['Close'] * 1.001 - 2 * atr:.2f}")
                print()

                # Calculate position
                entry = row['Close'] * 1.001
                stop = entry - 2 * atr
                risk_per_share = entry - stop
                max_risk = 100000 * 0.01  # 1% of 100k
                shares = int(max_risk / risk_per_share)

                print(f"Risk per share: {risk_per_share:.2f}")
                print(f"Max risk (1% of 100k): {max_risk:.2f}")
                print(f"Position size: {shares} shares")
                print(f"Position value: Rs {shares * entry:,.2f}")
                print()

                # Find exit
                remaining = day_data.loc[idx:]
                for exit_idx in remaining.index[1:]:
                    exit_row = df.loc[exit_idx]
                    if exit_row['Low'] <= stop:
                        print(f"STOP HIT at {exit_row['Low']:.2f}")
                        pnl = shares * (stop * 0.999 - entry)
                        print(f"P&L: Rs {pnl:,.2f} ({pnl/100000*100:.2f}%)")
                        return

                # EOD exit
                eod_price = remaining['Close'].iloc[-1]
                pnl = shares * (eod_price - entry)
                print(f"EOD Exit at: {eod_price:.2f}")
                print(f"P&L: Rs {pnl:,.2f} ({pnl/100000*100:.2f}%)")
                return

    print("No breakout found in first 10 days")


if __name__ == "__main__":
    # First verify a single trade
    verify_single_trade()

    print("\n\n")

    # Then run full backtest
    run_backtest_multi()
