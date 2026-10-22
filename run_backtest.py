"""
Comprehensive Backtest Runner

Run backtests for any strategy and automatically log results.
Currently supports:
- ORB (Opening Range Breakout)
- VWAP Strategy
- MTF (Multi-Timeframe Trend)
- MA Crossover
- Smart SIP vs Simple SIP
"""

import pandas as pd
import numpy as np
import yfinance as yf
from datetime import datetime, timedelta
from typing import Dict, List
import warnings
warnings.filterwarnings('ignore')

from intraday_system import (
    OpeningRangeBreakout, VWAPStrategy, MultiTimeframeTrend,
    IntradayBacktester, RiskManager
)
from indicator_strategy import MACrossoverStrategy, Backtester
from longterm_system import LongTermBacktester, MarketRegimeDetector
from backtest_results_tracker import log_result, print_summary, print_recent


# Stock lists
NIFTY_50_SAMPLE = [
    'RELIANCE.NS', 'TCS.NS', 'HDFCBANK.NS', 'INFY.NS', 'ICICIBANK.NS',
    'HINDUNILVR.NS', 'SBIN.NS', 'BHARTIARTL.NS', 'ITC.NS', 'KOTAKBANK.NS',
    'LT.NS', 'AXISBANK.NS', 'ASIANPAINT.NS', 'MARUTI.NS', 'SUNPHARMA.NS',
    'TITAN.NS', 'BAJFINANCE.NS', 'WIPRO.NS', 'HCLTECH.NS', 'ULTRACEMCO.NS'
]


def download_hourly_data(symbol: str, period: str = '60d') -> pd.DataFrame:
    """Download hourly data for intraday strategies."""
    data = yf.download(symbol, period=period, interval='1h', progress=False)
    if isinstance(data.columns, pd.MultiIndex):
        data.columns = data.columns.get_level_values(0)
    return data


def download_daily_data(symbol: str, years: int = 2) -> pd.DataFrame:
    """Download daily data for swing/long-term strategies."""
    end_date = datetime.now()
    start_date = end_date - timedelta(days=years * 365)
    data = yf.download(symbol, start=start_date, end=end_date, progress=False)
    if isinstance(data.columns, pd.MultiIndex):
        data.columns = data.columns.get_level_values(0)
    return data


def backtest_orb(symbols: List[str] = None, period: str = '60d',
                range_hours: int = 1, log_results: bool = True) -> Dict:
    """
    Comprehensive ORB backtest across multiple stocks.

    Args:
        symbols: List of stock symbols to test
        period: Data period (e.g., '60d', '30d')
        range_hours: Hours for opening range
        log_results: Whether to log results to tracker
    """
    if symbols is None:
        symbols = NIFTY_50_SAMPLE

    print("="*70)
    print(f"ORB BACKTEST - {len(symbols)} stocks, Period: {period}")
    print("="*70)

    strategy = OpeningRangeBreakout(range_hours=range_hours)
    backtester = IntradayBacktester(capital=100000, max_risk_pct=0.01, max_trades_per_day=2)

    all_results = []
    successful = 0

    for symbol in symbols:
        try:
            print(f"Testing {symbol}...", end=" ")
            df = download_hourly_data(symbol, period)

            if df.empty or len(df) < 20:
                print("Insufficient data")
                continue

            # Generate signals and backtest
            signals_df = strategy.generate_signals(df)
            result = backtester.run(signals_df)

            # Calculate additional metrics
            trades_df = result.get('trades', pd.DataFrame())
            avg_trade_return = trades_df['return_pct'].mean() if not trades_df.empty else 0

            stock_result = {
                'symbol': symbol,
                'total_return_pct': result['total_return_pct'],
                'num_trades': result['num_trades'],
                'win_rate': result['win_rate'],
                'profit_factor': result['profit_factor'],
                'avg_trade_return': avg_trade_return
            }

            all_results.append(stock_result)
            successful += 1

            # Log individual result
            if log_results:
                log_result(
                    strategy=f"ORB ({range_hours}h)",
                    symbol=symbol,
                    period=f"{period} hourly",
                    total_return_pct=result['total_return_pct'],
                    num_trades=result['num_trades'],
                    win_rate=result['win_rate'],
                    profit_factor=result['profit_factor'],
                    avg_trade_return=avg_trade_return,
                    notes=f"Max 2 trades/day, 1% risk"
                )

            print(f"Return: {result['total_return_pct']:.2f}%, WR: {result['win_rate']:.1%}")

        except Exception as e:
            print(f"Error: {e}")
            continue

    # Summary
    if all_results:
        results_df = pd.DataFrame(all_results)

        print("\n" + "="*70)
        print("ORB BACKTEST SUMMARY")
        print("="*70)
        print(f"Stocks tested: {successful}/{len(symbols)}")
        print(f"\nAggregate Results:")
        print(f"  Avg Return: {results_df['total_return_pct'].mean():.2f}%")
        print(f"  Median Return: {results_df['total_return_pct'].median():.2f}%")
        print(f"  Avg Win Rate: {results_df['win_rate'].mean():.1%}")
        print(f"  Avg Profit Factor: {results_df['profit_factor'].mean():.2f}")
        print(f"  Total Trades: {results_df['num_trades'].sum()}")
        print(f"  Profitable Stocks: {(results_df['total_return_pct'] > 0).sum()}/{len(results_df)}")

        print("\n--- Top Performers ---")
        top = results_df.nlargest(5, 'total_return_pct')
        for _, row in top.iterrows():
            print(f"  {row['symbol']}: {row['total_return_pct']:.2f}% ({row['win_rate']:.1%} WR)")

        print("\n--- Worst Performers ---")
        worst = results_df.nsmallest(3, 'total_return_pct')
        for _, row in worst.iterrows():
            print(f"  {row['symbol']}: {row['total_return_pct']:.2f}%")

        # Log aggregate result
        if log_results:
            log_result(
                strategy=f"ORB ({range_hours}h) - AGGREGATE",
                symbol=f"{successful} stocks",
                period=f"{period} hourly",
                total_return_pct=results_df['total_return_pct'].mean(),
                num_trades=int(results_df['num_trades'].sum()),
                win_rate=results_df['win_rate'].mean(),
                profit_factor=results_df['profit_factor'].mean(),
                avg_trade_return=results_df['avg_trade_return'].mean(),
                notes=f"Aggregate of {successful} stocks"
            )

        return {
            'results': results_df,
            'summary': {
                'avg_return': results_df['total_return_pct'].mean(),
                'median_return': results_df['total_return_pct'].median(),
                'avg_win_rate': results_df['win_rate'].mean(),
                'total_trades': results_df['num_trades'].sum()
            }
        }

    return {'results': pd.DataFrame(), 'summary': {}}


