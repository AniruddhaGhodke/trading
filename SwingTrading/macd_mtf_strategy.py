"""
MACD 3-SYSTEM TRUE MULTI-TIMEFRAME STRATEGY
=============================================

Uses ACTUAL multiple timeframes instead of simulating with different MACD periods:

TIMEFRAME HIERARCHY:
  - Weekly Chart: Overall trend bias (MACD above/below zero)
  - Daily Chart: Setup identification (crossover/divergence)
  - 4-Hour Chart: Entry trigger (histogram confirmation)

ALL THREE TIMEFRAMES MUST AGREE for a valid signal.

This should produce:
1. Fewer but higher quality trades
2. Better trend alignment
3. Reduced false signals
4. Higher win rate (at cost of fewer opportunities)

Based on the video's recommended approach:
- Step 1: Check Weekly MACD for bias
- Step 2: Find Daily MACD setup (crossover or divergence)
- Step 3: Confirm with 4H histogram flip
"""

import pandas as pd
import numpy as np
import yfinance as yf
from datetime import datetime, timedelta
from typing import Dict, List, Tuple, Optional
from dataclasses import dataclass
import warnings
warnings.filterwarnings('ignore')


# =============================================================================
# CONFIGURATION
# =============================================================================

@dataclass
class MTFConfig:
    """Multi-Timeframe Configuration"""
    # MACD Parameters (standard 12/26/9 for all timeframes)
    fast_period: int = 12
    slow_period: int = 26
    signal_period: int = 9
    
    # Timeframes
    trend_tf: str = 'weekly'    # Overall bias
    setup_tf: str = 'daily'     # Setup identification
    entry_tf: str = '4h'        # Entry trigger
    
    # Signal requirements
    require_trend_alignment: bool = True  # Weekly bias must match
    require_histogram_confirm: bool = True  # 4H histogram must confirm
    confirmation_candles: int = 1  # Candles to wait after signal
    
    # Risk Management
    risk_pct: float = 0.02  # 2% risk per trade
    risk_reward: float = 2.5  # 1:2.5 R:R
    max_position_pct: float = 0.25
    
    # Costs
    slippage: float = 0.001
    commission: float = 0.001
    
    # Mode
    long_only: bool = True


# =============================================================================
# DATA DOWNLOAD
# =============================================================================

def download_mtf_data(symbol: str) -> Tuple[Optional[pd.DataFrame], Optional[pd.DataFrame], Optional[pd.DataFrame]]:
    """
    Download Weekly, Daily, and 4-Hour data for a symbol
    
    Returns:
        weekly_df, daily_df, hourly_4h_df
    """
    try:
        # Weekly data (2 years)
        weekly = yf.download(symbol, period='2y', interval='1wk', progress=False)
        if isinstance(weekly.columns, pd.MultiIndex):
            weekly.columns = weekly.columns.get_level_values(0)
        
        # Daily data (1 year)
        daily = yf.download(symbol, period='1y', interval='1d', progress=False)
        if isinstance(daily.columns, pd.MultiIndex):
            daily.columns = daily.columns.get_level_values(0)
        
        # 4-Hour data (60 days - max available from Yahoo)
        hourly_4h = yf.download(symbol, period='60d', interval='1h', progress=False)
        if isinstance(hourly_4h.columns, pd.MultiIndex):
            hourly_4h.columns = hourly_4h.columns.get_level_values(0)
        
        # Resample 1H to 4H
        if not hourly_4h.empty:
            hourly_4h = hourly_4h.resample('4h').agg({
                'Open': 'first',
                'High': 'max',
                'Low': 'min',
                'Close': 'last',
                'Volume': 'sum'
            }).dropna()
        
        # Validate data
        if weekly.empty or len(weekly) < 20:
            return None, None, None
        if daily.empty or len(daily) < 60:
            return None, None, None
        if hourly_4h.empty or len(hourly_4h) < 50:
            return None, None, None
        
        return weekly, daily, hourly_4h
    
    except Exception as e:
        print(f"Error downloading {symbol}: {e}")
        return None, None, None


# =============================================================================
# INDICATORS
# =============================================================================

