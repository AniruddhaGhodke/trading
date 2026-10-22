"""
Momentum Trend Day Strategy

The goal: Catch obvious trending days like the IDEA.NS 4% drop on Jan 20.

Key Insights:
1. On trend days, the first 1-2 candles often set the direction
2. Trend days have fewer pullbacks - almost all candles in one direction
3. We should TRAIL stops, not use fixed targets
4. We need to detect "trend day" characteristics early

Strategies Implemented:
1. Opening Drive: First candle sets direction, ride the trend
2. Consecutive Candles: 3+ same-direction candles = strong trend
3. Failed Bounce: Price tries to rally, fails, continue shorting
4. Gap Continuation: Gap + first candle same direction = trend day

The secret: LET WINNERS RUN with trailing stops
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


def calculate_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """Calculate indicators."""
    df = df.copy()
    
    # ATR for trailing stop
    tr1 = df['High'] - df['Low']
    tr2 = abs(df['High'] - df['Close'].shift(1))
    tr3 = abs(df['Low'] - df['Close'].shift(1))
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    df['ATR'] = tr.rolling(window=14).mean()
    
    # Candle characteristics
    df['Body'] = df['Close'] - df['Open']
    df['Body_Pct'] = df['Body'] / df['Open'] * 100
    df['Upper_Wick'] = df['High'] - df[['Open', 'Close']].max(axis=1)
    df['Lower_Wick'] = df[['Open', 'Close']].min(axis=1) - df['Low']
    df['Range'] = df['High'] - df['Low']
    
    # Is it a strong candle? (body > 60% of range)
    df['Strong_Candle'] = abs(df['Body']) > 0.6 * df['Range']
    
    # Direction: 1 = bullish, -1 = bearish, 0 = neutral
    df['Direction'] = np.where(df['Body'] > 0, 1, np.where(df['Body'] < 0, -1, 0))
    
    # Consecutive same-direction candles
    df['Consec_Count'] = 0
    for i in range(1, len(df)):
        if df['Direction'].iloc[i] == df['Direction'].iloc[i-1] and df['Direction'].iloc[i] != 0:
            df.iloc[i, df.columns.get_loc('Consec_Count')] = df['Consec_Count'].iloc[i-1] + 1
    
    # Volume
    df['Vol_MA'] = df['Volume'].rolling(20).mean()
    df['High_Volume'] = df['Volume'] > df['Vol_MA'] * 1.2
    
    # VWAP (simplified)
    typical_price = (df['High'] + df['Low'] + df['Close']) / 3
    df['VWAP'] = (typical_price * df['Volume']).cumsum() / df['Volume'].cumsum()
    
    # EMA for trend
    df['EMA_9'] = df['Close'].ewm(span=9, adjust=False).mean()
    df['EMA_21'] = df['Close'].ewm(span=21, adjust=False).mean()
    
    # Date/Hour for intraday logic
    if hasattr(df.index[0], 'date'):
        df['Date'] = df.index.date
        df['Hour'] = df.index.hour
    
    return df


class OpeningDriveStrategy:
    """
    Opening Drive Strategy
    
    Logic:
    - Look at first candle of the day
    - If it's a STRONG candle (body > 60% of range), take position in that direction
    - Use trailing stop (not fixed target)
    - Exit at end of day
    
    This catches trend days from the very beginning.
    """
    
    def __init__(self, min_body_pct: float = 0.3, trail_atr: float = 1.5):
        self.min_body_pct = min_body_pct  # Minimum body as % of price
        self.trail_atr = trail_atr
        self.name = "Opening Drive"
    
    def generate_signals(self, df: pd.DataFrame) -> pd.DataFrame:
        df = calculate_indicators(df)
        
        df['Signal'] = 0
        df['Entry'] = np.nan
        df['Stop_Loss'] = np.nan
        df['Trail_ATR'] = np.nan
        df['Signal_Type'] = ''
        
        for date in df['Date'].unique():
            day_data = df[df['Date'] == date]
            
            if len(day_data) < 2:
                continue
            
            first_candle = day_data.iloc[0]
            
            # Check if first candle is strong
            if not first_candle['Strong_Candle']:
                continue
            
            atr = first_candle['ATR'] if not pd.isna(first_candle['ATR']) else first_candle['Range']
            
            # STRONG BEARISH first candle - SHORT signal
            if (first_candle['Body_Pct'] < -self.min_body_pct and 
                first_candle['High_Volume']):
                
                # Enter on SECOND candle (confirmation)
                if len(day_data) > 1:
                    second_idx = day_data.index[1]
                    second_candle = day_data.iloc[1]
                    
                    # Confirm: second candle also bearish or at least not strongly bullish
                    if second_candle['Direction'] <= 0:
                        entry = second_candle['Close']
                        stop = entry + self.trail_atr * atr
                        
                        df.loc[second_idx, 'Signal'] = -1
                        df.loc[second_idx, 'Entry'] = entry
                        df.loc[second_idx, 'Stop_Loss'] = stop
                        df.loc[second_idx, 'Trail_ATR'] = atr
                        df.loc[second_idx, 'Signal_Type'] = 'OpenDrive_Short'
            
            # STRONG BULLISH first candle - LONG signal
            elif (first_candle['Body_Pct'] > self.min_body_pct and 
                  first_candle['High_Volume']):
                
                if len(day_data) > 1:
                    second_idx = day_data.index[1]
                    second_candle = day_data.iloc[1]
                    
                    if second_candle['Direction'] >= 0:
                        entry = second_candle['Close']
                        stop = entry - self.trail_atr * atr
                        
                        df.loc[second_idx, 'Signal'] = 1
                        df.loc[second_idx, 'Entry'] = entry
                        df.loc[second_idx, 'Stop_Loss'] = stop
                        df.loc[second_idx, 'Trail_ATR'] = atr
                        df.loc[second_idx, 'Signal_Type'] = 'OpenDrive_Long'
        
        return df


class ConsecutiveCandleStrategy:
    """
    Consecutive Candle Momentum Strategy
    
    Logic:
    - 3 consecutive candles in same direction = strong momentum
    - Enter on 4th candle in same direction
    - Trail stop behind recent swing
    
    This catches trends that develop after the open.
    """
    
    def __init__(self, min_consecutive: int = 3, trail_atr: float = 1.5):
        self.min_consecutive = min_consecutive
        self.trail_atr = trail_atr
        self.name = "Consecutive Momentum"
    
    def generate_signals(self, df: pd.DataFrame) -> pd.DataFrame:
        df = calculate_indicators(df)
        
        df['Signal'] = 0
        df['Entry'] = np.nan
        df['Stop_Loss'] = np.nan
        df['Trail_ATR'] = np.nan
        df['Signal_Type'] = ''
        
        # Track if we already traded today
        traded_today = {}
        
        for i in range(self.min_consecutive + 1, len(df)):
            row = df.iloc[i]
            current_date = row['Date']
            
            # Only one trade per day per direction
            if current_date in traded_today:
                continue
            
            atr = row['ATR'] if not pd.isna(row['ATR']) else row['Range']
            
            # Check for consecutive bearish candles
            if row['Consec_Count'] >= self.min_consecutive and row['Direction'] == -1:
                # Confirm: current candle also bearish
                if row['Direction'] == -1:
                    entry = row['Close']
                    # Stop above recent high
                    recent_high = df['High'].iloc[i-self.min_consecutive:i+1].max()
                    stop = max(recent_high, entry + self.trail_atr * atr)
                    
                    idx = df.index[i]
                    df.loc[idx, 'Signal'] = -1
                    df.loc[idx, 'Entry'] = entry
                    df.loc[idx, 'Stop_Loss'] = stop
                    df.loc[idx, 'Trail_ATR'] = atr
                    df.loc[idx, 'Signal_Type'] = 'Consec_Short'
                    traded_today[current_date] = -1
            
            # Check for consecutive bullish candles
            elif row['Consec_Count'] >= self.min_consecutive and row['Direction'] == 1:
                if row['Direction'] == 1:
                    entry = row['Close']
                    recent_low = df['Low'].iloc[i-self.min_consecutive:i+1].min()
                    stop = min(recent_low, entry - self.trail_atr * atr)
                    
                    idx = df.index[i]
                    df.loc[idx, 'Signal'] = 1
                    df.loc[idx, 'Entry'] = entry
                    df.loc[idx, 'Stop_Loss'] = stop
                    df.loc[idx, 'Trail_ATR'] = atr
                    df.loc[idx, 'Signal_Type'] = 'Consec_Long'
                    traded_today[current_date] = 1
        
        return df


class FailedBounceStrategy:
    """
    Failed Bounce / Failed Rally Strategy
    
    Logic:
    - In a downtrend (price < EMA), look for failed rallies
    - If price tries to go up but makes LOWER HIGH, short
    - Similarly for uptrends with failed dips
    
    This is the classic "lower high, lower low" pattern.
    """
    
    def __init__(self, trail_atr: float = 1.5):
        self.trail_atr = trail_atr
        self.name = "Failed Bounce"
    
    def generate_signals(self, df: pd.DataFrame) -> pd.DataFrame:
        df = calculate_indicators(df)
        
        df['Signal'] = 0
        df['Entry'] = np.nan
        df['Stop_Loss'] = np.nan
        df['Trail_ATR'] = np.nan
        df['Signal_Type'] = ''
        
        # Find swing highs and lows
        df['Swing_High'] = (df['High'] > df['High'].shift(1)) & (df['High'] > df['High'].shift(-1))
        df['Swing_Low'] = (df['Low'] < df['Low'].shift(1)) & (df['Low'] < df['Low'].shift(-1))
        
        last_swing_high = None
        last_swing_low = None
        traded_today = {}
        
        for i in range(3, len(df)-1):
            row = df.iloc[i]
            current_date = row['Date']
            
            if current_date in traded_today:
                continue
            
            atr = row['ATR'] if not pd.isna(row['ATR']) else row['Range']
            
            # Update swing points
            if df['Swing_High'].iloc[i-1]:
                if last_swing_high is not None:
                    prev_swing_high = last_swing_high
                    curr_swing_high = df['High'].iloc[i-1]
                    
                    # LOWER HIGH in downtrend = short signal
                    if (curr_swing_high < prev_swing_high and 
                        row['Close'] < row['EMA_21'] and
                        row['Direction'] == -1):
                        
                        entry = row['Close']
                        stop = curr_swing_high + 0.5 * atr
                        
                        idx = df.index[i]
                        df.loc[idx, 'Signal'] = -1
                        df.loc[idx, 'Entry'] = entry
                        df.loc[idx, 'Stop_Loss'] = stop
                        df.loc[idx, 'Trail_ATR'] = atr
                        df.loc[idx, 'Signal_Type'] = 'LowerHigh_Short'
                        traded_today[current_date] = -1
                
                last_swing_high = df['High'].iloc[i-1]
            
            if df['Swing_Low'].iloc[i-1]:
                if last_swing_low is not None:
                    prev_swing_low = last_swing_low
                    curr_swing_low = df['Low'].iloc[i-1]
                    
                    # HIGHER LOW in uptrend = long signal
                    if (curr_swing_low > prev_swing_low and 
                        row['Close'] > row['EMA_21'] and
                        row['Direction'] == 1):
                        
                        entry = row['Close']
                        stop = curr_swing_low - 0.5 * atr
                        
                        idx = df.index[i]
                        df.loc[idx, 'Signal'] = 1
                        df.loc[idx, 'Entry'] = entry
                        df.loc[idx, 'Stop_Loss'] = stop
                        df.loc[idx, 'Trail_ATR'] = atr
                        df.loc[idx, 'Signal_Type'] = 'HigherLow_Long'
                        traded_today[current_date] = 1
                
                last_swing_low = df['Low'].iloc[i-1]
        
        return df


class VWAPTrendStrategy:
    """
    VWAP Trend Strategy
    
    Logic:
    - If price is BELOW VWAP all day, it's a bearish day
    - On any rally to VWAP that gets rejected, short
    - Vice versa for bullish days
    
    The key: Don't fight the VWAP trend
    """
    
    def __init__(self, trail_atr: float = 1.5):
        self.trail_atr = trail_atr
        self.name = "VWAP Trend"
    
    def generate_signals(self, df: pd.DataFrame) -> pd.DataFrame:
        df = calculate_indicators(df)
        
        df['Signal'] = 0
        df['Entry'] = np.nan
        df['Stop_Loss'] = np.nan
        df['Trail_ATR'] = np.nan
        df['Signal_Type'] = ''
        
        traded_today = {}
        
        for date in df['Date'].unique():
            day_data = df[df['Date'] == date]
            
            if len(day_data) < 3:
                continue
            
            # Determine day's trend based on first hour
            first_hour = day_data.iloc[:2]  # First 2 candles
            
            avg_close = first_hour['Close'].mean()
            avg_vwap = first_hour['VWAP'].mean()
            
            if pd.isna(avg_vwap):
                continue
            
            is_bearish_day = avg_close < avg_vwap * 0.998  # Below VWAP
            is_bullish_day = avg_close > avg_vwap * 1.002  # Above VWAP
            
            # Look for entries after first hour
            for i in range(2, len(day_data)):
                row_idx = day_data.index[i]
                row = df.loc[row_idx]
                
                if date in traded_today:
                    continue
                
                atr = row['ATR'] if not pd.isna(row['ATR']) else row['Range']
                
                # BEARISH DAY: Short on rally to VWAP that fails
                if is_bearish_day:
                    prev_row = df.loc[day_data.index[i-1]]
                    
                    # Touched VWAP (or close to it) and rejected
                    touched_vwap = prev_row['High'] >= prev_row['VWAP'] * 0.998
                    rejected = row['Close'] < row['VWAP'] and row['Direction'] == -1
                    
                    if touched_vwap and rejected:
                        entry = row['Close']
                        stop = row['VWAP'] + self.trail_atr * atr
                        
                        df.loc[row_idx, 'Signal'] = -1
                        df.loc[row_idx, 'Entry'] = entry
                        df.loc[row_idx, 'Stop_Loss'] = stop
                        df.loc[row_idx, 'Trail_ATR'] = atr
                        df.loc[row_idx, 'Signal_Type'] = 'VWAPReject_Short'
                        traded_today[date] = -1
                
                # BULLISH DAY: Long on dip to VWAP that holds
                elif is_bullish_day:
                    prev_row = df.loc[day_data.index[i-1]]
                    
                    touched_vwap = prev_row['Low'] <= prev_row['VWAP'] * 1.002
                    bounced = row['Close'] > row['VWAP'] and row['Direction'] == 1
                    
                    if touched_vwap and bounced:
                        entry = row['Close']
                        stop = row['VWAP'] - self.trail_atr * atr
                        
                        df.loc[row_idx, 'Signal'] = 1
                        df.loc[row_idx, 'Entry'] = entry
                        df.loc[row_idx, 'Stop_Loss'] = stop
                        df.loc[row_idx, 'Trail_ATR'] = atr
                        df.loc[row_idx, 'Signal_Type'] = 'VWAPBounce_Long'
                        traded_today[date] = 1
        
        return df


class TrailingStopBacktester:
    """
    Backtester with TRAILING STOPS
    
    The key difference: We let winners run by trailing the stop.
    """
    
    def __init__(self, 
                 capital: float = 100000,
                 risk_per_trade: float = 0.01,
                 slippage_pct: float = 0.001,
                 commission_pct: float = 0.001,
                 trail_atr_mult: float = 1.5):
        self.capital = capital
        self.risk_per_trade = risk_per_trade
        self.slippage_pct = slippage_pct
        self.commission_pct = commission_pct
        self.trail_atr_mult = trail_atr_mult
    
    def run(self, df: pd.DataFrame, exit_eod: bool = True) -> Dict:
        """Run backtest with trailing stops."""
        capital = self.capital
        position = 0
        entry_price = 0
        stop_loss = 0
        trail_atr = 0
        highest_since_entry = 0
        lowest_since_entry = float('inf')
        entry_date = None
        trades = []
        
        for i, (idx, row) in enumerate(df.iterrows()):
            current_date = row.get('Date', idx)
            
            # Update trailing stop for existing position
            if position > 0:  # Long position
                # Update highest price
                highest_since_entry = max(highest_since_entry, row['High'])
                # Trail stop up
                new_stop = highest_since_entry - self.trail_atr_mult * trail_atr
                stop_loss = max(stop_loss, new_stop)  # Only move up, never down
                
            elif position < 0:  # Short position
                # Update lowest price
                lowest_since_entry = min(lowest_since_entry, row['Low'])
                # Trail stop down
                new_stop = lowest_since_entry + self.trail_atr_mult * trail_atr
                stop_loss = min(stop_loss, new_stop)  # Only move down, never up
            
            # Check stop-loss
            exit_price = None
            exit_reason = None
            
            if position > 0 and row['Low'] <= stop_loss:
                exit_price = stop_loss * (1 - self.slippage_pct)
                exit_reason = 'trailing_stop'
            elif position < 0 and row['High'] >= stop_loss:
                exit_price = stop_loss * (1 + self.slippage_pct)
                exit_reason = 'trailing_stop'
            
            # Check end of day
            if exit_price is None and exit_eod and position != 0:
                if i + 1 < len(df):
                    next_date = df.iloc[i + 1].get('Date', df.index[i + 1])
                    if next_date != current_date:
                        exit_price = row['Close']
                        exit_reason = 'eod'
            
            # Process exit
            if exit_price is not None and position != 0:
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
                    'return_pct': ret_pct,
                    'highest': highest_since_entry if position > 0 else None,
                    'lowest': lowest_since_entry if position < 0 else None
                })
                
                position = 0
                highest_since_entry = 0
                lowest_since_entry = float('inf')
            
            # Check for new signal
            if position == 0 and row['Signal'] != 0:
                if pd.isna(row.get('Entry')) or pd.isna(row.get('Stop_Loss')):
                    continue
                
                # Apply slippage to entry
                if row['Signal'] == 1:
                    entry_price = row['Entry'] * (1 + self.slippage_pct)
                else:
                    entry_price = row['Entry'] * (1 - self.slippage_pct)
                
                stop_loss = row['Stop_Loss']
                trail_atr = row.get('Trail_ATR', row.get('ATR', abs(entry_price - stop_loss) / self.trail_atr_mult))
                if pd.isna(trail_atr):
                    trail_atr = abs(entry_price - stop_loss) / self.trail_atr_mult
                
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
                
                if row['Signal'] == 1:
                    position = shares
                    highest_since_entry = entry_price
                else:
                    position = -shares
                    lowest_since_entry = entry_price
                
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
            
            # Best trade
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
            'final_capital': capital,
            'trades': trades_df
        }


def run_all_strategies(symbols: List[str] = None):
    """Run all momentum strategies on multiple stocks."""
    if symbols is None:
        # Include IDEA.NS as user mentioned it
        symbols = [
            'IDEA.NS', 'RELIANCE.NS', 'TCS.NS', 'HDFCBANK.NS', 'INFY.NS',
            'SBIN.NS', 'BHARTIARTL.NS', 'ITC.NS', 'KOTAKBANK.NS', 'AXISBANK.NS'
        ]
    
    strategies = [
        OpeningDriveStrategy(min_body_pct=0.3, trail_atr=1.5),
        ConsecutiveCandleStrategy(min_consecutive=3, trail_atr=1.5),
        FailedBounceStrategy(trail_atr=1.5),
        VWAPTrendStrategy(trail_atr=1.5)
    ]
    
    backtester = TrailingStopBacktester(
        capital=100000,
        risk_per_trade=0.01,
        slippage_pct=0.001,
        commission_pct=0.001,
        trail_atr_mult=1.5
    )
    
    all_results = {s.name: [] for s in strategies}
    
    print("="*80)
    print("MOMENTUM TREND STRATEGIES - Catching Obvious Moves")
    print("="*80)
    print("Using TRAILING STOPS to let winners run\n")
    
    for symbol in symbols:
        try:
            df = download_data(symbol, '60d', '1h')
            
            if df.empty or len(df) < 50:
                print(f"{symbol}: Insufficient data")
                continue
            
            print(f"\n{symbol}:")
            
            for strategy in strategies:
                signals_df = strategy.generate_signals(df)
                result = backtester.run(signals_df)
                
                all_results[strategy.name].append({
                    'symbol': symbol,
                    'return': result['total_return_pct'],
                    'trades': result['num_trades'],
                    'win_rate': result['win_rate'],
                    'profit_factor': result['profit_factor'],
                    'best_trade': result['best_trade']
                })
                
                status = "+" if result['total_return_pct'] > 0 else "-"
                print(f"  {strategy.name:<20} Return: {result['total_return_pct']:>7.2f}%  Trades: {result['num_trades']:>2}  WR: {result['win_rate']:>5.1%}  Best: {result['best_trade']:>6.2f}% {status}")
                
        except Exception as e:
            print(f"{symbol}: Error - {e}")
    
    # Summary
    print("\n" + "="*80)
    print("STRATEGY COMPARISON")
    print("="*80)
    
    for name, results in all_results.items():
        if results:
            results_df = pd.DataFrame(results)
            print(f"\n{name}:")
            print(f"  Avg Return: {results_df['return'].mean():.2f}%")
            print(f"  Avg Win Rate: {results_df['win_rate'].mean():.1%}")
            print(f"  Profitable: {(results_df['return'] > 0).sum()}/{len(results_df)}")
            print(f"  Total Trades: {results_df['trades'].sum()}")
            print(f"  Best Single Trade: {results_df['best_trade'].max():.2f}%")
    
    return all_results


def analyze_specific_day(symbol: str, target_date: str):
    """Analyze a specific day to see what signals we would have gotten."""
    print(f"\n{'='*60}")
    print(f"ANALYZING {symbol} on {target_date}")
    print("="*60)
    
    df = download_data(symbol, '60d', '1h')
    df = calculate_indicators(df)
    
    # Filter to target date
    day_data = df[df['Date'] == pd.to_datetime(target_date).date()]
    
    if len(day_data) == 0:
        print(f"No data for {target_date}")
        return
    
    print(f"\nCandles for the day:")
    print(f"{'Time':<20} {'Open':>10} {'High':>10} {'Low':>10} {'Close':>10} {'Body%':>8} {'Dir':>5}")
    print("-"*75)
    
    for idx, row in day_data.iterrows():
        time_str = str(idx)[-14:-6] if hasattr(idx, 'hour') else str(idx)
        direction = "UP" if row['Direction'] == 1 else "DN" if row['Direction'] == -1 else "--"
        print(f"{time_str:<20} {row['Open']:>10.2f} {row['High']:>10.2f} {row['Low']:>10.2f} {row['Close']:>10.2f} {row['Body_Pct']:>7.2f}% {direction:>5}")
    
    # Calculate day's move
    open_price = day_data['Open'].iloc[0]
    close_price = day_data['Close'].iloc[-1]
    day_return = (close_price / open_price - 1) * 100
    
    print(f"\nDay's Move: {day_return:.2f}%")
    print(f"First Candle: {'BEARISH' if day_data.iloc[0]['Direction'] == -1 else 'BULLISH'}, Body: {day_data.iloc[0]['Body_Pct']:.2f}%")
    
    # What signals would we get?
    strategies = [
        OpeningDriveStrategy(),
        ConsecutiveCandleStrategy(),
        VWAPTrendStrategy()
    ]
    
    print(f"\nSignals we would generate:")
    for strategy in strategies:
        signals_df = strategy.generate_signals(df)
        day_signals = signals_df[signals_df['Date'] == pd.to_datetime(target_date).date()]
        signal_rows = day_signals[day_signals['Signal'] != 0]
        
        if len(signal_rows) > 0:
            for idx, row in signal_rows.iterrows():
                print(f"  {strategy.name}: {row['Signal_Type']} at {row['Entry']:.2f}, Stop: {row['Stop_Loss']:.2f}")
        else:
            print(f"  {strategy.name}: No signal")


if __name__ == "__main__":
    # Run on all stocks
    results = run_all_strategies()
    
    # Analyze the specific day user mentioned (Jan 20, 2026)
    print("\n\n")
    analyze_specific_day('IDEA.NS', '2026-01-20')
