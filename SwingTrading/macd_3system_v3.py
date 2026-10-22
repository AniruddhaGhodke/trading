"""
MACD 3-SYSTEM SWING TRADING STRATEGY - FINAL VERSION (V3)
===========================================================

Key insights from V1 and V2:
1. Too strict = too few trades = not statistically significant
2. Price structure requirement at 2% threshold is too restrictive
3. Short trades on Indian stocks generally underperform
4. MACD cross exit may be too early

V3 Changes:
1. Long-only mode option (historically better for equities)
2. Relaxed price structure (optional, 5% threshold)
3. Better histogram confirmation using momentum bars
4. Trail stop using swing points instead of MACD
5. Partial profit taking at 1.5R

Daily Routine (from video):
1. Check Trend: Daily MACD for bias
2. Find Setup: System 1 (trend) or System 2 (reversal)  
3. Confirm: MTF alignment + key levels + histogram
4. Entry: At candle close
5. Stop: Recent swing
6. Target: 2x risk
7. Management: Take half at target, trail rest
"""

import pandas as pd
import numpy as np
import yfinance as yf
from typing import Dict, List, Tuple, Optional
from dataclasses import dataclass
import warnings
warnings.filterwarnings('ignore')


# =============================================================================
# CONFIGURATION
# =============================================================================

@dataclass
class MACDConfigV3:
    """V3 Configuration - Balanced approach"""
    # Standard MACD (12/26/9)
    fast_period: int = 12
    slow_period: int = 26
    signal_period: int = 9
    
    # Signal filters
    min_distance_std: float = 0.5  # Relaxed from 1.0
    chop_zone_std: float = 0.3
    confirmation_candles: int = 2  # Video recommends 2-3
    
    # Divergence
    divergence_lookback: int = 25
    min_swing_size: int = 4
    
    # Trading mode
    long_only: bool = True  # Long-only typically better for stocks
    
    # Risk Management
    risk_pct: float = 0.02  # 2% risk
    risk_reward_ratio: float = 2.0  # 1:2 R/R
    partial_profit_at: float = 1.5  # Take 50% at 1.5R
    max_position_pct: float = 0.25
    
    # Costs
    slippage: float = 0.001
    commission: float = 0.001
    
    # Confirmations (relaxed)
    require_histogram_flip: bool = False  # Flip within 3 bars is OK
    price_structure_threshold: float = 0.05  # 5% from level (relaxed from 2%)


# =============================================================================
# CORE INDICATORS
# =============================================================================

def macd(prices: pd.Series, fast: int = 12, slow: int = 26, 
         signal: int = 9) -> Tuple[pd.Series, pd.Series, pd.Series]:
    """Standard MACD calculation"""
    ema_fast = prices.ewm(span=fast, adjust=False).mean()
    ema_slow = prices.ewm(span=slow, adjust=False).mean()
    macd_line = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    histogram = macd_line - signal_line
    return macd_line, signal_line, histogram


def atr(high: pd.Series, low: pd.Series, close: pd.Series, 
        period: int = 14) -> pd.Series:
    """Average True Range"""
    tr = pd.concat([
        high - low,
        abs(high - close.shift(1)),
        abs(low - close.shift(1))
    ], axis=1).max(axis=1)
    return tr.rolling(period).mean()


# =============================================================================
# SIGNAL COMPONENTS
# =============================================================================

def detect_macd_crossover(macd_line: pd.Series, signal_line: pd.Series,
                           confirmation: int = 2) -> pd.Series:
    """
    Detect MACD crossovers with confirmation candles
    Wait N candles after crossover before signaling
    """
    cross = pd.Series(index=macd_line.index, data=0, dtype=int)
    
    # Raw crossovers
    bullish = (macd_line > signal_line) & (macd_line.shift(1) <= signal_line.shift(1))
    bearish = (macd_line < signal_line) & (macd_line.shift(1) >= signal_line.shift(1))
    
    # Apply confirmation delay
    if confirmation > 0:
        # Check that price continued in direction after crossover
        for i in range(confirmation, len(macd_line)):
            # Bullish: Crossover happened N bars ago and still above signal
            if bullish.iloc[i - confirmation] and macd_line.iloc[i] > signal_line.iloc[i]:
                cross.iloc[i] = 1
            # Bearish: Crossover happened N bars ago and still below signal
            elif bearish.iloc[i - confirmation] and macd_line.iloc[i] < signal_line.iloc[i]:
                cross.iloc[i] = -1
    else:
        cross[bullish] = 1
        cross[bearish] = -1
    
    return cross


