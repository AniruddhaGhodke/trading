"""
Fair Value Gap (FVG) Strategy for Indian Stocks

Based on ICT (Inner Circle Trader) concepts:
- FVG = Imbalance zone created by 3 consecutive candles
- Bearish FVG: Candle 1's LOW > Candle 3's HIGH (gap not filled by candle 2)
- Bullish FVG: Candle 1's HIGH < Candle 3's LOW

Strategy Logic:
1. Identify trend using EMA or consecutive candles
2. Detect FVG zones
3. Wait for price to retrace and touch the FVG
4. Enter in trend direction when FVG is touched
5. Stop loss beyond FVG zone
6. Target: 2:1 reward-risk or next FVG

This mimics what we see in the IDEA.NS chart - price creating FVGs 
during a downtrend, then retracing to touch them before continuing down.
"""

import pandas as pd
import numpy as np
import yfinance as yf
from datetime import datetime, timedelta
from typing import Dict, List, Tuple, Optional
from dataclasses import dataclass
import warnings
warnings.filterwarnings('ignore')


# =====================================================================
# FVG DETECTION
# =====================================================================

@dataclass
class FVG:
    """Fair Value Gap data structure."""
    index: int              # Bar index where FVG was created
    timestamp: datetime     # Time of FVG creation
    direction: str          # 'bullish' or 'bearish'
    top: float              # Top of FVG zone
    bottom: float           # Bottom of FVG zone
    midpoint: float         # Middle of FVG zone
    size_pct: float         # Size as % of price
    filled: bool = False    # Whether FVG has been filled
    touched: bool = False   # Whether price has touched the FVG
    fill_pct: float = 0.0   # How much of FVG has been filled (0-100%)


def detect_fvg(df: pd.DataFrame, min_gap_pct: float = 0.1) -> List[FVG]:
    """
    Detect Fair Value Gaps in price data.
    
    Bearish FVG (imbalance to the downside):
    - Candle 1 (i-2): The high before the gap
    - Candle 2 (i-1): The impulse candle (big red candle)
    - Candle 3 (i): The candle after - its HIGH < Candle 1's LOW
    - FVG zone: from Candle 3's HIGH to Candle 1's LOW
    
    Bullish FVG (imbalance to the upside):
    - Candle 1 (i-2): The low before the gap
    - Candle 2 (i-1): The impulse candle (big green candle)
    - Candle 3 (i): The candle after - its LOW > Candle 1's HIGH
    - FVG zone: from Candle 1's HIGH to Candle 3's LOW
    """
    fvgs = []
    
    for i in range(2, len(df)):
        candle1 = df.iloc[i-2]  # First candle
        candle2 = df.iloc[i-1]  # Middle (impulse) candle  
        candle3 = df.iloc[i]    # Third candle
        
        # Get timestamp
        if hasattr(df.index[i], 'to_pydatetime'):
            timestamp = df.index[i].to_pydatetime()
        else:
            timestamp = df.index[i]
        
        # Bearish FVG: Gap down
        # Candle 1's LOW > Candle 3's HIGH (there's a gap between them)
        if candle1['Low'] > candle3['High']:
            gap_size = candle1['Low'] - candle3['High']
            gap_pct = (gap_size / candle2['Close']) * 100
            
            if gap_pct >= min_gap_pct:
                fvg = FVG(
                    index=i,
                    timestamp=timestamp,
                    direction='bearish',
                    top=candle1['Low'],      # Top of FVG
                    bottom=candle3['High'],  # Bottom of FVG
                    midpoint=(candle1['Low'] + candle3['High']) / 2,
                    size_pct=gap_pct
                )
                fvgs.append(fvg)
        
        # Bullish FVG: Gap up
        # Candle 1's HIGH < Candle 3's LOW (there's a gap between them)
        if candle1['High'] < candle3['Low']:
            gap_size = candle3['Low'] - candle1['High']
            gap_pct = (gap_size / candle2['Close']) * 100
            
            if gap_pct >= min_gap_pct:
                fvg = FVG(
                    index=i,
                    timestamp=timestamp,
                    direction='bullish',
                    top=candle3['Low'],      # Top of FVG
                    bottom=candle1['High'],  # Bottom of FVG
                    midpoint=(candle3['Low'] + candle1['High']) / 2,
                    size_pct=gap_pct
                )
                fvgs.append(fvg)
    
    return fvgs


