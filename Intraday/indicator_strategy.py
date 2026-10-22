"""
Indicator-Based Trading Strategies.

Implements rule-based strategies using technical indicators:
1. Moving Average Crossover
2. RSI Mean Reversion
3. Bollinger Band Breakout
4. Combined Multi-Indicator Strategy
"""

import pandas as pd
import numpy as np
from typing import Tuple, Optional
import yfinance as yf
from datetime import datetime
import warnings
warnings.filterwarnings('ignore')

from backtest_utils import Backtester, calculate_buy_and_hold, plot_backtest_results, print_results_summary


class TechnicalIndicators:
    """Calculate common technical indicators."""

    @staticmethod
    def sma(prices: pd.Series, window: int) -> pd.Series:
        """Simple Moving Average."""
        return prices.rolling(window=window).mean()

    @staticmethod
    def ema(prices: pd.Series, window: int) -> pd.Series:
        """Exponential Moving Average."""
        return prices.ewm(span=window, adjust=False).mean()

    @staticmethod
    def rsi(prices: pd.Series, window: int = 14) -> pd.Series:
        """Relative Strength Index."""
        delta = prices.diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=window).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=window).mean()
        rs = gain / loss
        return 100 - (100 / (1 + rs))

    @staticmethod
    def bollinger_bands(prices: pd.Series, window: int = 20, num_std: float = 2.0) -> Tuple[pd.Series, pd.Series, pd.Series]:
        """Bollinger Bands: returns (middle, upper, lower)."""
        middle = prices.rolling(window=window).mean()
        std = prices.rolling(window=window).std()
        upper = middle + (std * num_std)
        lower = middle - (std * num_std)
        return middle, upper, lower

    @staticmethod
    def macd(prices: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9) -> Tuple[pd.Series, pd.Series, pd.Series]:
        """MACD: returns (macd_line, signal_line, histogram)."""
        fast_ema = prices.ewm(span=fast, adjust=False).mean()
        slow_ema = prices.ewm(span=slow, adjust=False).mean()
        macd_line = fast_ema - slow_ema
        signal_line = macd_line.ewm(span=signal, adjust=False).mean()
        histogram = macd_line - signal_line
        return macd_line, signal_line, histogram

    @staticmethod
    def atr(high: pd.Series, low: pd.Series, close: pd.Series, window: int = 14) -> pd.Series:
        """Average True Range."""
        tr1 = high - low
        tr2 = abs(high - close.shift())
        tr3 = abs(low - close.shift())
        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        return tr.rolling(window=window).mean()


class MACrossoverStrategy:
    """
    Moving Average Crossover Strategy.

    Buy when fast MA crosses above slow MA.
    Sell when fast MA crosses below slow MA.
    """

    def __init__(self, fast_period: int = 5, slow_period: int = 20):
        self.fast_period = fast_period
        self.slow_period = slow_period
        self.name = f"MA Crossover ({fast_period}/{slow_period})"

    def generate_signals(self, prices: pd.Series) -> pd.Series:
        """Generate buy/sell signals."""
        fast_ma = TechnicalIndicators.sma(prices, self.fast_period)
        slow_ma = TechnicalIndicators.sma(prices, self.slow_period)

        signals = pd.Series(0, index=prices.index)

        # Buy signal: fast MA crosses above slow MA
        signals[(fast_ma > slow_ma) & (fast_ma.shift(1) <= slow_ma.shift(1))] = 1

        # Sell signal: fast MA crosses below slow MA
        signals[(fast_ma < slow_ma) & (fast_ma.shift(1) >= slow_ma.shift(1))] = -1

        return signals


class RSIMeanReversionStrategy:
    """
    RSI Mean Reversion Strategy.

    Buy when RSI goes below oversold level (30).
    Sell when RSI goes above overbought level (70).
    """

    def __init__(self, rsi_period: int = 14, oversold: int = 30, overbought: int = 70):
        self.rsi_period = rsi_period
        self.oversold = oversold
        self.overbought = overbought
        self.name = f"RSI Mean Reversion ({oversold}/{overbought})"

    def generate_signals(self, prices: pd.Series) -> pd.Series:
        """Generate buy/sell signals."""
        rsi = TechnicalIndicators.rsi(prices, self.rsi_period)

        signals = pd.Series(0, index=prices.index)

        # Buy signal: RSI crosses above oversold from below
        signals[(rsi > self.oversold) & (rsi.shift(1) <= self.oversold)] = 1

        # Sell signal: RSI crosses below overbought from above
        signals[(rsi < self.overbought) & (rsi.shift(1) >= self.overbought)] = -1

        return signals


