"""
Smart SIP v2 - Improved Systematic Investment with Better Returns

Key Improvements over v1:
1. XIRR calculation for true annualized returns
2. Aggressive buying during market crashes (VIX/drawdown based)
3. Multi-stock portfolio diversification
4. Weekly investment option for better DCA
5. Momentum rotation - shift to winning stocks

Target: Beat simple SIP by 3-5% annually
"""

import pandas as pd
import numpy as np
import yfinance as yf
from datetime import datetime, timedelta
from typing import Dict, List, Tuple, Optional
from scipy import optimize
import warnings
warnings.filterwarnings('ignore')


def calculate_xirr(cashflows: List[Tuple[datetime, float]], guess: float = 0.1) -> float:
    """
    Calculate XIRR (Extended Internal Rate of Return).
    
    Args:
        cashflows: List of (date, amount) tuples. Negative = investment, Positive = return
        guess: Initial guess for rate
        
    Returns:
        Annualized return rate
    """
    if len(cashflows) < 2:
        return 0.0
    
    # Convert dates to years from first date
    dates = [cf[0] for cf in cashflows]
    amounts = [cf[1] for cf in cashflows]
    
    min_date = min(dates)
    years = [(d - min_date).days / 365.0 for d in dates]
    
    def npv(rate):
        return sum([amt / ((1 + rate) ** yr) for amt, yr in zip(amounts, years)])
    
    try:
        result = optimize.newton(npv, guess, maxiter=100)
        return result
    except:
        try:
            result = optimize.brentq(npv, -0.99, 10.0)
            return result
        except:
            return 0.0


