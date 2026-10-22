"""
Inside Candle Breakout Strategy - 15min Intraday

An inside candle occurs when a candle's entire range fits within the previous candle.
This signals consolidation before a breakout.

Strategy Rules:
- Timeframe: 15-minute candles
- Entry: Breakout above/below inside candle range with volume confirmation
- Stop: Opposite side of inside candle
- Target: 1.5x risk distance (R:R = 1:1.5)
- Filter: Mother bar must be >= 0.3% range, trade 10 AM - 2:30 PM only
"""

import pandas as pd
import numpy as np
import yfinance as yf
from typing import Dict, List, Optional
from datetime import datetime, time
import warnings
warnings.filterwarnings('ignore')


def download_data(symbol: str, period: str = '60d', interval: str = '15m') -> pd.DataFrame:
    """Download data from Yahoo Finance."""
    try:
        data = yf.download(symbol, period=period, interval=interval, progress=False)
        if isinstance(data.columns, pd.MultiIndex):
            data.columns = data.columns.get_level_values(0)
        if len(data) == 0:
            print(f"  No data for {symbol}")
            return pd.DataFrame()
        return data
    except Exception as e:
        print(f"  Error downloading {symbol}: {e}")
        return pd.DataFrame()


def calculate_atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Calculate Average True Range."""
    tr1 = df['High'] - df['Low']
    tr2 = abs(df['High'] - df['Close'].shift(1))
    tr3 = abs(df['Low'] - df['Close'].shift(1))
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    return tr.rolling(window=period).mean()


class InsideCandleStrategy:
    """Inside Candle Breakout Strategy for Intraday Trading."""
    
    def __init__(
        self,
        min_mother_range: float = 0.003,  # 0.3% minimum range
        volume_mult: float = 1.2,          # Breakout volume > 1.2x average
        rr_ratio: float = 1.5,             # Risk-Reward ratio
        # Yahoo Finance returns UTC times. IST = UTC + 5:30
        # 10:00 AM IST = 4:30 AM UTC, 2:30 PM IST = 9:00 AM UTC
        trade_start_utc: time = time(4, 30),   # 10:00 AM IST in UTC
        trade_end_utc: time = time(9, 0),      # 2:30 PM IST in UTC
    ):
        self.min_mother_range = min_mother_range
        self.volume_mult = volume_mult
        self.rr_ratio = rr_ratio
        self.trade_start_utc = trade_start_utc
        self.trade_end_utc = trade_end_utc
        self.name = "Inside Candle Breakout"
    
    def is_inside_candle(self, current_high: float, current_low: float,
                         prev_high: float, prev_low: float) -> bool:
        """Check if current candle is an inside candle."""
        return current_high < prev_high and current_low > prev_low
    
    def is_valid_time(self, dt: datetime) -> bool:
        """Check if time is within trading window (UTC times from yfinance)."""
        candle_time = dt.time()
        return self.trade_start_utc <= candle_time <= self.trade_end_utc
    
    def backtest(
        self,
        df: pd.DataFrame,
        capital: float = 100000,
        risk_pct: float = 0.02,
        slippage: float = 0.001,
        commission_pct: float = 0.001
    ) -> Dict:
        """Run backtest on the data."""
        
        if len(df) < 20:
            return self._empty_results(capital)
        
        df = df.copy()
        df['Date'] = df.index.date
        df['Time'] = df.index.time
        df['ATR'] = calculate_atr(df, 14)
        
        # Calculate average volume for volume filter
        df['AvgVolume'] = df['Volume'].rolling(window=20).mean()
        
        trades = []
        current_capital = capital
        
        # Process each trading day
        for date in df['Date'].unique():
            day_data = df[df['Date'] == date].copy()
            
            if len(day_data) < 5:  # Need minimum candles
                continue
            
            # Track if we already have a trade today
            in_trade = False
            
            # Scan for inside candles (start from 2nd candle)
            for i in range(1, len(day_data) - 1):
                if in_trade:
                    break  # Only one trade per day
                
                current = day_data.iloc[i]
                prev = day_data.iloc[i - 1]
                
                # Check if current is an inside candle
                if not self.is_inside_candle(
                    current['High'], current['Low'],
                    prev['High'], prev['Low']
                ):
                    continue
                
                # Check time filter for inside candle
                candle_dt = day_data.index[i]
                if not self.is_valid_time(candle_dt):
                    continue
                
                # Check mother bar (previous candle) range
                mother_range = (prev['High'] - prev['Low']) / prev['Close']
                if mother_range < self.min_mother_range:
                    continue
                
                # Inside candle found! Now wait for breakout on next candle
                inside_high = current['High']
                inside_low = current['Low']
                
                # Check breakout candle (next candle)
                breakout_candle = day_data.iloc[i + 1]
                breakout_close = breakout_candle['Close']
                breakout_volume = breakout_candle['Volume']
                avg_volume = current['AvgVolume']
                
                # Determine breakout direction
                direction = None
                if breakout_close > inside_high:
                    direction = 'LONG'
                elif breakout_close < inside_low:
                    direction = 'SHORT'
                else:
                    continue  # No breakout
                
                # Volume confirmation
                if pd.notna(avg_volume) and avg_volume > 0:
                    if breakout_volume < avg_volume * self.volume_mult:
                        continue  # Volume too low
                
                # Calculate entry, stop, target
                if direction == 'LONG':
                    entry_price = breakout_close * (1 + slippage)
                    stop_loss = inside_low * (1 - slippage)
                    risk_distance = entry_price - stop_loss
                    target = entry_price + (risk_distance * self.rr_ratio)
                else:  # SHORT
                    entry_price = breakout_close * (1 - slippage)
                    stop_loss = inside_high * (1 + slippage)
                    risk_distance = stop_loss - entry_price
                    target = entry_price - (risk_distance * self.rr_ratio)
                
                # Position sizing
                risk_per_share = risk_distance
                if risk_per_share <= 0:
                    continue
                
                max_risk = current_capital * risk_pct
                shares = int(max_risk / risk_per_share)
                max_shares = int(current_capital * 0.25 / entry_price)
                shares = min(shares, max_shares)
                
                if shares <= 0:
                    continue
                
                # Simulate trade through remaining day
                exit_price = None
                exit_reason = None
                remaining_data = day_data.iloc[i + 2:]  # After breakout candle
                
                for j in range(len(remaining_data)):
                    candle = remaining_data.iloc[j]
                    
                    if direction == 'LONG':
                        # Check stop hit
                        if candle['Low'] <= stop_loss:
                            exit_price = stop_loss * (1 - slippage)
                            exit_reason = 'Stop'
                            break
                        # Check target hit
                        if candle['High'] >= target:
                            exit_price = target * (1 - slippage)
                            exit_reason = 'Target'
                            break
                    else:  # SHORT
                        # Check stop hit
                        if candle['High'] >= stop_loss:
                            exit_price = stop_loss * (1 + slippage)
                            exit_reason = 'Stop'
                            break
                        # Check target hit
                        if candle['Low'] <= target:
                            exit_price = target * (1 + slippage)
                            exit_reason = 'Target'
                            break
                
                # EOD exit if no stop/target
                if exit_price is None:
                    eod_close = day_data['Close'].iloc[-1]
                    if direction == 'LONG':
                        exit_price = eod_close * (1 - slippage)
                    else:
                        exit_price = eod_close * (1 + slippage)
                    exit_reason = 'EOD'
                
                # Calculate PnL
                if direction == 'LONG':
                    pnl = shares * (exit_price - entry_price)
                else:
                    pnl = shares * (entry_price - exit_price)
                
                # Apply commission
                commission = shares * entry_price * commission_pct * 2  # Round trip
                pnl -= commission
                
                # Update capital
                current_capital += pnl
                
                # Record trade
                return_pct = (pnl / (shares * entry_price)) * 100
                trades.append({
                    'date': date,
                    'direction': direction,
                    'entry_price': entry_price,
                    'exit_price': exit_price,
                    'stop_loss': stop_loss,
                    'target': target,
                    'shares': shares,
                    'pnl': pnl,
                    'return_pct': return_pct,
                    'exit_reason': exit_reason,
                    'mother_range_pct': mother_range * 100
                })
                
                in_trade = True
        
        return self._calculate_results(trades, capital, current_capital)
    
    def _empty_results(self, capital: float) -> Dict:
        """Return empty results structure."""
        return {
            'total_return_pct': 0,
            'num_trades': 0,
            'win_rate': 0,
            'profit_factor': 0,
            'avg_win': 0,
            'avg_loss': 0,
            'avg_return': 0,
            'final_capital': capital,
            'trades': pd.DataFrame(),
            'exit_breakdown': {}
        }
    
    def _calculate_results(
        self,
        trades: List[Dict],
        initial_capital: float,
        final_capital: float
    ) -> Dict:
        """Calculate strategy performance metrics."""
        
        if not trades:
            return self._empty_results(initial_capital)
        
        trades_df = pd.DataFrame(trades)
        total_return = (final_capital / initial_capital - 1) * 100
        
        wins = trades_df[trades_df['pnl'] > 0]
        losses = trades_df[trades_df['pnl'] < 0]
        
        win_rate = len(wins) / len(trades_df) if len(trades_df) > 0 else 0
        
        avg_win = wins['return_pct'].mean() if len(wins) > 0 else 0
        avg_loss = abs(losses['return_pct'].mean()) if len(losses) > 0 else 0
        avg_return = trades_df['return_pct'].mean()
        
        total_wins = wins['pnl'].sum() if len(wins) > 0 else 0
        total_losses = abs(losses['pnl'].sum()) if len(losses) > 0 else 0
        profit_factor = total_wins / total_losses if total_losses > 0 else float('inf')
        
        # Exit breakdown
        exit_breakdown = trades_df['exit_reason'].value_counts().to_dict()
        
        return {
            'total_return_pct': total_return,
            'num_trades': len(trades_df),
            'win_rate': win_rate,
            'profit_factor': profit_factor,
            'avg_win': avg_win,
            'avg_loss': avg_loss,
            'avg_return': avg_return,
            'final_capital': final_capital,
            'trades': trades_df,
            'exit_breakdown': exit_breakdown
        }


def run_strategy(symbols: List[str] = None):
    """Run strategy on multiple stocks."""
    
    if symbols is None:
        symbols = [
            'RELIANCE.NS', 'TCS.NS', 'HDFCBANK.NS', 'INFY.NS', 'ICICIBANK.NS',
            'HINDUNILVR.NS', 'SBIN.NS', 'BHARTIARTL.NS', 'KOTAKBANK.NS', 'ITC.NS',
            'LT.NS', 'AXISBANK.NS', 'BAJFINANCE.NS', 'MARUTI.NS', 'TITAN.NS'
        ]
    
    print("=" * 70)
    print("INSIDE CANDLE BREAKOUT STRATEGY - 15min Intraday")
    print("=" * 70)
    print(f"Settings:")
    print(f"  - Timeframe: 15-minute candles")
    print(f"  - Direction: Both Long & Short")
    print(f"  - Risk-Reward: 1:1.5")
    print(f"  - Mother bar filter: >= 0.3% range")
    print(f"  - Volume filter: Breakout > 1.2x average")
    print(f"  - Trading hours: 10:00 AM - 2:30 PM")
    print(f"  - Costs: 0.1% slippage + 0.1% commission")
    print("=" * 70)
    print()
    
    strategy = InsideCandleStrategy()
    all_results = []
    all_trades = []
    
    print("Downloading data and running backtests...")
    print()
    
    for symbol in symbols:
        print(f"  Processing {symbol}...", end=" ")
        
        df = download_data(symbol, period='60d', interval='15m')
        
        if len(df) < 20:
            print("Insufficient data")
            continue
        
        result = strategy.backtest(df)
        
        all_results.append({
            'symbol': symbol,
            'trades': result['num_trades'],
            'win_rate': result['win_rate'] * 100,
            'avg_return': result['avg_return'],
            'total_return': result['total_return_pct'],
            'profit_factor': result['profit_factor'],
            'exit_breakdown': result['exit_breakdown']
        })
        
        if len(result['trades']) > 0:
            trades_copy = result['trades'].copy()
            trades_copy['symbol'] = symbol
            all_trades.append(trades_copy)
        
        print(f"Done - {result['num_trades']} trades, {result['win_rate']*100:.1f}% win rate")
    
    # Print summary table
    print()
    print("=" * 70)
    print("RESULTS SUMMARY")
    print("=" * 70)
    print(f"{'Stock':<15} {'Trades':>7} {'Win%':>8} {'Avg Ret':>10} {'Total Ret':>12} {'PF':>8}")
    print("-" * 70)
    
    total_trades = 0
    total_wins = 0
    weighted_return = 0
    
    for r in all_results:
        pf_str = f"{r['profit_factor']:.2f}" if r['profit_factor'] != float('inf') else "inf"
        print(f"{r['symbol']:<15} {r['trades']:>7} {r['win_rate']:>7.1f}% {r['avg_return']:>9.2f}% {r['total_return']:>11.2f}% {pf_str:>8}")
        
        total_trades += r['trades']
        total_wins += r['trades'] * r['win_rate'] / 100
        weighted_return += r['trades'] * r['avg_return']
    
    print("-" * 70)
    
    if total_trades > 0:
        overall_win_rate = (total_wins / total_trades) * 100
        overall_avg_return = weighted_return / total_trades
        print(f"{'OVERALL':<15} {total_trades:>7} {overall_win_rate:>7.1f}% {overall_avg_return:>9.2f}%")
    else:
        print("No trades executed")
    
    print("=" * 70)
    
    # Exit reason breakdown
    if all_trades:
        all_trades_df = pd.concat(all_trades, ignore_index=True)
        print()
        print("EXIT BREAKDOWN:")
        exit_counts = all_trades_df['exit_reason'].value_counts()
        for reason, count in exit_counts.items():
            pct = count / len(all_trades_df) * 100
            avg_ret = all_trades_df[all_trades_df['exit_reason'] == reason]['return_pct'].mean()
            print(f"  {reason}: {count} trades ({pct:.1f}%) - Avg return: {avg_ret:.2f}%")
        
        # Direction breakdown
        print()
        print("DIRECTION BREAKDOWN:")
        for direction in ['LONG', 'SHORT']:
            dir_trades = all_trades_df[all_trades_df['direction'] == direction]
            if len(dir_trades) > 0:
                win_rate = (dir_trades['pnl'] > 0).mean() * 100
                avg_ret = dir_trades['return_pct'].mean()
                print(f"  {direction}: {len(dir_trades)} trades, {win_rate:.1f}% win rate, {avg_ret:.2f}% avg return")
    
    print()
    print("=" * 70)
    print("INTERPRETATION:")
    print("=" * 70)
    
    if total_trades > 0 and overall_avg_return > 0:
        print("  Strategy shows POSITIVE average return.")
        print("  Consider paper trading for 1-2 months before live trading.")
    elif total_trades > 0:
        print("  Strategy shows NEGATIVE average return.")
        print("  This is expected - most intraday strategies lose after costs.")
        print("  Consider:")
        print("    - Adjusting filters (larger mother bar, higher volume threshold)")
        print("    - Using longer timeframe (30min or 1h)")
        print("    - Sticking with long-term investing instead")
    else:
        print("  No trades were generated with current filters.")
        print("  Consider relaxing the mother bar range filter.")
    
    return all_results, all_trades


if __name__ == "__main__":
    run_strategy()