def calculate_macd(prices: pd.Series, fast: int = 12, slow: int = 26, 
                   signal: int = 9) -> Tuple[pd.Series, pd.Series, pd.Series]:
    """Calculate MACD, Signal Line, and Histogram"""
    ema_fast = prices.ewm(span=fast, adjust=False).mean()
    ema_slow = prices.ewm(span=slow, adjust=False).mean()
    macd_line = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    histogram = macd_line - signal_line
    return macd_line, signal_line, histogram


def calculate_atr(high: pd.Series, low: pd.Series, close: pd.Series, 
                  period: int = 14) -> pd.Series:
    """Calculate ATR"""
    tr = pd.concat([
        high - low,
        abs(high - close.shift(1)),
        abs(low - close.shift(1))
    ], axis=1).max(axis=1)
    return tr.rolling(period).mean()


# =============================================================================
# MULTI-TIMEFRAME ANALYSIS
# =============================================================================

def get_weekly_bias(weekly_df: pd.DataFrame, config: MTFConfig) -> pd.DataFrame:
    """
    Weekly Chart Analysis - Determine overall trend bias
    
    Rules:
    - MACD above zero = Bullish bias (only look for longs)
    - MACD below zero = Bearish bias (only look for shorts)
    """
    df = weekly_df.copy()
    
    df['MACD'], df['Signal'], df['Histogram'] = calculate_macd(
        df['Close'], config.fast_period, config.slow_period, config.signal_period
    )
    
    # Bias determination
    df['Bias'] = 0
    df.loc[df['MACD'] > 0, 'Bias'] = 1  # Bullish
    df.loc[df['MACD'] < 0, 'Bias'] = -1  # Bearish
    
    # Trend strength (how far from zero)
    macd_std = df['MACD'].rolling(20).std()
    df['Trend_Strength'] = abs(df['MACD']) / macd_std.replace(0, np.nan)
    df['Strong_Trend'] = df['Trend_Strength'] > 1.0
    
    return df


def get_daily_setups(daily_df: pd.DataFrame, config: MTFConfig) -> pd.DataFrame:
    """
    Daily Chart Analysis - Identify trading setups
    
    System 1: Crossover signals (trend continuation)
    System 2: Divergence signals (potential reversal)
    """
    df = daily_df.copy()
    
    df['MACD'], df['Signal'], df['Histogram'] = calculate_macd(
        df['Close'], config.fast_period, config.slow_period, config.signal_period
    )
    df['ATR'] = calculate_atr(df['High'], df['Low'], df['Close'])
    
    # Crossover detection
    df['Crossover'] = 0
    bullish_cross = (df['MACD'] > df['Signal']) & (df['MACD'].shift(1) <= df['Signal'].shift(1))
    bearish_cross = (df['MACD'] < df['Signal']) & (df['MACD'].shift(1) >= df['Signal'].shift(1))
    df.loc[bullish_cross, 'Crossover'] = 1
    df.loc[bearish_cross, 'Crossover'] = -1
    
    # MACD zone (distance from zero)
    macd_std = df['MACD'].rolling(50).std()
    df['Above_Zero'] = df['MACD'] > 0
    df['Below_Zero'] = df['MACD'] < 0
    df['Far_From_Zero'] = abs(df['MACD']) > macd_std * 0.5
    df['In_Chop'] = abs(df['MACD']) < macd_std * 0.3
    
    # Divergence detection
    df['Bullish_Div'] = 0
    df['Bearish_Div'] = 0
    
    lookback = 20
    for i in range(lookback, len(df)):
        # Bearish divergence: Higher high in price, lower high in MACD
        price_window = df['High'].iloc[i-lookback:i+1]
        macd_window = df['MACD'].iloc[i-lookback:i+1]
        
        mid = lookback // 2
        if (price_window.iloc[-1] > price_window.iloc[mid] and 
            macd_window.iloc[-1] < macd_window.iloc[mid]):
            df.iloc[i, df.columns.get_loc('Bearish_Div')] = 1
        
        # Bullish divergence: Lower low in price, higher low in MACD
        price_low_window = df['Low'].iloc[i-lookback:i+1]
        if (price_low_window.iloc[-1] < price_low_window.iloc[mid] and 
            macd_window.iloc[-1] > macd_window.iloc[mid]):
            df.iloc[i, df.columns.get_loc('Bullish_Div')] = 1
    
    # Setup signals
    df['Setup'] = 0
    df['Setup_Type'] = ''
    
    # System 1: Trend crossover (crossover + far from zero + right side of zero)
    s1_bull = (df['Crossover'] == 1) & df['Above_Zero'] & df['Far_From_Zero'] & ~df['In_Chop']
    s1_bear = (df['Crossover'] == -1) & df['Below_Zero'] & df['Far_From_Zero'] & ~df['In_Chop']
    
    df.loc[s1_bull, 'Setup'] = 1
    df.loc[s1_bull, 'Setup_Type'] = 'S1_Trend'
    df.loc[s1_bear, 'Setup'] = -1
    df.loc[s1_bear, 'Setup_Type'] = 'S1_Trend'
    
    # System 2: Divergence
    s2_bull = (df['Bullish_Div'] == 1) & (df['Histogram'] > df['Histogram'].shift(1))
    s2_bear = (df['Bearish_Div'] == 1) & (df['Histogram'] < df['Histogram'].shift(1))
    
    # Only apply if no S1 signal
    df.loc[s2_bull & (df['Setup'] == 0), 'Setup'] = 1
    df.loc[s2_bull & (df['Setup_Type'] == ''), 'Setup_Type'] = 'S2_Div'
    df.loc[s2_bear & (df['Setup'] == 0), 'Setup'] = -1
    df.loc[s2_bear & (df['Setup_Type'] == ''), 'Setup_Type'] = 'S2_Div'
    
    return df