class BollingerBandStrategy:
    """
    Bollinger Band Breakout Strategy.

    Buy when price touches/breaks lower band (mean reversion expected).
    Sell when price touches/breaks upper band.
    """

    def __init__(self, window: int = 20, num_std: float = 2.0):
        self.window = window
        self.num_std = num_std
        self.name = f"Bollinger Bands ({window}, {num_std}std)"

    def generate_signals(self, prices: pd.Series) -> pd.Series:
        """Generate buy/sell signals."""
        middle, upper, lower = TechnicalIndicators.bollinger_bands(prices, self.window, self.num_std)

        signals = pd.Series(0, index=prices.index)

        # Buy signal: price crosses above lower band from below
        signals[(prices > lower) & (prices.shift(1) <= lower.shift(1))] = 1

        # Sell signal: price crosses below upper band from above
        signals[(prices < upper) & (prices.shift(1) >= upper.shift(1))] = -1

        return signals


class CombinedStrategy:
    """
    Combined Multi-Indicator Strategy.

    Requires multiple indicators to agree before trading.
    More conservative but potentially more reliable.
    """

    def __init__(self,
                 ma_fast: int = 10,
                 ma_slow: int = 30,
                 rsi_period: int = 14,
                 rsi_oversold: int = 35,
                 rsi_overbought: int = 65):
        self.ma_fast = ma_fast
        self.ma_slow = ma_slow
        self.rsi_period = rsi_period
        self.rsi_oversold = rsi_oversold
        self.rsi_overbought = rsi_overbought
        self.name = f"Combined (MA {ma_fast}/{ma_slow} + RSI)"

    def generate_signals(self, prices: pd.Series) -> pd.Series:
        """Generate buy/sell signals - requires confirmation from multiple indicators."""
        fast_ma = TechnicalIndicators.sma(prices, self.ma_fast)
        slow_ma = TechnicalIndicators.sma(prices, self.ma_slow)
        rsi = TechnicalIndicators.rsi(prices, self.rsi_period)

        signals = pd.Series(0, index=prices.index)

        # Buy: MA bullish crossover AND RSI not overbought
        ma_bullish = (fast_ma > slow_ma) & (fast_ma.shift(1) <= slow_ma.shift(1))
        rsi_ok_buy = rsi < self.rsi_overbought
        signals[ma_bullish & rsi_ok_buy] = 1

        # Sell: MA bearish crossover OR RSI overbought
        ma_bearish = (fast_ma < slow_ma) & (fast_ma.shift(1) >= slow_ma.shift(1))
        rsi_overbought_signal = (rsi < self.rsi_overbought) & (rsi.shift(1) >= self.rsi_overbought)
        signals[ma_bearish | rsi_overbought_signal] = -1

        return signals


def download_stock_data(symbol: str, start_date: str, end_date: str) -> pd.DataFrame:
    """Download stock data from Yahoo Finance."""
    print(f"Downloading {symbol}...")
    data = yf.download(symbol, start=start_date, end=end_date, progress=False)

    # Flatten multi-level columns if present
    if isinstance(data.columns, pd.MultiIndex):
        data.columns = data.columns.get_level_values(0)

    return data


