"""
Simple First Candle Direction Strategy

The simplest possible approach:
1. Look at the first candle of the day
2. If it's red, SHORT
3. If it's green, LONG
4. Use trailing stop to let winners run
5. Exit at EOD

No fancy indicators, no confirmation - just ride the trend.
"""

import pandas as pd
import numpy as np
import yfinance as yf
from datetime import datetime
from typing import Dict, List
import warnings
warnings.filterwarnings('ignore')


def download_data(symbol: str, period: str = '60d', interval: str = '1h') -> pd.DataFrame:
    """Download data from Yahoo Finance."""
    data = yf.download(symbol, period=period, interval=interval, progress=False)
    if isinstance(data.columns, pd.MultiIndex):
        data.columns = data.columns.get_level_values(0)
    return data


class FirstCandleStrategy:
    """
    First Candle Direction Strategy
    
    Super simple:
    - First candle RED -> SHORT for the day
    - First candle GREEN -> LONG for the day
    - Trail stop at high/low of last N candles
    - Exit at EOD
    """
    
    def __init__(self, trail_candles: int = 2, min_body_pct: float = 0.1):
        self.trail_candles = trail_candles
        self.min_body_pct = min_body_pct  # Minimum body size (0.1%)
        self.name = "First Candle"
    
    def backtest(self, df: pd.DataFrame, capital: float = 100000, 
                 risk_pct: float = 0.02, slippage: float = 0.001) -> Dict:
        """Run simple backtest."""
        df = df.copy()
        
        # Calculate indicators
        df['Body'] = df['Close'] - df['Open']
        df['Body_Pct'] = abs(df['Body']) / df['Open'] * 100
        df['Direction'] = np.where(df['Body'] > 0, 1, np.where(df['Body'] < 0, -1, 0))
        
        # ATR for position sizing
        tr1 = df['High'] - df['Low']
        tr2 = abs(df['High'] - df['Close'].shift(1))
        tr3 = abs(df['Low'] - df['Close'].shift(1))
        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        df['ATR'] = tr.rolling(window=14).mean()
        
        if hasattr(df.index[0], 'date'):
            df['Date'] = df.index.date
        else:
            return {'total_return_pct': 0, 'trades': pd.DataFrame()}
        
        trades = []
        current_capital = capital
        
        for date in df['Date'].unique():
            day_data = df[df['Date'] == date]
            
            if len(day_data) < 3:
                continue
            
            first_candle = day_data.iloc[0]
            
            # Skip if first candle is too small
            if first_candle['Body_Pct'] < self.min_body_pct:
                continue
            
            # Determine direction from first candle
            direction = first_candle['Direction']
            if direction == 0:
                continue
            
            # Entry on second candle
            entry_candle = day_data.iloc[1]
            entry_price = entry_candle['Open'] * (1 + slippage * direction)  # Slippage
            
            atr = first_candle['ATR'] if not pd.isna(first_candle['ATR']) else (first_candle['High'] - first_candle['Low'])
            
            # Initial stop
            if direction == 1:  # Long
                stop_loss = entry_price - 2 * atr
            else:  # Short
                stop_loss = entry_price + 2 * atr
            
            # Position sizing
            risk_per_share = abs(entry_price - stop_loss)
            if risk_per_share <= 0:
                continue
            
            max_risk = current_capital * risk_pct
            shares = int(max_risk / risk_per_share)
            max_shares = int(current_capital * 0.25 / entry_price)
            shares = min(shares, max_shares)
            
            if shares <= 0:
                continue
            
            # Simulate the day with trailing stop
            highest = entry_price
            lowest = entry_price
            exit_price = None
            exit_reason = None
            
            for i in range(2, len(day_data)):
                candle = day_data.iloc[i]
                
                if direction == 1:  # Long
                    highest = max(highest, candle['High'])
                    # Trail stop: 2 candle low or initial stop
                    recent_low = day_data['Low'].iloc[max(0, i-self.trail_candles):i].min()
                    trail_stop = max(stop_loss, recent_low - 0.5 * atr)
                    
                    if candle['Low'] <= trail_stop:
                        exit_price = trail_stop * (1 - slippage)
                        exit_reason = 'stop'
                        break
                else:  # Short
                    lowest = min(lowest, candle['Low'])
                    recent_high = day_data['High'].iloc[max(0, i-self.trail_candles):i].max()
                    trail_stop = min(stop_loss, recent_high + 0.5 * atr)
                    
                    if candle['High'] >= trail_stop:
                        exit_price = trail_stop * (1 + slippage)
                        exit_reason = 'stop'
                        break
            
            # EOD exit if no stop hit
            if exit_price is None:
                exit_price = day_data['Close'].iloc[-1]
                exit_reason = 'eod'
            
            # Calculate P&L
            if direction == 1:
                pnl = shares * (exit_price - entry_price)
                ret_pct = (exit_price / entry_price - 1) * 100
            else:
                pnl = shares * (entry_price - exit_price)
                ret_pct = (entry_price / exit_price - 1) * 100
            
            commission = shares * entry_price * 0.001 * 2  # Round trip
            pnl -= commission
            
            current_capital += pnl
            
            trades.append({
                'date': date,
                'direction': 'LONG' if direction == 1 else 'SHORT',
                'first_candle_body': first_candle['Body_Pct'],
                'entry': entry_price,
                'exit': exit_price,
                'exit_reason': exit_reason,
                'pnl': pnl,
                'return_pct': ret_pct,
                'capital': current_capital
            })
        
        trades_df = pd.DataFrame(trades) if trades else pd.DataFrame()
        total_return = (current_capital / capital - 1) * 100
        
        if len(trades_df) > 0:
            win_rate = (trades_df['pnl'] > 0).mean()
            wins = trades_df[trades_df['pnl'] > 0]
            losses = trades_df[trades_df['pnl'] < 0]
            profit_factor = abs(wins['pnl'].sum() / losses['pnl'].sum()) if len(losses) > 0 and losses['pnl'].sum() != 0 else 0
            
            # Long vs Short performance
            long_trades = trades_df[trades_df['direction'] == 'LONG']
            short_trades = trades_df[trades_df['direction'] == 'SHORT']
            
            long_return = long_trades['pnl'].sum() if len(long_trades) > 0 else 0
            short_return = short_trades['pnl'].sum() if len(short_trades) > 0 else 0
        else:
            win_rate = 0
            profit_factor = 0
            long_return = 0
            short_return = 0
        
        return {
            'total_return_pct': total_return,
            'num_trades': len(trades_df),
            'win_rate': win_rate,
            'profit_factor': profit_factor,
            'long_pnl': long_return,
            'short_pnl': short_return,
            'final_capital': current_capital,
            'trades': trades_df
        }