def get_4h_entry_triggers(hourly_4h_df: pd.DataFrame, config: MTFConfig) -> pd.DataFrame:
    """
    4-Hour Chart Analysis - Entry triggers
    
    Look for histogram confirmation:
    - First green bar after red (bullish entry)
    - First red bar after green (bearish entry)
    """
    df = hourly_4h_df.copy()
    
    df['MACD'], df['Signal'], df['Histogram'] = calculate_macd(
        df['Close'], config.fast_period, config.slow_period, config.signal_period
    )
    df['ATR'] = calculate_atr(df['High'], df['Low'], df['Close'])
    
    # Histogram flip detection
    df['Hist_Flip'] = 0
    df.loc[(df['Histogram'] > 0) & (df['Histogram'].shift(1) <= 0), 'Hist_Flip'] = 1
    df.loc[(df['Histogram'] < 0) & (df['Histogram'].shift(1) >= 0), 'Hist_Flip'] = -1
    
    # Histogram momentum
    df['Hist_Growing'] = df['Histogram'].diff() > 0
    df['Hist_Shrinking'] = df['Histogram'].diff() < 0
    
    # Entry trigger
    df['Entry_Trigger'] = 0
    
    # Bullish: Histogram flip to positive OR growing positive histogram
    bull_trigger = (df['Hist_Flip'] == 1) | ((df['Histogram'] > 0) & df['Hist_Growing'])
    bear_trigger = (df['Hist_Flip'] == -1) | ((df['Histogram'] < 0) & df['Hist_Shrinking'])
    
    df.loc[bull_trigger, 'Entry_Trigger'] = 1
    df.loc[bear_trigger, 'Entry_Trigger'] = -1
    
    return df