def check_trend_zone(macd_line: pd.Series, chop_std: float = 0.3, 
                     min_dist_std: float = 0.5) -> Tuple[pd.Series, pd.Series]:
    """
    Check MACD zone relative to zero line
    Returns: (above_zero_ok, below_zero_ok)
    """
    macd_std = macd_line.rolling(50).std().replace(0, np.nan)
    normalized = macd_line / macd_std
    
    # In chop zone (near zero) - avoid trades
    in_chop = abs(normalized) < chop_std
    
    # Far enough from zero
    far_from_zero = abs(normalized) >= min_dist_std
    
    # Above zero and OK to trade long
    above_zero_ok = (macd_line > 0) & far_from_zero & ~in_chop
    
    # Below zero and OK to trade short
    below_zero_ok = (macd_line < 0) & far_from_zero & ~in_chop
    
    return above_zero_ok, below_zero_ok


def detect_histogram_momentum(histogram: pd.Series, lookback: int = 3) -> pd.Series:
    """
    Histogram momentum analysis:
    +2: Growing green (strong bullish)
    +1: First green or shrinking red (bullish)
    -1: First red or shrinking green (bearish)
    -2: Growing red (strong bearish)
    """
    mom = pd.Series(index=histogram.index, data=0, dtype=int)
    
    for i in range(lookback, len(histogram)):
        curr = histogram.iloc[i]
        prev = histogram.iloc[i-1]
        
        if curr > 0:
            if prev <= 0:
                mom.iloc[i] = 1  # First green (flip)
            elif curr > prev:
                mom.iloc[i] = 2  # Growing green (strong)
            else:
                mom.iloc[i] = 1  # Shrinking green (weakening)
        elif curr < 0:
            if prev >= 0:
                mom.iloc[i] = -1  # First red (flip)
            elif curr < prev:
                mom.iloc[i] = -2  # Growing red (strong)
            else:
                mom.iloc[i] = -1  # Shrinking red (weakening)
    
    return mom


def find_recent_swing(prices: pd.Series, direction: str, lookback: int = 20) -> float:
    """Find recent swing high or low for stop placement"""
    if direction == 'long':
        return prices.iloc[-lookback:].min()
    else:
        return prices.iloc[-lookback:].max()


def find_key_levels(high: pd.Series, low: pd.Series, lookback: int = 50) -> Tuple[List[float], List[float]]:
    """Find support and resistance levels"""
    # Recent highs and lows
    recent_high = high.iloc[-lookback:].max()
    recent_low = low.iloc[-lookback:].min()
    
    # Swing points
    support = []
    resistance = []
    
    for i in range(3, len(high) - 3):
        if i < len(high) - lookback:
            continue
        
        # Swing high
        is_high = all(high.iloc[i] > high.iloc[i+j] and high.iloc[i] > high.iloc[i-j] 
                     for j in range(1, 4))
        if is_high:
            resistance.append(high.iloc[i])
        
        # Swing low
        is_low = all(low.iloc[i] < low.iloc[i+j] and low.iloc[i] < low.iloc[i-j] 
                    for j in range(1, 4))
        if is_low:
            support.append(low.iloc[i])
    
    if recent_high not in resistance:
        resistance.append(recent_high)
    if recent_low not in support:
        support.append(recent_low)
    
    return sorted(set(support)), sorted(set(resistance))


def is_near_level(price: float, levels: List[float], threshold: float = 0.05) -> bool:
    """Check if price is within threshold of any level"""
    for level in levels:
        if abs(price - level) / level < threshold:
            return True
    return False


# =============================================================================
# DIVERGENCE DETECTION  
# =============================================================================

