"""
MACD 3-SYSTEM INTRADAY STRATEGY
================================

Adapts the MACD 3-System swing strategy for intraday trading.

Key Differences from Swing Trading:
1. Faster MACD settings (5/13/3 or 8/17/5 instead of 12/26/9)
2. Shorter confirmation periods
3. Mandatory EOD exit (no overnight positions)
4. Higher transaction costs (more trades)
5. Tighter stops (ATR-based, smaller multiplier)
6. Use 5-min or 15-min timeframes

Intraday Adaptations:
- System 1: Zero line rule with 1-candle confirmation (faster)
- System 2: Divergence on 15-min, entry on 5-min
- System 3: Simulated MTF (15-min trend, 5-min entry)
- Exit: Close all positions by 3:15 PM IST

Market Hours: 9:15 AM - 3:30 PM IST
Trading Window: 9:30 AM - 3:15 PM (avoid first 15 min, last 15 min)
"""

import pandas as pd
import numpy as np
import yfinance as yf
from datetime import datetime, timedelta, time
from typing import Dict, List, Tuple, Optional
from dataclasses import dataclass
import warnings
warnings.filterwarnings('ignore')


# =============================================================================
# CONFIGURATION
# =============================================================================

@dataclass
class IntradayMACDConfig:
    """Intraday MACD Configuration"""
    # Faster MACD for intraday
    fast_period: int = 8
    slow_period: int = 17
    signal_period: int = 5
    
    # Trend MACD (simulates higher timeframe)
    trend_fast: int = 12
    trend_slow: int = 26
    trend_signal: int = 9
    
    # Signal filters
    confirmation_candles: int = 1  # Faster for intraday
    min_distance_std: float = 0.5
    chop_zone_std: float = 0.3
    
    # Risk Management - Tighter for intraday
    risk_pct: float = 0.01  # 1% risk per trade (lower for intraday)
    atr_stop_mult: float = 1.5  # Tighter stops
    atr_target_mult: float = 2.0  # 1:1.33 R:R initially, trail for more
    max_position_pct: float = 0.20
    max_trades_per_day: int = 3  # Limit overtrading
    
    # Intraday specific
    entry_start_time: str = "09:30"  # Start trading 15 min after open
    entry_end_time: str = "14:30"    # Stop new entries
    exit_time: str = "15:15"         # Close all positions
    
    # Transaction costs (higher for intraday)
    slippage: float = 0.001  # 0.1%
    commission: float = 0.001  # 0.1%
    
    # Mode
    long_only: bool = True


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
    """Calculate Average True Range"""
    tr = pd.concat([
        high - low,
        abs(high - close.shift(1)),
        abs(low - close.shift(1))
    ], axis=1).max(axis=1)
    return tr.rolling(period).mean()


def calculate_vwap(high: pd.Series, low: pd.Series, close: pd.Series, 
                   volume: pd.Series) -> pd.Series:
    """Calculate VWAP (important for intraday)"""
    typical_price = (high + low + close) / 3
    cumulative_tp_vol = (typical_price * volume).cumsum()
    cumulative_vol = volume.cumsum()
    return cumulative_tp_vol / cumulative_vol


def calculate_ema(prices: pd.Series, period: int) -> pd.Series:
    """Calculate EMA"""
    return prices.ewm(span=period, adjust=False).mean()


# =============================================================================
# INTRADAY SIGNAL DETECTION
# =============================================================================

def detect_crossover(macd: pd.Series, signal: pd.Series, 
                     confirmation: int = 1) -> pd.Series:
    """Detect MACD crossover with confirmation"""
    cross = pd.Series(index=macd.index, data=0)
    
    for i in range(confirmation + 1, len(macd)):
        # Bullish crossover N bars ago, still above
        if (macd.iloc[i-confirmation] > signal.iloc[i-confirmation] and
            macd.iloc[i-confirmation-1] <= signal.iloc[i-confirmation-1] and
            macd.iloc[i] > signal.iloc[i]):
            cross.iloc[i] = 1
        
        # Bearish crossover N bars ago, still below
        elif (macd.iloc[i-confirmation] < signal.iloc[i-confirmation] and
              macd.iloc[i-confirmation-1] >= signal.iloc[i-confirmation-1] and
              macd.iloc[i] < signal.iloc[i]):
            cross.iloc[i] = -1
    
    return cross


