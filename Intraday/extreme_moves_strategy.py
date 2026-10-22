"""
Extreme Moves Strategy - Trade Only the Obvious

Philosophy:
- Most intraday trading loses money due to transaction costs and noise
- BUT extreme moves (big gaps, multi-day momentum) have higher probability
- Trade ONLY when the setup is screaming at you
- Be very patient - maybe 1-2 trades per week

Criteria for a trade:
1. Gap > 1% (strong overnight move)
2. First candle confirms direction (not against the gap)
3. Multi-day trend aligned (last 2+ days same direction)
4. Let it run with trailing stop, exit at EOD

This is about QUALITY over QUANTITY.
"""

import pandas as pd
import numpy as np
import yfinance as yf
from datetime import datetime, timedelta
from typing import Dict, List, Tuple
import warnings
warnings.filterwarnings('ignore')


def download_data(symbol: str, period: str = '60d', interval: str = '1h') -> pd.DataFrame:
    """Download data from Yahoo Finance."""
    data = yf.download(symbol, period=period, interval=interval, progress=False)
    if isinstance(data.columns, pd.MultiIndex):
        data.columns = data.columns.get_level_values(0)
    return data


class ExtremeMoveStrategy:
    """
    Extreme Move Strategy - Trade Only When It's Obvious
    
    Entry Criteria (ALL must be met):
    1. Gap > min_gap_pct (default 1%)
    2. First candle in gap direction
    3. Previous day closed in same direction (trend confirmation)
    4. No gap reversal signal (first candle doesn't close gap)
    
    Exit:
    - Trailing stop (loose, to let winners run)
    - End of day
    """
    
    def __init__(self, 
                 min_gap_pct: float = 1.0,    # Only trade gaps > 1%
                 require_trend: bool = True,   # Require multi-day trend
                 trail_atr_mult: float = 2.0,  # Loose trailing stop
                 min_first_candle_pct: float = 0.0):  # First candle min body
        self.min_gap_pct = min_gap_pct
        self.require_trend = require_trend
        self.trail_atr_mult = trail_atr_mult
        self.min_first_candle_pct = min_first_candle_pct
        self.name = f"Extreme ({min_gap_pct}%+ gap)"
    
    def backtest(self, df: pd.DataFrame, capital: float = 100000,
                 risk_pct: float = 0.02, slippage: float = 0.001) -> Dict:
        """Run backtest with strict filtering."""
        df = df.copy()
        
        # Calculate ATR
        tr1 = df['High'] - df['Low']
        tr2 = abs(df['High'] - df['Close'].shift(1))
        tr3 = abs(df['Low'] - df['Close'].shift(1))
        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        df['ATR'] = tr.rolling(window=14).mean()
        
        if not hasattr(df.index[0], 'date'):
            return self._empty_result(capital)
        
        df['Date'] = df.index.date
        
        # Get daily OHLC
        dates = sorted(df['Date'].unique())
        daily_data = {}
        
        for date in dates:
            day_df = df[df['Date'] == date]
            if len(day_df) > 0:
                daily_data[date] = {
                    'open': day_df['Open'].iloc[0],
                    'high': day_df['High'].max(),
                    'low': day_df['Low'].min(),
                    'close': day_df['Close'].iloc[-1],
                    'first_candle_dir': 1 if day_df.iloc[0]['Close'] > day_df.iloc[0]['Open'] else -1,
                    'first_candle_body': abs(day_df.iloc[0]['Close'] - day_df.iloc[0]['Open']) / day_df.iloc[0]['Open'] * 100,
                    'atr': day_df['ATR'].iloc[0] if not pd.isna(day_df['ATR'].iloc[0]) else day_df['High'].iloc[0] - day_df['Low'].iloc[0]
                }
        
        trades = []
        current_capital = capital
        
        for i, date in enumerate(dates):
            if i < 2:  # Need at least 2 days of history
                continue
            
            today = daily_data.get(date)
            yesterday = daily_data.get(dates[i-1])
            day_before = daily_data.get(dates[i-2])
            
            if not all([today, yesterday, day_before]):
                continue
            
            day_df = df[df['Date'] == date]
            if len(day_df) < 3:
                continue
            
            # CRITERION 1: Calculate gap
            gap_pct = (today['open'] / yesterday['close'] - 1) * 100
            
            # Skip if gap is too small
            if abs(gap_pct) < self.min_gap_pct:
                continue
            
            direction = 1 if gap_pct > 0 else -1
            
            # CRITERION 2: First candle must confirm
            if today['first_candle_dir'] != direction:
                continue
            
            # CRITERION 3: Multi-day trend (optional but recommended)
            if self.require_trend:
                yesterday_dir = 1 if yesterday['close'] > yesterday['open'] else -1
                day_before_dir = 1 if day_before['close'] > day_before['open'] else -1
                
                # At least yesterday must be in same direction
                if yesterday_dir != direction:
                    continue
            
            # CRITERION 4: First candle didn't close the gap
            if direction == 1:
                # Gap up - first candle shouldn't close below yesterday's close
                first_candle_close = day_df.iloc[0]['Close']
                if first_candle_close < yesterday['close']:
                    continue
            else:
                # Gap down - first candle shouldn't close above yesterday's close
                first_candle_close = day_df.iloc[0]['Close']
                if first_candle_close > yesterday['close']:
                    continue
            
            # ALL CRITERIA MET - TAKE THE TRADE
            entry_candle = day_df.iloc[1]  # Enter on second candle
            entry_price = entry_candle['Open'] * (1 + slippage * direction)
            
            atr = today['atr']
            
            # Loose stop to let winners run
            if direction == 1:
                initial_stop = entry_price - self.trail_atr_mult * atr
            else:
                initial_stop = entry_price + self.trail_atr_mult * atr
            
            # Position sizing (2% risk)
            risk_per_share = abs(entry_price - initial_stop)
            if risk_per_share <= 0:
                continue
            
            max_risk = current_capital * risk_pct
            shares = int(max_risk / risk_per_share)
            max_shares = int(current_capital * 0.25 / entry_price)
            shares = min(shares, max_shares)
            
            if shares <= 0:
                continue
            
            # Simulate day with trailing stop
            stop_loss = initial_stop
            best_price = entry_price
            exit_price = None
            exit_reason = None
            
            for j in range(2, len(day_df)):
                candle = day_df.iloc[j]
                
                if direction == 1:  # Long
                    # Update best price and trail stop
                    if candle['High'] > best_price:
                        best_price = candle['High']
                        new_stop = best_price - self.trail_atr_mult * atr
                        stop_loss = max(stop_loss, new_stop)
                    
                    # Check stop
                    if candle['Low'] <= stop_loss:
                        exit_price = stop_loss * (1 - slippage)
                        exit_reason = 'trailing_stop'
                        break
                else:  # Short
                    if candle['Low'] < best_price:
                        best_price = candle['Low']
                        new_stop = best_price + self.trail_atr_mult * atr
                        stop_loss = min(stop_loss, new_stop)
                    
                    if candle['High'] >= stop_loss:
                        exit_price = stop_loss * (1 + slippage)
                        exit_reason = 'trailing_stop'
                        break
            
            # EOD exit if no stop hit
            if exit_price is None:
                exit_price = day_df['Close'].iloc[-1]
                exit_reason = 'eod'
            
            # Calculate P&L
            if direction == 1:
                pnl = shares * (exit_price - entry_price)
                ret_pct = (exit_price / entry_price - 1) * 100
            else:
                pnl = shares * (entry_price - exit_price)
                ret_pct = (entry_price / exit_price - 1) * 100
            
            # Commission
            commission = shares * entry_price * 0.001 * 2  # Round trip
            pnl -= commission
            
            current_capital += pnl
            
            trades.append({
                'date': date,
                'gap_pct': gap_pct,
                'direction': 'LONG' if direction == 1 else 'SHORT',
                'entry': entry_price,
                'exit': exit_price,
                'best_price': best_price,
                'exit_reason': exit_reason,
                'shares': shares,
                'pnl': pnl,
                'return_pct': ret_pct,
                'capital': current_capital
            })
        
        return self._calculate_results(trades, capital, current_capital)
    
    def _empty_result(self, capital):
        return {
            'total_return_pct': 0,
            'num_trades': 0,
            'win_rate': 0,
            'profit_factor': 0,
            'avg_win': 0,
            'avg_loss': 0,
            'best_trade': 0,
            'worst_trade': 0,
            'final_capital': capital,
            'trades': pd.DataFrame()
        }
    
    def _calculate_results(self, trades: List[Dict], initial_capital: float, final_capital: float) -> Dict:
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
            
            best_trade = trades_df['return_pct'].max()
            worst_trade = trades_df['return_pct'].min()
        else:
            win_rate = 0
            profit_factor = 0
            avg_win = 0
            avg_loss = 0
            best_trade = 0
            worst_trade = 0
        
        return {
            'total_return_pct': total_return,
            'num_trades': len(trades_df),
            'win_rate': win_rate,
            'profit_factor': profit_factor,
            'avg_win': avg_win,
            'avg_loss': avg_loss,
            'best_trade': best_trade,
            'worst_trade': worst_trade,
            'final_capital': final_capital,
            'trades': trades_df
        }