def update_fvg_status(fvgs: List[FVG], candle: pd.Series) -> List[FVG]:
    """
    Update FVG status based on current candle.
    Check if FVG has been touched or filled.
    """
    high = candle['High']
    low = candle['Low']
    
    for fvg in fvgs:
        if fvg.filled:
            continue
        
        if fvg.direction == 'bearish':
            # Bearish FVG is above current price (we expect price to retrace UP to it)
            # Touched: price went up into the FVG zone
            if high >= fvg.bottom:
                fvg.touched = True
                # Calculate fill percentage
                if high >= fvg.top:
                    fvg.filled = True
                    fvg.fill_pct = 100.0
                else:
                    fvg.fill_pct = ((high - fvg.bottom) / (fvg.top - fvg.bottom)) * 100
        
        elif fvg.direction == 'bullish':
            # Bullish FVG is below current price (we expect price to retrace DOWN to it)
            # Touched: price went down into the FVG zone
            if low <= fvg.top:
                fvg.touched = True
                # Calculate fill percentage
                if low <= fvg.bottom:
                    fvg.filled = True
                    fvg.fill_pct = 100.0
                else:
                    fvg.fill_pct = ((fvg.top - low) / (fvg.top - fvg.bottom)) * 100
    
    return fvgs


# =====================================================================
# TREND DETECTION
# =====================================================================

def detect_trend(df: pd.DataFrame, lookback: int = 20) -> pd.Series:
    """
    Detect trend using multiple methods.
    Returns: 1 (uptrend), -1 (downtrend), 0 (sideways)
    """
    trends = pd.Series(index=df.index, data=0)
    
    # EMA trend
    ema_fast = df['Close'].ewm(span=10, adjust=False).mean()
    ema_slow = df['Close'].ewm(span=20, adjust=False).mean()
    
    # Price above both EMAs = uptrend
    # Price below both EMAs = downtrend
    for i in range(lookback, len(df)):
        close = df['Close'].iloc[i]
        ema_f = ema_fast.iloc[i]
        ema_s = ema_slow.iloc[i]
        
        # Check recent price action (lower highs/lows or higher highs/lows)
        recent_highs = df['High'].iloc[i-lookback:i]
        recent_lows = df['Low'].iloc[i-lookback:i]
        
        # Count lower highs and lower lows (downtrend characteristics)
        lower_highs = sum(recent_highs.iloc[j] < recent_highs.iloc[j-1] 
                         for j in range(1, len(recent_highs)))
        lower_lows = sum(recent_lows.iloc[j] < recent_lows.iloc[j-1] 
                        for j in range(1, len(recent_lows)))
        
        # Count higher highs and higher lows (uptrend characteristics)
        higher_highs = sum(recent_highs.iloc[j] > recent_highs.iloc[j-1] 
                          for j in range(1, len(recent_highs)))
        higher_lows = sum(recent_lows.iloc[j] > recent_lows.iloc[j-1] 
                         for j in range(1, len(recent_lows)))
        
        # Determine trend
        if close > ema_f > ema_s and higher_highs > lower_highs:
            trends.iloc[i] = 1  # Uptrend
        elif close < ema_f < ema_s and lower_lows > higher_lows:
            trends.iloc[i] = -1  # Downtrend
        else:
            trends.iloc[i] = 0  # Sideways/neutral
    
    return trends