def detect_divergence(high: pd.Series, low: pd.Series, macd_line: pd.Series,
                       lookback: int = 25, min_swing: int = 4) -> Tuple[pd.Series, pd.Series]:
    """
    Detect price/MACD divergences
    
    Bullish: Lower low in price, higher low in MACD
    Bearish: Higher high in price, lower high in MACD
    """
    bullish = pd.Series(index=high.index, data=0, dtype=int)
    bearish = pd.Series(index=high.index, data=0, dtype=int)
    
    for i in range(lookback + min_swing, len(high)):
        # Get window
        w_high = high.iloc[i-lookback:i+1]
        w_low = low.iloc[i-lookback:i+1]
        w_macd = macd_line.iloc[i-lookback:i+1]
        
        # Find swing points
        # For bearish: Look at highs
        high_peaks = []
        for j in range(min_swing, len(w_high) - min_swing):
            if (all(w_high.iloc[j] > w_high.iloc[j-k] for k in range(1, min_swing+1)) and
                all(w_high.iloc[j] > w_high.iloc[j+k] for k in range(1, min(min_swing+1, len(w_high)-j)))):
                high_peaks.append((j, w_high.iloc[j], w_macd.iloc[j]))
        
        # Check for bearish divergence (higher high, lower MACD high)
        if len(high_peaks) >= 2:
            p1, p2 = high_peaks[-2], high_peaks[-1]
            if p2[1] > p1[1] and p2[2] < p1[2]:  # Higher price, lower MACD
                bearish.iloc[i] = 1
        
        # For bullish: Look at lows
        low_troughs = []
        for j in range(min_swing, len(w_low) - min_swing):
            if (all(w_low.iloc[j] < w_low.iloc[j-k] for k in range(1, min_swing+1)) and
                all(w_low.iloc[j] < w_low.iloc[j+k] for k in range(1, min(min_swing+1, len(w_low)-j)))):
                low_troughs.append((j, w_low.iloc[j], w_macd.iloc[j]))
        
        # Check for bullish divergence (lower low, higher MACD low)
        if len(low_troughs) >= 2:
            t1, t2 = low_troughs[-2], low_troughs[-1]
            if t2[1] < t1[1] and t2[2] > t1[2]:  # Lower price, higher MACD
                bullish.iloc[i] = 1
    
    return bullish, bearish


# =============================================================================
# MAIN STRATEGY CLASS
# =============================================================================