def backtest_vwap(symbols: List[str] = None, period: str = '60d',
                 log_results: bool = True) -> Dict:
    """VWAP strategy backtest across multiple stocks."""
    if symbols is None:
        symbols = NIFTY_50_SAMPLE

    print("="*70)
    print(f"VWAP BACKTEST - {len(symbols)} stocks, Period: {period}")
    print("="*70)

    strategy = VWAPStrategy()
    backtester = IntradayBacktester(capital=100000, max_risk_pct=0.01, max_trades_per_day=2)

    all_results = []

    for symbol in symbols:
        try:
            print(f"Testing {symbol}...", end=" ")
            df = download_hourly_data(symbol, period)

            if df.empty or len(df) < 20:
                print("Insufficient data")
                continue

            signals_df = strategy.generate_signals(df)
            result = backtester.run(signals_df)

            trades_df = result.get('trades', pd.DataFrame())
            avg_trade_return = trades_df['return_pct'].mean() if not trades_df.empty else 0

            all_results.append({
                'symbol': symbol,
                'total_return_pct': result['total_return_pct'],
                'num_trades': result['num_trades'],
                'win_rate': result['win_rate'],
                'profit_factor': result['profit_factor']
            })

            if log_results:
                log_result(
                    strategy="VWAP Bounce",
                    symbol=symbol,
                    period=f"{period} hourly",
                    total_return_pct=result['total_return_pct'],
                    num_trades=result['num_trades'],
                    win_rate=result['win_rate'],
                    profit_factor=result['profit_factor'],
                    avg_trade_return=avg_trade_return,
                    notes="VWAP bounce/rejection"
                )

            print(f"Return: {result['total_return_pct']:.2f}%")

        except Exception as e:
            print(f"Error: {e}")

    if all_results:
        results_df = pd.DataFrame(all_results)
        print(f"\nAggregate: Avg Return: {results_df['total_return_pct'].mean():.2f}%")

    return {'results': pd.DataFrame(all_results)}