def align_timeframes(weekly_df: pd.DataFrame, daily_df: pd.DataFrame, 
                     hourly_4h_df: pd.DataFrame) -> pd.DataFrame:
    """
    Align all timeframes to daily index for backtesting
    
    For each daily bar:
    - Get weekly bias (from the week that daily bar belongs to)
    - Get daily setup signal
    - Get 4H confirmation (from 4H bars within that day)
    """
    # Process each timeframe
    weekly_df = weekly_df.copy()
    daily_df = daily_df.copy()
    hourly_4h_df = hourly_4h_df.copy()
    
    # Create aligned dataframe based on daily
    aligned = daily_df.copy()
    
    # Map weekly bias to daily
    # Convert weekly index to date for matching
    weekly_df['Week_Start'] = weekly_df.index
    
    aligned['Weekly_Bias'] = 0
    aligned['Weekly_Strong'] = False
    
    for idx in aligned.index:
        # Find the weekly bar that contains this daily bar
        date = idx.date() if hasattr(idx, 'date') else pd.Timestamp(idx).date()
        
        # Get weekly data up to this date
        weekly_before = weekly_df[weekly_df.index.date <= date]
        if len(weekly_before) > 0:
            latest_weekly = weekly_before.iloc[-1]
            aligned.loc[idx, 'Weekly_Bias'] = latest_weekly['Bias']
            aligned.loc[idx, 'Weekly_Strong'] = latest_weekly['Strong_Trend']
    
    # Map 4H entry triggers to daily
    aligned['Has_4H_Bull_Trigger'] = False
    aligned['Has_4H_Bear_Trigger'] = False
    
    for idx in aligned.index:
        date = idx.date() if hasattr(idx, 'date') else pd.Timestamp(idx).date()
        
        # Get 4H bars for this day
        try:
            day_4h = hourly_4h_df[hourly_4h_df.index.date == date]
            if len(day_4h) > 0:
                # Check if any 4H bar has entry trigger
                if (day_4h['Entry_Trigger'] == 1).any():
                    aligned.loc[idx, 'Has_4H_Bull_Trigger'] = True
                if (day_4h['Entry_Trigger'] == -1).any():
                    aligned.loc[idx, 'Has_4H_Bear_Trigger'] = True
        except:
            pass
    
    return aligned


# =============================================================================
# MTF SIGNAL GENERATION
# =============================================================================

def generate_mtf_signals(aligned_df: pd.DataFrame, config: MTFConfig) -> pd.DataFrame:
    """
    Generate signals requiring ALL timeframes to align
    
    For a valid LONG signal:
    1. Weekly MACD > 0 (bullish bias)
    2. Daily has buy setup (crossover or divergence)
    3. 4H has bullish histogram trigger
    
    For a valid SHORT signal:
    1. Weekly MACD < 0 (bearish bias)
    2. Daily has sell setup
    3. 4H has bearish histogram trigger
    """
    df = aligned_df.copy()
    
    df['MTF_Signal'] = 0
    df['MTF_Signal_Type'] = ''
    df['MTF_Quality'] = 0
    
    for i in range(1, len(df)):
        idx = df.index[i]
        
        weekly_bias = df.loc[idx, 'Weekly_Bias']
        weekly_strong = df.loc[idx, 'Weekly_Strong']
        daily_setup = df.loc[idx, 'Setup']
        daily_setup_type = df.loc[idx, 'Setup_Type']
        has_4h_bull = df.loc[idx, 'Has_4H_Bull_Trigger']
        has_4h_bear = df.loc[idx, 'Has_4H_Bear_Trigger']
        
        signal = 0
        sig_type = ''
        quality = 0
        
        # LONG SIGNAL: All three timeframes bullish
        if daily_setup == 1:  # Daily has buy setup
            quality = 1
            
            # Check weekly alignment
            if weekly_bias == 1 and config.require_trend_alignment:
                quality += 1
                
                # Check 4H confirmation
                if has_4h_bull and config.require_histogram_confirm:
                    quality += 1
                    signal = 1
                    sig_type = f"MTF_{daily_setup_type}"
                    
                    if weekly_strong:
                        quality += 1
                        sig_type += "+StrongTrend"
                
                elif not config.require_histogram_confirm:
                    signal = 1
                    sig_type = f"MTF_{daily_setup_type}"
            
            elif not config.require_trend_alignment:
                if has_4h_bull:
                    signal = 1
                    sig_type = daily_setup_type
                    quality = 2
        
        # SHORT SIGNAL: All three timeframes bearish
        if daily_setup == -1 and not config.long_only:
            quality = 1
            
            if weekly_bias == -1 and config.require_trend_alignment:
                quality += 1
                
                if has_4h_bear and config.require_histogram_confirm:
                    quality += 1
                    signal = -1
                    sig_type = f"MTF_{daily_setup_type}"
                    
                    if weekly_strong:
                        quality += 1
                        sig_type += "+StrongTrend"
                
                elif not config.require_histogram_confirm:
                    signal = -1
                    sig_type = f"MTF_{daily_setup_type}"
            
            elif not config.require_trend_alignment:
                if has_4h_bear:
                    signal = -1
                    sig_type = daily_setup_type
                    quality = 2
        
        df.loc[idx, 'MTF_Signal'] = signal
        df.loc[idx, 'MTF_Signal_Type'] = sig_type
        df.loc[idx, 'MTF_Quality'] = quality
    
    return df