def calculate_atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Calculate Average True Range."""
    h, l, c = df['High'], df['Low'], df['Close']
    tr1 = h - l
    tr2 = abs(h - c.shift(1))
    tr3 = abs(l - c.shift(1))
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    return tr.rolling(window=period).mean()


# =====================================================================
# FVG STRATEGY
# =====================================================================

class FVGStrategy:
    """
    Fair Value Gap Trading Strategy.
    
    Entry Logic:
    1. Identify trend direction
    2. Look for FVGs in the trend direction
    3. Wait for price to retrace and touch the FVG
    4. Enter when FVG is touched and reversal candle forms
    
    Exit Logic:
    - Stop loss: Beyond the FVG zone (top for bearish, bottom for bullish)
    - Take profit: 2:1 reward-risk ratio OR next FVG zone
    """
    
    def __init__(self,
                 min_fvg_pct: float = 0.3,        # Minimum FVG size (% of price)
                 max_fvg_age: int = 20,           # Max bars before FVG expires
                 require_trend: bool = True,      # Require trend confirmation
                 rr_ratio: float = 2.0,           # Reward-risk ratio
                 slippage: float = 0.001,         # 0.1%
                 commission: float = 0.001,       # 0.1%
                 risk_per_trade: float = 0.02):   # 2% risk
        
        self.min_fvg_pct = min_fvg_pct
        self.max_fvg_age = max_fvg_age
        self.require_trend = require_trend
        self.rr_ratio = rr_ratio
        self.slippage = slippage
        self.commission = commission
        self.risk_per_trade = risk_per_trade
        self.name = f"FVG Strategy (min={min_fvg_pct}%, RR={rr_ratio})"
    
    def backtest(self, df: pd.DataFrame, capital: float = 100000) -> Dict:
        """Run backtest on prepared DataFrame."""
        df = df.copy()
        df['ATR'] = calculate_atr(df)
        df['Trend'] = detect_trend(df)
        
        # Detect all FVGs upfront
        all_fvgs = detect_fvg(df, min_gap_pct=self.min_fvg_pct)
        
        trades = []
        current_capital = capital
        position = None
        active_fvgs = []  # Track active (unfilled) FVGs
        
        for i in range(50, len(df)):
            row = df.iloc[i]
            current_bar = i
            trend = row['Trend']
            atr = row['ATR']
            
            if pd.isna(atr) or atr <= 0:
                continue
            
            # Add new FVGs that were created at this bar
            new_fvgs = [f for f in all_fvgs if f.index == i]
            active_fvgs.extend(new_fvgs)
            
            # Update FVG status
            active_fvgs = update_fvg_status(active_fvgs, row)
            
            # Remove expired or filled FVGs
            active_fvgs = [f for f in active_fvgs 
                          if not f.filled and (current_bar - f.index) <= self.max_fvg_age]
            
            # Check exit if in position
            if position is not None:
                exit_triggered = False
                exit_price = None
                exit_reason = None
                
                if position['direction'] == 'short':
                    # Check stop loss (price goes above SL)
                    if row['High'] >= position['stop_loss']:
                        exit_price = position['stop_loss'] * (1 + self.slippage)
                        exit_reason = 'stop_loss'
                        exit_triggered = True
                    # Check take profit
                    elif row['Low'] <= position['take_profit']:
                        exit_price = position['take_profit'] * (1 - self.slippage)
                        exit_reason = 'take_profit'
                        exit_triggered = True
                
                elif position['direction'] == 'long':
                    # Check stop loss (price goes below SL)
                    if row['Low'] <= position['stop_loss']:
                        exit_price = position['stop_loss'] * (1 - self.slippage)
                        exit_reason = 'stop_loss'
                        exit_triggered = True
                    # Check take profit
                    elif row['High'] >= position['take_profit']:
                        exit_price = position['take_profit'] * (1 + self.slippage)
                        exit_reason = 'take_profit'
                        exit_triggered = True
                
                if exit_triggered:
                    # Calculate P&L
                    if position['direction'] == 'long':
                        pnl = position['shares'] * (exit_price - position['entry_price'])
                        ret_pct = (exit_price / position['entry_price'] - 1) * 100
                    else:
                        pnl = position['shares'] * (position['entry_price'] - exit_price)
                        ret_pct = (position['entry_price'] / exit_price - 1) * 100
                    
                    # Commission
                    pnl -= position['shares'] * exit_price * self.commission
                    current_capital += pnl
                    
                    trades.append({
                        'entry_date': position['entry_date'],
                        'exit_date': df.index[i],
                        'direction': position['direction'].upper(),
                        'entry_price': position['entry_price'],
                        'exit_price': exit_price,
                        'stop_loss': position['stop_loss'],
                        'take_profit': position['take_profit'],
                        'fvg_zone': f"{position['fvg_bottom']:.2f}-{position['fvg_top']:.2f}",
                        'fvg_size_pct': position['fvg_size'],
                        'shares': position['shares'],
                        'pnl': pnl,
                        'return_pct': ret_pct,
                        'exit_reason': exit_reason,
                        'capital': current_capital
                    })
                    
                    position = None
            
            # Look for new entry if not in position
            if position is None:
                # Find FVGs that match our criteria
                for fvg in active_fvgs:
                    # Skip if FVG doesn't match trend
                    if self.require_trend:
                        if fvg.direction == 'bearish' and trend != -1:
                            continue
                        if fvg.direction == 'bullish' and trend != 1:
                            continue
                    
                    # Check if price touched the FVG this bar
                    touched_this_bar = False
                    
                    if fvg.direction == 'bearish':
                        # Price retraced UP into bearish FVG
                        if row['High'] >= fvg.bottom and row['High'] <= fvg.top:
                            touched_this_bar = True
                            # Confirm reversal: close below open (bearish candle)
                            if row['Close'] < row['Open']:
                                # Entry signal!
                                entry_price = row['Close'] * (1 - self.slippage)
                                stop_loss = fvg.top * (1 + 0.002)  # Just above FVG
                                risk = stop_loss - entry_price
                                take_profit = entry_price - (risk * self.rr_ratio)
                                
                                # Position sizing
                                risk_amount = current_capital * self.risk_per_trade
                                shares = int(risk_amount / risk) if risk > 0 else 0
                                max_shares = int(current_capital * 0.25 / entry_price)
                                shares = min(shares, max_shares)
                                
                                if shares > 0:
                                    current_capital -= shares * entry_price * self.commission
                                    position = {
                                        'direction': 'short',
                                        'entry_price': entry_price,
                                        'entry_date': df.index[i],
                                        'stop_loss': stop_loss,
                                        'take_profit': take_profit,
                                        'shares': shares,
                                        'fvg_top': fvg.top,
                                        'fvg_bottom': fvg.bottom,
                                        'fvg_size': fvg.size_pct
                                    }
                                    # Mark this FVG as used
                                    fvg.filled = True
                                    break
                    
                    elif fvg.direction == 'bullish':
                        # Price retraced DOWN into bullish FVG
                        if row['Low'] <= fvg.top and row['Low'] >= fvg.bottom:
                            touched_this_bar = True
                            # Confirm reversal: close above open (bullish candle)
                            if row['Close'] > row['Open']:
                                # Entry signal!
                                entry_price = row['Close'] * (1 + self.slippage)
                                stop_loss = fvg.bottom * (1 - 0.002)  # Just below FVG
                                risk = entry_price - stop_loss
                                take_profit = entry_price + (risk * self.rr_ratio)
                                
                                # Position sizing
                                risk_amount = current_capital * self.risk_per_trade
                                shares = int(risk_amount / risk) if risk > 0 else 0
                                max_shares = int(current_capital * 0.25 / entry_price)
                                shares = min(shares, max_shares)
                                
                                if shares > 0:
                                    current_capital -= shares * entry_price * self.commission
                                    position = {
                                        'direction': 'long',
                                        'entry_price': entry_price,
                                        'entry_date': df.index[i],
                                        'stop_loss': stop_loss,
                                        'take_profit': take_profit,
                                        'shares': shares,
                                        'fvg_top': fvg.top,
                                        'fvg_bottom': fvg.bottom,
                                        'fvg_size': fvg.size_pct
                                    }
                                    # Mark this FVG as used
                                    fvg.filled = True
                                    break
        
        return self._calculate_results(trades, capital, current_capital, all_fvgs)
    
    def _calculate_results(self, trades: List[Dict], 
                          initial_capital: float, 
                          final_capital: float,
                          all_fvgs: List[FVG]) -> Dict:
        """Calculate backtest statistics."""
        trades_df = pd.DataFrame(trades) if trades else pd.DataFrame()
        total_return = (final_capital / initial_capital - 1) * 100
        
        # FVG statistics
        bearish_fvgs = sum(1 for f in all_fvgs if f.direction == 'bearish')
        bullish_fvgs = sum(1 for f in all_fvgs if f.direction == 'bullish')
        
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
            
            long_trades = len(trades_df[trades_df['direction'] == 'LONG'])
            short_trades = len(trades_df[trades_df['direction'] == 'SHORT'])
        else:
            win_rate = 0
            profit_factor = 0
            avg_win = 0
            avg_loss = 0
            best_trade = 0
            worst_trade = 0
            long_trades = 0
            short_trades = 0
        
        return {
            'total_return_pct': total_return,
            'num_trades': len(trades_df),
            'win_rate': win_rate,
            'profit_factor': profit_factor,
            'avg_win': avg_win,
            'avg_loss': avg_loss,
            'best_trade': best_trade,
            'worst_trade': worst_trade,
            'long_trades': long_trades,
            'short_trades': short_trades,
            'bearish_fvgs': bearish_fvgs,
            'bullish_fvgs': bullish_fvgs,
            'final_capital': final_capital,
            'trades': trades_df
        }


# =====================================================================
# DATA DOWNLOAD
# =====================================================================

def download_data(symbol: str, period: str = '60d', interval: str = '1h') -> pd.DataFrame:
    """Download data from Yahoo Finance."""
    data = yf.download(symbol, period=period, interval=interval, progress=False)
    if isinstance(data.columns, pd.MultiIndex):
        data.columns = data.columns.get_level_values(0)
    return data


# =====================================================================
# MAIN BACKTEST RUNNER
# =====================================================================

def run_fvg_backtest(symbols: List[str] = None,
                     period: str = '60d',
                     interval: str = '1h'):
    """
    Run FVG strategy on multiple stocks.
    """
    if symbols is None:
        symbols = [
            'IDEA.NS', 'RELIANCE.NS', 'TCS.NS', 'HDFCBANK.NS', 'INFY.NS',
            'SBIN.NS', 'BHARTIARTL.NS', 'ITC.NS', 'KOTAKBANK.NS', 'AXISBANK.NS',
            'LT.NS', 'MARUTI.NS', 'SUNPHARMA.NS', 'TITAN.NS', 'WIPRO.NS'
        ]
    
    print("=" * 90)
    print("FAIR VALUE GAP (FVG) STRATEGY - Trade Imbalance Zones")
    print("=" * 90)
    print(f"\nPeriod: {period}, Interval: {interval}")
    print("Strategy: Enter when price retraces to touch FVG zone in trending market")
    print("Settings: Min FVG 0.3%, Max Age 20 bars, 2:1 RR, Trend confirmation required")
    print("Costs: 0.1% slippage + 0.1% commission")
    
    # Test different parameters
    strategies = [
        FVGStrategy(min_fvg_pct=0.3, max_fvg_age=20, rr_ratio=2.0, require_trend=True),
        FVGStrategy(min_fvg_pct=0.5, max_fvg_age=15, rr_ratio=2.0, require_trend=True),
        FVGStrategy(min_fvg_pct=0.3, max_fvg_age=20, rr_ratio=1.5, require_trend=True),
    ]
    
    all_results = {s.name: [] for s in strategies}
    
    for symbol in symbols:
        try:
            df = download_data(symbol, period, interval)
            
            if df.empty or len(df) < 100:
                print(f"Skipping {symbol}: Insufficient data")
                continue
            
            for strategy in strategies:
                result = strategy.backtest(df)
                
                all_results[strategy.name].append({
                    'symbol': symbol,
                    'return': result['total_return_pct'],
                    'trades': result['num_trades'],
                    'win_rate': result['win_rate'],
                    'profit_factor': result['profit_factor'],
                    'long_trades': result['long_trades'],
                    'short_trades': result['short_trades'],
                    'bearish_fvgs': result['bearish_fvgs'],
                    'bullish_fvgs': result['bullish_fvgs'],
                    'best_trade': result['best_trade'],
                    'worst_trade': result['worst_trade'],
                    'trades_df': result['trades']
                })
                
        except Exception as e:
            print(f"Error with {symbol}: {e}")
    
    # Print results for each strategy
    for strategy_name, results in all_results.items():
        print("\n" + "=" * 90)
        print(f"RESULTS: {strategy_name}")
        print("=" * 90)
        
        if results:
            results_df = pd.DataFrame(results)
            
            print(f"\n{'Symbol':<15} | {'Return':<8} | {'Trades':<6} | {'WR':<6} | {'PF':<6} | {'L/S':<7} | {'FVGs':<10}")
            print("-" * 90)
            
            for _, r in results_df.iterrows():
                status = "OK" if r['return'] > 0 else "--"
                ls = f"{r['long_trades']}/{r['short_trades']}"
                fvgs = f"{r['bullish_fvgs']}/{r['bearish_fvgs']}"
                print(f"{r['symbol']:<15} | {r['return']:>6.2f}% | {r['trades']:>6} | {r['win_rate']:>5.1%} | {r['profit_factor']:>5.2f} | {ls:>7} | {fvgs:>10} {status}")
            
            # Summary
            print("-" * 90)
            avg_return = results_df['return'].mean()
            total_trades = results_df['trades'].sum()
            avg_wr = results_df['win_rate'].mean()
            profitable = (results_df['return'] > 0).sum()
            
            print(f"\n{'AVERAGE':<15} | {avg_return:>6.2f}% | {total_trades:>6} | {avg_wr:>5.1%}")
            print(f"Profitable stocks: {profitable}/{len(results_df)}")
    
    # Show sample trades for best strategy
    best_strategy = max(all_results.keys(), 
                       key=lambda k: pd.DataFrame(all_results[k])['return'].mean() if all_results[k] else float('-inf'))
    
    print("\n" + "=" * 90)
    print(f"SAMPLE TRADES ({best_strategy}):")
    print("=" * 90)
    
    for r in all_results[best_strategy][:5]:
        if len(r['trades_df']) > 0:
            print(f"\n{r['symbol']}:")
            trades = r['trades_df'].head(3)
            for _, t in trades.iterrows():
                status = "WIN" if t['pnl'] > 0 else "LOSS"
                print(f"  {t['direction']} | FVG: {t['fvg_zone']} | Entry: {t['entry_price']:.2f} | "
                      f"Exit: {t['exit_price']:.2f} | Ret: {t['return_pct']:+.2f}% | {t['exit_reason']} | {status}")
    
    return all_results


def show_fvg_analysis(symbol: str = 'IDEA.NS', period: str = '10d', interval: str = '1h'):
    """
    Show FVG analysis for a single stock - useful for understanding the concept.
    """
    print("\n" + "=" * 90)
    print(f"FVG ANALYSIS: {symbol}")
    print("=" * 90)
    
    df = download_data(symbol, period, interval)
    
    if df.empty:
        print("No data available")
        return
    
    # Detect FVGs
    fvgs = detect_fvg(df, min_gap_pct=0.3)
    
    # Calculate trend
    df['Trend'] = detect_trend(df)
    
    print(f"\nData: {len(df)} bars from {df.index[0]} to {df.index[-1]}")
    print(f"Current price: {df['Close'].iloc[-1]:.2f}")
    print(f"Current trend: {'UPTREND' if df['Trend'].iloc[-1] == 1 else 'DOWNTREND' if df['Trend'].iloc[-1] == -1 else 'SIDEWAYS'}")
    
    print(f"\nTotal FVGs detected: {len(fvgs)}")
    print(f"  Bullish FVGs: {sum(1 for f in fvgs if f.direction == 'bullish')}")
    print(f"  Bearish FVGs: {sum(1 for f in fvgs if f.direction == 'bearish')}")
    
    # Update FVG status with all candles
    for i in range(len(df)):
        fvgs = update_fvg_status(fvgs, df.iloc[i])
    
    # Show recent FVGs
    print("\nRecent FVGs (last 10):")
    print("-" * 90)
    print(f"{'Time':<20} | {'Type':<8} | {'Zone':<20} | {'Size':<6} | {'Touched':<8} | {'Filled':<8}")
    print("-" * 90)
    
    for fvg in fvgs[-10:]:
        zone = f"{fvg.bottom:.2f} - {fvg.top:.2f}"
        print(f"{str(fvg.timestamp):<20} | {fvg.direction:<8} | {zone:<20} | {fvg.size_pct:>5.2f}% | "
              f"{'Yes' if fvg.touched else 'No':<8} | {'Yes' if fvg.filled else 'No':<8}")
    
    # Show active (unfilled) FVGs
    active = [f for f in fvgs if not f.filled]
    print(f"\nActive (unfilled) FVGs: {len(active)}")
    for fvg in active[-5:]:
        zone = f"{fvg.bottom:.2f} - {fvg.top:.2f}"
        print(f"  {fvg.direction.upper()}: {zone} (created {fvg.timestamp})")
    
    return fvgs


# =====================================================================
# MAIN
# =====================================================================

def run_extended_backtest():
    """
    Run FVG strategy on extended data for statistical significance.
    Uses 730 days of 1h data (5000+ bars per stock).
    """
    symbols = [
        'RELIANCE.NS', 'TCS.NS', 'HDFCBANK.NS', 'INFY.NS', 'SBIN.NS',
        'BHARTIARTL.NS', 'ITC.NS', 'KOTAKBANK.NS', 'AXISBANK.NS', 'LT.NS',
        'MARUTI.NS', 'SUNPHARMA.NS', 'TITAN.NS', 'WIPRO.NS', 'IDEA.NS'
    ]
    
    print("=" * 95)
    print("FVG STRATEGY - EXTENDED BACKTEST (730 days, 1h data)")
    print("=" * 95)
    print("\nThis uses ~5000 bars per stock for statistical significance")
    
    # Best performing strategy from previous test
    strategy = FVGStrategy(
        min_fvg_pct=0.5,      # Larger FVGs only
        max_fvg_age=20,       # FVG expires after 20 bars
        rr_ratio=2.0,         # 2:1 reward-risk
        require_trend=True,   # Trade with trend only
        slippage=0.001,
        commission=0.001
    )
    
    all_results = []
    
    for symbol in symbols:
        try:
            print(f"\nProcessing {symbol}...", end=" ")
            
            # Download extended data (730 days of 1h)
            df = yf.download(symbol, period='730d', interval='1h', progress=False)
            
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = df.columns.get_level_values(0)
            
            if df.empty or len(df) < 500:
                print(f"Skipped (only {len(df)} bars)")
                continue
            
            print(f"{len(df)} bars", end=" -> ")
            
            # Run backtest
            result = strategy.backtest(df)
            
            print(f"{result['num_trades']} trades, {result['total_return_pct']:.2f}%")
            
            all_results.append({
                'symbol': symbol,
                'bars': len(df),
                'return': result['total_return_pct'],
                'trades': result['num_trades'],
                'win_rate': result['win_rate'],
                'profit_factor': result['profit_factor'],
                'avg_win': result['avg_win'],
                'avg_loss': result['avg_loss'],
                'long_trades': result['long_trades'],
                'short_trades': result['short_trades'],
                'bearish_fvgs': result['bearish_fvgs'],
                'bullish_fvgs': result['bullish_fvgs'],
                'best_trade': result['best_trade'],
                'worst_trade': result['worst_trade'],
                'trades_df': result['trades']
            })
            
        except Exception as e:
            print(f"Error: {e}")
    
    # Print detailed results
    print("\n" + "=" * 95)
    print("EXTENDED BACKTEST RESULTS (730 days)")
    print("=" * 95)
    
    if all_results:
        results_df = pd.DataFrame(all_results)
        
        print(f"\n{'Symbol':<15} | {'Bars':<6} | {'Return':<8} | {'Trades':<6} | {'WR':<6} | {'PF':<6} | {'AvgWin':<7} | {'AvgLoss':<7} | {'Best':<7} | {'Worst':<7}")
        print("-" * 95)
        
        for _, r in results_df.iterrows():
            status = "OK" if r['return'] > 0 else "--"
            print(f"{r['symbol']:<15} | {r['bars']:>6} | {r['return']:>6.2f}% | {r['trades']:>6} | {r['win_rate']:>5.1%} | {r['profit_factor']:>5.2f} | {r['avg_win']:>6.2f}% | {r['avg_loss']:>6.2f}% | {r['best_trade']:>6.2f}% | {r['worst_trade']:>6.2f}% {status}")
        
        # Summary
        print("-" * 95)
        avg_return = results_df['return'].mean()
        total_trades = results_df['trades'].sum()
        avg_trades = results_df['trades'].mean()
        avg_wr = results_df['win_rate'].mean()
        profitable = (results_df['return'] > 0).sum()
        
        print(f"\n{'SUMMARY':<15}")
        print(f"  Total trades across all stocks: {total_trades}")
        print(f"  Average trades per stock: {avg_trades:.1f}")
        print(f"  Average return: {avg_return:.2f}%")
        print(f"  Average win rate: {avg_wr:.1%}")
        print(f"  Profitable stocks: {profitable}/{len(results_df)}")
        
        # Detailed trade analysis
        all_trades = pd.concat([r['trades_df'] for r in all_results if len(r['trades_df']) > 0], ignore_index=True)
        
        if len(all_trades) > 0:
            print(f"\n{'TRADE ANALYSIS':<15}")
            print(f"  Total trades: {len(all_trades)}")
            print(f"  Winning trades: {(all_trades['pnl'] > 0).sum()} ({(all_trades['pnl'] > 0).mean():.1%})")
            print(f"  Losing trades: {(all_trades['pnl'] < 0).sum()} ({(all_trades['pnl'] < 0).mean():.1%})")
            print(f"  Long trades: {(all_trades['direction'] == 'LONG').sum()}")
            print(f"  Short trades: {(all_trades['direction'] == 'SHORT').sum()}")
            print(f"  Avg return per trade: {all_trades['return_pct'].mean():.2f}%")
            print(f"  Best trade: {all_trades['return_pct'].max():.2f}%")
            print(f"  Worst trade: {all_trades['return_pct'].min():.2f}%")
            
            # Win rate by direction
            long_trades = all_trades[all_trades['direction'] == 'LONG']
            short_trades = all_trades[all_trades['direction'] == 'SHORT']
            
            if len(long_trades) > 0:
                long_wr = (long_trades['pnl'] > 0).mean()
                print(f"\n  Long trades win rate: {long_wr:.1%} ({len(long_trades)} trades)")
            
            if len(short_trades) > 0:
                short_wr = (short_trades['pnl'] > 0).mean()
                print(f"  Short trades win rate: {short_wr:.1%} ({len(short_trades)} trades)")
        
        # Show sample trades
        print("\n" + "=" * 95)
        print("SAMPLE TRADES (first 5 per top 3 stocks by trades)")
        print("=" * 95)
        
        top_by_trades = results_df.nlargest(3, 'trades')
        for _, r in top_by_trades.iterrows():
            if len(r['trades_df']) > 0:
                print(f"\n{r['symbol']} ({r['trades']} trades, {r['return']:.2f}% return):")
                trades = r['trades_df'].head(5)
                for _, t in trades.iterrows():
                    status = "WIN" if t['pnl'] > 0 else "LOSS"
                    print(f"  {t['direction']:<5} | FVG: {t['fvg_zone']:<20} | Entry: {t['entry_price']:>8.2f} | "
                          f"Exit: {t['exit_price']:>8.2f} | Ret: {t['return_pct']:>+6.2f}% | {t['exit_reason']:<11} | {status}")
    
    return all_results


if __name__ == "__main__":
    # First, show FVG analysis for IDEA.NS (from the image)
    show_fvg_analysis('IDEA.NS', period='10d', interval='1h')
    
    # Run EXTENDED backtest with 730 days of data
    results = run_extended_backtest()