def backtest_ma_crossover(symbols: List[str] = None, fast: int = 5, slow: int = 20,
                          years: int = 2, log_results: bool = True) -> Dict:
    """MA Crossover backtest on daily data."""
    if symbols is None:
        symbols = NIFTY_50_SAMPLE

    print("="*70)
    print(f"MA CROSSOVER ({fast}/{slow}) BACKTEST - {len(symbols)} stocks")
    print("="*70)

    strategy = MACrossoverStrategy(fast_period=fast, slow_period=slow)
    backtester = Backtester(initial_capital=100000, transaction_cost=0.001)

    all_results = []

    for symbol in symbols:
        try:
            print(f"Testing {symbol}...", end=" ")
            df = download_daily_data(symbol, years)

            if df.empty or len(df) < slow + 10:
                print("Insufficient data")
                continue

            signals = strategy.generate_signals(df['Close'])
            result = backtester.run(df['Close'], signals)

            all_results.append({
                'symbol': symbol,
                'total_return_pct': result['total_return_pct'],
                'num_trades': result['num_trades'],
                'win_rate': result['win_rate'],
                'profit_factor': result['profit_factor'],
                'sharpe_ratio': result['sharpe_ratio'],
                'max_drawdown_pct': result['max_drawdown_pct']
            })

            if log_results:
                log_result(
                    strategy=f"MA Crossover ({fast}/{slow})",
                    symbol=symbol,
                    period=f"{years}y daily",
                    total_return_pct=result['total_return_pct'],
                    num_trades=result['num_trades'],
                    win_rate=result['win_rate'],
                    profit_factor=result['profit_factor'],
                    sharpe_ratio=result['sharpe_ratio'],
                    max_drawdown_pct=result['max_drawdown_pct'],
                    notes="Daily timeframe"
                )

            print(f"Return: {result['total_return_pct']:.2f}%")

        except Exception as e:
            print(f"Error: {e}")

    if all_results:
        results_df = pd.DataFrame(all_results)
        print(f"\nAggregate: Avg Return: {results_df['total_return_pct'].mean():.2f}%")

    return {'results': pd.DataFrame(all_results)}


def backtest_sip_strategies(symbol: str = 'RELIANCE.NS', years: int = 5,
                           log_results: bool = True) -> Dict:
    """Compare SIP strategies on a stock."""
    print("="*70)
    print(f"SIP STRATEGIES BACKTEST - {symbol}")
    print("="*70)

    df = download_daily_data(symbol, years)
    if df.empty:
        print("No data available")
        return {}

    prices = df['Close']
    backtester = LongTermBacktester(monthly_budget=10000)
    detector = MarketRegimeDetector()
    regime_df = detector.detect_regime(prices)

    # Test each strategy
    simple = backtester.backtest_simple_sip(prices)
    smart = backtester.backtest_smart_sip(prices, regime_df)
    trend = backtester.backtest_trend_sip(prices)

    results = {
        'Simple SIP': simple,
        'Smart SIP': smart,
        'Trend SIP': trend
    }

    print(f"\nPeriod: {df.index[0].date()} to {df.index[-1].date()}")
    print(f"\n{'Strategy':<15} {'Invested':<12} {'Final Value':<12} {'Return':<10}")
    print("-"*50)

    for name, res in results.items():
        print(f"{name:<15} {res['total_invested']:<12,.0f} {res['final_value']:<12,.0f} {res['total_return_pct']:<10.2f}%")

        if log_results:
            log_result(
                strategy=name,
                symbol=symbol,
                period=f"{years}y monthly",
                total_return_pct=res['total_return_pct'],
                num_trades=len(res.get('investments', [])),
                notes=f"Monthly investment Rs 10,000 base"
            )

    return results


def run_all_backtests():
    """Run all backtests and generate comprehensive report."""
    print("\n" + "="*70)
    print("RUNNING ALL BACKTESTS")
    print("="*70 + "\n")

    # 1. ORB on top stocks
    print("\n[1/4] ORB Backtest...")
    orb_results = backtest_orb(symbols=NIFTY_50_SAMPLE[:10], period='60d')

    # 2. VWAP on top stocks
    print("\n[2/4] VWAP Backtest...")
    vwap_results = backtest_vwap(symbols=NIFTY_50_SAMPLE[:10], period='60d')

    # 3. MA Crossover
    print("\n[3/4] MA Crossover Backtest...")
    ma_results = backtest_ma_crossover(symbols=NIFTY_50_SAMPLE[:10], fast=5, slow=20, years=2)

    # 4. SIP Strategies
    print("\n[4/4] SIP Strategies Backtest...")
    sip_results = backtest_sip_strategies('RELIANCE.NS', years=5)

    # Final summary
    print("\n")
    print_summary()

    return {
        'orb': orb_results,
        'vwap': vwap_results,
        'ma': ma_results,
        'sip': sip_results
    }


if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1:
        cmd = sys.argv[1].lower()

        if cmd == 'orb':
            backtest_orb()
        elif cmd == 'vwap':
            backtest_vwap()
        elif cmd == 'ma':
            backtest_ma_crossover()
        elif cmd == 'sip':
            backtest_sip_strategies()
        elif cmd == 'all':
            run_all_backtests()
        elif cmd == 'summary':
            print_summary()
        elif cmd == 'recent':
            print_recent(20)
        else:
            print(f"Unknown command: {cmd}")
            print("Usage: python run_backtest.py [orb|vwap|ma|sip|all|summary|recent]")
    else:
        # Default: run ORB backtest
        print("Running ORB backtest (default)")
        print("Usage: python run_backtest.py [orb|vwap|ma|sip|all|summary|recent]\n")
        backtest_orb()