def detect_histogram_flip(histogram: pd.Series) -> pd.Series:
    """Detect histogram color flip"""
    flip = pd.Series(index=histogram.index, data=0)
    
    # First green after red
    flip[(histogram > 0) & (histogram.shift(1) <= 0)] = 1
    # First red after green
    flip[(histogram < 0) & (histogram.shift(1) >= 0)] = -1
    
    return flip


def get_macd_zone(macd: pd.Series, lookback: int = 50) -> pd.Series:
    """Determine MACD zone relative to zero"""
    macd_std = macd.rolling(lookback).std()
    normalized = macd / macd_std.replace(0, np.nan)
    
    zone = pd.Series(index=macd.index, data='chop')
    zone[(normalized > 0.5)] = 'bullish'
    zone[(normalized < -0.5)] = 'bearish'
    zone[(normalized.abs() < 0.3)] = 'chop'
    
    return zone


def detect_divergence_fast(high: pd.Series, low: pd.Series, macd: pd.Series,
                           lookback: int = 15) -> Tuple[pd.Series, pd.Series]:
    """Fast divergence detection for intraday"""
    bullish = pd.Series(index=high.index, data=0)
    bearish = pd.Series(index=high.index, data=0)
    
    for i in range(lookback, len(high)):
        # Recent window
        h_window = high.iloc[i-lookback:i+1]
        l_window = low.iloc[i-lookback:i+1]
        m_window = macd.iloc[i-lookback:i+1]
        
        # Bearish: Higher price high, lower MACD high
        mid = lookback // 2
        if (h_window.iloc[-1] > h_window.iloc[mid] and 
            m_window.iloc[-1] < m_window.iloc[mid]):
            bearish.iloc[i] = 1
        
        # Bullish: Lower price low, higher MACD low
        if (l_window.iloc[-1] < l_window.iloc[mid] and 
            m_window.iloc[-1] > m_window.iloc[mid]):
            bullish.iloc[i] = 1
    
    return bullish, bearish


def find_intraday_levels(df: pd.DataFrame, lookback: int = 50) -> Tuple[List[float], List[float]]:
    """Find support/resistance for intraday"""
    high = df['High'].iloc[-lookback:]
    low = df['Low'].iloc[-lookback:]
    
    # Previous day high/low (most important for intraday)
    prev_day_high = high.iloc[:-20].max() if len(high) > 20 else high.max()
    prev_day_low = low.iloc[:-20].min() if len(low) > 20 else low.min()
    
    # Recent swing points
    support = [prev_day_low, low.min()]
    resistance = [prev_day_high, high.max()]
    
    return list(set(support)), list(set(resistance))


def is_near_level(price: float, levels: List[float], threshold: float = 0.005) -> bool:
    """Check if price is near a level (tighter for intraday)"""
    for level in levels:
        if abs(price - level) / level < threshold:
            return True
    return False


# =============================================================================
# INTRADAY MACD STRATEGY
# =============================================================================

