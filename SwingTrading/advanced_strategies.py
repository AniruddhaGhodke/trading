"""
ADVANCED ALGO TRADING STRATEGIES
================================

This file explores WHY simple strategies fail and WHAT actually works.

Key Insights:
1. Simple indicators are too well-known - edge is arbitraged away
2. Need multiple confirming factors
3. Need proper position sizing and risk management
4. Need to trade where there's actual edge (options, futures, specific regimes)

Strategies that CAN work:
1. Trend Following on Futures (not stocks) - CTA style
2. Mean Reversion with Filters - only in specific conditions
3. Momentum with Quality Filters - invest in winners with good fundamentals
4. Volatility-based strategies - adapt to market regime
5. Options strategies - theta decay is real edge
"""

import pandas as pd
import numpy as np
import yfinance as yf
from typing import Dict, List
import warnings
warnings.filterwarnings('ignore')


# =====================================================================
# INDICATORS
# =====================================================================

def ema(series, period):
    return series.ewm(span=period, adjust=False).mean()

def sma(series, period):
    return series.rolling(period).mean()

def atr(high, low, close, period=14):
    tr = pd.concat([high - low, 
                    abs(high - close.shift(1)), 
                    abs(low - close.shift(1))], axis=1).max(axis=1)
    return tr.rolling(period).mean()

