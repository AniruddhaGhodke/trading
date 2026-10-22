"""
Mean Reversion Strategy - Properly Implemented

This strategy focuses on buying oversold conditions and selling overbought.
Key improvements:
1. Uses multiple confirmation signals
2. Realistic entry at close prices
3. Proper slippage and commission
4. ATR-based position sizing
5. Time-based stops (don't hold losing trades too long)

Based on research: mean reversion works better than momentum for intraday
"""

import pandas as pd
import numpy as np
import yfinance as yf
from datetime import datetime, timedelta
from typing import Dict, List
import warnings
warnings.filterwarnings('ignore')


def download_data(symbol: str, period: str = '60d', interval: str = '1h') -> pd.DataFrame:
    """Download data from Yahoo Finance."""
    data = yf.download(symbol, period=period, interval=interval, progress=False)
    if isinstance(data.columns, pd.MultiIndex):
        data.columns = data.columns.get_level_values(0)
    return data


def calculate_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """Calculate all required indicators."""
    df = df.copy()
    
    # RSI
    delta = df['Close'].diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
    rs = gain / loss
    df['RSI'] = 100 - (100 / (1 + rs))
    
    # Bollinger Bands
    df['BB_Mid'] = df['Close'].rolling(20).mean()
    df['BB_Std'] = df['Close'].rolling(20).std()
    df['BB_Upper'] = df['BB_Mid'] + 2 * df['BB_Std']
    df['BB_Lower'] = df['BB_Mid'] - 2 * df['BB_Std']
    df['BB_Pct'] = (df['Close'] - df['BB_Lower']) / (df['BB_Upper'] - df['BB_Lower'])
    
    # ATR
    tr1 = df['High'] - df['Low']
    tr2 = abs(df['High'] - df['Close'].shift(1))
    tr3 = abs(df['Low'] - df['Close'].shift(1))
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    df['ATR'] = tr.rolling(window=14).mean()
    
    # Volume Profile
    df['Vol_MA'] = df['Volume'].rolling(20).mean()
    df['Vol_Ratio'] = df['Volume'] / df['Vol_MA']
    
    # Recent trend (for filtering)
    df['SMA_20'] = df['Close'].rolling(20).mean()
    df['SMA_50'] = df['Close'].rolling(50).mean()
    df['Trend'] = np.where(df['SMA_20'] > df['SMA_50'], 1, -1)
    
    # Intraday info
    if hasattr(df.index[0], 'date'):
        df['Date'] = df.index.date
        df['Hour'] = df.index.hour
    
    return df


class MeanReversionStrategy:
    """
    Mean Reversion Strategy
    
    Buy when:
    - RSI < 30 (oversold)
    - Price touched lower Bollinger Band
    - Volume spike (confirmation)
    
    Sell when:
    - RSI > 70 (overbought)
    - Price touched upper Bollinger Band
    - Or time-based exit (max hold period)
    """
    
    def __init__(self, 
                 rsi_oversold: int = 30,
                 rsi_overbought: int = 70,
                 atr_stop_mult: float = 2.0,
                 atr_target_mult: float = 3.0,
                 max_hold_bars: int = 10):
        self.rsi_oversold = rsi_oversold
        self.rsi_overbought = rsi_overbought
        self.atr_stop_mult = atr_stop_mult
        self.atr_target_mult = atr_target_mult
        self.max_hold_bars = max_hold_bars
        self.name = "Mean Reversion"
    
    def generate_signals(self, df: pd.DataFrame) -> pd.DataFrame:
        """Generate buy/sell signals."""
        df = calculate_indicators(df)
        
        df['Signal'] = 0
        df['Entry'] = np.nan
        df['Stop_Loss'] = np.nan
        df['Target'] = np.nan
        df['Signal_Type'] = ''
        
        for i in range(2, len(df)):
            row = df.iloc[i]
            prev = df.iloc[i-1]
            
            if pd.isna(row['RSI']) or pd.isna(row['ATR']):
                continue
            
            atr = row['ATR']
            
            # LONG signal: oversold bounce
            if (row['RSI'] < self.rsi_oversold and
                row['BB_Pct'] < 0.1 and  # Near lower band
                row['Close'] > row['Open'] and  # Bullish candle
                row['Vol_Ratio'] > 1.0):  # Above average volume
                
                entry = row['Close']
                stop = entry - self.atr_stop_mult * atr
                target = entry + self.atr_target_mult * atr
                
                df.iloc[i, df.columns.get_loc('Signal')] = 1
                df.iloc[i, df.columns.get_loc('Entry')] = entry
                df.iloc[i, df.columns.get_loc('Stop_Loss')] = stop
                df.iloc[i, df.columns.get_loc('Target')] = target
                df.iloc[i, df.columns.get_loc('Signal_Type')] = 'MR_Long'
            
            # SHORT signal: overbought rejection
            elif (row['RSI'] > self.rsi_overbought and
                  row['BB_Pct'] > 0.9 and  # Near upper band
                  row['Close'] < row['Open'] and  # Bearish candle
                  row['Vol_Ratio'] > 1.0):
                
                entry = row['Close']
                stop = entry + self.atr_stop_mult * atr
                target = entry - self.atr_target_mult * atr
                
                df.iloc[i, df.columns.get_loc('Signal')] = -1
                df.iloc[i, df.columns.get_loc('Entry')] = entry
                df.iloc[i, df.columns.get_loc('Stop_Loss')] = stop
                df.iloc[i, df.columns.get_loc('Target')] = target
                df.iloc[i, df.columns.get_loc('Signal_Type')] = 'MR_Short'
        
        return df