class GapContinuationStrategy:
    """
    Gap Continuation Strategy
    
    Logic:
    - Calculate overnight gap (today's open vs yesterday's close)
    - If gap is significant (> 0.5%), trade in gap direction
    - Gaps tend to continue in direction, especially with momentum
    """
    
    def __init__(self, min_gap_pct: float = 0.5, trail_candles: int = 2):
        self.min_gap_pct = min_gap_pct
        self.trail_candles = trail_candles
        self.name = "Gap Continuation"
    
    def backtest(self, df: pd.DataFrame, capital: float = 100000,
                 risk_pct: float = 0.02, slippage: float = 0.001) -> Dict:
        """Run backtest."""
        df = df.copy()
        
        # ATR
        tr1 = df['High'] - df['Low']
        tr2 = abs(df['High'] - df['Close'].shift(1))
        tr3 = abs(df['Low'] - df['Close'].shift(1))
        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        df['ATR'] = tr.rolling(window=14).mean()
        
        if hasattr(df.index[0], 'date'):
            df['Date'] = df.index.date
        else:
            return {'total_return_pct': 0, 'trades': pd.DataFrame()}
        
        trades = []
        current_capital = capital
        prev_close = None
        
        for date in df['Date'].unique():
            day_data = df[df['Date'] == date]
            
            if len(day_data) < 3 or prev_close is None:
                if len(day_data) > 0:
                    prev_close = day_data['Close'].iloc[-1]
                continue
            
            # Calculate gap
            today_open = day_data['Open'].iloc[0]
            gap_pct = (today_open / prev_close - 1) * 100
            
            # Skip if gap is too small
            if abs(gap_pct) < self.min_gap_pct:
                prev_close = day_data['Close'].iloc[-1]
                continue
            
            # Determine direction from gap
            direction = 1 if gap_pct > 0 else -1
            
            # Confirm with first candle
            first_candle_dir = 1 if day_data.iloc[0]['Close'] > day_data.iloc[0]['Open'] else -1
            
            # Only trade if gap and first candle agree
            if first_candle_dir != direction:
                prev_close = day_data['Close'].iloc[-1]
                continue
            
            # Entry on second candle
            entry_candle = day_data.iloc[1]
            entry_price = entry_candle['Open'] * (1 + slippage * direction)
            
            atr = day_data['ATR'].iloc[0]
            if pd.isna(atr):
                atr = day_data['High'].iloc[0] - day_data['Low'].iloc[0]
            
            # Stop loss
            if direction == 1:
                stop_loss = entry_price - 2 * atr
            else:
                stop_loss = entry_price + 2 * atr
            
            # Position sizing
            risk_per_share = abs(entry_price - stop_loss)
            if risk_per_share <= 0:
                prev_close = day_data['Close'].iloc[-1]
                continue
            
            max_risk = current_capital * risk_pct
            shares = int(max_risk / risk_per_share)
            max_shares = int(current_capital * 0.25 / entry_price)
            shares = min(shares, max_shares)
            
            if shares <= 0:
                prev_close = day_data['Close'].iloc[-1]
                continue
            
            # Simulate day
            exit_price = None
            exit_reason = None
            
            for i in range(2, len(day_data)):
                candle = day_data.iloc[i]
                
                if direction == 1:
                    recent_low = day_data['Low'].iloc[max(0, i-self.trail_candles):i].min()
                    trail_stop = max(stop_loss, recent_low - 0.3 * atr)
                    
                    if candle['Low'] <= trail_stop:
                        exit_price = trail_stop * (1 - slippage)
                        exit_reason = 'stop'
                        break
                else:
                    recent_high = day_data['High'].iloc[max(0, i-self.trail_candles):i].max()
                    trail_stop = min(stop_loss, recent_high + 0.3 * atr)
                    
                    if candle['High'] >= trail_stop:
                        exit_price = trail_stop * (1 + slippage)
                        exit_reason = 'stop'
                        break
            
            if exit_price is None:
                exit_price = day_data['Close'].iloc[-1]
                exit_reason = 'eod'
            
            # P&L
            if direction == 1:
                pnl = shares * (exit_price - entry_price)
                ret_pct = (exit_price / entry_price - 1) * 100
            else:
                pnl = shares * (entry_price - exit_price)
                ret_pct = (entry_price / exit_price - 1) * 100
            
            commission = shares * entry_price * 0.001 * 2
            pnl -= commission
            
            current_capital += pnl
            
            trades.append({
                'date': date,
                'gap_pct': gap_pct,
                'direction': 'LONG' if direction == 1 else 'SHORT',
                'entry': entry_price,
                'exit': exit_price,
                'exit_reason': exit_reason,
                'pnl': pnl,
                'return_pct': ret_pct
            })
            
            prev_close = day_data['Close'].iloc[-1]
        
        trades_df = pd.DataFrame(trades) if trades else pd.DataFrame()
        total_return = (current_capital / capital - 1) * 100
        
        if len(trades_df) > 0:
            win_rate = (trades_df['pnl'] > 0).mean()
            wins = trades_df[trades_df['pnl'] > 0]
            losses = trades_df[trades_df['pnl'] < 0]
            profit_factor = abs(wins['pnl'].sum() / losses['pnl'].sum()) if len(losses) > 0 and losses['pnl'].sum() != 0 else 0
        else:
            win_rate = 0
            profit_factor = 0
        
        return {
            'total_return_pct': total_return,
            'num_trades': len(trades_df),
            'win_rate': win_rate,
            'profit_factor': profit_factor,
            'final_capital': current_capital,
            'trades': trades_df
        }