# =============================================================================
# BACKTESTING
# =============================================================================

def backtest_mtf_strategy(symbol: str, config: MTFConfig = None) -> Optional[Dict]:
    """
    Backtest the true multi-timeframe MACD strategy
    """
    config = config or MTFConfig()
    
    # Download data
    weekly, daily, hourly_4h = download_mtf_data(symbol)
    if weekly is None or daily is None or hourly_4h is None:
        return None
    
    # Process each timeframe
    weekly = get_weekly_bias(weekly, config)
    daily = get_daily_setups(daily, config)
    hourly_4h = get_4h_entry_triggers(hourly_4h, config)
    
    # Align timeframes
    aligned = align_timeframes(weekly, daily, hourly_4h)
    
    # Generate MTF signals
    df = generate_mtf_signals(aligned, config)
    
    # Backtest
    trades = []
    capital = 100000
    current_capital = capital
    position = None
    
    for i in range(30, len(df)):
        row = df.iloc[i]
        curr_atr = row.get('ATR', 0)
        
        if pd.isna(curr_atr) or curr_atr <= 0:
            continue
        
        # Exit logic
        if position is not None:
            exit_triggered = False
            exit_price = None
            exit_reason = None
            
            if position['direction'] == 'long':
                if row['Low'] <= position['stop_loss']:
                    exit_price = position['stop_loss'] * (1 - config.slippage)
                    exit_triggered = True
                    exit_reason = 'stop_loss'
                elif row['High'] >= position['take_profit']:
                    exit_price = position['take_profit']
                    exit_triggered = True
                    exit_reason = 'take_profit'
                # Trail stop after 1R
                elif not position.get('trailing', False):
                    unrealized = row['Close'] - position['entry_price']
                    if unrealized >= position['initial_risk']:
                        position['stop_loss'] = position['entry_price']
                        position['trailing'] = True
            else:
                if row['High'] >= position['stop_loss']:
                    exit_price = position['stop_loss'] * (1 + config.slippage)
                    exit_triggered = True
                    exit_reason = 'stop_loss'
                elif row['Low'] <= position['take_profit']:
                    exit_price = position['take_profit']
                    exit_triggered = True
                    exit_reason = 'take_profit'
                elif not position.get('trailing', False):
                    unrealized = position['entry_price'] - row['Close']
                    if unrealized >= position['initial_risk']:
                        position['stop_loss'] = position['entry_price']
                        position['trailing'] = True
            
            if exit_triggered:
                if position['direction'] == 'long':
                    pnl = position['shares'] * (exit_price - position['entry_price'])
                else:
                    pnl = position['shares'] * (position['entry_price'] - exit_price)
                
                pnl -= position['shares'] * exit_price * config.commission
                current_capital += pnl
                
                trades.append({
                    'entry_date': position['entry_date'],
                    'exit_date': df.index[i],
                    'direction': position['direction'],
                    'entry_price': position['entry_price'],
                    'exit_price': exit_price,
                    'shares': position['shares'],
                    'pnl': pnl,
                    'return_pct': (pnl / (position['shares'] * position['entry_price'])) * 100,
                    'exit_reason': exit_reason,
                    'signal_type': position['signal_type'],
                    'quality': position['quality']
                })
                position = None
        
        # Entry logic
        if position is None:
            signal = row.get('MTF_Signal', 0)
            quality = row.get('MTF_Quality', 0)
            sig_type = row.get('MTF_Signal_Type', '')
            
            # Require quality >= 3 (all timeframes aligned)
            if signal != 0 and quality >= 3:
                close = row['Close']
                lookback = min(20, i)
                
                if signal == 1:
                    entry_price = close * (1 + config.slippage)
                    recent_low = df['Low'].iloc[i-lookback:i].min()
                    stop_loss = min(recent_low * 0.998, entry_price - curr_atr * 2)
                    initial_risk = entry_price - stop_loss
                    take_profit = entry_price + initial_risk * config.risk_reward
                    direction = 'long'
                else:
                    entry_price = close * (1 - config.slippage)
                    recent_high = df['High'].iloc[i-lookback:i].max()
                    stop_loss = max(recent_high * 1.002, entry_price + curr_atr * 2)
                    initial_risk = stop_loss - entry_price
                    take_profit = entry_price - initial_risk * config.risk_reward
                    direction = 'short'
                
                risk_amount = current_capital * config.risk_pct
                shares = int(risk_amount / initial_risk) if initial_risk > 0 else 0
                max_shares = int(current_capital * config.max_position_pct / entry_price)
                shares = min(shares, max_shares)
                
                if shares > 0:
                    current_capital -= shares * entry_price * config.commission
                    
                    position = {
                        'entry_date': df.index[i],
                        'direction': direction,
                        'entry_price': entry_price,
                        'stop_loss': stop_loss,
                        'take_profit': take_profit,
                        'initial_risk': initial_risk,
                        'shares': shares,
                        'signal_type': sig_type,
                        'quality': quality,
                        'trailing': False
                    }
    
    # Calculate results
    trades_df = pd.DataFrame(trades) if trades else pd.DataFrame()
    total_return = (current_capital / capital - 1) * 100
    
    results = {
        'symbol': symbol,
        'strategy': 'MACD 3-System MTF',
        'capital_start': capital,
        'capital_end': current_capital,
        'total_return': total_return,
        'total_trades': len(trades_df),
    }
    
    if len(trades_df) > 0:
        wins = trades_df[trades_df['pnl'] > 0]
        losses = trades_df[trades_df['pnl'] < 0]
        
        results['win_rate'] = len(wins) / len(trades_df)
        results['avg_win'] = wins['return_pct'].mean() if len(wins) > 0 else 0
        results['avg_loss'] = losses['return_pct'].mean() if len(losses) > 0 else 0
        results['profit_factor'] = abs(wins['pnl'].sum() / losses['pnl'].sum()) if len(losses) > 0 and losses['pnl'].sum() != 0 else float('inf')
        results['avg_trade'] = trades_df['return_pct'].mean()
        results['exits'] = trades_df['exit_reason'].value_counts().to_dict()
        results['signal_types'] = trades_df['signal_type'].value_counts().to_dict()
    else:
        results['win_rate'] = 0
        results['avg_win'] = 0
        results['avg_loss'] = 0
        results['profit_factor'] = 0
        results['avg_trade'] = 0
        results['exits'] = {}
        results['signal_types'] = {}
    
    results['trades_df'] = trades_df
    
    return results