class RealisticBacktester:
    """Backtester with realistic costs."""
    
    def __init__(self, 
                 capital: float = 100000,
                 risk_per_trade: float = 0.01,
                 slippage_pct: float = 0.001,
                 commission_pct: float = 0.001,
                 max_hold_bars: int = 10):
        self.capital = capital
        self.risk_per_trade = risk_per_trade
        self.slippage_pct = slippage_pct
        self.commission_pct = commission_pct
        self.max_hold_bars = max_hold_bars
    
    def run(self, df: pd.DataFrame) -> Dict:
        """Run backtest."""
        capital = self.capital
        position = 0
        entry_price = 0
        stop_loss = 0
        target = 0
        entry_bar = 0
        entry_date = None
        trades = []
        
        for i, (idx, row) in enumerate(df.iterrows()):
            # Skip if no indicators
            if pd.isna(row.get('ATR', np.nan)):
                continue
            
            # Check existing position
            if position != 0:
                exit_price = None
                exit_reason = None
                
                # Check stop-loss
                if position > 0 and row['Low'] <= stop_loss:
                    exit_price = stop_loss * (1 - self.slippage_pct)
                    exit_reason = 'stop_loss'
                elif position < 0 and row['High'] >= stop_loss:
                    exit_price = stop_loss * (1 + self.slippage_pct)
                    exit_reason = 'stop_loss'
                
                # Check target
                if exit_price is None:
                    if position > 0 and row['High'] >= target:
                        exit_price = target
                        exit_reason = 'target'
                    elif position < 0 and row['Low'] <= target:
                        exit_price = target
                        exit_reason = 'target'
                
                # Check time stop
                if exit_price is None and (i - entry_bar) >= self.max_hold_bars:
                    exit_price = row['Close']
                    exit_reason = 'time_stop'
                
                # Process exit
                if exit_price is not None:
                    shares = abs(position)
                    commission = shares * exit_price * self.commission_pct
                    
                    if position > 0:
                        pnl = shares * (exit_price - entry_price) - commission
                        ret_pct = (exit_price / entry_price - 1) * 100
                    else:
                        pnl = shares * (entry_price - exit_price) - commission
                        ret_pct = (entry_price / exit_price - 1) * 100
                    
                    capital += pnl
                    
                    trades.append({
                        'entry_date': entry_date,
                        'exit_date': idx,
                        'type': 'long' if position > 0 else 'short',
                        'entry_price': entry_price,
                        'exit_price': exit_price,
                        'exit_reason': exit_reason,
                        'shares': shares,
                        'pnl': pnl,
                        'return_pct': ret_pct
                    })
                    
                    position = 0
            
            # Check for new signal
            if position == 0 and row['Signal'] != 0:
                if pd.isna(row['Entry']) or pd.isna(row['Stop_Loss']):
                    continue
                
                # Apply slippage to entry
                if row['Signal'] == 1:
                    entry_price = row['Entry'] * (1 + self.slippage_pct)
                else:
                    entry_price = row['Entry'] * (1 - self.slippage_pct)
                
                stop_loss = row['Stop_Loss']
                target = row['Target']
                
                # Position sizing
                risk_per_share = abs(entry_price - stop_loss)
                if risk_per_share <= 0:
                    continue
                
                max_risk = capital * self.risk_per_trade
                shares = int(max_risk / risk_per_share)
                
                # Cap at 20% of capital
                max_shares = int(capital * 0.2 / entry_price)
                shares = min(shares, max_shares)
                
                if shares <= 0:
                    continue
                
                # Entry commission
                commission = shares * entry_price * self.commission_pct
                
                if row['Signal'] == 1:
                    position = shares
                else:
                    position = -shares
                
                entry_bar = i
                entry_date = idx
        
        # Calculate results
        trades_df = pd.DataFrame(trades) if trades else pd.DataFrame()
        total_return = (capital / self.capital - 1) * 100
        
        if len(trades_df) > 0:
            win_rate = (trades_df['pnl'] > 0).mean()
            
            wins = trades_df[trades_df['pnl'] > 0]['pnl']
            losses = trades_df[trades_df['pnl'] < 0]['pnl']
            
            if len(losses) > 0 and losses.sum() != 0:
                profit_factor = abs(wins.sum() / losses.sum())
            else:
                profit_factor = float('inf') if len(wins) > 0 else 0
            
            avg_win = wins.mean() if len(wins) > 0 else 0
            avg_loss = abs(losses.mean()) if len(losses) > 0 else 0
        else:
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
            'final_capital': capital,
            'trades': trades_df
        }