class IntradayMACD3System:
    """
    MACD 3-System adapted for intraday trading
    """
    
    def __init__(self, config: IntradayMACDConfig = None):
        self.config = config or IntradayMACDConfig()
        self.name = "MACD 3-System Intraday"
    
    def generate_signals(self, df: pd.DataFrame) -> pd.DataFrame:
        """Generate intraday signals"""
        df = df.copy()
        cfg = self.config
        
        # Fast MACD (entry)
        df['MACD'], df['Signal'], df['Histogram'] = calculate_macd(
            df['Close'], cfg.fast_period, cfg.slow_period, cfg.signal_period
        )
        
        # Trend MACD (filter)
        df['MACD_Trend'], df['Signal_Trend'], _ = calculate_macd(
            df['Close'], cfg.trend_fast, cfg.trend_slow, cfg.trend_signal
        )
        
        # ATR
        df['ATR'] = calculate_atr(df['High'], df['Low'], df['Close'], 14)
        
        # VWAP (important for intraday)
        if 'Volume' in df.columns:
            df['VWAP'] = calculate_vwap(df['High'], df['Low'], df['Close'], df['Volume'])
        
        # EMA 20 (trend reference)
        df['EMA20'] = calculate_ema(df['Close'], 20)
        
        # Detect crossovers
        df['Crossover'] = detect_crossover(df['MACD'], df['Signal'], cfg.confirmation_candles)
        
        # Histogram flip
        df['Hist_Flip'] = detect_histogram_flip(df['Histogram'])
        
        # MACD zone
        df['MACD_Zone'] = get_macd_zone(df['MACD'])
        
        # Divergence
        df['Bullish_Div'], df['Bearish_Div'] = detect_divergence_fast(
            df['High'], df['Low'], df['MACD'], 15
        )
        
        # Trend alignment (simulated MTF)
        df['Trend_Bullish'] = df['MACD_Trend'] > 0
        df['Trend_Bearish'] = df['MACD_Trend'] < 0
        
        # Generate signals
        df['Signal_Dir'] = 0
        df['Signal_Type'] = ''
        df['Signal_Quality'] = 0
        
        warmup = max(cfg.slow_period, cfg.trend_slow) + 10
        
        for i in range(warmup, len(df)):
            idx = df.index[i]
            
            crossover = df.loc[idx, 'Crossover']
            hist_flip = df.loc[idx, 'Hist_Flip']
            zone = df.loc[idx, 'MACD_Zone']
            trend_bull = df.loc[idx, 'Trend_Bullish']
            trend_bear = df.loc[idx, 'Trend_Bearish']
            bull_div = df.loc[idx, 'Bullish_Div']
            bear_div = df.loc[idx, 'Bearish_Div']
            curr_price = df.loc[idx, 'Close']
            curr_macd = df.loc[idx, 'MACD']
            
            # Get levels
            support, resistance = find_intraday_levels(df.iloc[:i+1], 50)
            near_support = is_near_level(curr_price, support, 0.005)
            near_resistance = is_near_level(curr_price, resistance, 0.005)
            
            signal = 0
            sig_type = ''
            quality = 0
            
            # SYSTEM 1: Trend trades (crossover in right zone with trend alignment)
            if crossover == 1 and zone == 'bullish' and trend_bull:
                signal = 1
                sig_type = 'S1_Trend'
                quality = 2
                
                if hist_flip == 1:
                    quality += 1
                    sig_type += '+Hist'
                if near_support:
                    quality += 1
                    sig_type += '+Level'
            
            elif crossover == -1 and zone == 'bearish' and trend_bear and not cfg.long_only:
                signal = -1
                sig_type = 'S1_Trend'
                quality = 2
                
                if hist_flip == -1:
                    quality += 1
                    sig_type += '+Hist'
                if near_resistance:
                    quality += 1
                    sig_type += '+Level'
            
            # SYSTEM 2: Divergence (reversal)
            if signal == 0:
                if bull_div == 1 and hist_flip == 1:
                    signal = 1
                    sig_type = 'S2_Div'
                    quality = 3
                    if near_support:
                        quality += 1
                        sig_type += '+Level'
                
                elif bear_div == 1 and hist_flip == -1 and not cfg.long_only:
                    signal = -1
                    sig_type = 'S2_Div'
                    quality = 3
                    if near_resistance:
                        quality += 1
                        sig_type += '+Level'
            
            # SYSTEM 3: Histogram flip with trend (momentum trade)
            if signal == 0:
                if hist_flip == 1 and trend_bull and curr_macd > 0:
                    signal = 1
                    sig_type = 'S3_Momentum'
                    quality = 2
                    if near_support:
                        quality += 1
                        sig_type += '+Level'
                
                elif hist_flip == -1 and trend_bear and curr_macd < 0 and not cfg.long_only:
                    signal = -1
                    sig_type = 'S3_Momentum'
                    quality = 2
                    if near_resistance:
                        quality += 1
                        sig_type += '+Level'
            
            df.loc[idx, 'Signal_Dir'] = signal
            df.loc[idx, 'Signal_Type'] = sig_type
            df.loc[idx, 'Signal_Quality'] = quality
        
        return df


# =============================================================================
# INTRADAY BACKTESTER
# =============================================================================

