"""
Long-term Smart Investment System

For monthly investing with optimized allocation:
1. Smart SIP - Invest more when market is down, less when overheated
2. Trend-based Allocation - Adjust based on market regime
3. Stock Selection - Pick best opportunities each month
4. Momentum + Value - Combine factors for selection

Risk tolerance: 5% per position
"""

import pandas as pd
import numpy as np
import yfinance as yf
from datetime import datetime, timedelta
from typing import Dict, List, Tuple, Optional
import warnings
warnings.filterwarnings('ignore')


class MarketRegimeDetector:
    """
    Detect market regime (Bull/Bear/Sideways) using multiple indicators.
    """

    def __init__(self):
        self.name = "Market Regime Detector"

    def detect_regime(self, prices: pd.Series) -> pd.DataFrame:
        """
        Classify market regime based on multiple factors.

        Returns DataFrame with regime classification.
        """
        df = pd.DataFrame(index=prices.index)
        df['Close'] = prices

        # Moving Averages
        df['SMA_50'] = prices.rolling(50).mean()
        df['SMA_200'] = prices.rolling(200).mean()

        # Price position
        df['Above_50'] = prices > df['SMA_50']
        df['Above_200'] = prices > df['SMA_200']
        df['MA_50_Above_200'] = df['SMA_50'] > df['SMA_200']

        # Momentum
        df['Return_20d'] = prices.pct_change(20)
        df['Return_60d'] = prices.pct_change(60)

        # Volatility
        df['Volatility'] = prices.pct_change().rolling(20).std() * np.sqrt(252)

        # RSI
        delta = prices.diff()
        gain = (delta.where(delta > 0, 0)).rolling(14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
        rs = gain / loss
        df['RSI'] = 100 - (100 / (1 + rs))

        # Regime classification
        conditions = [
            # Strong Bull: Above both MAs, positive momentum, RSI not extreme
            (df['Above_50'] & df['Above_200'] & df['MA_50_Above_200'] &
             (df['Return_20d'] > 0) & (df['RSI'] < 75)),

            # Bull: Above 200 SMA
            (df['Above_200'] & (df['RSI'] < 80)),

            # Bear: Below both MAs
            (~df['Above_50'] & ~df['Above_200']),

            # Strong Bear: Below both MAs, negative momentum
            (~df['Above_50'] & ~df['Above_200'] & (df['Return_20d'] < -0.05))
        ]

        choices = ['Strong_Bull', 'Bull', 'Bear', 'Strong_Bear']
        df['Regime'] = np.select(conditions, choices, default='Sideways')

        # Regime score: -2 (strong bear) to +2 (strong bull)
        regime_scores = {
            'Strong_Bear': -2,
            'Bear': -1,
            'Sideways': 0,
            'Bull': 1,
            'Strong_Bull': 2
        }
        df['Regime_Score'] = df['Regime'].map(regime_scores)

        return df


class SmartSIP:
    """
    Smart Systematic Investment Plan.

    Instead of fixed monthly investment:
    - Invest MORE when market is down (value opportunity)
    - Invest LESS when market is overheated (expensive)
    """

    def __init__(self, base_amount: float = 10000):
        self.base_amount = base_amount
        self.name = "Smart SIP"

    def calculate_allocation(self, regime_df: pd.DataFrame) -> pd.DataFrame:
        """
        Calculate how much to invest based on market regime.

        Multipliers:
        - Strong Bear: 2.0x (great buying opportunity)
        - Bear: 1.5x (good value)
        - Sideways: 1.0x (normal)
        - Bull: 0.75x (slightly expensive)
        - Strong Bull: 0.5x (very expensive, wait)
        """
        df = regime_df.copy()

        multipliers = {
            'Strong_Bear': 2.0,
            'Bear': 1.5,
            'Sideways': 1.0,
            'Bull': 0.75,
            'Strong_Bull': 0.5
        }

        df['Multiplier'] = df['Regime'].map(multipliers)
        df['Investment_Amount'] = self.base_amount * df['Multiplier']

        return df


class TrendBasedAllocation:
    """
    Allocate based on trend strength.

    Rules:
    - Strong uptrend: Full allocation
    - Weak uptrend: 75% allocation
    - Sideways: 50% allocation
    - Downtrend: 25% allocation (DCA opportunity)
    - Strong downtrend: Wait or minimal
    """

    def __init__(self, max_allocation: float = 1.0):
        self.max_allocation = max_allocation
        self.name = "Trend-Based Allocation"

    def calculate_allocation(self, regime_df: pd.DataFrame) -> pd.DataFrame:
        """Calculate allocation percentage based on trend."""
        df = regime_df.copy()

        allocations = {
            'Strong_Bull': 1.0,
            'Bull': 0.75,
            'Sideways': 0.50,
            'Bear': 0.25,
            'Strong_Bear': 0.10  # Keep some for extreme DCA
        }

        df['Allocation_Pct'] = df['Regime'].map(allocations) * self.max_allocation

        return df


class MomentumScorer:
    """
    Score stocks based on momentum factors.
    Higher score = stronger momentum.
    """

    def __init__(self):
        self.name = "Momentum Scorer"

    def score(self, prices: pd.Series) -> float:
        """
        Calculate momentum score (0-100).

        Factors:
        - 1-month return
        - 3-month return
        - 6-month return
        - Price vs 50 SMA
        - RSI (not too high)
        """
        if len(prices) < 130:  # Need ~6 months of data
            return 0

        scores = []

        # 1-month return (20 days)
        ret_1m = (prices.iloc[-1] / prices.iloc[-20] - 1) * 100
        score_1m = min(max(ret_1m + 10, 0), 30)  # -10% = 0, +20% = 30
        scores.append(score_1m)

        # 3-month return (60 days)
        ret_3m = (prices.iloc[-1] / prices.iloc[-60] - 1) * 100
        score_3m = min(max(ret_3m / 2 + 15, 0), 25)
        scores.append(score_3m)

        # 6-month return (130 days)
        ret_6m = (prices.iloc[-1] / prices.iloc[-130] - 1) * 100
        score_6m = min(max(ret_6m / 3 + 15, 0), 25)
        scores.append(score_6m)

        # Price vs 50 SMA
        sma_50 = prices.rolling(50).mean().iloc[-1]
        above_sma = (prices.iloc[-1] / sma_50 - 1) * 100
        score_sma = min(max(above_sma + 5, 0), 10)
        scores.append(score_sma)

        # RSI penalty (avoid overbought)
        delta = prices.diff()
        gain = (delta.where(delta > 0, 0)).rolling(14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
        rs = gain / loss
        rsi = (100 - (100 / (1 + rs))).iloc[-1]

        if rsi > 80:
            rsi_score = 0  # Overbought penalty
        elif rsi > 70:
            rsi_score = 5
        elif rsi < 30:
            rsi_score = 5  # Oversold could be opportunity
        else:
            rsi_score = 10
        scores.append(rsi_score)

        return sum(scores)


class ValueScorer:
    """
    Score stocks based on value/mean reversion factors.
    Higher score = better value (more oversold).
    """

    def __init__(self):
        self.name = "Value Scorer"

    def score(self, prices: pd.Series) -> float:
        """
        Calculate value score (0-100).

        Factors:
        - Distance from 52-week high (lower = better value)
        - RSI (lower = oversold = better value)
        - Price vs 200 SMA
        """
        if len(prices) < 252:
            return 50  # Default neutral

        scores = []

        # Distance from 52-week high
        high_52w = prices.rolling(252).max().iloc[-1]
        dist_from_high = (1 - prices.iloc[-1] / high_52w) * 100
        # 0% from high = 0 points, 30%+ from high = 40 points
        score_high = min(dist_from_high * 1.33, 40)
        scores.append(score_high)

        # RSI value (lower = oversold = more value)
        delta = prices.diff()
        gain = (delta.where(delta > 0, 0)).rolling(14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
        rs = gain / loss
        rsi = (100 - (100 / (1 + rs))).iloc[-1]

        # RSI < 30 = 30 points, RSI > 70 = 0 points
        score_rsi = max(0, (70 - rsi) / 40 * 30)
        scores.append(score_rsi)

        # Price vs 200 SMA
        sma_200 = prices.rolling(200).mean().iloc[-1]
        below_sma = (1 - prices.iloc[-1] / sma_200) * 100
        # 10% below = 30 points, 10% above = 0 points
        score_sma = min(max(below_sma * 3, 0), 30)
        scores.append(score_sma)

        return sum(scores)


class StockRanker:
    """
    Rank stocks for monthly selection.
    Combines momentum and value scores.
    """

    def __init__(self, momentum_weight: float = 0.6, value_weight: float = 0.4):
        self.momentum_weight = momentum_weight
        self.value_weight = value_weight
        self.momentum_scorer = MomentumScorer()
        self.value_scorer = ValueScorer()
        self.name = "Stock Ranker"

    def rank_stocks(self, stock_data: Dict[str, pd.DataFrame]) -> pd.DataFrame:
        """
        Rank all stocks by combined score.

        Args:
            stock_data: Dict of symbol -> price DataFrame

        Returns:
            DataFrame with rankings
        """
        rankings = []

        for symbol, data in stock_data.items():
            if data.empty or len(data) < 252:
                continue

            prices = data['Close']

            momentum_score = self.momentum_scorer.score(prices)
            value_score = self.value_scorer.score(prices)

            combined_score = (momentum_score * self.momentum_weight +
                            value_score * self.value_weight)

            rankings.append({
                'Symbol': symbol,
                'Momentum_Score': momentum_score,
                'Value_Score': value_score,
                'Combined_Score': combined_score,
                'Current_Price': prices.iloc[-1],
                'Return_1M': (prices.iloc[-1] / prices.iloc[-20] - 1) * 100 if len(prices) >= 20 else 0,
                'Return_3M': (prices.iloc[-1] / prices.iloc[-60] - 1) * 100 if len(prices) >= 60 else 0,
            })

        df = pd.DataFrame(rankings)
        df = df.sort_values('Combined_Score', ascending=False)
        df['Rank'] = range(1, len(df) + 1)

        return df


class LongTermBacktester:
    """
    Backtest long-term monthly investment strategies.
    """

    def __init__(self, monthly_budget: float = 10000):
        self.monthly_budget = monthly_budget
        self.name = "Long-term Backtester"

    def backtest_simple_sip(self, prices: pd.Series, start_date: str = None) -> Dict:
        """
        Simple SIP: Fixed amount every month.
        """
        # Resample to monthly
        monthly = prices.resample('ME').last()

        if start_date:
            monthly = monthly[monthly.index >= start_date]

        total_invested = 0
        total_units = 0
        investments = []

        for date, price in monthly.items():
            units = self.monthly_budget / price
            total_units += units
            total_invested += self.monthly_budget

            investments.append({
                'date': date,
                'price': price,
                'amount': self.monthly_budget,
                'units': units,
                'total_units': total_units,
                'total_invested': total_invested,
                'portfolio_value': total_units * price
            })

        df = pd.DataFrame(investments)
        final_value = total_units * monthly.iloc[-1]

        return {
            'name': 'Simple SIP',
            'total_invested': total_invested,
            'final_value': final_value,
            'total_return_pct': (final_value / total_invested - 1) * 100,
            'total_units': total_units,
            'avg_cost': total_invested / total_units,
            'investments': df
        }

    def backtest_smart_sip(self, prices: pd.Series, regime_df: pd.DataFrame = None) -> Dict:
        """
        Smart SIP: Adjust amount based on market conditions.
        """
        # Detect regime if not provided
        if regime_df is None:
            detector = MarketRegimeDetector()
            regime_df = detector.detect_regime(prices)

        smart_sip = SmartSIP(self.monthly_budget)
        allocation_df = smart_sip.calculate_allocation(regime_df)

        # Resample to monthly
        monthly_prices = prices.resample('ME').last()
        monthly_regime = allocation_df['Regime'].resample('ME').last()
        monthly_multiplier = allocation_df['Multiplier'].resample('ME').last()

        total_invested = 0
        total_units = 0
        investments = []

        for date in monthly_prices.index:
            if date not in monthly_multiplier.index:
                continue

            price = monthly_prices[date]
            multiplier = monthly_multiplier[date]
            amount = self.monthly_budget * multiplier
            units = amount / price

            total_units += units
            total_invested += amount

            investments.append({
                'date': date,
                'price': price,
                'regime': monthly_regime.get(date, 'Unknown'),
                'multiplier': multiplier,
                'amount': amount,
                'units': units,
                'total_units': total_units,
                'total_invested': total_invested,
                'portfolio_value': total_units * price
            })

        df = pd.DataFrame(investments)
        final_value = total_units * monthly_prices.iloc[-1] if total_units > 0 else 0

        return {
            'name': 'Smart SIP',
            'total_invested': total_invested,
            'final_value': final_value,
            'total_return_pct': (final_value / total_invested - 1) * 100 if total_invested > 0 else 0,
            'total_units': total_units,
            'avg_cost': total_invested / total_units if total_units > 0 else 0,
            'investments': df
        }

    def backtest_trend_sip(self, prices: pd.Series) -> Dict:
        """
        Trend-based SIP: Only invest in uptrends.
        """
        detector = MarketRegimeDetector()
        regime_df = detector.detect_regime(prices)

        trend_alloc = TrendBasedAllocation()
        allocation_df = trend_alloc.calculate_allocation(regime_df)

        monthly_prices = prices.resample('ME').last()
        monthly_alloc = allocation_df['Allocation_Pct'].resample('ME').last()

        total_invested = 0
        total_units = 0
        cash_reserve = 0  # Money not invested
        investments = []

        for date in monthly_prices.index:
            if date not in monthly_alloc.index:
                continue

            price = monthly_prices[date]
            alloc_pct = monthly_alloc[date]
            amount = self.monthly_budget * alloc_pct
            cash_reserve += self.monthly_budget * (1 - alloc_pct)

            units = amount / price
            total_units += units
            total_invested += amount

            investments.append({
                'date': date,
                'price': price,
                'allocation_pct': alloc_pct * 100,
                'amount': amount,
                'units': units,
                'total_units': total_units,
                'total_invested': total_invested,
                'cash_reserve': cash_reserve,
                'portfolio_value': total_units * price + cash_reserve
            })

        df = pd.DataFrame(investments)
        final_value = total_units * monthly_prices.iloc[-1] + cash_reserve if total_units > 0 else cash_reserve

        return {
            'name': 'Trend-based SIP',
            'total_invested': total_invested,
            'cash_reserve': cash_reserve,
            'final_value': final_value,
            'total_return_pct': (final_value / (total_invested + cash_reserve) - 1) * 100 if (total_invested + cash_reserve) > 0 else 0,
            'total_units': total_units,
            'investments': df
        }


def download_stock_data(symbol: str, years: int = 5) -> pd.DataFrame:
    """Download historical stock data."""
    end_date = datetime.now()
    start_date = end_date - timedelta(days=years * 365)

    print(f"Downloading {symbol}...")
    data = yf.download(symbol, start=start_date, end=end_date, progress=False)

    if isinstance(data.columns, pd.MultiIndex):
        data.columns = data.columns.get_level_values(0)

    return data


def run_longterm_backtest(symbol: str = 'RELIANCE.NS'):
    """Run long-term investment strategy comparison."""

    print("="*60)
    print(f"LONG-TERM INVESTMENT SYSTEM - {symbol}")
    print("="*60)

    # Download data
    data = download_stock_data(symbol, years=5)

    if data.empty:
        print("Error: No data downloaded")
        return None

    prices = data['Close']
    print(f"\nData range: {data.index[0].date()} to {data.index[-1].date()}")
    print(f"Starting price: {prices.iloc[0]:.2f}")
    print(f"Ending price: {prices.iloc[-1]:.2f}")
    print(f"Buy & Hold return: {(prices.iloc[-1]/prices.iloc[0]-1)*100:.2f}%")

    # Detect market regime
    detector = MarketRegimeDetector()
    regime_df = detector.detect_regime(prices)

    print("\nMarket Regime Distribution:")
    regime_counts = regime_df['Regime'].value_counts()
    for regime, count in regime_counts.items():
        print(f"  {regime}: {count} days ({count/len(regime_df)*100:.1f}%)")

    # Run backtests
    backtester = LongTermBacktester(monthly_budget=10000)

    simple_result = backtester.backtest_simple_sip(prices)
    smart_result = backtester.backtest_smart_sip(prices, regime_df)
    trend_result = backtester.backtest_trend_sip(prices)

    results = {
        'Simple SIP': simple_result,
        'Smart SIP': smart_result,
        'Trend SIP': trend_result
    }

    # Print results
    print("\n" + "="*60)
    print("SIP STRATEGY COMPARISON")
    print("="*60)

    for name, res in results.items():
        print(f"\n{name}:")
        print(f"  Total Invested: Rs {res['total_invested']:,.0f}")
        print(f"  Final Value: Rs {res['final_value']:,.0f}")
        print(f"  Total Return: {res['total_return_pct']:.2f}%")
        if 'avg_cost' in res and res['avg_cost'] > 0:
            print(f"  Avg Cost/Unit: Rs {res['avg_cost']:.2f}")
        if 'cash_reserve' in res:
            print(f"  Cash Reserve: Rs {res['cash_reserve']:,.0f}")

    # Summary comparison
    print("\n" + "="*60)
    print("SUMMARY")
    print("="*60)
    print(f"{'Strategy':<20} {'Invested':<15} {'Final Value':<15} {'Return':<10}")
    print("-"*60)
    for name, res in results.items():
        print(f"{name:<20} {res['total_invested']:<15,.0f} {res['final_value']:<15,.0f} {res['total_return_pct']:<10.2f}%")

    return results


def run_stock_selection(symbols: List[str] = None):
    """Run stock selection/ranking for monthly investment."""

    if symbols is None:
        # Use a subset of Nifty 100
        symbols = [
            'RELIANCE.NS', 'TCS.NS', 'HDFCBANK.NS', 'INFY.NS', 'ICICIBANK.NS',
            'HINDUNILVR.NS', 'SBIN.NS', 'BHARTIARTL.NS', 'ITC.NS', 'KOTAKBANK.NS',
            'LT.NS', 'AXISBANK.NS', 'ASIANPAINT.NS', 'MARUTI.NS', 'SUNPHARMA.NS',
            'TITAN.NS', 'BAJFINANCE.NS', 'WIPRO.NS', 'HCLTECH.NS', 'ULTRACEMCO.NS'
        ]

    print("="*60)
    print("STOCK SELECTION FOR MONTHLY INVESTMENT")
    print("="*60)
    print(f"\nAnalyzing {len(symbols)} stocks...")

    # Download data
    stock_data = {}
    for symbol in symbols:
        try:
            data = download_stock_data(symbol, years=2)
            if not data.empty:
                stock_data[symbol] = data
        except Exception as e:
            print(f"  Error with {symbol}: {e}")

    print(f"\nSuccessfully loaded: {len(stock_data)} stocks")

    # Rank stocks
    ranker = StockRanker(momentum_weight=0.6, value_weight=0.4)
    rankings = ranker.rank_stocks(stock_data)

    # Display rankings
    print("\n" + "="*60)
    print("STOCK RANKINGS (Best to Worst)")
    print("="*60)
    print(f"{'Rank':<6} {'Symbol':<15} {'Combined':<10} {'Momentum':<10} {'Value':<10} {'1M Ret':<10} {'3M Ret':<10}")
    print("-"*75)

    for _, row in rankings.head(20).iterrows():
        print(f"{row['Rank']:<6} {row['Symbol']:<15} {row['Combined_Score']:<10.1f} {row['Momentum_Score']:<10.1f} {row['Value_Score']:<10.1f} {row['Return_1M']:<10.1f}% {row['Return_3M']:<10.1f}%")

    # Recommendations
    print("\n" + "="*60)
    print("RECOMMENDATIONS FOR THIS MONTH")
    print("="*60)

    top_5 = rankings.head(5)
    print("\nTOP 5 PICKS (Momentum + Value):")
    for _, row in top_5.iterrows():
        print(f"  {row['Rank']}. {row['Symbol']} - Score: {row['Combined_Score']:.1f}")

    # Value picks (high value score)
    value_picks = rankings.nlargest(5, 'Value_Score')
    print("\nTOP VALUE PICKS (Oversold/Undervalued):")
    for _, row in value_picks.iterrows():
        print(f"  - {row['Symbol']} - Value Score: {row['Value_Score']:.1f}")

    # Momentum picks
    momentum_picks = rankings.nlargest(5, 'Momentum_Score')
    print("\nTOP MOMENTUM PICKS (Strong Trend):")
    for _, row in momentum_picks.iterrows():
        print(f"  - {row['Symbol']} - Momentum Score: {row['Momentum_Score']:.1f}")

    return rankings


if __name__ == "__main__":
    # Run SIP comparison
    print("\n" + "="*70)
    print("PART 1: SIP STRATEGY COMPARISON")
    print("="*70)
    sip_results = run_longterm_backtest('RELIANCE.NS')

    # Run stock selection
    print("\n" + "="*70)
    print("PART 2: STOCK SELECTION")
    print("="*70)
    rankings = run_stock_selection()