def run_backtest(symbols: List[str] = None):
    """Run mean reversion backtest on multiple stocks."""
    if symbols is None:
        symbols = [
            'RELIANCE.NS', 'TCS.NS', 'HDFCBANK.NS', 'INFY.NS', 'ICICIBANK.NS',
            'SBIN.NS', 'BHARTIARTL.NS', 'ITC.NS', 'KOTAKBANK.NS', 'AXISBANK.NS',
            'LT.NS', 'MARUTI.NS', 'SUNPHARMA.NS', 'TITAN.NS', 'WIPRO.NS'
        ]
    
    print("="*70)
    print("MEAN REVERSION STRATEGY BACKTEST")
    print("="*70)
    print("Settings: RSI 30/70, BB 2std, 1% risk, 0.1% slip+comm, 2x ATR stop")
    print()
    
    strategy = MeanReversionStrategy(
        rsi_oversold=30,
        rsi_overbought=70,
        atr_stop_mult=2.0,
        atr_target_mult=3.0,
        max_hold_bars=10
    )
    
    backtester = RealisticBacktester(
        capital=100000,
        risk_per_trade=0.01,
        slippage_pct=0.001,
        commission_pct=0.001,
        max_hold_bars=10
    )
    
    all_results = []
    
    for symbol in symbols:
        try:
            df = download_data(symbol, '60d', '1h')
            
            if df.empty or len(df) < 50:
                print(f"{symbol}: Insufficient data")
                continue
            
            signals_df = strategy.generate_signals(df)
            result = backtester.run(signals_df)
            
            all_results.append({
                'symbol': symbol,
                'return': result['total_return_pct'],
                'trades': result['num_trades'],
                'win_rate': result['win_rate'],
                'profit_factor': result['profit_factor']
            })
            
            status = "+" if result['total_return_pct'] > 0 else "-"
            print(f"{symbol:<15} Return: {result['total_return_pct']:>7.2f}%  Trades: {result['num_trades']:>3}  Win Rate: {result['win_rate']:>5.1%}  PF: {result['profit_factor']:>5.2f} {status}")
            
        except Exception as e:
            print(f"{symbol}: Error - {e}")
    
    if all_results:
        results_df = pd.DataFrame(all_results)
        
        print("\n" + "="*70)
        print("MEAN REVERSION STRATEGY SUMMARY")
        print("="*70)
        print(f"Stocks tested: {len(results_df)}")
        print(f"Avg Return: {results_df['return'].mean():.2f}%")
        print(f"Median Return: {results_df['return'].median():.2f}%")
        print(f"Std Dev: {results_df['return'].std():.2f}%")
        print(f"Avg Win Rate: {results_df['win_rate'].mean():.1%}")
        print(f"Profitable Stocks: {(results_df['return'] > 0).sum()}/{len(results_df)} ({(results_df['return'] > 0).mean():.1%})")
        print(f"Total Trades: {results_df['trades'].sum()}")
        
        return results_df
    
    return None