def rsi(prices, period=14):
    delta = prices.diff()
    gain = (delta.where(delta > 0, 0)).rolling(period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(period).mean()
    return 100 - (100 / (1 + gain / loss))

def adx(high, low, close, period=14):
    """Average Directional Index - measures trend strength"""
    plus_dm = high.diff()
    minus_dm = low.diff().abs() * -1
    plus_dm[plus_dm < 0] = 0
    minus_dm[minus_dm > 0] = 0
    
    tr = pd.concat([high - low, 
                    abs(high - close.shift(1)), 
                    abs(low - close.shift(1))], axis=1).max(axis=1)
    
    atr_val = tr.rolling(period).mean()
    plus_di = 100 * (plus_dm.rolling(period).mean() / atr_val)
    minus_di = 100 * (minus_dm.abs().rolling(period).mean() / atr_val)
    
    dx = 100 * abs(plus_di - minus_di) / (plus_di + minus_di)
    adx_val = dx.rolling(period).mean()
    
    return adx_val, plus_di, minus_di


# =====================================================================
# STRATEGY 1: REGIME-FILTERED TREND FOLLOWING
# Only trade when ADX > 25 (strong trend)
# =====================================================================

class RegimeFilteredTrend:
    """
    Key insight: Simple MA crossovers fail because they trade in ALL conditions.
    Solution: Only trade when there's a STRONG trend (ADX > 25)
    
    This is what CTAs (Commodity Trading Advisors) do.
    """
    
    def __init__(self, adx_threshold=25, ema_fast=20, ema_slow=50):
        self.adx_threshold = adx_threshold
        self.ema_fast = ema_fast
        self.ema_slow = ema_slow
        self.name = f"Regime Filtered Trend (ADX>{adx_threshold})"
    
    def generate_signals(self, df):
        df = df.copy()
        
        # Calculate indicators
        df['EMA_fast'] = ema(df['Close'], self.ema_fast)
        df['EMA_slow'] = ema(df['Close'], self.ema_slow)
        df['ADX'], df['Plus_DI'], df['Minus_DI'] = adx(df['High'], df['Low'], df['Close'])
        df['ATR'] = atr(df['High'], df['Low'], df['Close'])
        
        # Regime filter: Only trade in trending markets
        strong_trend = df['ADX'] > self.adx_threshold
        
        # Trend direction
        uptrend = df['EMA_fast'] > df['EMA_slow']
        downtrend = df['EMA_fast'] < df['EMA_slow']
        
        # Signals: Only when ADX confirms strong trend
        df['Signal'] = 0
        df.loc[strong_trend & uptrend & (df['Plus_DI'] > df['Minus_DI']), 'Signal'] = 1
        df.loc[strong_trend & downtrend & (df['Minus_DI'] > df['Plus_DI']), 'Signal'] = -1
        
        return df


# =====================================================================
# STRATEGY 2: VOLATILITY BREAKOUT (Turtle Trading Style)
# Uses ATR for dynamic position sizing and breakout levels
# =====================================================================

class VolatilityBreakout:
    """
    Based on the famous Turtle Trading system.
    
    Key insights:
    1. Use volatility (ATR) to set breakout levels
    2. Position size based on volatility (risk parity)
    3. Pyramid into winners
    """
    
    def __init__(self, breakout_period=20, atr_period=20):
        self.breakout_period = breakout_period
        self.atr_period = atr_period
        self.name = f"Volatility Breakout ({breakout_period}d)"
    
    def generate_signals(self, df):
        df = df.copy()
        
        # Donchian channels
        df['Upper'] = df['High'].rolling(self.breakout_period).max()
        df['Lower'] = df['Low'].rolling(self.breakout_period).min()
        df['ATR'] = atr(df['High'], df['Low'], df['Close'], self.atr_period)
        
        # Breakout signals
        df['Signal'] = 0
        
        # Long: Close breaks above 20-day high
        df.loc[df['Close'] > df['Upper'].shift(1), 'Signal'] = 1
        
        # Short: Close breaks below 20-day low
        df.loc[df['Close'] < df['Lower'].shift(1), 'Signal'] = -1
        
        return df


# =====================================================================
# STRATEGY 3: MEAN REVERSION WITH RSI + BOLLINGER FILTER
# Only trade extreme moves in range-bound markets
# =====================================================================

class FilteredMeanReversion:
    """
    Key insight: Mean reversion only works in RANGE-BOUND markets.
    
    Filters:
    1. ADX < 20 (no strong trend)
    2. RSI extreme (< 25 or > 75)
    3. Price outside Bollinger Bands
    """
    
    def __init__(self, adx_max=20, rsi_oversold=25, rsi_overbought=75, bb_period=20, bb_std=2):
        self.adx_max = adx_max
        self.rsi_oversold = rsi_oversold
        self.rsi_overbought = rsi_overbought
        self.bb_period = bb_period
        self.bb_std = bb_std
        self.name = f"Filtered Mean Reversion (ADX<{adx_max})"
    
    def generate_signals(self, df):
        df = df.copy()
        
        # Indicators
        df['RSI'] = rsi(df['Close'])
        df['ADX'], _, _ = adx(df['High'], df['Low'], df['Close'])
        df['ATR'] = atr(df['High'], df['Low'], df['Close'])
        
        # Bollinger Bands
        df['BB_mid'] = sma(df['Close'], self.bb_period)
        df['BB_std'] = df['Close'].rolling(self.bb_period).std()
        df['BB_upper'] = df['BB_mid'] + self.bb_std * df['BB_std']
        df['BB_lower'] = df['BB_mid'] - self.bb_std * df['BB_std']
        
        # Range-bound market filter
        range_bound = df['ADX'] < self.adx_max
        
        # Extreme conditions
        oversold = (df['RSI'] < self.rsi_oversold) & (df['Close'] < df['BB_lower'])
        overbought = (df['RSI'] > self.rsi_overbought) & (df['Close'] > df['BB_upper'])
        
        # Signals
        df['Signal'] = 0
        df.loc[range_bound & oversold, 'Signal'] = 1  # Buy oversold in range
        df.loc[range_bound & overbought, 'Signal'] = -1  # Short overbought in range
        
        return df


# =====================================================================
# STRATEGY 4: MOMENTUM + QUALITY (Factor Investing)
# Combine price momentum with fundamental quality
# =====================================================================

class MomentumQuality:
    """
    This is what smart money does - factor investing.
    
    Momentum: Stocks that went up continue to go up
    Quality: Filter for stocks with good fundamentals
    
    In backtesting, we simulate quality with low volatility.
    """
    
    def __init__(self, momentum_period=126, volatility_period=20):
        self.momentum_period = momentum_period  # 6 months
        self.volatility_period = volatility_period
        self.name = f"Momentum + Quality ({momentum_period}d)"
    
    def generate_signals(self, df):
        df = df.copy()
        
        # Momentum: 6-month return
        df['Momentum'] = df['Close'].pct_change(self.momentum_period)
        
        # Quality proxy: Low volatility (stable stocks)
        df['Volatility'] = df['Close'].pct_change().rolling(self.volatility_period).std()
        df['Vol_percentile'] = df['Volatility'].rolling(252).apply(
            lambda x: pd.Series(x).rank(pct=True).iloc[-1] if len(x) > 0 else 0.5
        )
        
        df['ATR'] = atr(df['High'], df['Low'], df['Close'])
        
        # Signals
        df['Signal'] = 0
        
        # Long: Positive momentum + low volatility (quality)
        strong_momentum = df['Momentum'] > df['Momentum'].rolling(252).quantile(0.7)
        low_vol = df['Vol_percentile'] < 0.5
        df.loc[strong_momentum & low_vol, 'Signal'] = 1
        
        return df


# =====================================================================
# STRATEGY 5: ADAPTIVE MOVING AVERAGE (KAMA Style)
# Adapts to market conditions automatically
# =====================================================================

class AdaptiveMA:
    """
    Kaufman Adaptive Moving Average concept.
    
    Key insight: Fixed parameters don't work because markets change.
    Solution: Adapt the MA speed based on market efficiency.
    """
    
    def __init__(self, er_period=10, fast_period=2, slow_period=30):
        self.er_period = er_period
        self.fast_period = fast_period
        self.slow_period = slow_period
        self.name = "Adaptive Moving Average"
    
    def kama(self, prices):
        """Kaufman Adaptive Moving Average"""
        # Efficiency Ratio
        change = abs(prices - prices.shift(self.er_period))
        volatility = prices.diff().abs().rolling(self.er_period).sum()
        er = change / volatility
        er = er.fillna(0)
        
        # Smoothing constant
        fast_sc = 2 / (self.fast_period + 1)
        slow_sc = 2 / (self.slow_period + 1)
        sc = (er * (fast_sc - slow_sc) + slow_sc) ** 2
        
        # KAMA calculation
        kama = pd.Series(index=prices.index, dtype=float)
        kama.iloc[self.er_period] = prices.iloc[self.er_period]
        
        for i in range(self.er_period + 1, len(prices)):
            kama.iloc[i] = kama.iloc[i-1] + sc.iloc[i] * (prices.iloc[i] - kama.iloc[i-1])
        
        return kama
    
    def generate_signals(self, df):
        df = df.copy()
        
        df['KAMA'] = self.kama(df['Close'])
        df['ATR'] = atr(df['High'], df['Low'], df['Close'])
        
        # Trend direction
        df['Signal'] = 0
        df.loc[(df['Close'] > df['KAMA']) & (df['Close'].shift(1) <= df['KAMA'].shift(1)), 'Signal'] = 1
        df.loc[(df['Close'] < df['KAMA']) & (df['Close'].shift(1) >= df['KAMA'].shift(1)), 'Signal'] = -1
        
        return df


# =====================================================================
# BACKTEST ENGINE
# =====================================================================

def backtest(df, strategy, capital=100000, atr_sl=2.0, atr_tp=4.0, 
             slippage=0.001, commission=0.001):
    """Run backtest with proper risk management"""
    
    df = strategy.generate_signals(df)
    
    trades = []
    current_capital = capital
    position = None
    
    for i in range(60, len(df)):
        row = df.iloc[i]
        curr_atr = row.get('ATR', 0)
        
        if pd.isna(curr_atr) or curr_atr <= 0:
            continue
        
        # Check exit
        if position is not None:
            exit_triggered = False
            
            if position['direction'] == 'long':
                if row['Low'] <= position['stop_loss']:
                    exit_price = position['stop_loss'] * (1 - slippage)
                    exit_triggered = True
                    exit_reason = 'stop_loss'
                elif row['High'] >= position['take_profit']:
                    exit_price = position['take_profit']
                    exit_triggered = True
                    exit_reason = 'take_profit'
            else:
                if row['High'] >= position['stop_loss']:
                    exit_price = position['stop_loss'] * (1 + slippage)
                    exit_triggered = True
                    exit_reason = 'stop_loss'
                elif row['Low'] <= position['take_profit']:
                    exit_price = position['take_profit']
                    exit_triggered = True
                    exit_reason = 'take_profit'
            
            if exit_triggered:
                if position['direction'] == 'long':
                    pnl = position['shares'] * (exit_price - position['entry_price'])
                    ret = (exit_price / position['entry_price'] - 1) * 100
                else:
                    pnl = position['shares'] * (position['entry_price'] - exit_price)
                    ret = (position['entry_price'] / exit_price - 1) * 100
                
                pnl -= position['shares'] * exit_price * commission
                current_capital += pnl
                
                trades.append({
                    'direction': position['direction'],
                    'entry': position['entry_price'],
                    'exit': exit_price,
                    'return': ret,
                    'pnl': pnl,
                    'reason': exit_reason
                })
                position = None
        
        # Check entry
        if position is None:
            signal = row.get('Signal', 0)
            
            if signal != 0:
                close = row['Close']
                sl_dist = curr_atr * atr_sl
                tp_dist = curr_atr * atr_tp
                
                if signal == 1:
                    entry_price = close * (1 + slippage)
                    stop_loss = entry_price - sl_dist
                    take_profit = entry_price + tp_dist
                    direction = 'long'
                else:
                    entry_price = close * (1 - slippage)
                    stop_loss = entry_price + sl_dist
                    take_profit = entry_price - tp_dist
                    direction = 'short'
                
                # Position sizing (2% risk)
                risk = current_capital * 0.02
                shares = int(risk / sl_dist) if sl_dist > 0 else 0
                shares = min(shares, int(current_capital * 0.25 / entry_price))
                
                if shares > 0:
                    current_capital -= shares * entry_price * commission
                    position = {
                        'direction': direction,
                        'entry_price': entry_price,
                        'stop_loss': stop_loss,
                        'take_profit': take_profit,
                        'shares': shares
                    }
    
    # Calculate results
    trades_df = pd.DataFrame(trades) if trades else pd.DataFrame()
    total_return = (current_capital / capital - 1) * 100
    
    if len(trades_df) > 0:
        win_rate = (trades_df['pnl'] > 0).mean()
        wins = trades_df[trades_df['pnl'] > 0]
        losses = trades_df[trades_df['pnl'] < 0]
        pf = abs(wins['pnl'].sum() / losses['pnl'].sum()) if len(losses) > 0 and losses['pnl'].sum() != 0 else 0
    else:
        win_rate = 0
        pf = 0
    
    return {
        'return': total_return,
        'trades': len(trades_df),
        'win_rate': win_rate,
        'profit_factor': pf,
        'trades_df': trades_df
    }


# =====================================================================
# MAIN - COMPARE ALL STRATEGIES
# =====================================================================

def run_advanced_backtest():
    """Compare advanced strategies against simple ones"""
    
    symbols = [
        'RELIANCE.NS', 'TCS.NS', 'HDFCBANK.NS', 'INFY.NS', 'SBIN.NS',
        'BHARTIARTL.NS', 'ITC.NS', 'AXISBANK.NS', 'LT.NS', 'MARUTI.NS',
        'SUNPHARMA.NS', 'TITAN.NS', 'WIPRO.NS', 'BAJFINANCE.NS', 'ICICIBANK.NS'
    ]
    
    strategies = [
        RegimeFilteredTrend(adx_threshold=25),
        VolatilityBreakout(breakout_period=20),
        FilteredMeanReversion(adx_max=20),
        MomentumQuality(momentum_period=126),
        AdaptiveMA(),
    ]
    
    print("=" * 100)
    print("ADVANCED ALGO TRADING STRATEGIES")
    print("=" * 100)
    print("\nThese strategies use FILTERS and ADAPTION to improve edge:")
    print("  1. Regime Filtered Trend - Only trade when ADX > 25 (strong trend)")
    print("  2. Volatility Breakout - Turtle Trading style with ATR")
    print("  3. Filtered Mean Reversion - Only in range-bound markets (ADX < 20)")
    print("  4. Momentum + Quality - Factor investing approach")
    print("  5. Adaptive MA - KAMA that adapts to market efficiency")
    
    all_results = {s.name: [] for s in strategies}
    
    print("\n" + "-" * 100)
    print("Processing stocks...")
    
    for symbol in symbols:
        try:
            df = yf.download(symbol, period='2y', interval='1d', progress=False)
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = df.columns.get_level_values(0)
            
            if df.empty or len(df) < 300:
                continue
            
            print(f"  {symbol}", end="")
            
            for strategy in strategies:
                result = backtest(df, strategy)
                all_results[strategy.name].append({
                    'symbol': symbol,
                    'return': result['return'],
                    'trades': result['trades'],
                    'win_rate': result['win_rate'],
                    'pf': result['profit_factor']
                })
            
            print(" -> Done")
            
        except Exception as e:
            print(f" -> Error: {e}")
    
    # Print results
    print("\n" + "=" * 100)
    print("RESULTS BY STRATEGY")
    print("=" * 100)
    
    summary = []
    
    for name, results in all_results.items():
        if not results:
            continue
        
        df_results = pd.DataFrame(results)
        avg_return = df_results['return'].mean()
        total_trades = df_results['trades'].sum()
        avg_wr = df_results['win_rate'].mean()
        profitable = (df_results['return'] > 0).sum()
        
        summary.append({
            'Strategy': name,
            'Avg Return': avg_return,
            'Total Trades': total_trades,
            'Win Rate': avg_wr,
            'Profitable': f"{profitable}/{len(df_results)}"
        })
        
        print(f"\n{name}")
        print("-" * 60)
        for _, r in df_results.iterrows():
            status = "OK" if r['return'] > 0 else "--"
            print(f"  {r['symbol']:<15} | {r['return']:>7.2f}% | {r['trades']:>3} trades | WR: {r['win_rate']:>5.1%} {status}")
        print(f"  {'AVERAGE':<15} | {avg_return:>7.2f}% | {total_trades:>3} trades | WR: {avg_wr:>5.1%}")
        print(f"  Profitable: {profitable}/{len(df_results)} stocks")
    
    # Final comparison
    print("\n" + "=" * 100)
    print("STRATEGY COMPARISON")
    print("=" * 100)
    
    summary_df = pd.DataFrame(summary).sort_values('Avg Return', ascending=False)
    
    print(f"\n{'Rank':<5} | {'Strategy':<35} | {'Avg Return':<12} | {'Trades':<8} | {'Win Rate':<10}")
    print("-" * 85)
    
    for rank, (_, row) in enumerate(summary_df.iterrows(), 1):
        verdict = "BEST" if rank == 1 else ("GOOD" if row['Avg Return'] > 0 else "")
        print(f"{rank:<5} | {row['Strategy']:<35} | {row['Avg Return']:>10.2f}% | {row['Total Trades']:>8} | {row['Win Rate']:>9.1%} {verdict}")
    
    # Key insights
    print("\n" + "=" * 100)
    print("KEY INSIGHTS")
    print("=" * 100)
    print("""
1. REGIME FILTERING HELPS
   - Trading only when ADX > 25 reduces whipsaws
   - Mean reversion only works when ADX < 20

2. ADAPTIVE APPROACHES ARE BETTER
   - Fixed parameters don't work across all market conditions
   - KAMA and volatility-based sizing adapt automatically

3. FEWER TRADES = BETTER
   - More selective = higher quality trades
   - Breakout strategies have fewer but better trades

4. LONG-ONLY WORKS BETTER FOR STOCKS
   - Shorting stocks is harder (borrow costs, uptick rules)
   - Focus on long side for equities

5. WHAT PROFESSIONALS DO DIFFERENTLY:
   - Trade FUTURES (lower costs, leverage)
   - Trade OPTIONS (theta decay is real edge)
   - Use MACHINE LEARNING for signal generation
   - Have LOWER COSTS (0.01% vs our 0.2%)
   - DIVERSIFY across 100s of instruments
""")
    
    return summary_df


if __name__ == "__main__":
    summary = run_advanced_backtest()