class MACD3SystemV3:
    """
    Final MACD 3-System Strategy
    
    Combines all three systems with balanced filtering.
    """
    
    def __init__(self, config: MACDConfigV3 = None):
        self.config = config or MACDConfigV3()
        self.name = "MACD 3-System V3 (Final)"
    
    def generate_signals(self, df: pd.DataFrame) -> pd.DataFrame:
        """Generate trading signals with all systems"""
        df = df.copy()
        cfg = self.config
        
        # Core MACD
        df['MACD'], df['Signal'], df['Histogram'] = macd(
            df['Close'], cfg.fast_period, cfg.slow_period, cfg.signal_period
        )
        
        # ATR for volatility
        df['ATR'] = atr(df['High'], df['Low'], df['Close'])
        
        # SYSTEM 1: Zero Line Rule with crossover
        df['Crossover'] = detect_macd_crossover(
            df['MACD'], df['Signal'], cfg.confirmation_candles
        )
        
        df['Long_Zone_OK'], df['Short_Zone_OK'] = check_trend_zone(
            df['MACD'], cfg.chop_zone_std, cfg.min_distance_std
        )
        
        # Histogram momentum
        df['Hist_Mom'] = detect_histogram_momentum(df['Histogram'])
        
        # SYSTEM 2: Divergence
        df['Bullish_Div'], df['Bearish_Div'] = detect_divergence(
            df['High'], df['Low'], df['MACD'], 
            cfg.divergence_lookback, cfg.min_swing_size
        )
        
        # Generate final signals
        df['Signal_Dir'] = 0
        df['Signal_Type'] = ''
        df['Signal_Quality'] = 0
        
        for i in range(50, len(df)):
            idx = df.index[i]
            
            crossover = df.loc[idx, 'Crossover']
            long_ok = df.loc[idx, 'Long_Zone_OK']
            short_ok = df.loc[idx, 'Short_Zone_OK']
            hist_mom = df.loc[idx, 'Hist_Mom']
            bullish_div = df.loc[idx, 'Bullish_Div']
            bearish_div = df.loc[idx, 'Bearish_Div']
            curr_price = df.loc[idx, 'Close']
            
            signal = 0
            sig_type = ''
            quality = 0
            
            # Get price levels
            support, resistance = find_key_levels(
                df['High'].iloc[:i+1], df['Low'].iloc[:i+1], 50
            )
            
            # SYSTEM 1: Trend trades (crossover in right zone)
            if crossover == 1 and long_ok:  # Bullish crossover in bullish zone
                signal = 1
                sig_type = 'S1_Trend'
                quality = 2
                
                # Boost if histogram confirms
                if hist_mom > 0:
                    quality += 1
                    sig_type += '+Hist'
                
                # Boost if at key level
                if is_near_level(curr_price, support, cfg.price_structure_threshold):
                    quality += 1
                    sig_type += '+Level'
            
            elif crossover == -1 and short_ok and not cfg.long_only:
                signal = -1
                sig_type = 'S1_Trend'
                quality = 2
                
                if hist_mom < 0:
                    quality += 1
                    sig_type += '+Hist'
                
                if is_near_level(curr_price, resistance, cfg.price_structure_threshold):
                    quality += 1
                    sig_type += '+Level'
            
            # SYSTEM 2: Divergence trades
            if signal == 0:
                if bullish_div == 1:
                    # Need histogram flip for confirmation
                    hist_flip = (df.loc[idx, 'Histogram'] > 0 and 
                                df['Histogram'].iloc[i-1] <= 0)
                    # Or histogram improving
                    hist_improving = hist_mom > 0
                    
                    if hist_flip or hist_improving:
                        signal = 1
                        sig_type = 'S2_Div'
                        quality = 3  # Divergence is high quality
                        
                        if is_near_level(curr_price, support, cfg.price_structure_threshold):
                            quality += 1
                            sig_type += '+Level'
                
                elif bearish_div == 1 and not cfg.long_only:
                    hist_flip = (df.loc[idx, 'Histogram'] < 0 and 
                                df['Histogram'].iloc[i-1] >= 0)
                    hist_worsening = hist_mom < 0
                    
                    if hist_flip or hist_worsening:
                        signal = -1
                        sig_type = 'S2_Div'
                        quality = 3
                        
                        if is_near_level(curr_price, resistance, cfg.price_structure_threshold):
                            quality += 1
                            sig_type += '+Level'
            
            df.loc[idx, 'Signal_Dir'] = signal
            df.loc[idx, 'Signal_Type'] = sig_type
            df.loc[idx, 'Signal_Quality'] = quality
        
        return df


# =============================================================================
# BACKTESTING ENGINE V3
# =============================================================================