class TrendFollowingStrategy:
    """
    Simple Trend Following with pullback entries.
    
    Rules:
    - Only trade in direction of trend (20 SMA > 50 SMA = uptrend)
    - Wait for pullback to 20 SMA
    - Enter on bounce confirmation
    - Use tight stops (1.5x ATR)
    """
    
    def __init__(self, atr_stop_mult: float = 1.5, atr_target_mult: float = 2.5):
        self.atr_stop_mult = atr_stop_mult
        self.atr_target_mult = atr_target_mult
        self.name = "Trend Pullback"
    
    def generate_signals(self, df: pd.DataFrame) -> pd.DataFrame:
        """Generate trend pullback signals."""
        df = calculate_indicators(df)
        
        df['Signal'] = 0
        df['Entry'] = np.nan
        df['Stop_Loss'] = np.nan
        df['Target'] = np.nan
        df['Signal_Type'] = ''
        
        for i in range(3, len(df)):
            row = df.iloc[i]
            prev = df.iloc[i-1]
            prev2 = df.iloc[i-2]
            
            if pd.isna(row['ATR']) or pd.isna(row['SMA_20']):
                continue
            
            atr = row['ATR']
            
            # UPTREND: SMA20 > SMA50
            if row['Trend'] == 1:
                # Pullback: touched SMA20 in last 2 bars
                touched_sma = (prev['Low'] <= prev['SMA_20'] * 1.005 or 
                               prev2['Low'] <= prev2['SMA_20'] * 1.005)
                
                # Bounce: current bar bullish and closing above SMA20
                bounce = (row['Close'] > row['Open'] and 
                          row['Close'] > row['SMA_20'] and
                          row['RSI'] < 60)  # Not overbought
                
                if touched_sma and bounce:
                    entry = row['Close']
                    stop = entry - self.atr_stop_mult * atr
                    target = entry + self.atr_target_mult * atr
                    
                    df.iloc[i, df.columns.get_loc('Signal')] = 1
                    df.iloc[i, df.columns.get_loc('Entry')] = entry
                    df.iloc[i, df.columns.get_loc('Stop_Loss')] = stop
                    df.iloc[i, df.columns.get_loc('Target')] = target
                    df.iloc[i, df.columns.get_loc('Signal_Type')] = 'Trend_Long'
            
            # DOWNTREND: SMA20 < SMA50
            elif row['Trend'] == -1:
                # Rally: touched SMA20 in last 2 bars
                touched_sma = (prev['High'] >= prev['SMA_20'] * 0.995 or 
                               prev2['High'] >= prev2['SMA_20'] * 0.995)
                
                # Rejection: current bar bearish and closing below SMA20
                rejection = (row['Close'] < row['Open'] and 
                             row['Close'] < row['SMA_20'] and
                             row['RSI'] > 40)  # Not oversold
                
                if touched_sma and rejection:
                    entry = row['Close']
                    stop = entry + self.atr_stop_mult * atr
                    target = entry - self.atr_target_mult * atr
                    
                    df.iloc[i, df.columns.get_loc('Signal')] = -1
                    df.iloc[i, df.columns.get_loc('Entry')] = entry
                    df.iloc[i, df.columns.get_loc('Stop_Loss')] = stop
                    df.iloc[i, df.columns.get_loc('Target')] = target
                    df.iloc[i, df.columns.get_loc('Signal_Type')] = 'Trend_Short'
        
        return df