def is_trading_time(timestamp, start_time: str = "09:30", end_time: str = "14:30") -> bool:
    """Check if within trading window"""
    if pd.isna(timestamp):
        return False
    
    t = timestamp.time() if hasattr(timestamp, 'time') else timestamp
    start = time(int(start_time.split(':')[0]), int(start_time.split(':')[1]))
    end = time(int(end_time.split(':')[0]), int(end_time.split(':')[1]))
    
    return start <= t <= end


def is_exit_time(timestamp, exit_time: str = "15:15") -> bool:
    """Check if it's time to exit all positions"""
    if pd.isna(timestamp):
        return False
    
    t = timestamp.time() if hasattr(timestamp, 'time') else timestamp
    exit_t = time(int(exit_time.split(':')[0]), int(exit_time.split(':')[1]))
    
    return t >= exit_t


def get_date(timestamp) -> str:
    """Extract date from timestamp"""
    if hasattr(timestamp, 'date'):
        return str(timestamp.date())
    return str(timestamp)[:10]


def backtest_intraday_macd(df: pd.DataFrame, config: IntradayMACDConfig = None,
                           capital: float = 100000) -> Dict:
    """
    Backtest intraday MACD strategy with proper constraints
    """
    config = config or IntradayMACDConfig()
    strategy = IntradayMACD3System(config)
    
    df = strategy.generate_signals(df)
    
    trades = []
    current_capital = capital
    position = None
    daily_trades = 0
    current_date = None
    
    for i in range(50, len(df)):
        row = df.iloc[i]
        idx = df.index[i]
        curr_atr = row.get('ATR', 0)
        
        if pd.isna(curr_atr) or curr_atr <= 0:
            continue
        
        # Reset daily trade counter
        row_date = get_date(idx)
        if current_date != row_date:
            current_date = row_date
            daily_trades = 0
        
        # Check for mandatory EOD exit
        if position is not None and is_exit_time(idx, config.exit_time):
            # Force close position
            exit_price = row['Close'] * (1 - config.slippage if position['direction'] == 'long' else 1 + config.slippage)
            
            if position['direction'] == 'long':
                pnl = position['shares'] * (exit_price - position['entry_price'])
            else:
                pnl = position['shares'] * (position['entry_price'] - exit_price)
            
            pnl -= position['shares'] * exit_price * config.commission
            current_capital += pnl
            
            trades.append({
                'entry_date': position['entry_date'],
                'exit_date': idx,
                'direction': position['direction'],
                'entry_price': position['entry_price'],
                'exit_price': exit_price,
                'shares': position['shares'],
                'pnl': pnl,
                'return_pct': (pnl / (position['shares'] * position['entry_price'])) * 100,
                'exit_reason': 'eod_exit',
                'signal_type': position['signal_type']
            })
            position = None
            continue
        
        # Check exit for existing position
        if position is not None:
            exit_triggered = False
            exit_price = None
            exit_reason = None
            
            if position['direction'] == 'long':
                # Stop loss
                if row['Low'] <= position['stop_loss']:
                    exit_price = position['stop_loss'] * (1 - config.slippage)
                    exit_triggered = True
                    exit_reason = 'stop_loss'
                # Take profit
                elif row['High'] >= position['take_profit']:
                    exit_price = position['take_profit']
                    exit_triggered = True
                    exit_reason = 'take_profit'
                # Trail stop after 1R profit
                elif not position.get('trailing', False):
                    unrealized = row['Close'] - position['entry_price']
                    if unrealized >= position['initial_risk']:
                        position['stop_loss'] = position['entry_price'] + position['initial_risk'] * 0.5
                        position['trailing'] = True
            
            else:  # Short
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
                        position['stop_loss'] = position['entry_price'] - position['initial_risk'] * 0.5
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
                    'exit_date': idx,
                    'direction': position['direction'],
                    'entry_price': position['entry_price'],
                    'exit_price': exit_price,
                    'shares': position['shares'],
                    'pnl': pnl,
                    'return_pct': (pnl / (position['shares'] * position['entry_price'])) * 100,
                    'exit_reason': exit_reason,
                    'signal_type': position['signal_type']
                })
                position = None
        
        # Check for new entry (only during trading hours)
        if position is None and daily_trades < config.max_trades_per_day:
            if is_trading_time(idx, config.entry_start_time, config.entry_end_time):
                signal = row.get('Signal_Dir', 0)
                quality = row.get('Signal_Quality', 0)
                sig_type = row.get('Signal_Type', '')
                
                # Only take quality signals
                if signal != 0 and quality >= 2:
                    close = row['Close']
                    
                    if signal == 1:
                        entry_price = close * (1 + config.slippage)
                        stop_loss = entry_price - curr_atr * config.atr_stop_mult
                        initial_risk = entry_price - stop_loss
                        take_profit = entry_price + initial_risk * config.atr_target_mult
                        direction = 'long'
                    else:
                        entry_price = close * (1 - config.slippage)
                        stop_loss = entry_price + curr_atr * config.atr_stop_mult
                        initial_risk = stop_loss - entry_price
                        take_profit = entry_price - initial_risk * config.atr_target_mult
                        direction = 'short'
                    
                    # Position sizing
                    risk_amount = current_capital * config.risk_pct
                    shares = int(risk_amount / initial_risk) if initial_risk > 0 else 0
                    max_shares = int(current_capital * config.max_position_pct / entry_price)
                    shares = min(shares, max_shares)
                    
                    if shares > 0:
                        current_capital -= shares * entry_price * config.commission
                        daily_trades += 1
                        
                        position = {
                            'entry_date': idx,
                            'direction': direction,
                            'entry_price': entry_price,
                            'stop_loss': stop_loss,
                            'take_profit': take_profit,
                            'initial_risk': initial_risk,
                            'shares': shares,
                            'signal_type': sig_type,
                            'trailing': False
                        }
    
    # Results
    trades_df = pd.DataFrame(trades) if trades else pd.DataFrame()
    total_return = (current_capital / capital - 1) * 100
    
    results = {
        'strategy': 'MACD 3-System Intraday',
        'timeframe': 'Intraday',
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
        results['max_win'] = trades_df['return_pct'].max()
        results['max_loss'] = trades_df['return_pct'].min()
        
        # Exit reason breakdown
        results['exits'] = trades_df['exit_reason'].value_counts().to_dict()
        results['signal_types'] = trades_df['signal_type'].value_counts().to_dict()
        
        # Daily stats
        trades_df['date'] = pd.to_datetime(trades_df['entry_date']).dt.date
        daily_pnl = trades_df.groupby('date')['pnl'].sum()
        results['winning_days'] = (daily_pnl > 0).sum()
        results['losing_days'] = (daily_pnl < 0).sum()
        results['avg_daily_pnl'] = daily_pnl.mean()
    else:
        results['win_rate'] = 0
        results['avg_win'] = 0
        results['avg_loss'] = 0
        results['profit_factor'] = 0
        results['avg_trade'] = 0
        results['max_win'] = 0
        results['max_loss'] = 0
        results['exits'] = {}
        results['signal_types'] = {}
        results['winning_days'] = 0
        results['losing_days'] = 0
        results['avg_daily_pnl'] = 0
    
    results['trades_df'] = trades_df
    
    return results


# =============================================================================
# DATA DOWNLOAD
# =============================================================================

def download_intraday_data(symbol: str, period: str = '60d', 
                           interval: str = '15m') -> Optional[pd.DataFrame]:
    """Download intraday data from Yahoo Finance"""
    try:
        df = yf.download(symbol, period=period, interval=interval, progress=False)
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        
        if df.empty or len(df) < 100:
            return None
        
        return df
    except Exception as e:
        print(f"Error downloading {symbol}: {e}")
        return None


# =============================================================================
# MAIN BACKTEST
# =============================================================================

def run_intraday_backtest():
    """Run comprehensive intraday backtest"""
    
    symbols = [
        'RELIANCE.NS', 'TCS.NS', 'HDFCBANK.NS', 'INFY.NS', 'SBIN.NS',
        'BHARTIARTL.NS', 'ITC.NS', 'AXISBANK.NS', 'LT.NS', 'MARUTI.NS',
        'SUNPHARMA.NS', 'TITAN.NS', 'WIPRO.NS', 'BAJFINANCE.NS', 'ICICIBANK.NS'
    ]
    
    print("=" * 95)
    print("MACD 3-SYSTEM INTRADAY STRATEGY BACKTEST")
    print("=" * 95)
    print("""
Intraday Adaptations:
  - Fast MACD: 8/17/5 (vs 12/26/9 for swing)
  - Trend MACD: 12/26/9 (simulates higher TF)
  - 1-candle confirmation (faster entries)
  - ATR-based stops: 1.5 ATR
  - Target: 2 ATR (1:1.33 R:R)
  - Max 3 trades per day
  - Mandatory EOD exit at 3:15 PM
  
Data: 60 days of 15-min bars
Risk: 1% per trade
Costs: 0.1% slippage + 0.1% commission
    """)
    
    # Test with different intervals
    intervals = ['15m', '5m']
    all_results = {}
    
    for interval in intervals:
        print(f"\n{'='*95}")
        print(f"TESTING WITH {interval.upper()} BARS")
        print(f"{'='*95}")
        
        config = IntradayMACDConfig()
        results_list = []
        
        print(f"\n{'-'*95}")
        print(f"{'Symbol':<15} | {'Return':>8} | {'Trades':>6} | {'Win%':>6} | {'PF':>6} | {'AvgTrade':>8} | {'WinDays':>8}")
        print(f"{'-'*95}")
        
        for symbol in symbols:
            try:
                # Download data
                df = download_intraday_data(symbol, period='60d', interval=interval)
                
                if df is None or len(df) < 200:
                    print(f"{symbol:<15} | Skip - insufficient data")
                    continue
                
                # Run backtest
                result = backtest_intraday_macd(df, config)
                result['symbol'] = symbol
                result['interval'] = interval
                results_list.append(result)
                
                # Print results
                status = "OK" if result['total_return'] > 0 else "--"
                pf = min(result['profit_factor'], 99.9) if result['profit_factor'] != float('inf') else 99.9
                win_days = f"{result.get('winning_days', 0)}/{result.get('winning_days', 0) + result.get('losing_days', 0)}"
                
                print(f"{symbol:<15} | {result['total_return']:>7.2f}% | {result['total_trades']:>6} | "
                      f"{result['win_rate']:>5.1%} | {pf:>6.2f} | {result['avg_trade']:>7.2f}% | "
                      f"{win_days:>8} {status}")
                
            except Exception as e:
                print(f"{symbol:<15} | Error: {str(e)[:40]}")
        
        all_results[interval] = results_list
        
        # Summary for this interval
        if results_list:
            print(f"\n{'-'*95}")
            print(f"SUMMARY ({interval.upper()})")
            print(f"{'-'*95}")
            
            returns = [r['total_return'] for r in results_list]
            win_rates = [r['win_rate'] for r in results_list]
            total_trades = sum(r['total_trades'] for r in results_list)
            profitable = sum(1 for r in results_list if r['total_return'] > 0)
            
            print(f"Stocks Tested:     {len(results_list)}")
            print(f"Profitable:        {profitable}/{len(results_list)} ({profitable/len(results_list):.1%})")
            print(f"Average Return:    {np.mean(returns):.2f}%")
            print(f"Median Return:     {np.median(returns):.2f}%")
            print(f"Total Trades:      {total_trades}")
            print(f"Avg Win Rate:      {np.mean(win_rates):.1%}")
            
            # Exit analysis
            all_exits = {}
            for r in results_list:
                for reason, count in r.get('exits', {}).items():
                    all_exits[reason] = all_exits.get(reason, 0) + count
            
            print(f"\nExit Breakdown:")
            for reason, count in sorted(all_exits.items(), key=lambda x: -x[1]):
                pct = count / total_trades * 100 if total_trades > 0 else 0
                print(f"  {reason:<15}: {count:>4} ({pct:>5.1f}%)")
    
    # Final comparison
    print("\n" + "=" * 95)
    print("INTRADAY vs SWING COMPARISON")
    print("=" * 95)
    
    print("""
Based on backtests:

| Metric              | Intraday (15m) | Intraday (5m) | Swing (Daily) |
|---------------------|----------------|---------------|---------------|
| Avg Return          | See above      | See above     | +0.39%        |
| Profitable Stocks   | See above      | See above     | 53%           |
| Avg Win Rate        | See above      | See above     | 37.9%         |
| Trades per Stock    | See above      | See above     | 3 trades      |

KEY OBSERVATIONS:

1. INTRADAY CHALLENGES:
   - More noise = more false signals
   - Higher transaction costs (more trades)
   - EOD exit forces closing profitable runners
   - Gaps between days can't be captured
   
2. WHAT MIGHT WORK:
   - Focus on first 2 hours (highest volatility)
   - Only trade with trend (trend MACD above zero for longs)
   - Require divergence + histogram confirmation
   - Tight stops (1.5 ATR) with quick profit taking

3. REALISTIC EXPECTATIONS:
   - Intraday trading is HARDER than swing trading
   - Transaction costs eat into profits
   - Need high accuracy (>50%) with tight R:R
   - Most retail traders lose money intraday
""")
    
    return all_results


# =============================================================================
# INTRADAY SIGNAL SCANNER
# =============================================================================

def scan_intraday_signals(symbols: List[str] = None, interval: str = '15m'):
    """Scan for current intraday signals"""
    
    if symbols is None:
        symbols = [
            'RELIANCE.NS', 'TCS.NS', 'HDFCBANK.NS', 'INFY.NS', 'SBIN.NS',
            'BHARTIARTL.NS', 'ITC.NS', 'AXISBANK.NS', 'LT.NS', 'ICICIBANK.NS'
        ]
    
    print("=" * 80)
    print(f"INTRADAY MACD SIGNAL SCANNER ({interval.upper()})")
    print("=" * 80)
    print(f"Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"Scanning {len(symbols)} stocks...")
    print()
    
    config = IntradayMACDConfig()
    strategy = IntradayMACD3System(config)
    signals = []
    
    for symbol in symbols:
        try:
            df = download_intraday_data(symbol, period='5d', interval=interval)
            if df is None:
                continue
            
            df = strategy.generate_signals(df)
            
            # Check last bar for signal
            last = df.iloc[-1]
            if last['Signal_Dir'] != 0 and last['Signal_Quality'] >= 2:
                atr = last['ATR']
                price = last['Close']
                
                if last['Signal_Dir'] == 1:
                    entry = price * 1.001
                    stop = entry - atr * config.atr_stop_mult
                    target = entry + (entry - stop) * config.atr_target_mult
                else:
                    entry = price * 0.999
                    stop = entry + atr * config.atr_stop_mult
                    target = entry - (stop - entry) * config.atr_target_mult
                
                signals.append({
                    'symbol': symbol,
                    'signal': 'BUY' if last['Signal_Dir'] == 1 else 'SELL',
                    'quality': int(last['Signal_Quality']),
                    'type': last['Signal_Type'],
                    'price': round(price, 2),
                    'entry': round(entry, 2),
                    'stop': round(stop, 2),
                    'target': round(target, 2),
                    'macd': round(last['MACD'], 4),
                    'trend': 'Bullish' if last['MACD_Trend'] > 0 else 'Bearish'
                })
        except Exception as e:
            continue
    
    # Print signals
    if signals:
        print(f"{'Symbol':<15} {'Signal':<6} {'Quality':<8} {'Type':<20} {'Price':>10} {'Entry':>10} {'Stop':>10} {'Target':>10}")
        print("-" * 95)
        
        for sig in sorted(signals, key=lambda x: -x['quality']):
            stars = '*' * sig['quality']
            print(f"{sig['symbol']:<15} {sig['signal']:<6} {stars:<8} {sig['type']:<20} "
                  f"{sig['price']:>10.2f} {sig['entry']:>10.2f} {sig['stop']:>10.2f} {sig['target']:>10.2f}")
    else:
        print("No signals found matching criteria.")
    
    print()
    return signals


# =============================================================================
# MAIN
# =============================================================================

if __name__ == "__main__":
    import sys
    
    if len(sys.argv) > 1 and sys.argv[1] == '--scan':
        # Run scanner mode
        signals = scan_intraday_signals()
    else:
        # Run backtest mode
        results = run_intraday_backtest()