class MultiDayMomentumStrategy:
    """
    Multi-Day Momentum Strategy
    
    Logic:
    - Look at last 3 days' direction
    - If all 3 days closed lower -> SHORT bias
    - If today opens weak + first candle weak -> SHORT
    - Let it run with trailing stop
    
    This catches extended moves like the IDEA.NS drop.
    """
    
    def __init__(self, lookback_days: int = 3, trail_candles: int = 3):
        self.lookback_days = lookback_days
        self.trail_candles = trail_candles
        self.name = "Multi-Day Momentum"
    
    def backtest(self, df: pd.DataFrame, capital: float = 100000,
                 risk_pct: float = 0.02, slippage: float = 0.001) -> Dict:
        """Run backtest."""
        df = df.copy()
        
        # ATR
        tr1 = df['High'] - df['Low']
        tr2 = abs(df['High'] - df['Close'].shift(1))
        tr3 = abs(df['Low'] - df['Close'].shift(1))
        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        df['ATR'] = tr.rolling(window=14).mean()
        
        if hasattr(df.index[0], 'date'):
            df['Date'] = df.index.date
        else:
            return {'total_return_pct': 0, 'trades': pd.DataFrame()}
        
        # Get daily closes
        dates = df['Date'].unique()
        daily_closes = {}
        for date in dates:
            day_data = df[df['Date'] == date]
            if len(day_data) > 0:
                daily_closes[date] = {
                    'open': day_data['Open'].iloc[0],
                    'close': day_data['Close'].iloc[-1],
                    'direction': 1 if day_data['Close'].iloc[-1] > day_data['Open'].iloc[0] else -1
                }
        
        trades = []
        current_capital = capital
        
        for i, date in enumerate(dates):
            if i < self.lookback_days:
                continue
            
            day_data = df[df['Date'] == date]
            if len(day_data) < 3:
                continue
            
            # Check multi-day momentum
            lookback_dates = dates[i-self.lookback_days:i]
            directions = [daily_closes.get(d, {}).get('direction', 0) for d in lookback_dates]
            
            # All days same direction?
            if all(d == -1 for d in directions):
                bias = -1  # Bearish momentum
            elif all(d == 1 for d in directions):
                bias = 1  # Bullish momentum
            else:
                continue  # No clear momentum
            
            # Confirm with today's first candle
            first_candle = day_data.iloc[0]
            first_dir = 1 if first_candle['Close'] > first_candle['Open'] else -1
            
            if first_dir != bias:
                continue  # First candle against momentum, skip
            
            # Entry
            entry_candle = day_data.iloc[1]
            direction = bias
            entry_price = entry_candle['Open'] * (1 + slippage * direction)
            
            atr = first_candle['ATR']
            if pd.isna(atr):
                atr = first_candle['High'] - first_candle['Low']
            
            # Wide stop for momentum trades
            if direction == 1:
                stop_loss = entry_price - 2.5 * atr
            else:
                stop_loss = entry_price + 2.5 * atr
            
            # Position sizing
            risk_per_share = abs(entry_price - stop_loss)
            if risk_per_share <= 0:
                continue
            
            max_risk = current_capital * risk_pct
            shares = int(max_risk / risk_per_share)
            max_shares = int(current_capital * 0.25 / entry_price)
            shares = min(shares, max_shares)
            
            if shares <= 0:
                continue
            
            # Simulate with loose trailing stop
            exit_price = None
            exit_reason = None
            
            for j in range(2, len(day_data)):
                candle = day_data.iloc[j]
                
                if direction == 1:
                    recent_low = day_data['Low'].iloc[max(0, j-self.trail_candles):j].min()
                    trail_stop = max(stop_loss, recent_low - 0.5 * atr)
                    
                    if candle['Low'] <= trail_stop:
                        exit_price = trail_stop * (1 - slippage)
                        exit_reason = 'stop'
                        break
                else:
                    recent_high = day_data['High'].iloc[max(0, j-self.trail_candles):j].max()
                    trail_stop = min(stop_loss, recent_high + 0.5 * atr)
                    
                    if candle['High'] >= trail_stop:
                        exit_price = trail_stop * (1 + slippage)
                        exit_reason = 'stop'
                        break
            
            if exit_price is None:
                exit_price = day_data['Close'].iloc[-1]
                exit_reason = 'eod'
            
            # P&L
            if direction == 1:
                pnl = shares * (exit_price - entry_price)
                ret_pct = (exit_price / entry_price - 1) * 100
            else:
                pnl = shares * (entry_price - exit_price)
                ret_pct = (entry_price / exit_price - 1) * 100
            
            commission = shares * entry_price * 0.001 * 2
            pnl -= commission
            
            current_capital += pnl
            
            trades.append({
                'date': date,
                'lookback_dirs': directions,
                'direction': 'LONG' if direction == 1 else 'SHORT',
                'entry': entry_price,
                'exit': exit_price,
                'exit_reason': exit_reason,
                'pnl': pnl,
                'return_pct': ret_pct
            })
        
        trades_df = pd.DataFrame(trades) if trades else pd.DataFrame()
        total_return = (current_capital / capital - 1) * 100
        
        if len(trades_df) > 0:
            win_rate = (trades_df['pnl'] > 0).mean()
            wins = trades_df[trades_df['pnl'] > 0]
            losses = trades_df[trades_df['pnl'] < 0]
            profit_factor = abs(wins['pnl'].sum() / losses['pnl'].sum()) if len(losses) > 0 and losses['pnl'].sum() != 0 else 0
        else:
            win_rate = 0
            profit_factor = 0
        
        return {
            'total_return_pct': total_return,
            'num_trades': len(trades_df),
            'win_rate': win_rate,
            'profit_factor': profit_factor,
            'final_capital': current_capital,
            'trades': trades_df
        }