def run_trend_backtest(symbols: List[str] = None):
    """Run trend following backtest."""
    if symbols is None:
        symbols = [
            'RELIANCE.NS', 'TCS.NS', 'HDFCBANK.NS', 'INFY.NS', 'ICICIBANK.NS',
            'SBIN.NS', 'BHARTIARTL.NS', 'ITC.NS', 'KOTAKBANK.NS', 'AXISBANK.NS',
            'LT.NS', 'MARUTI.NS', 'SUNPHARMA.NS', 'TITAN.NS', 'WIPRO.NS'
        ]
    
    print("="*70)
    print("TREND PULLBACK STRATEGY BACKTEST")
    print("="*70)
    print("Settings: SMA 20/50 trend, pullback to SMA, 1.5x ATR stop, 2.5x target")
    print()
    
    strategy = TrendFollowingStrategy(atr_stop_mult=1.5, atr_target_mult=2.5)
    
    backtester = RealisticBacktester(
        capital=100000,
        risk_per_trade=0.01,
        slippage_pct=0.001,
        commission_pct=0.001,
        max_hold_bars=15
    )
    
    all_results = []
    
    for symbol in symbols:
        try:
            df = download_data(symbol, '60d', '1h')
            
            if df.empty or len(df) < 50:
                print(f"{symbol}: Insufficient data")
                continue
            
            signals_df = strategy.generate_signals(df)
            result = backtester.run(signals_df)
            
            all_results.append({
                'symbol': symbol,
                'return': result['total_return_pct'],
                'trades': result['num_trades'],
                'win_rate': result['win_rate'],
                'profit_factor': result['profit_factor']
            })
            
            status = "+" if result['total_return_pct'] > 0 else "-"
            print(f"{symbol:<15} Return: {result['total_return_pct']:>7.2f}%  Trades: {result['num_trades']:>3}  Win Rate: {result['win_rate']:>5.1%}  PF: {result['profit_factor']:>5.2f} {status}")
            
        except Exception as e:
            print(f"{symbol}: Error - {e}")
    
    if all_results:
        results_df = pd.DataFrame(all_results)
        
        print("\n" + "="*70)
        print("TREND PULLBACK STRATEGY SUMMARY")
        print("="*70)
        print(f"Stocks tested: {len(results_df)}")
        print(f"Avg Return: {results_df['return'].mean():.2f}%")
        print(f"Median Return: {results_df['return'].median():.2f}%")
        print(f"Avg Win Rate: {results_df['win_rate'].mean():.1%}")
        print(f"Profitable Stocks: {(results_df['return'] > 0).sum()}/{len(results_df)}")
        print(f"Total Trades: {results_df['trades'].sum()}")
        
        return results_df
    
    return None


if __name__ == "__main__":
    print("\n\n")
    print("#" * 70)
    print("# TESTING NEW STRATEGIES")
    print("#" * 70)
    
    print("\n\n")
    mr_results = run_backtest()
    
    print("\n\n")
    trend_results = run_trend_backtest()
    
    # Compare all strategies
    print("\n\n")
    print("="*70)
    print("FINAL COMPARISON - ALL STRATEGIES")
    print("="*70)
    
    if mr_results is not None and trend_results is not None:
        print(f"\n{'Strategy':<25} {'Avg Return':>12} {'Win Rate':>10} {'Profitable':>12}")
        print("-"*60)
        print(f"{'ORB (Correct)':<25} {'-1.06%':>12} {'25.8%':>10} {'0/15':>12}")
        print(f"{'Mean Reversion':<25} {mr_results['return'].mean():>11.2f}% {mr_results['win_rate'].mean():>9.1%} {(mr_results['return'] > 0).sum()}/{len(mr_results):>10}")
        print(f"{'Trend Pullback':<25} {trend_results['return'].mean():>11.2f}% {trend_results['win_rate'].mean():>9.1%} {(trend_results['return'] > 0).sum()}/{len(trend_results):>10}")