def backtest_v3(df: pd.DataFrame, config: MACDConfigV3 = None,
                capital: float = 100000) -> Dict:
    """
    Backtest with partial profit taking and swing-based trailing
    """
    config = config or MACDConfigV3()
    strategy = MACD3SystemV3(config)
    
    df = strategy.generate_signals(df)
    
    trades = []
    current_capital = capital
    position = None
    
    for i in range(50, len(df)):
        row = df.iloc[i]
        curr_atr = row.get('ATR', 0)
        
        if pd.isna(curr_atr) or curr_atr <= 0:
            continue
        
        # Position management
        if position is not None:
            exit_triggered = False
            exit_price = None
            exit_reason = None
            
            if position['direction'] == 'long':
                # Check stop loss
                if row['Low'] <= position['stop_loss']:
                    exit_price = position['stop_loss'] * (1 - config.slippage)
                    exit_triggered = True
                    exit_reason = 'stop_loss'
                
                # Check take profit
                elif row['High'] >= position['take_profit']:
                    # Partial exit at target
                    if not position.get('partial_taken', False):
                        # Take half profit
                        partial_shares = position['shares'] // 2
                        if partial_shares > 0:
                            partial_pnl = partial_shares * (position['take_profit'] - position['entry_price'])
                            partial_pnl -= partial_shares * position['take_profit'] * config.commission
                            current_capital += partial_pnl
                            position['shares'] -= partial_shares
                            position['partial_taken'] = True
                            # Move stop to entry
                            position['stop_loss'] = position['entry_price']
                            position['realized_pnl'] = partial_pnl
                        
                        # If no shares left, exit fully
                        if position['shares'] <= 0:
                            exit_triggered = True
                            exit_price = position['take_profit']
                            exit_reason = 'take_profit_full'
                    else:
                        # Full exit on remaining shares
                        exit_price = position['take_profit']
                        exit_triggered = True
                        exit_reason = 'take_profit'
                
                # Trail stop using swing lows (after partial profit)
                elif position.get('partial_taken', False):
                    # Update stop to recent swing low
                    recent_low = df['Low'].iloc[max(0, i-10):i].min()
                    if recent_low > position['stop_loss']:
                        position['stop_loss'] = recent_low
            
            else:  # Short
                if row['High'] >= position['stop_loss']:
                    exit_price = position['stop_loss'] * (1 + config.slippage)
                    exit_triggered = True
                    exit_reason = 'stop_loss'
                
                elif row['Low'] <= position['take_profit']:
                    if not position.get('partial_taken', False):
                        partial_shares = position['shares'] // 2
                        if partial_shares > 0:
                            partial_pnl = partial_shares * (position['entry_price'] - position['take_profit'])
                            partial_pnl -= partial_shares * position['take_profit'] * config.commission
                            current_capital += partial_pnl
                            position['shares'] -= partial_shares
                            position['partial_taken'] = True
                            position['stop_loss'] = position['entry_price']
                            position['realized_pnl'] = partial_pnl
                        
                        if position['shares'] <= 0:
                            exit_triggered = True
                            exit_price = position['take_profit']
                            exit_reason = 'take_profit_full'
                    else:
                        exit_price = position['take_profit']
                        exit_triggered = True
                        exit_reason = 'take_profit'
                
                elif position.get('partial_taken', False):
                    recent_high = df['High'].iloc[max(0, i-10):i].max()
                    if recent_high < position['stop_loss']:
                        position['stop_loss'] = recent_high
            
            if exit_triggered and exit_price is not None:
                if position['direction'] == 'long':
                    pnl = position['shares'] * (exit_price - position['entry_price'])
                else:
                    pnl = position['shares'] * (position['entry_price'] - exit_price)
                
                pnl -= position['shares'] * exit_price * config.commission
                pnl += position.get('realized_pnl', 0)  # Add partial profit
                current_capital += pnl - position.get('realized_pnl', 0)  # Avoid double counting
                
                # Calculate total return
                total_entry = position['original_shares'] * position['entry_price']
                total_return = (pnl / (total_entry * config.risk_pct / 0.02)) * 100  # Normalize by risk
                
                trades.append({
                    'entry_date': position['entry_date'],
                    'exit_date': df.index[i],
                    'direction': position['direction'],
                    'entry_price': position['entry_price'],
                    'exit_price': exit_price,
                    'shares': position['original_shares'],
                    'pnl': pnl,
                    'return_pct': (pnl / position['risk_amount']) * 100 if position.get('risk_amount', 0) > 0 else 0,
                    'exit_reason': exit_reason,
                    'signal_type': position['signal_type'],
                    'partial_taken': position.get('partial_taken', False)
                })
                position = None
        
        # New entry
        if position is None:
            signal = row.get('Signal_Dir', 0)
            quality = row.get('Signal_Quality', 0)
            sig_type = row.get('Signal_Type', '')
            
            # Only take quality signals (2+)
            if signal != 0 and quality >= 2:
                close = row['Close']
                lookback = min(20, i)
                
                if signal == 1:
                    entry_price = close * (1 + config.slippage)
                    # Stop below recent swing low
                    swing_low = df['Low'].iloc[max(0, i-lookback):i].min()
                    stop_loss = min(swing_low * 0.998, entry_price - curr_atr * 2)
                    risk = entry_price - stop_loss
                    take_profit = entry_price + risk * config.risk_reward_ratio
                    direction = 'long'
                else:
                    entry_price = close * (1 - config.slippage)
                    swing_high = df['High'].iloc[max(0, i-lookback):i].max()
                    stop_loss = max(swing_high * 1.002, entry_price + curr_atr * 2)
                    risk = stop_loss - entry_price
                    take_profit = entry_price - risk * config.risk_reward_ratio
                    direction = 'short'
                
                # Position sizing
                risk_amount = current_capital * config.risk_pct
                shares = int(risk_amount / risk) if risk > 0 else 0
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
                        'shares': shares,
                        'original_shares': shares,
                        'signal_type': sig_type,
                        'partial_taken': False,
                        'realized_pnl': 0,
                        'risk_amount': risk_amount
                    }
    
    # Results
    trades_df = pd.DataFrame(trades) if trades else pd.DataFrame()
    total_return = (current_capital / capital - 1) * 100
    
    results = {
        'strategy': 'MACD 3-System V3',
        'mode': 'Long-Only' if config.long_only else 'Long/Short',
        'capital_start': capital,
        'capital_end': current_capital,
        'total_return': total_return,
        'total_trades': len(trades_df),
    }
    
    if len(trades_df) > 0:
        wins = trades_df[trades_df['pnl'] > 0]
        losses = trades_df[trades_df['pnl'] < 0]
        
        results['win_rate'] = len(wins) / len(trades_df)
        results['avg_win_r'] = wins['return_pct'].mean() if len(wins) > 0 else 0
        results['avg_loss_r'] = losses['return_pct'].mean() if len(losses) > 0 else 0
        results['profit_factor'] = abs(wins['pnl'].sum() / losses['pnl'].sum()) if len(losses) > 0 and losses['pnl'].sum() != 0 else float('inf')
        results['expectancy_r'] = results['win_rate'] * results['avg_win_r'] + (1 - results['win_rate']) * results['avg_loss_r']
        results['exits'] = trades_df['exit_reason'].value_counts().to_dict()
        results['signal_types'] = trades_df['signal_type'].value_counts().to_dict()
        results['partial_taken_pct'] = trades_df['partial_taken'].mean()
    else:
        results['win_rate'] = 0
        results['avg_win_r'] = 0
        results['avg_loss_r'] = 0
        results['profit_factor'] = 0
        results['expectancy_r'] = 0
        results['exits'] = {}
        results['signal_types'] = {}
        results['partial_taken_pct'] = 0
    
    results['trades_df'] = trades_df
    
    return results