def run_simple_strategies(symbols: List[str] = None):
    """Run simple strategies on multiple stocks."""
    if symbols is None:
        symbols = [
            'IDEA.NS', 'RELIANCE.NS', 'TCS.NS', 'HDFCBANK.NS', 'INFY.NS',
            'SBIN.NS', 'BHARTIARTL.NS', 'ITC.NS', 'KOTAKBANK.NS', 'AXISBANK.NS'
        ]
    
    strategies = [
        FirstCandleStrategy(trail_candles=2, min_body_pct=0.1),
        GapContinuationStrategy(min_gap_pct=0.3, trail_candles=2),
        MultiDayMomentumStrategy(lookback_days=3, trail_candles=3)
    ]
    
    all_results = {s.name: [] for s in strategies}
    
    print("="*80)
    print("SIMPLE INTRADAY STRATEGIES - Back to Basics")
    print("="*80)
    print("Settings: 2% risk, 0.1% slippage, trailing stops\n")
    
    for symbol in symbols:
        try:
            df = download_data(symbol, '60d', '1h')
            
            if df.empty or len(df) < 100:
                print(f"{symbol}: Insufficient data")
                continue
            
            print(f"\n{symbol}:")
            
            for strategy in strategies:
                result = strategy.backtest(df)
                
                all_results[strategy.name].append({
                    'symbol': symbol,
                    'return': result['total_return_pct'],
                    'trades': result['num_trades'],
                    'win_rate': result['win_rate'],
                    'profit_factor': result['profit_factor']
                })
                
                status = "+" if result['total_return_pct'] > 0 else "-"
                pf = result['profit_factor'] if result['profit_factor'] < 100 else 99.99
                print(f"  {strategy.name:<20} Return: {result['total_return_pct']:>7.2f}%  Trades: {result['num_trades']:>2}  WR: {result['win_rate']:>5.1%}  PF: {pf:>5.2f} {status}")
                
        except Exception as e:
            print(f"{symbol}: Error - {e}")
    
    # Summary
    print("\n" + "="*80)
    print("STRATEGY COMPARISON")
    print("="*80)
    
    for name, results in all_results.items():
        if results:
            results_df = pd.DataFrame(results)
            avg_ret = results_df['return'].mean()
            status = "PROFITABLE" if avg_ret > 0 else "LOSING"
            print(f"\n{name}: {status}")
            print(f"  Avg Return: {avg_ret:.2f}%")
            print(f"  Median Return: {results_df['return'].median():.2f}%")
            print(f"  Avg Win Rate: {results_df['win_rate'].mean():.1%}")
            print(f"  Profitable Stocks: {(results_df['return'] > 0).sum()}/{len(results_df)}")
            print(f"  Total Trades: {results_df['trades'].sum()}")
            
            # Show best performing stock
            if len(results_df) > 0:
                best = results_df.loc[results_df['return'].idxmax()]
                print(f"  Best: {best['symbol']} (+{best['return']:.2f}%)")
    
    return all_results