def run_extreme_strategy(symbols: List[str] = None):
    """Run extreme moves strategy with different gap thresholds."""
    if symbols is None:
        symbols = [
            'IDEA.NS', 'RELIANCE.NS', 'TCS.NS', 'HDFCBANK.NS', 'INFY.NS',
            'SBIN.NS', 'BHARTIARTL.NS', 'ITC.NS', 'KOTAKBANK.NS', 'AXISBANK.NS',
            'LT.NS', 'MARUTI.NS', 'SUNPHARMA.NS', 'TITAN.NS', 'WIPRO.NS'
        ]
    
    # Test different gap thresholds
    gap_thresholds = [0.5, 0.75, 1.0, 1.5, 2.0]
    
    print("="*85)
    print("EXTREME MOVES STRATEGY - Only Trade the Obvious")
    print("="*85)
    print("\nCriteria: Gap > X% + First candle confirms + Previous day aligned")
    print("Settings: 2% risk, 2x ATR trailing stop, exit at EOD")
    
    results_by_threshold = {}
    
    for gap_thresh in gap_thresholds:
        strategy = ExtremeMoveStrategy(
            min_gap_pct=gap_thresh,
            require_trend=True,
            trail_atr_mult=2.0
        )
        
        all_results = []
        
        for symbol in symbols:
            try:
                df = download_data(symbol, '60d', '1h')
                
                if df.empty or len(df) < 100:
                    continue
                
                result = strategy.backtest(df)
                
                all_results.append({
                    'symbol': symbol,
                    'return': result['total_return_pct'],
                    'trades': result['num_trades'],
                    'win_rate': result['win_rate'],
                    'profit_factor': result['profit_factor'],
                    'best_trade': result['best_trade'],
                    'trades_df': result['trades']
                })
                
            except Exception as e:
                print(f"Error with {symbol}: {e}")
        
        results_by_threshold[gap_thresh] = all_results
    
    # Summary by threshold
    print("\n" + "="*85)
    print("RESULTS BY GAP THRESHOLD")
    print("="*85)
    print(f"\n{'Gap':>6} | {'Trades':>7} | {'Avg Ret':>8} | {'Win Rate':>8} | {'PF':>6} | {'Profitable':>10} | {'Best':>8}")
    print("-"*85)
    
    for gap_thresh, results in results_by_threshold.items():
        if results:
            results_df = pd.DataFrame(results)
            total_trades = results_df['trades'].sum()
            avg_return = results_df['return'].mean()
            avg_wr = results_df['win_rate'].mean()
            
            # Calculate overall PF
            all_pnl = []
            for r in results:
                if len(r['trades_df']) > 0:
                    all_pnl.extend(r['trades_df']['pnl'].tolist())
            
            if all_pnl:
                wins_sum = sum(p for p in all_pnl if p > 0)
                losses_sum = abs(sum(p for p in all_pnl if p < 0))
                overall_pf = wins_sum / losses_sum if losses_sum > 0 else 0
            else:
                overall_pf = 0
            
            profitable = (results_df['return'] > 0).sum()
            best = results_df['best_trade'].max() if len(results_df) > 0 else 0
            
            status = "OK" if avg_return > 0 else "--"
            print(f"{gap_thresh:>5}% | {total_trades:>7} | {avg_return:>7.2f}% | {avg_wr:>7.1%} | {overall_pf:>6.2f} | {profitable:>4}/{len(results_df):<4} | {best:>7.2f}% {status}")
    
    # Show individual stock results for best threshold
    best_threshold = max(results_by_threshold.keys(), 
                         key=lambda k: pd.DataFrame(results_by_threshold[k])['return'].mean() if results_by_threshold[k] else float('-inf'))
    
    print(f"\n\nBest Threshold: {best_threshold}% gap")
    print("="*85)
    print(f"\n{'Symbol':<15} | {'Return':>8} | {'Trades':>6} | {'WR':>6} | {'Best':>8} | {'Worst':>8}")
    print("-"*70)
    
    for r in sorted(results_by_threshold[best_threshold], key=lambda x: x['return'], reverse=True):
        if r['trades'] > 0:
            worst = r['trades_df']['return_pct'].min() if len(r['trades_df']) > 0 else 0
            print(f"{r['symbol']:<15} | {r['return']:>7.2f}% | {r['trades']:>6} | {r['win_rate']:>5.1%} | {r['best_trade']:>7.2f}% | {worst:>7.2f}%")
    
    # Show sample trades
    print("\n\nSAMPLE TRADES (showing trades with gap > 1%):")
    print("="*85)
    
    for r in results_by_threshold.get(1.0, []):
        if len(r['trades_df']) > 0:
            print(f"\n{r['symbol']}:")
            trades = r['trades_df'].head(5)
            for _, t in trades.iterrows():
                pnl_status = "WIN" if t['pnl'] > 0 else "LOSS"
                print(f"  {t['date']} | Gap: {t['gap_pct']:+.1f}% | {t['direction']} | Return: {t['return_pct']:+.2f}% | {pnl_status}")
    
    return results_by_threshold


