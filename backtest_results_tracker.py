"""
Backtest Results Tracker

Stores all backtest results in a CSV file for easy tracking and comparison.
Each backtest is logged with:
- DateTime
- Strategy name
- Symbol(s) tested
- Key metrics (return, win rate, sharpe, etc.)
- Notes
"""

import pandas as pd
import os
from datetime import datetime
from typing import Dict, List, Optional


RESULTS_FILE = "d:\\python\\backtest_results.csv"


def init_results_file():
    """Initialize results file if it doesn't exist."""
    if not os.path.exists(RESULTS_FILE):
        columns = [
            'datetime',
            'strategy',
            'symbol',
            'period',
            'total_return_pct',
            'num_trades',
            'win_rate',
            'profit_factor',
            'sharpe_ratio',
            'max_drawdown_pct',
            'avg_trade_return',
            'notes'
        ]
        df = pd.DataFrame(columns=columns)
        df.to_csv(RESULTS_FILE, index=False)
        print(f"Created results file: {RESULTS_FILE}")


def log_result(
    strategy: str,
    symbol: str,
    period: str,
    total_return_pct: float,
    num_trades: int = 0,
    win_rate: float = 0.0,
    profit_factor: float = 0.0,
    sharpe_ratio: float = 0.0,
    max_drawdown_pct: float = 0.0,
    avg_trade_return: float = 0.0,
    notes: str = ""
) -> None:
    """
    Log a backtest result to the tracking file.

    Args:
        strategy: Name of the strategy
        symbol: Stock symbol(s) tested
        period: Test period (e.g., "2024-01-01 to 2024-12-31")
        total_return_pct: Total return percentage
        num_trades: Number of trades
        win_rate: Win rate (0-1)
        profit_factor: Profit factor
        sharpe_ratio: Sharpe ratio
        max_drawdown_pct: Maximum drawdown percentage
        avg_trade_return: Average return per trade
        notes: Additional notes
    """
    init_results_file()

    result = {
        'datetime': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'strategy': strategy,
        'symbol': symbol,
        'period': period,
        'total_return_pct': round(total_return_pct, 2),
        'num_trades': num_trades,
        'win_rate': round(win_rate * 100, 1),  # Store as percentage
        'profit_factor': round(profit_factor, 2),
        'sharpe_ratio': round(sharpe_ratio, 2),
        'max_drawdown_pct': round(max_drawdown_pct, 2),
        'avg_trade_return': round(avg_trade_return, 2),
        'notes': notes
    }

    # Append to CSV
    df = pd.read_csv(RESULTS_FILE)
    df = pd.concat([df, pd.DataFrame([result])], ignore_index=True)
    df.to_csv(RESULTS_FILE, index=False)

    print(f"Logged: {strategy} | {symbol} | Return: {total_return_pct:.2f}%")


def log_batch_results(results: List[Dict]) -> None:
    """Log multiple results at once."""
    for result in results:
        log_result(**result)


def get_results(strategy: str = None, symbol: str = None) -> pd.DataFrame:
    """
    Get backtest results, optionally filtered by strategy or symbol.
    """
    init_results_file()
    df = pd.read_csv(RESULTS_FILE)

    if strategy:
        df = df[df['strategy'].str.contains(strategy, case=False)]
    if symbol:
        df = df[df['symbol'].str.contains(symbol, case=False)]

    return df


def get_best_strategies(metric: str = 'total_return_pct', top_n: int = 10) -> pd.DataFrame:
    """Get top performing strategies by a given metric."""
    df = get_results()
    return df.nlargest(top_n, metric)


def print_summary():
    """Print summary of all backtest results."""
    df = get_results()

    if df.empty:
        print("No backtest results found.")
        return

    print("\n" + "="*80)
    print("BACKTEST RESULTS SUMMARY")
    print("="*80)
    print(f"Total backtests: {len(df)}")
    print(f"Strategies tested: {df['strategy'].nunique()}")
    print(f"Symbols tested: {df['symbol'].nunique()}")

    print("\n--- Best Performing (by Return) ---")
    best = df.nlargest(5, 'total_return_pct')[['strategy', 'symbol', 'total_return_pct', 'win_rate', 'profit_factor']]
    print(best.to_string(index=False))

    print("\n--- Best Win Rate ---")
    best_wr = df.nlargest(5, 'win_rate')[['strategy', 'symbol', 'win_rate', 'total_return_pct', 'num_trades']]
    print(best_wr.to_string(index=False))

    print("\n--- Strategy Averages ---")
    avg = df.groupby('strategy').agg({
        'total_return_pct': 'mean',
        'win_rate': 'mean',
        'profit_factor': 'mean',
        'num_trades': 'sum'
    }).round(2)
    print(avg.to_string())


def print_recent(n: int = 10):
    """Print most recent backtest results."""
    df = get_results()

    if df.empty:
        print("No backtest results found.")
        return

    print(f"\n--- Last {n} Backtests ---")
    recent = df.tail(n)[['datetime', 'strategy', 'symbol', 'total_return_pct', 'win_rate', 'num_trades']]
    print(recent.to_string(index=False))


if __name__ == "__main__":
    # Initialize and show summary
    init_results_file()
    print_summary()