def analyze_idea_jan20():
    """Deep dive into IDEA.NS Jan 20 to understand why we missed it."""
    print("\n" + "="*80)
    print("DEEP DIVE: IDEA.NS Jan 20, 2026")
    print("="*80)
    
    df = download_data('IDEA.NS', '60d', '1h')
    
    if df.empty:
        print("No data available")
        return
    
    df['Date'] = df.index.date
    df['Body'] = df['Close'] - df['Open']
    df['Body_Pct'] = df['Body'] / df['Open'] * 100
    
    target_date = pd.to_datetime('2026-01-20').date()
    day_data = df[df['Date'] == target_date]
    
    if len(day_data) == 0:
        print(f"No data for Jan 20, 2026")
        # Try to find the most recent day with big move
        print("\nLooking for recent big moves...")
        
        for date in sorted(df['Date'].unique(), reverse=True)[:10]:
            day = df[df['Date'] == date]
            if len(day) > 0:
                day_return = (day['Close'].iloc[-1] / day['Open'].iloc[0] - 1) * 100
                if abs(day_return) > 2:
                    print(f"  {date}: {day_return:+.2f}%")
        return
    
    print(f"\nDay's candles:")
    print(f"{'#':>3} {'Time':>8} {'Open':>8} {'High':>8} {'Low':>8} {'Close':>8} {'Body%':>8}")
    print("-"*60)
    
    for i, (idx, row) in enumerate(day_data.iterrows()):
        time_str = str(idx.hour) + ":" + str(idx.minute).zfill(2) if hasattr(idx, 'hour') else ""
        print(f"{i+1:>3} {time_str:>8} {row['Open']:>8.2f} {row['High']:>8.2f} {row['Low']:>8.2f} {row['Close']:>8.2f} {row['Body_Pct']:>7.2f}%")
    
    # Calculate day's stats
    open_price = day_data['Open'].iloc[0]
    close_price = day_data['Close'].iloc[-1]
    high_price = day_data['High'].max()
    low_price = day_data['Low'].min()
    
    day_return = (close_price / open_price - 1) * 100
    day_range = (high_price - low_price) / open_price * 100
    
    print(f"\nDay Summary:")
    print(f"  Open: {open_price:.2f}")
    print(f"  Close: {close_price:.2f}")
    print(f"  Day Return: {day_return:.2f}%")
    print(f"  Day Range: {day_range:.2f}%")
    
    # First candle analysis
    first = day_data.iloc[0]
    print(f"\nFirst Candle Analysis:")
    print(f"  Direction: {'DOWN' if first['Body'] < 0 else 'UP'}")
    print(f"  Body Size: {first['Body_Pct']:.2f}%")
    print(f"  Would trigger First Candle Strategy: {'YES' if abs(first['Body_Pct']) >= 0.1 else 'NO'}")
    
    # What would have happened if we shorted after first candle?
    if first['Body'] < 0 and len(day_data) > 1:
        entry = day_data.iloc[1]['Open']
        exit_price = close_price
        trade_return = (entry / exit_price - 1) * 100
        print(f"\n  IF WE SHORTED:")
        print(f"    Entry: {entry:.2f}")
        print(f"    Exit (EOD): {exit_price:.2f}")
        print(f"    Return: {trade_return:.2f}%")


if __name__ == "__main__":
    # Run simple strategies
    results = run_simple_strategies()
    
    # Analyze the specific day
    print("\n\n")
    analyze_idea_jan20()