# =============================================================================
# MAIN
# =============================================================================

def run_mtf_backtest():
    """Run comprehensive MTF backtest"""
    
    symbols = [
        'RELIANCE.NS', 'TCS.NS', 'HDFCBANK.NS', 'INFY.NS', 'SBIN.NS',
        'BHARTIARTL.NS', 'ITC.NS', 'AXISBANK.NS', 'LT.NS', 'MARUTI.NS',
        'SUNPHARMA.NS', 'TITAN.NS', 'WIPRO.NS', 'BAJFINANCE.NS', 'ICICIBANK.NS'
    ]
    
    print("=" * 100)
    print("MACD 3-SYSTEM TRUE MULTI-TIMEFRAME STRATEGY")
    print("=" * 100)
    print("""
TIMEFRAME HIERARCHY:
  - Weekly Chart: Overall trend bias (MACD above/below zero)
  - Daily Chart:  Setup identification (crossover/divergence)
  - 4-Hour Chart: Entry trigger (histogram confirmation)

ALL THREE TIMEFRAMES MUST ALIGN FOR A VALID SIGNAL

Signal Quality:
  - Quality 4: Weekly strong trend + Daily setup + 4H confirm
  - Quality 3: Weekly bias + Daily setup + 4H confirm (minimum required)
  
Risk Management:
  - 2% risk per trade
  - 1:2.5 Risk:Reward
  - Trail stop to breakeven at 1R profit
    """)
    
    config = MTFConfig()
    all_results = []
    
    print("-" * 100)
    print(f"{'Symbol':<15} | {'Return':>8} | {'Trades':>6} | {'Win%':>6} | {'PF':>6} | {'AvgTrade':>8} | {'Exits':<20}")
    print("-" * 100)
    
    for symbol in symbols:
        try:
            result = backtest_mtf_strategy(symbol, config)
            
            if result is None:
                print(f"{symbol:<15} | Skip - insufficient data")
                continue
            
            all_results.append(result)
            
            # Format exits
            exits_str = ', '.join([f"{k}:{v}" for k, v in result.get('exits', {}).items()])[:20]
            
            status = "OK" if result['total_return'] > 0 else "--"
            pf = min(result['profit_factor'], 99.9) if result['profit_factor'] != float('inf') else 99.9
            
            print(f"{symbol:<15} | {result['total_return']:>7.2f}% | {result['total_trades']:>6} | "
                  f"{result['win_rate']:>5.1%} | {pf:>6.2f} | {result['avg_trade']:>7.2f}% | "
                  f"{exits_str:<20} {status}")
            
        except Exception as e:
            print(f"{symbol:<15} | Error: {str(e)[:50]}")
    
    # Summary
    if all_results:
        print("\n" + "=" * 100)
        print("MTF STRATEGY SUMMARY")
        print("=" * 100)
        
        returns = [r['total_return'] for r in all_results]
        win_rates = [r['win_rate'] for r in all_results]
        total_trades = sum(r['total_trades'] for r in all_results)
        profitable = sum(1 for r in all_results if r['total_return'] > 0)
        
        pfs = [r['profit_factor'] for r in all_results if r['profit_factor'] < 100]
        
        print(f"\nStocks Tested:     {len(all_results)}")
        print(f"Profitable:        {profitable}/{len(all_results)} ({profitable/len(all_results):.1%})")
        print(f"\nAverage Return:    {np.mean(returns):.2f}%")
        print(f"Median Return:     {np.median(returns):.2f}%")
        print(f"Best:              {max(returns):.2f}%")
        print(f"Worst:             {min(returns):.2f}%")
        print(f"\nTotal Trades:      {total_trades}")
        print(f"Avg Win Rate:      {np.mean(win_rates):.1%}")
        print(f"Avg Profit Factor: {np.mean(pfs):.2f}" if pfs else "N/A")
        
        # Exit breakdown
        print("\n" + "-" * 100)
        print("EXIT BREAKDOWN")
        print("-" * 100)
        all_exits = {}
        for r in all_results:
            for reason, count in r.get('exits', {}).items():
                all_exits[reason] = all_exits.get(reason, 0) + count
        for reason, count in sorted(all_exits.items(), key=lambda x: -x[1]):
            pct = count / total_trades * 100 if total_trades > 0 else 0
            print(f"  {reason:<15}: {count:>4} ({pct:>5.1f}%)")
        
        # Signal types
        print("\n" + "-" * 100)
        print("SIGNAL TYPE BREAKDOWN")
        print("-" * 100)
        all_types = {}
        for r in all_results:
            for sig_type, count in r.get('signal_types', {}).items():
                all_types[sig_type] = all_types.get(sig_type, 0) + count
        for sig_type, count in sorted(all_types.items(), key=lambda x: -x[1]):
            pct = count / total_trades * 100 if total_trades > 0 else 0
            print(f"  {sig_type:<30}: {count:>4} ({pct:>5.1f}%)")
    
    # Comparison
    print("\n" + "=" * 100)
    print("MTF vs SINGLE TIMEFRAME COMPARISON")
    print("=" * 100)
    print("""
Expected Results Comparison:

| Metric          | Daily Only (V3) | MTF (Weekly/Daily/4H) |
|-----------------|-----------------|------------------------|
| Avg Return      | +0.39%          | See above              |
| Profitable      | 53%             | See above              |
| Total Trades    | 45              | See above              |
| Win Rate        | 37.9%           | See above              |

KEY INSIGHTS:

1. MTF ADVANTAGES:
   - Fewer but higher quality trades
   - Weekly trend filter reduces counter-trend trades
   - 4H confirmation improves entry timing
   
2. MTF CHALLENGES:
   - Fewer trading opportunities
   - May miss moves that don't align all TFs
   - 4H data limited to 60 days on Yahoo
   
3. WHEN MTF WORKS BEST:
   - Strong trending markets
   - Clear weekly direction
   - Patience for setups to align
""")
    
    return all_results


if __name__ == "__main__":
    results = run_mtf_backtest()
