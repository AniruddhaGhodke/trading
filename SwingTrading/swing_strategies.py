"""
Swing Trading Strategies for Indian Stocks (NSE)

Swing Trading = Holding positions for days to weeks
Uses DAILY data for 2 years (500+ bars per stock)

Strategies:
1. Ichimoku Cloud + EMA - Cloud breakout with trend confirmation
2. EMA Crossover (20/50) - Classic dual moving average crossover
3. RSI Reversal - Oversold/Overbought with confirmation
4. Breakout (52-week High/Low) - Trade breakouts from range
5. Pullback to 20 EMA - Buy dips in uptrend
6. MACD Crossover - MACD line crosses signal line

All strategies use:
- Daily candles
- 2 years of data
- ATR-based stops
- 0.1% slippage + 0.1% commission
"""

import pandas as pd
import numpy as np
import yfinance as yf
from datetime import datetime, timedelta
from typing import Dict, List, Tuple, Optional
from dataclasses import dataclass
from abc import ABC, abstractmethod
import warnings
warnings.filterwarnings('ignore')


# =====================================================================
# INDICATORS
# =====================================================================

def ema(series: pd.Series, period: int) -> pd.Series:
    """Exponential Moving Average"""
    return series.ewm(span=period, adjust=False).mean()

def sma(series: pd.Series, period: int) -> pd.Series:
    """Simple Moving Average"""
    return series.rolling(window=period).mean()

def rsi(prices: pd.Series, period: int = 14) -> pd.Series:
    """Relative Strength Index"""
    delta = prices.diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
    rs = gain / loss
    return 100 - (100 / (1 + rs))