def run_strategy_comparison(symbol: str = 'RELIANCE.NS'):
    """Run and compare all strategies on a single stock."""

    # Download data
    print("="*60)
    print(f"STRATEGY COMPARISON FOR {symbol}")
    print("="*60)

    # Training period: 2020 to Nov 2025
    train_data = download_stock_data(symbol, '2020-01-01', '2025-11-30')

    # Test period: Dec 2025 to Jan 2026
    test_data = download_stock_data(symbol, '2025-12-01', '2026-01-31')

    if train_data.empty or test_data.empty:
        print(f"Error: Could not download data for {symbol}")
        return None

    print(f"\nTraining period: {train_data.index[0].date()} to {train_data.index[-1].date()} ({len(train_data)} days)")
    print(f"Test period: {test_data.index[0].date()} to {test_data.index[-1].date()} ({len(test_data)} days)")

    # Initialize strategies
    strategies = [
        MACrossoverStrategy(fast_period=5, slow_period=20),
        MACrossoverStrategy(fast_period=10, slow_period=50),
        RSIMeanReversionStrategy(rsi_period=14, oversold=30, overbought=70),
        BollingerBandStrategy(window=20, num_std=2.0),
        CombinedStrategy(ma_fast=10, ma_slow=30, rsi_period=14),
    ]

    # Backtest each strategy
    backtester = Backtester(initial_capital=100000, transaction_cost=0.001)
    results = {}

    print("\n" + "="*60)
    print("TRAINING PERIOD RESULTS (2020 - Nov 2025)")
    print("="*60)

    # Buy and hold benchmark
    train_benchmark = calculate_buy_and_hold(train_data['Close'])
    print_results_summary(train_benchmark, "Buy & Hold (Benchmark)")

    for strategy in strategies:
        signals = strategy.generate_signals(train_data['Close'])
        result = backtester.run(train_data['Close'], signals)
        results[strategy.name] = {'train': result, 'strategy': strategy}
        print_results_summary(result, strategy.name)

    # Test on out-of-sample data
    print("\n" + "="*60)
    print("TEST PERIOD RESULTS (Dec 2025 - Jan 2026) - OUT OF SAMPLE")
    print("="*60)

    test_benchmark = calculate_buy_and_hold(test_data['Close'])
    print_results_summary(test_benchmark, "Buy & Hold (Benchmark)")

    for strategy in strategies:
        signals = strategy.generate_signals(test_data['Close'])
        result = backtester.run(test_data['Close'], signals)
        results[strategy.name]['test'] = result
        print_results_summary(result, strategy.name)

    # Summary comparison
    print("\n" + "="*70)
    print("SUMMARY COMPARISON")
    print("="*70)
    print(f"{'Strategy':<35} {'Train Return':<15} {'Test Return':<15} {'Test Sharpe':<12}")
    print("-"*70)
    print(f"{'Buy & Hold':<35} {train_benchmark['total_return_pct']:<15.2f} {test_benchmark['total_return_pct']:<15.2f} {test_benchmark['sharpe_ratio']:<12.2f}")

    for name, res in results.items():
        train_ret = res['train']['total_return_pct']
        test_ret = res['test']['total_return_pct']
        test_sharpe = res['test']['sharpe_ratio']
        print(f"{name:<35} {train_ret:<15.2f} {test_ret:<15.2f} {test_sharpe:<12.2f}")

    return results, train_benchmark, test_benchmark


def run_portfolio_backtest(symbols: list, strategy_class, **strategy_params):
    """
    Run strategy across multiple stocks and aggregate results.
    """
    print(f"\nRunning {strategy_class.__name__} on {len(symbols)} stocks...")

    all_returns = []

    for symbol in symbols:
        try:
            # Download data
            train_data = download_stock_data(symbol, '2020-01-01', '2025-11-30')
            test_data = download_stock_data(symbol, '2025-12-01', '2026-01-31')

            if train_data.empty or len(test_data) < 5:
                continue

            # Generate signals and backtest
            strategy = strategy_class(**strategy_params)
            backtester = Backtester(initial_capital=100000, transaction_cost=0.001)

            signals = strategy.generate_signals(test_data['Close'])
            result = backtester.run(test_data['Close'], signals)

            all_returns.append({
                'symbol': symbol,
                'return': result['total_return_pct'],
                'sharpe': result['sharpe_ratio'],
                'trades': result['num_trades']
            })

        except Exception as e:
            print(f"  Error with {symbol}: {e}")
            continue

    if all_returns:
        df = pd.DataFrame(all_returns)
        print(f"\nPortfolio Results ({len(df)} stocks):")
        print(f"  Average Return: {df['return'].mean():.2f}%")
        print(f"  Median Return: {df['return'].median():.2f}%")
        print(f"  Average Sharpe: {df['sharpe'].mean():.2f}")
        print(f"  Win Rate (stocks): {(df['return'] > 0).mean():.1%}")
        return df

    return None


if __name__ == "__main__":
    # Run comparison on a single stock first
    results, train_bm, test_bm = run_strategy_comparison('RELIANCE.NS')

    # Plot best performing strategy
    if results:
        best_strategy = max(results.keys(), key=lambda x: results[x]['test']['total_return_pct'])
        print(f"\nBest strategy: {best_strategy}")

        # Plot the results
        plot_backtest_results(
            results[best_strategy]['test'],
            test_bm,
            title=f"{best_strategy} vs Buy & Hold (Test Period)"
        )