class ImprovedSmartSIP:
    """
    Smart SIP v2 with aggressive crash buying and momentum rotation.
    """
    
    def __init__(self, base_monthly: float = 10000, 
                 frequency: str = 'monthly',
                 crash_multiplier: float = 3.0,
                 max_single_stock: float = 0.25):
        """
        Args:
            base_monthly: Base monthly investment amount
            frequency: 'monthly' or 'weekly'
            crash_multiplier: Max multiplier during crashes (e.g., 3x)
            max_single_stock: Max allocation to single stock (0.25 = 25%)
        """
        self.base_monthly = base_monthly
        self.frequency = frequency
        self.crash_multiplier = crash_multiplier
        self.max_single_stock = max_single_stock
        
    def calculate_crash_score(self, prices: pd.Series, date: pd.Timestamp) -> float:
        """
        Calculate crash/opportunity score (0 to 1, higher = better buying opportunity).
        
        Uses multiple factors:
        1. Drawdown from recent high
        2. Distance below 200 SMA
        3. RSI (oversold = opportunity)
        4. Volatility spike
        """
        # Get data up to this date
        hist = prices[:date]
        if len(hist) < 200:
            return 0.5  # Neutral
        
        current_price = hist.iloc[-1]
        
        # 1. Drawdown from 52-week high (max 0.3)
        high_52w = hist.tail(252).max()
        drawdown = (high_52w - current_price) / high_52w
        drawdown_score = min(drawdown / 0.30, 1.0) * 0.3  # 30% drawdown = max score
        
        # 2. Distance below 200 SMA (max 0.25)
        sma_200 = hist.tail(200).mean()
        below_sma = (sma_200 - current_price) / sma_200
        sma_score = min(max(below_sma / 0.15, 0), 1.0) * 0.25
        
        # 3. RSI oversold (max 0.25)
        delta = hist.diff()
        gain = delta.where(delta > 0, 0).rolling(14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
        rs = gain / loss
        rsi = (100 - (100 / (1 + rs))).iloc[-1]
        rsi_score = max(0, (40 - rsi) / 40) * 0.25  # RSI < 40 = opportunity
        
        # 4. Volatility spike (max 0.2)
        vol_20 = hist.pct_change().tail(20).std() * np.sqrt(252)
        vol_60 = hist.pct_change().tail(60).std() * np.sqrt(252)
        vol_ratio = vol_20 / vol_60 if vol_60 > 0 else 1.0
        vol_score = min(max(vol_ratio - 1, 0) / 0.5, 1.0) * 0.2
        
        total_score = drawdown_score + sma_score + rsi_score + vol_score
        return min(total_score, 1.0)
    
    def calculate_investment_multiplier(self, crash_score: float) -> float:
        """
        Convert crash score to investment multiplier.
        
        crash_score=0: Normal market, invest 1x
        crash_score=0.5: Mild opportunity, invest 1.5x
        crash_score=1.0: Major crash, invest 3x (crash_multiplier)
        """
        # Exponential scaling for aggressive buying during crashes
        multiplier = 1.0 + (self.crash_multiplier - 1.0) * (crash_score ** 1.5)
        return multiplier
    
    def calculate_momentum_score(self, prices: pd.Series, date: pd.Timestamp) -> float:
        """
        Calculate momentum score for stock rotation.
        Returns score from 0-100.
        """
        hist = prices[:date]
        if len(hist) < 130:
            return 50
        
        # Returns at different timeframes
        ret_1m = (hist.iloc[-1] / hist.iloc[-20] - 1) if len(hist) >= 20 else 0
        ret_3m = (hist.iloc[-1] / hist.iloc[-60] - 1) if len(hist) >= 60 else 0
        ret_6m = (hist.iloc[-1] / hist.iloc[-130] - 1) if len(hist) >= 130 else 0
        
        # Weighted momentum (recent matters more)
        momentum = ret_1m * 0.5 + ret_3m * 0.3 + ret_6m * 0.2
        
        # Scale to 0-100
        score = 50 + momentum * 200  # +25% return = 100, -25% = 0
        return max(0, min(100, score))


class MultiStockSIPBacktester:
    """
    Backtest Smart SIP across multiple stocks with rotation.
    """
    
    def __init__(self, monthly_budget: float = 50000):
        self.monthly_budget = monthly_budget
        self.smart_sip = ImprovedSmartSIP(base_monthly=monthly_budget)
        
    def download_data(self, symbols: List[str], years: int = 5) -> Dict[str, pd.DataFrame]:
        """Download historical data for all symbols."""
        end_date = datetime.now()
        start_date = end_date - timedelta(days=years * 365 + 30)
        
        data = {}
        for symbol in symbols:
            print(f"  Downloading {symbol}...")
            try:
                df = yf.download(symbol, start=start_date, end=end_date, progress=False)
                if isinstance(df.columns, pd.MultiIndex):
                    df.columns = df.columns.get_level_values(0)
                if not df.empty and len(df) > 252:
                    data[symbol] = df
            except Exception as e:
                print(f"    Error: {e}")
        
        return data
    
    def backtest_simple_sip(self, stock_data: Dict[str, pd.DataFrame], 
                            top_n: int = 5) -> Dict:
        """
        Simple SIP: Equal allocation to top N stocks monthly.
        """
        # Get common date range
        all_dates = None
        for symbol, df in stock_data.items():
            if all_dates is None:
                all_dates = set(df.index)
            else:
                all_dates &= set(df.index)
        
        common_dates = sorted(list(all_dates))
        
        # Monthly investment dates
        monthly_dates = pd.DatetimeIndex(common_dates).to_series().resample('ME').last().dropna()
        
        # Track investments
        portfolio = {symbol: 0.0 for symbol in stock_data.keys()}  # units held
        cashflows = []  # For XIRR
        total_invested = 0
        investment_per_stock = self.monthly_budget / min(top_n, len(stock_data))
        
        history = []
        
        for date in monthly_dates.values:
            date = pd.Timestamp(date)
            
            # Simple allocation: equal to all stocks
            for symbol in list(stock_data.keys())[:top_n]:
                price = stock_data[symbol].loc[:date, 'Close'].iloc[-1]
                units = investment_per_stock / price
                portfolio[symbol] += units
            
            total_invested += self.monthly_budget
            cashflows.append((date.to_pydatetime(), -self.monthly_budget))
            
            # Calculate portfolio value
            portfolio_value = sum(
                portfolio[s] * stock_data[s].loc[:date, 'Close'].iloc[-1]
                for s in portfolio.keys()
            )
            
            history.append({
                'date': date,
                'invested': total_invested,
                'portfolio_value': portfolio_value,
                'return_pct': (portfolio_value / total_invested - 1) * 100
            })
        
        # Final value
        final_date = monthly_dates.values[-1]
        final_value = sum(
            portfolio[s] * stock_data[s].loc[:pd.Timestamp(final_date), 'Close'].iloc[-1]
            for s in portfolio.keys()
        )
        
        # Add final value to cashflows
        cashflows.append((pd.Timestamp(final_date).to_pydatetime(), final_value))
        
        # Calculate XIRR
        xirr = calculate_xirr(cashflows)
        
        return {
            'name': 'Simple SIP',
            'total_invested': total_invested,
            'final_value': final_value,
            'total_return_pct': (final_value / total_invested - 1) * 100,
            'xirr': xirr * 100,  # As percentage
            'history': pd.DataFrame(history),
            'months': len(monthly_dates)
        }
    
    def backtest_smart_sip_v2(self, stock_data: Dict[str, pd.DataFrame],
                              top_n: int = 5) -> Dict:
        """
        Smart SIP v2: Aggressive crash buying + momentum rotation.
        """
        # Get common date range
        all_dates = None
        for symbol, df in stock_data.items():
            if all_dates is None:
                all_dates = set(df.index)
            else:
                all_dates &= set(df.index)
        
        common_dates = sorted(list(all_dates))
        
        # Monthly investment dates
        monthly_dates = pd.DatetimeIndex(common_dates).to_series().resample('ME').last().dropna()
        
        # Track investments
        portfolio = {symbol: 0.0 for symbol in stock_data.keys()}
        cashflows = []
        total_invested = 0
        
        history = []
        
        for date in monthly_dates.values:
            date = pd.Timestamp(date)
            
            # Calculate crash scores for each stock
            crash_scores = {}
            momentum_scores = {}
            
            for symbol, df in stock_data.items():
                prices = df['Close']
                crash_scores[symbol] = self.smart_sip.calculate_crash_score(prices, date)
                momentum_scores[symbol] = self.smart_sip.calculate_momentum_score(prices, date)
            
            # Average crash score (market-wide)
            avg_crash_score = np.mean(list(crash_scores.values()))
            
            # Investment multiplier based on market crash
            multiplier = self.smart_sip.calculate_investment_multiplier(avg_crash_score)
            month_budget = self.monthly_budget * multiplier
            
            # Rank stocks by combined score (momentum when not crashing, value when crashing)
            combined_scores = {}
            for symbol in stock_data.keys():
                # During crashes, favor value (high crash score)
                # During normal times, favor momentum
                if avg_crash_score > 0.5:
                    combined_scores[symbol] = crash_scores[symbol] * 0.7 + momentum_scores[symbol] / 100 * 0.3
                else:
                    combined_scores[symbol] = crash_scores[symbol] * 0.3 + momentum_scores[symbol] / 100 * 0.7
            
            # Sort by combined score, pick top N
            ranked = sorted(combined_scores.items(), key=lambda x: x[1], reverse=True)
            selected = [s[0] for s in ranked[:top_n]]
            
            # Allocate budget to selected stocks
            per_stock = month_budget / len(selected)
            
            for symbol in selected:
                price = stock_data[symbol].loc[:date, 'Close'].iloc[-1]
                units = per_stock / price
                portfolio[symbol] += units
            
            total_invested += month_budget
            cashflows.append((date.to_pydatetime(), -month_budget))
            
            # Calculate portfolio value
            portfolio_value = sum(
                portfolio[s] * stock_data[s].loc[:date, 'Close'].iloc[-1]
                for s in portfolio.keys()
            )
            
            history.append({
                'date': date,
                'crash_score': avg_crash_score,
                'multiplier': multiplier,
                'month_investment': month_budget,
                'invested': total_invested,
                'portfolio_value': portfolio_value,
                'return_pct': (portfolio_value / total_invested - 1) * 100,
                'selected_stocks': ', '.join(selected)
            })
        
        # Final value
        final_date = monthly_dates.values[-1]
        final_value = sum(
            portfolio[s] * stock_data[s].loc[:pd.Timestamp(final_date), 'Close'].iloc[-1]
            for s in portfolio.keys()
        )
        
        # Add final value to cashflows
        cashflows.append((pd.Timestamp(final_date).to_pydatetime(), final_value))
        
        # Calculate XIRR
        xirr = calculate_xirr(cashflows)
        
        return {
            'name': 'Smart SIP v2',
            'total_invested': total_invested,
            'final_value': final_value,
            'total_return_pct': (final_value / total_invested - 1) * 100,
            'xirr': xirr * 100,
            'history': pd.DataFrame(history),
            'months': len(monthly_dates),
            'avg_multiplier': np.mean([h['multiplier'] for h in history])
        }
    
    def backtest_aggressive_crash_sip(self, stock_data: Dict[str, pd.DataFrame],
                                       top_n: int = 5) -> Dict:
        """
        Aggressive Crash SIP: Save money during bull markets, deploy heavily during crashes.
        
        Rules:
        - Bull market (crash_score < 0.2): Invest only 50% of budget, save rest
        - Normal (0.2-0.5): Invest 100%
        - Opportunity (0.5-0.7): Invest 150% + saved cash
        - Crash (>0.7): Invest 200% + all saved cash
        """
        all_dates = None
        for symbol, df in stock_data.items():
            if all_dates is None:
                all_dates = set(df.index)
            else:
                all_dates &= set(df.index)
        
        common_dates = sorted(list(all_dates))
        monthly_dates = pd.DatetimeIndex(common_dates).to_series().resample('ME').last().dropna()
        
        portfolio = {symbol: 0.0 for symbol in stock_data.keys()}
        cashflows = []
        total_invested = 0
        cash_reserve = 0  # Saved cash for crashes
        
        history = []
        
        for date in monthly_dates.values:
            date = pd.Timestamp(date)
            
            # Calculate market-wide crash score
            crash_scores = {}
            for symbol, df in stock_data.items():
                prices = df['Close']
                crash_scores[symbol] = self.smart_sip.calculate_crash_score(prices, date)
            
            avg_crash_score = np.mean(list(crash_scores.values()))
            
            # Determine investment amount
            if avg_crash_score < 0.2:
                # Bull market - save 50%
                invest_amount = self.monthly_budget * 0.5
                cash_reserve += self.monthly_budget * 0.5
                regime = "Bull (saving)"
            elif avg_crash_score < 0.5:
                # Normal - invest 100%
                invest_amount = self.monthly_budget
                regime = "Normal"
            elif avg_crash_score < 0.7:
                # Opportunity - invest 150% + some reserves
                invest_amount = self.monthly_budget * 1.5
                use_reserve = min(cash_reserve * 0.3, self.monthly_budget)
                invest_amount += use_reserve
                cash_reserve -= use_reserve
                regime = "Opportunity"
            else:
                # Crash - go all in!
                invest_amount = self.monthly_budget * 2.0 + cash_reserve
                cash_reserve = 0
                regime = "CRASH - All In!"
            
            # Pick stocks (favor high crash scores during crashes)
            ranked = sorted(crash_scores.items(), key=lambda x: x[1], reverse=True)
            selected = [s[0] for s in ranked[:top_n]]
            
            per_stock = invest_amount / len(selected)
            
            for symbol in selected:
                price = stock_data[symbol].loc[:date, 'Close'].iloc[-1]
                units = per_stock / price
                portfolio[symbol] += units
            
            total_invested += invest_amount
            cashflows.append((date.to_pydatetime(), -invest_amount))
            
            portfolio_value = sum(
                portfolio[s] * stock_data[s].loc[:date, 'Close'].iloc[-1]
                for s in portfolio.keys()
            )
            
            history.append({
                'date': date,
                'crash_score': avg_crash_score,
                'regime': regime,
                'month_investment': invest_amount,
                'cash_reserve': cash_reserve,
                'invested': total_invested,
                'portfolio_value': portfolio_value,
                'return_pct': (portfolio_value / total_invested - 1) * 100
            })
        
        final_date = monthly_dates.values[-1]
        final_value = sum(
            portfolio[s] * stock_data[s].loc[:pd.Timestamp(final_date), 'Close'].iloc[-1]
            for s in portfolio.keys()
        ) + cash_reserve
        
        cashflows.append((pd.Timestamp(final_date).to_pydatetime(), final_value))
        xirr = calculate_xirr(cashflows)
        
        return {
            'name': 'Aggressive Crash SIP',
            'total_invested': total_invested,
            'final_value': final_value,
            'total_return_pct': (final_value / total_invested - 1) * 100,
            'xirr': xirr * 100,
            'history': pd.DataFrame(history),
            'months': len(monthly_dates),
            'final_cash_reserve': cash_reserve
        }


def run_comprehensive_test():
    """Run comprehensive test of all SIP strategies."""
    
    print("=" * 70)
    print("SMART SIP v2 - COMPREHENSIVE BACKTEST")
    print("=" * 70)
    
    # Nifty 50 stocks (diversified)
    symbols = [
        'RELIANCE.NS', 'TCS.NS', 'HDFCBANK.NS', 'INFY.NS', 'ICICIBANK.NS',
        'HINDUNILVR.NS', 'SBIN.NS', 'BHARTIARTL.NS', 'ITC.NS', 'KOTAKBANK.NS',
        'LT.NS', 'AXISBANK.NS', 'ASIANPAINT.NS', 'MARUTI.NS', 'SUNPHARMA.NS',
        'TITAN.NS', 'BAJFINANCE.NS', 'WIPRO.NS', 'HCLTECH.NS', 'ULTRACEMCO.NS'
    ]
    
    print(f"\nDownloading data for {len(symbols)} stocks...")
    
    backtester = MultiStockSIPBacktester(monthly_budget=50000)
    stock_data = backtester.download_data(symbols, years=5)
    
    print(f"\nSuccessfully loaded: {len(stock_data)} stocks")
    
    if len(stock_data) < 5:
        print("Error: Not enough data")
        return None
    
    # Run all strategies
    print("\n" + "=" * 70)
    print("RUNNING BACKTESTS...")
    print("=" * 70)
    
    print("\n1. Simple SIP (baseline)...")
    simple = backtester.backtest_simple_sip(stock_data, top_n=5)
    
    print("2. Smart SIP v2 (crash buying + momentum)...")
    smart_v2 = backtester.backtest_smart_sip_v2(stock_data, top_n=5)
    
    print("3. Aggressive Crash SIP (save for crashes)...")
    aggressive = backtester.backtest_aggressive_crash_sip(stock_data, top_n=5)
    
    # Results comparison
    print("\n" + "=" * 70)
    print("RESULTS COMPARISON")
    print("=" * 70)
    
    results = [simple, smart_v2, aggressive]
    
    print(f"\n{'Strategy':<25} {'Invested':>15} {'Final Value':>15} {'Total Ret':>12} {'XIRR':>10}")
    print("-" * 80)
    
    for r in results:
        print(f"{r['name']:<25} {r['total_invested']:>15,.0f} {r['final_value']:>15,.0f} "
              f"{r['total_return_pct']:>11.2f}% {r['xirr']:>9.2f}%")
    
    print("\n" + "=" * 70)
    print("KEY METRICS")
    print("=" * 70)
    
    for r in results:
        years = r['months'] / 12
        print(f"\n{r['name']}:")
        print(f"  Investment Period: {r['months']} months ({years:.1f} years)")
        print(f"  Total Invested: Rs {r['total_invested']:,.0f}")
        print(f"  Final Value: Rs {r['final_value']:,.0f}")
        print(f"  Total Return: {r['total_return_pct']:.2f}%")
        print(f"  XIRR (Annualized): {r['xirr']:.2f}%")
        if 'avg_multiplier' in r:
            print(f"  Avg Investment Multiplier: {r['avg_multiplier']:.2f}x")
        if 'final_cash_reserve' in r:
            print(f"  Final Cash Reserve: Rs {r['final_cash_reserve']:,.0f}")
    
    # Show crash events
    print("\n" + "=" * 70)
    print("CRASH BUYING EVENTS (Smart SIP v2)")
    print("=" * 70)
    
    hist = smart_v2['history']
    crash_events = hist[hist['multiplier'] > 1.5]
    
    if len(crash_events) > 0:
        print(f"\nFound {len(crash_events)} high-opportunity months:")
        for _, row in crash_events.iterrows():
            print(f"  {row['date'].strftime('%Y-%m')}: "
                  f"Crash Score={row['crash_score']:.2f}, "
                  f"Invested {row['multiplier']:.1f}x = Rs {row['month_investment']:,.0f}")
    else:
        print("No major crash events detected in period")
    
    # Compare with Buy & Hold
    print("\n" + "=" * 70)
    print("VS BUY & HOLD (First stock: RELIANCE.NS)")
    print("=" * 70)
    
    if 'RELIANCE.NS' in stock_data:
        rel = stock_data['RELIANCE.NS']['Close']
        buy_hold_return = (rel.iloc[-1] / rel.iloc[0] - 1) * 100
        years = len(rel) / 252
        buy_hold_cagr = ((rel.iloc[-1] / rel.iloc[0]) ** (1/years) - 1) * 100
        
        print(f"\nRELIANCE.NS Buy & Hold:")
        print(f"  Total Return: {buy_hold_return:.2f}%")
        print(f"  CAGR: {buy_hold_cagr:.2f}%")
        print(f"\nSmart SIP v2 XIRR: {smart_v2['xirr']:.2f}%")
        
        if smart_v2['xirr'] > buy_hold_cagr:
            print(f"  -> Smart SIP BEATS Buy & Hold by {smart_v2['xirr'] - buy_hold_cagr:.2f}%!")
        else:
            print(f"  -> Buy & Hold wins by {buy_hold_cagr - smart_v2['xirr']:.2f}%")
    
    return {'simple': simple, 'smart_v2': smart_v2, 'aggressive': aggressive}


def quick_single_stock_test(symbol: str = 'RELIANCE.NS'):
    """Quick test on single stock for debugging."""
    
    print(f"\nQuick test on {symbol}...")
    
    end_date = datetime.now()
    start_date = end_date - timedelta(days=5 * 365)
    
    df = yf.download(symbol, start=start_date, end=end_date, progress=False)
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    
    prices = df['Close']
    
    # Simple XIRR test
    monthly = prices.resample('ME').last()
    
    cashflows = []
    total_units = 0
    
    for date, price in monthly.items():
        units = 10000 / price
        total_units += units
        cashflows.append((date.to_pydatetime(), -10000))
    
    final_value = total_units * monthly.iloc[-1]
    cashflows.append((monthly.index[-1].to_pydatetime(), final_value))
    
    xirr = calculate_xirr(cashflows) * 100
    total_invested = 10000 * len(monthly)
    total_return = (final_value / total_invested - 1) * 100
    
    print(f"\nSimple SIP on {symbol}:")
    print(f"  Months: {len(monthly)}")
    print(f"  Total Invested: Rs {total_invested:,.0f}")
    print(f"  Final Value: Rs {final_value:,.0f}")
    print(f"  Total Return: {total_return:.2f}%")
    print(f"  XIRR (Annualized): {xirr:.2f}%")
    
    # Buy & Hold comparison
    buy_hold = (prices.iloc[-1] / prices.iloc[0] - 1) * 100
    years = len(prices) / 252
    cagr = ((prices.iloc[-1] / prices.iloc[0]) ** (1/years) - 1) * 100
    
    print(f"\nBuy & Hold on {symbol}:")
    print(f"  Total Return: {buy_hold:.2f}%")
    print(f"  CAGR: {cagr:.2f}%")


if __name__ == "__main__":
    # Quick single stock test first
    quick_single_stock_test('RELIANCE.NS')
    
    print("\n" + "=" * 70)
    
    # Comprehensive multi-stock test
    results = run_comprehensive_test()