def atr(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
    """Average True Range"""
    tr1 = high - low
    tr2 = abs(high - close.shift(1))
    tr3 = abs(low - close.shift(1))
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    return tr.rolling(window=period).mean()

def macd(prices: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9) -> Tuple[pd.Series, pd.Series, pd.Series]:
    """MACD indicator - returns (macd_line, signal_line, histogram)"""
    ema_fast = prices.ewm(span=fast, adjust=False).mean()
    ema_slow = prices.ewm(span=slow, adjust=False).mean()
    macd_line = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    histogram = macd_line - signal_line
    return macd_line, signal_line, histogram

def ichimoku(df: pd.DataFrame, tenkan: int = 9, kijun: int = 26, senkou_b: int = 52):
    """Ichimoku Cloud indicator"""
    high, low = df['High'], df['Low']
    
    # Tenkan-sen (Conversion Line)
    tenkan_line = (high.rolling(tenkan).max() + low.rolling(tenkan).min()) / 2
    
    # Kijun-sen (Base Line)
    kijun_line = (high.rolling(kijun).max() + low.rolling(kijun).min()) / 2
    
    # Senkou Span A
    span_a = (tenkan_line + kijun_line) / 2
    
    # Senkou Span B
    span_b = (high.rolling(senkou_b).max() + low.rolling(senkou_b).min()) / 2
    
    return tenkan_line, kijun_line, span_a, span_b


# =====================================================================
# BASE STRATEGY CLASS
# =====================================================================

class SwingStrategy(ABC):
    """Base class for all swing trading strategies"""
    
    def __init__(self, 
                 name: str,
                 atr_sl_mult: float = 2.0,
                 atr_tp_mult: float = 4.0,
                 slippage: float = 0.001,
                 commission: float = 0.001,
                 risk_per_trade: float = 0.02):
        self.name = name
        self.atr_sl_mult = atr_sl_mult
        self.atr_tp_mult = atr_tp_mult
        self.slippage = slippage
        self.commission = commission
        self.risk_per_trade = risk_per_trade
    
    @abstractmethod
    def generate_signals(self, df: pd.DataFrame) -> pd.Series:
        """Generate trading signals: 1=Long, -1=Short, 0=No signal"""
        pass
    
    def backtest(self, df: pd.DataFrame, capital: float = 100000) -> Dict:
        """Run backtest on daily data"""
        df = df.copy()
        df['ATR'] = atr(df['High'], df['Low'], df['Close'])
        df['Signal'] = self.generate_signals(df)
        
        trades = []
        current_capital = capital
        position = None
        
        for i in range(50, len(df)):
            row = df.iloc[i]
            curr_atr = row['ATR']
            
            if pd.isna(curr_atr) or curr_atr <= 0:
                continue
            
            # Check exit if in position
            if position is not None:
                exit_triggered = False
                exit_price = None
                exit_reason = None
                
                if position['direction'] == 'long':
                    if row['Low'] <= position['stop_loss']:
                        exit_price = position['stop_loss'] * (1 - self.slippage)
                        exit_reason = 'stop_loss'
                        exit_triggered = True
                    elif row['High'] >= position['take_profit']:
                        exit_price = position['take_profit'] * (1 - self.slippage)
                        exit_reason = 'take_profit'
                        exit_triggered = True
                        
                elif position['direction'] == 'short':
                    if row['High'] >= position['stop_loss']:
                        exit_price = position['stop_loss'] * (1 + self.slippage)
                        exit_reason = 'stop_loss'
                        exit_triggered = True
                    elif row['Low'] <= position['take_profit']:
                        exit_price = position['take_profit'] * (1 + self.slippage)
                        exit_reason = 'take_profit'
                        exit_triggered = True
                
                if exit_triggered:
                    if position['direction'] == 'long':
                        pnl = position['shares'] * (exit_price - position['entry_price'])
                        ret_pct = (exit_price / position['entry_price'] - 1) * 100
                    else:
                        pnl = position['shares'] * (position['entry_price'] - exit_price)
                        ret_pct = (position['entry_price'] / exit_price - 1) * 100
                    
                    pnl -= position['shares'] * exit_price * self.commission
                    current_capital += pnl
                    
                    trades.append({
                        'entry_date': position['entry_date'],
                        'exit_date': df.index[i],
                        'direction': position['direction'].upper(),
                        'entry_price': position['entry_price'],
                        'exit_price': exit_price,
                        'shares': position['shares'],
                        'pnl': pnl,
                        'return_pct': ret_pct,
                        'exit_reason': exit_reason,
                        'capital': current_capital
                    })
                    position = None
            
            # Check for new entry
            if position is None:
                signal = row['Signal']
                
                if signal != 0:
                    close = row['Close']
                    sl_dist = curr_atr * self.atr_sl_mult
                    tp_dist = curr_atr * self.atr_tp_mult
                    
                    if signal == 1:  # Long
                        entry_price = close * (1 + self.slippage)
                        stop_loss = entry_price - sl_dist
                        take_profit = entry_price + tp_dist
                        direction = 'long'
                    else:  # Short
                        entry_price = close * (1 - self.slippage)
                        stop_loss = entry_price + sl_dist
                        take_profit = entry_price - tp_dist
                        direction = 'short'
                    
                    # Position sizing
                    risk_amount = current_capital * self.risk_per_trade
                    shares = int(risk_amount / sl_dist) if sl_dist > 0 else 0
                    max_shares = int(current_capital * 0.25 / entry_price)
                    shares = min(shares, max_shares)
                    
                    if shares > 0:
                        current_capital -= shares * entry_price * self.commission
                        position = {
                            'direction': direction,
                            'entry_price': entry_price,
                            'entry_date': df.index[i],
                            'stop_loss': stop_loss,
                            'take_profit': take_profit,
                            'shares': shares
                        }
        
        return self._calculate_results(trades, capital, current_capital)
    
    def _calculate_results(self, trades: List[Dict], initial_capital: float, final_capital: float) -> Dict:
        """Calculate backtest statistics"""
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
            
            long_trades = len(trades_df[trades_df['direction'] == 'LONG'])
            short_trades = len(trades_df[trades_df['direction'] == 'SHORT'])
        else:
            win_rate = 0
            profit_factor = 0
            avg_win = 0
            avg_loss = 0
            long_trades = 0
            short_trades = 0
        
        return {
            'total_return_pct': total_return,
            'num_trades': len(trades_df),
            'win_rate': win_rate,
            'profit_factor': profit_factor,
            'avg_win': avg_win,
            'avg_loss': avg_loss,
            'long_trades': long_trades,
            'short_trades': short_trades,
            'final_capital': final_capital,
            'trades': trades_df
        }


# =====================================================================
# STRATEGY 1: ICHIMOKU CLOUD
# =====================================================================

class IchimokuStrategy(SwingStrategy):
    """
    Ichimoku Cloud + EMA Strategy
    
    Long: Price above cloud, Tenkan > Kijun, Close > EMA100
    Short: Price below cloud, Tenkan < Kijun, Close < EMA100
    """
    
    def __init__(self, ema_period: int = 100, **kwargs):
        super().__init__(name="Ichimoku Cloud", **kwargs)
        self.ema_period = ema_period
    
    def generate_signals(self, df: pd.DataFrame) -> pd.Series:
        tenkan, kijun, span_a, span_b = ichimoku(df)
        ema_line = ema(df['Close'], self.ema_period)
        
        cloud_top = pd.concat([span_a, span_b], axis=1).max(axis=1)
        cloud_bot = pd.concat([span_a, span_b], axis=1).min(axis=1)
        
        signals = pd.Series(index=df.index, data=0)
        
        # Long: Price above cloud, bullish TK cross, above EMA
        long_cond = (
            (df['Close'] > cloud_top) &
            (tenkan > kijun) &
            (df['Close'] > ema_line) &
            (tenkan.shift(1) <= kijun.shift(1))  # TK cross happened
        )
        
        # Short: Price below cloud, bearish TK cross, below EMA
        short_cond = (
            (df['Close'] < cloud_bot) &
            (tenkan < kijun) &
            (df['Close'] < ema_line) &
            (tenkan.shift(1) >= kijun.shift(1))  # TK cross happened
        )
        
        signals[long_cond] = 1
        signals[short_cond] = -1
        
        return signals


# =====================================================================
# STRATEGY 2: EMA CROSSOVER (20/50)
# =====================================================================

class EMACrossoverStrategy(SwingStrategy):
    """
    Dual EMA Crossover Strategy
    
    Long: EMA20 crosses above EMA50
    Short: EMA20 crosses below EMA50
    """
    
    def __init__(self, fast_period: int = 20, slow_period: int = 50, **kwargs):
        super().__init__(name=f"EMA Crossover ({fast_period}/{slow_period})", **kwargs)
        self.fast_period = fast_period
        self.slow_period = slow_period
    
    def generate_signals(self, df: pd.DataFrame) -> pd.Series:
        ema_fast = ema(df['Close'], self.fast_period)
        ema_slow = ema(df['Close'], self.slow_period)
        
        signals = pd.Series(index=df.index, data=0)
        
        # Long: Fast EMA crosses above Slow EMA
        long_cond = (ema_fast > ema_slow) & (ema_fast.shift(1) <= ema_slow.shift(1))
        
        # Short: Fast EMA crosses below Slow EMA
        short_cond = (ema_fast < ema_slow) & (ema_fast.shift(1) >= ema_slow.shift(1))
        
        signals[long_cond] = 1
        signals[short_cond] = -1
        
        return signals


# =====================================================================
# STRATEGY 3: RSI REVERSAL
# =====================================================================

class RSIReversalStrategy(SwingStrategy):
    """
    RSI Oversold/Overbought Reversal Strategy
    
    Long: RSI crosses above 30 (coming from oversold)
    Short: RSI crosses below 70 (coming from overbought)
    """
    
    def __init__(self, rsi_period: int = 14, oversold: int = 30, overbought: int = 70, **kwargs):
        super().__init__(name=f"RSI Reversal ({oversold}/{overbought})", **kwargs)
        self.rsi_period = rsi_period
        self.oversold = oversold
        self.overbought = overbought
    
    def generate_signals(self, df: pd.DataFrame) -> pd.Series:
        rsi_values = rsi(df['Close'], self.rsi_period)
        
        signals = pd.Series(index=df.index, data=0)
        
        # Long: RSI crosses above oversold level
        long_cond = (rsi_values > self.oversold) & (rsi_values.shift(1) <= self.oversold)
        
        # Short: RSI crosses below overbought level
        short_cond = (rsi_values < self.overbought) & (rsi_values.shift(1) >= self.overbought)
        
        signals[long_cond] = 1
        signals[short_cond] = -1
        
        return signals


# =====================================================================
# STRATEGY 4: BREAKOUT (52-WEEK HIGH/LOW)
# =====================================================================

class BreakoutStrategy(SwingStrategy):
    """
    52-Week High/Low Breakout Strategy
    
    Long: Price breaks above 52-week high
    Short: Price breaks below 52-week low
    """
    
    def __init__(self, lookback: int = 252, **kwargs):  # 252 trading days = 1 year
        super().__init__(name=f"Breakout ({lookback}d)", **kwargs)
        self.lookback = lookback
    
    def generate_signals(self, df: pd.DataFrame) -> pd.Series:
        high_52w = df['High'].rolling(self.lookback).max()
        low_52w = df['Low'].rolling(self.lookback).min()
        
        signals = pd.Series(index=df.index, data=0)
        
        # Long: Close breaks above 52-week high
        long_cond = (df['Close'] > high_52w.shift(1)) & (df['Close'].shift(1) <= high_52w.shift(1))
        
        # Short: Close breaks below 52-week low
        short_cond = (df['Close'] < low_52w.shift(1)) & (df['Close'].shift(1) >= low_52w.shift(1))
        
        signals[long_cond] = 1
        signals[short_cond] = -1
        
        return signals


# =====================================================================
# STRATEGY 5: PULLBACK TO 20 EMA
# =====================================================================

class PullbackStrategy(SwingStrategy):
    """
    Pullback to Moving Average Strategy
    
    Long: In uptrend (EMA20 > EMA50), price pulls back to touch EMA20, then bounces
    Short: In downtrend (EMA20 < EMA50), price rallies to touch EMA20, then drops
    """
    
    def __init__(self, fast_ema: int = 20, slow_ema: int = 50, **kwargs):
        super().__init__(name=f"Pullback to EMA{fast_ema}", **kwargs)
        self.fast_ema = fast_ema
        self.slow_ema = slow_ema
    
    def generate_signals(self, df: pd.DataFrame) -> pd.Series:
        ema_fast = ema(df['Close'], self.fast_ema)
        ema_slow = ema(df['Close'], self.slow_ema)
        
        signals = pd.Series(index=df.index, data=0)
        
        # Uptrend: EMA20 > EMA50
        uptrend = ema_fast > ema_slow
        
        # Downtrend: EMA20 < EMA50
        downtrend = ema_fast < ema_slow
        
        # Long: In uptrend, price touched EMA20 (low <= EMA20) and closed above it
        # Also require bullish candle (close > open)
        long_cond = (
            uptrend &
            (df['Low'] <= ema_fast) &
            (df['Close'] > ema_fast) &
            (df['Close'] > df['Open'])
        )
        
        # Short: In downtrend, price touched EMA20 (high >= EMA20) and closed below it
        # Also require bearish candle (close < open)
        short_cond = (
            downtrend &
            (df['High'] >= ema_fast) &
            (df['Close'] < ema_fast) &
            (df['Close'] < df['Open'])
        )
        
        signals[long_cond] = 1
        signals[short_cond] = -1
        
        return signals


# =====================================================================
# STRATEGY 6: MACD CROSSOVER
# =====================================================================

class MACDStrategy(SwingStrategy):
    """
    MACD Crossover Strategy
    
    Long: MACD line crosses above Signal line, and histogram > 0
    Short: MACD line crosses below Signal line, and histogram < 0
    """
    
    def __init__(self, fast: int = 12, slow: int = 26, signal: int = 9, **kwargs):
        super().__init__(name=f"MACD ({fast}/{slow}/{signal})", **kwargs)
        self.fast = fast
        self.slow = slow
        self.signal = signal
    
    def generate_signals(self, df: pd.DataFrame) -> pd.Series:
        macd_line, signal_line, histogram = macd(df['Close'], self.fast, self.slow, self.signal)
        
        signals = pd.Series(index=df.index, data=0)
        
        # Long: MACD crosses above Signal
        long_cond = (macd_line > signal_line) & (macd_line.shift(1) <= signal_line.shift(1))
        
        # Short: MACD crosses below Signal
        short_cond = (macd_line < signal_line) & (macd_line.shift(1) >= signal_line.shift(1))
        
        signals[long_cond] = 1
        signals[short_cond] = -1
        
        return signals


# =====================================================================
# DATA DOWNLOAD
# =====================================================================

def download_data(symbol: str, period: str = '2y') -> pd.DataFrame:
    """Download daily data from Yahoo Finance"""
    df = yf.download(symbol, period=period, interval='1d', progress=False)
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    return df


# =====================================================================
# MAIN BACKTEST RUNNER
# =====================================================================

def run_swing_backtest(symbols: List[str] = None):
    """Run all swing trading strategies on multiple stocks"""
    
    if symbols is None:
        symbols = [
            'RELIANCE.NS', 'TCS.NS', 'HDFCBANK.NS', 'INFY.NS', 'SBIN.NS',
            'BHARTIARTL.NS', 'ITC.NS', 'KOTAKBANK.NS', 'AXISBANK.NS', 'LT.NS',
            'MARUTI.NS', 'SUNPHARMA.NS', 'TITAN.NS', 'WIPRO.NS', 'TATAMOTORS.NS'
        ]
    
    # Initialize all strategies
    strategies = [
        IchimokuStrategy(atr_sl_mult=2.0, atr_tp_mult=4.0),
        EMACrossoverStrategy(fast_period=20, slow_period=50, atr_sl_mult=2.0, atr_tp_mult=4.0),
        RSIReversalStrategy(rsi_period=14, oversold=30, overbought=70, atr_sl_mult=1.5, atr_tp_mult=3.0),
        BreakoutStrategy(lookback=252, atr_sl_mult=2.0, atr_tp_mult=4.0),
        PullbackStrategy(fast_ema=20, slow_ema=50, atr_sl_mult=1.5, atr_tp_mult=3.0),
        MACDStrategy(fast=12, slow=26, signal=9, atr_sl_mult=2.0, atr_tp_mult=4.0),
    ]
    
    print("=" * 100)
    print("SWING TRADING STRATEGIES - COMPREHENSIVE BACKTEST")
    print("=" * 100)
    print(f"\nData: 2 Years Daily | Stocks: {len(symbols)}")
    print(f"Strategies: {len(strategies)}")
    print("Costs: 0.1% slippage + 0.1% commission")
    print("\nStrategies being tested:")
    for i, s in enumerate(strategies, 1):
        print(f"  {i}. {s.name}")
    
    # Results storage
    all_results = {s.name: [] for s in strategies}
    
    # Run backtests
    print("\n" + "-" * 100)
    print("Processing stocks...")
    print("-" * 100)
    
    for symbol in symbols:
        try:
            df = download_data(symbol, period='2y')
            
            if df.empty or len(df) < 300:
                print(f"  {symbol}: Skipped (insufficient data)")
                continue
            
            print(f"  {symbol}: {len(df)} bars", end="")
            
            for strategy in strategies:
                result = strategy.backtest(df)
                all_results[strategy.name].append({
                    'symbol': symbol,
                    'return': result['total_return_pct'],
                    'trades': result['num_trades'],
                    'win_rate': result['win_rate'],
                    'profit_factor': result['profit_factor'],
                    'avg_win': result['avg_win'],
                    'avg_loss': result['avg_loss'],
                    'long_trades': result['long_trades'],
                    'short_trades': result['short_trades'],
                    'trades_df': result['trades']
                })
            
            print(" -> Done")
            
        except Exception as e:
            print(f"  {symbol}: Error - {e}")
    
    # Print results for each strategy
    print("\n" + "=" * 100)
    print("RESULTS BY STRATEGY")
    print("=" * 100)
    
    strategy_summary = []
    
    for strategy_name, results in all_results.items():
        if not results:
            continue
        
        results_df = pd.DataFrame(results)
        
        print(f"\n{'='*50}")
        print(f"STRATEGY: {strategy_name}")
        print(f"{'='*50}")
        
        print(f"\n{'Symbol':<15} | {'Return':<8} | {'Trades':<6} | {'WR':<6} | {'PF':<6} | {'AvgWin':<7} | {'AvgLoss':<7}")
        print("-" * 75)
        
        for _, r in results_df.iterrows():
            status = "OK" if r['return'] > 0 else "--"
            pf = f"{r['profit_factor']:.2f}" if r['profit_factor'] != float('inf') else "INF"
            print(f"{r['symbol']:<15} | {r['return']:>6.2f}% | {r['trades']:>6} | {r['win_rate']:>5.1%} | {pf:>6} | {r['avg_win']:>6.2f}% | {r['avg_loss']:>6.2f}% {status}")
        
        # Strategy summary
        avg_return = results_df['return'].mean()
        total_trades = results_df['trades'].sum()
        avg_wr = results_df['win_rate'].mean()
        profitable_stocks = (results_df['return'] > 0).sum()
        
        print("-" * 75)
        print(f"{'AVERAGE':<15} | {avg_return:>6.2f}% | {total_trades:>6} | {avg_wr:>5.1%} | {'--':>6} | {'--':>7} | {'--':>7}")
        print(f"Profitable: {profitable_stocks}/{len(results_df)} stocks")
        
        strategy_summary.append({
            'Strategy': strategy_name,
            'Avg Return': avg_return,
            'Total Trades': total_trades,
            'Avg WR': avg_wr,
            'Profitable Stocks': f"{profitable_stocks}/{len(results_df)}"
        })
    
    # Final comparison
    print("\n" + "=" * 100)
    print("STRATEGY COMPARISON (RANKED BY AVERAGE RETURN)")
    print("=" * 100)
    
    summary_df = pd.DataFrame(strategy_summary)
    summary_df = summary_df.sort_values('Avg Return', ascending=False)
    
    print(f"\n{'Rank':<5} | {'Strategy':<30} | {'Avg Return':<12} | {'Trades':<8} | {'Win Rate':<10} | {'Profitable'}")
    print("-" * 90)
    
    for rank, (_, row) in enumerate(summary_df.iterrows(), 1):
        verdict = "BEST" if rank == 1 else ("GOOD" if row['Avg Return'] > 0 else "AVOID")
        print(f"{rank:<5} | {row['Strategy']:<30} | {row['Avg Return']:>10.2f}% | {row['Total Trades']:>8} | {row['Avg WR']:>9.1%} | {row['Profitable Stocks']} {verdict}")
    
    # Show sample trades from best strategy
    best_strategy = summary_df.iloc[0]['Strategy']
    print(f"\n" + "=" * 100)
    print(f"SAMPLE TRADES FROM BEST STRATEGY: {best_strategy}")
    print("=" * 100)
    
    best_results = all_results[best_strategy]
    for r in sorted(best_results, key=lambda x: x['return'], reverse=True)[:3]:
        if len(r['trades_df']) > 0:
            print(f"\n{r['symbol']} ({r['return']:.2f}% return, {r['trades']} trades):")
            for _, t in r['trades_df'].head(3).iterrows():
                status = "WIN" if t['pnl'] > 0 else "LOSS"
                print(f"  {t['direction']:<5} | Entry: {t['entry_price']:>10.2f} | Exit: {t['exit_price']:>10.2f} | "
                      f"Ret: {t['return_pct']:>+7.2f}% | {t['exit_reason']:<11} | {status}")
    
    return all_results, summary_df


# =====================================================================
# MAIN
# =====================================================================

if __name__ == "__main__":
    results, summary = run_swing_backtest()