def find_recent_extreme_moves(symbols: List[str] = None):
    """Find recent extreme moves to show what we would have traded."""
    if symbols is None:
        symbols = ['IDEA.NS', 'RELIANCE.NS', 'TCS.NS', 'HDFCBANK.NS', 'SBIN.NS']
    
    print("\n" + "="*85)
    print("RECENT EXTREME MOVES (Gaps > 1%)")
    print("="*85)
    
    for symbol in symbols:
        try:
            df = download_data(symbol, '30d', '1h')
            
            if df.empty:
                continue
            
            df['Date'] = df.index.date
            dates = sorted(df['Date'].unique())
            
            print(f"\n{symbol}:")
            found = 0
            
            for i in range(1, len(dates)):
                today = df[df['Date'] == dates[i]]
                yesterday = df[df['Date'] == dates[i-1]]
                
                if len(today) == 0 or len(yesterday) == 0:
                    continue
                
                today_open = today['Open'].iloc[0]
                yesterday_close = yesterday['Close'].iloc[-1]
                
                gap_pct = (today_open / yesterday_close - 1) * 100
                
                if abs(gap_pct) >= 1.0:
                    day_return = (today['Close'].iloc[-1] / today_open - 1) * 100
                    gap_dir = "UP" if gap_pct > 0 else "DOWN"
                    day_dir = "UP" if day_return > 0 else "DOWN"
                    continuation = "YES" if (gap_pct > 0 and day_return > 0) or (gap_pct < 0 and day_return < 0) else "NO"
                    
                    print(f"  {dates[i]} | Gap: {gap_pct:+5.2f}% ({gap_dir}) | Day: {day_return:+5.2f}% ({day_dir}) | Continuation: {continuation}")
                    found += 1
            
            if found == 0:
                print("  No gaps > 1% in last 30 days")
                
        except Exception as e:
            print(f"  Error: {e}")


if __name__ == "__main__":
    # Run main strategy test
    results = run_extreme_strategy()
    
    # Show recent extreme moves
    find_recent_extreme_moves()