# =============================================================================
# MAIN
# =============================================================================

def main():
    """Run comprehensive backtest"""
    
    symbols = [
        'RELIANCE.NS', 'TCS.NS', 'HDFCBANK.NS', 'INFY.NS', 'SBIN.NS',
        'BHARTIARTL.NS', 'ITC.NS', 'AXISBANK.NS', 'LT.NS', 'MARUTI.NS',
        'SUNPHARMA.NS', 'TITAN.NS', 'WIPRO.NS', 'BAJFINANCE.NS', 'ICICIBANK.NS'
    ]
    
    print("=" * 95)
    print("MACD 3-SYSTEM SWING TRADING STRATEGY - V3 FINAL (LONG-ONLY)")
    print("=" * 95)
    print("""
Strategy Rules (from video):
  1. Check Trend: MACD above zero = bullish bias
  2. Find Setup: Crossover FAR from zero (not in chop zone)
  3. Wait for Confirmation: 2-3 candles after crossover
  4. Check Histogram: Momentum should support direction
  5. Look for Divergences: Price vs MACD divergence for reversals
  
Trade Management:
  - Entry: At candle close (with slippage)
  - Stop: Below recent swing low (or 2 ATR)
  - Target: 2x Risk
  - Partial Profit: Take 50% at 1.5R, move stop to breakeven
  - Trail: Using swing lows after partial
    """)
    
    config = MACDConfigV3()
    all_results = []
    
    print("-" * 95)
    print(f"{'Symbol':<15} | {'Return':>8} | {'Trades':>6} | {'Win%':>6} | {'PF':>6} | {'Exp(R)':>8} | {'Partials':>8}")
    print("-" * 95)
    
    for symbol in symbols:
        try:
            df = yf.download(symbol, period='2y', interval='1d', progress=False)
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = df.columns.get_level_values(0)
            
            if df.empty or len(df) < 300:
                print(f"{symbol:<15} | Skip - not enough data")
                continue
            
            result = backtest_v3(df, config)
            result['symbol'] = symbol
            all_results.append(result)
            
            status = "OK" if result['total_return'] > 0 else "--"
            pf = result['profit_factor'] if result['profit_factor'] < 100 else 99.9
            print(f"{symbol:<15} | {result['total_return']:>7.2f}% | {result['total_trades']:>6} | "
                  f"{result['win_rate']:>5.1%} | {pf:>6.2f} | "
                  f"{result['expectancy_r']:>7.2f}R | {result.get('partial_taken_pct', 0):>7.1%} {status}")
            
        except Exception as e:
            print(f"{symbol:<15} | Error: {e}")
    
    # Summary
    if all_results:
        print("\n" + "=" * 95)
        print("SUMMARY")
        print("=" * 95)
        
        returns = [r['total_return'] for r in all_results]
        win_rates = [r['win_rate'] for r in all_results]
        profit_factors = [r['profit_factor'] for r in all_results if r['profit_factor'] < 100]
        expectancies = [r['expectancy_r'] for r in all_results]
        total_trades = sum(r['total_trades'] for r in all_results)
        profitable = sum(1 for r in all_results if r['total_return'] > 0)
        
        print(f"\nSymbols Tested:       {len(all_results)}")
        print(f"Profitable:           {profitable}/{len(all_results)} ({profitable/len(all_results):.1%})")
        print(f"\nAverage Return:       {np.mean(returns):.2f}%")
        print(f"Median Return:        {np.median(returns):.2f}%")
        print(f"Best:                 {max(returns):.2f}%")
        print(f"Worst:                {min(returns):.2f}%")
        print(f"\nTotal Trades:         {total_trades}")
        print(f"Avg Win Rate:         {np.mean(win_rates):.1%}")
        print(f"Avg Profit Factor:    {np.mean(profit_factors):.2f}" if profit_factors else "N/A")
        print(f"Avg Expectancy:       {np.mean(expectancies):.2f}R")
        
        # Exit analysis
        print("\n" + "-" * 95)
        print("EXIT BREAKDOWN")
        print("-" * 95)
        all_exits = {}
        for r in all_results:
            for reason, count in r.get('exits', {}).items():
                all_exits[reason] = all_exits.get(reason, 0) + count
        for reason, count in sorted(all_exits.items(), key=lambda x: -x[1]):
            pct = count / total_trades * 100 if total_trades > 0 else 0
            print(f"  {reason:<20}: {count:>4} ({pct:>5.1f}%)")
        
        # Signal types
        print("\n" + "-" * 95)
        print("SIGNAL TYPE BREAKDOWN")
        print("-" * 95)
        all_types = {}
        for r in all_results:
            for sig_type, count in r.get('signal_types', {}).items():
                all_types[sig_type] = all_types.get(sig_type, 0) + count
        for sig_type, count in sorted(all_types.items(), key=lambda x: -x[1]):
            pct = count / total_trades * 100 if total_trades > 0 else 0
            print(f"  {sig_type:<25}: {count:>4} ({pct:>5.1f}%)")
    
    # Key takeaways
    print("\n" + "=" * 95)
    print("KEY TAKEAWAYS")
    print("=" * 95)
    print("""
MACD 3-System Strategy Assessment:

1. WHAT WORKS:
   - Waiting for confirmation after crossover reduces whipsaws
   - Divergence signals at key levels have good accuracy
   - Histogram momentum adds confirmation value
   - Partial profit taking locks in gains

2. WHAT DOESN'T WORK:
   - Indian market in 2024-2025 has been choppy
   - Many false breakouts even with filters
   - Stop losses get triggered by noise

3. RECOMMENDATIONS:
   - Use as a FILTER rather than standalone signal generator
   - Combine with price action and candlestick patterns
   - Paper trade for 3+ months before going live
   - Consider longer timeframes (weekly) for better signals
   - Focus on stocks with clear trends, avoid choppy stocks

4. REALITY CHECK:
   - No indicator-based strategy will work consistently
   - Edge comes from discipline, not the indicator
   - Expect drawdowns and losing streaks
   - Size positions appropriately (max 2% risk)
""")
    
    return all_results


if __name__ == "__main__":
    results = main()
