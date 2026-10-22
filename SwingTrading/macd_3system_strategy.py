"""
MACD 3-SYSTEM SWING TRADING STRATEGY
=====================================

Based on professional MACD trading methodology combining three systems:

SYSTEM 1: Zero Line Rule (Trend Trading)
  - MACD above zero = only buy trades
  - MACD below zero = only sell trades
  - Distance rule: Only take crossovers far from zero line
  - Avoid chop zone near zero line

SYSTEM 2: Divergence Detection (Reversal Trading)
  - Bearish Divergence: Price higher high, MACD lower high
  - Bullish Divergence: Price lower low, MACD higher low
  - Confirmed with histogram signals

SYSTEM 3: Multi-Timeframe Analysis
  - Daily: Overall trend bias
  - 4-Hour: Setup identification
  - 1-Hour: Entry trigger
  - All timeframes must align

HISTOGRAM CONFIRMATION:
  - The Flip: First bar of opposite color (momentum shift)
  - Shrinking Tower: Bars getting smaller (losing momentum)
  - Zero Bounce: Histogram bounces off zero (trend continuation)

RISK MANAGEMENT:
  - Entry: At candle close (never mid-candle)
  - Stop Loss: At recent swing high/low
  - Target: 2x risk (Risk:Reward = 1:2)
  - Take half profit at target, trail rest with MACD cross
"""

import pandas as pd
import numpy as np
import yfinance as yf
from typing import Dict, List, Tuple, Optional
from dataclasses import dataclass
from datetime import datetime, timedelta
import warnings
warnings.filterwarnings('ignore')


# =============================================================================
# CONFIGURATION
# =============================================================================

@dataclass
class MACDConfig:
    """MACD Strategy Configuration"""
    # MACD Parameters
    fast_period: int = 12
    slow_period: int = 26
    signal_period: int = 9
    
    # System 1: Zero Line Rule
    min_distance_from_zero: float = 0.3  # Minimum MACD distance from zero
    chop_zone: float = 0.2  # Avoid trades in this zone around zero
    confirmation_candles: int = 2  # Wait N candles after crossover
    
    # System 2: Divergence Detection
    divergence_lookback: int = 20  # Bars to look back for divergences
    min_swing_size: int = 5  # Minimum bars between swing points
    
    # System 3: Multi-Timeframe
    use_mtf: bool = True  # Enable multi-timeframe analysis
    
    # Risk Management
    risk_pct: float = 0.02  # 2% risk per trade
    risk_reward_ratio: float = 2.0  # 1:2 Risk:Reward
    max_position_pct: float = 0.25  # Max 25% of capital per position
    
    # Transaction Costs
    slippage: float = 0.001  # 0.1%
    commission: float = 0.001  # 0.1%


# =============================================================================
# INDICATORS
# =============================================================================

def calculate_macd(prices: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9) -> Tuple[pd.Series, pd.Series, pd.Series]:
    """
    Calculate MACD, Signal Line, and Histogram
    
    Returns:
        macd_line: MACD Line (fast EMA - slow EMA)
        signal_line: Signal Line (EMA of MACD)
        histogram: MACD - Signal
    """
    ema_fast = prices.ewm(span=fast, adjust=False).mean()
    ema_slow = prices.ewm(span=slow, adjust=False).mean()
    
    macd_line = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    histogram = macd_line - signal_line
    
    return macd_line, signal_line, histogram


def calculate_atr(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
    """Calculate Average True Range"""
    tr = pd.concat([
        high - low,
        abs(high - close.shift(1)),
        abs(low - close.shift(1))
    ], axis=1).max(axis=1)
    return tr.rolling(period).mean()


def calculate_ema(prices: pd.Series, period: int) -> pd.Series:
    """Calculate Exponential Moving Average"""
    return prices.ewm(span=period, adjust=False).mean()


def find_swing_highs(high: pd.Series, lookback: int = 5) -> pd.Series:
    """Find swing highs (local maxima)"""
    swing_highs = pd.Series(index=high.index, dtype=float)
    
    for i in range(lookback, len(high) - lookback):
        is_high = True
        for j in range(1, lookback + 1):
            if high.iloc[i] <= high.iloc[i-j] or high.iloc[i] <= high.iloc[i+j]:
                is_high = False
                break
        if is_high:
            swing_highs.iloc[i] = high.iloc[i]
    
    return swing_highs


def find_swing_lows(low: pd.Series, lookback: int = 5) -> pd.Series:
    """Find swing lows (local minima)"""
    swing_lows = pd.Series(index=low.index, dtype=float)
    
    for i in range(lookback, len(low) - lookback):
        is_low = True
        for j in range(1, lookback + 1):
            if low.iloc[i] >= low.iloc[i-j] or low.iloc[i] >= low.iloc[i+j]:
                is_low = False
                break
        if is_low:
            swing_lows.iloc[i] = low.iloc[i]
    
    return swing_lows


def find_support_resistance(df: pd.DataFrame, lookback: int = 50, num_levels: int = 5) -> Tuple[List[float], List[float]]:
    """Find key support and resistance levels"""
    high = df['High'].iloc[-lookback:]
    low = df['Low'].iloc[-lookback:]
    
    # Find swing highs and lows
    swing_highs = find_swing_highs(high, 3).dropna().values
    swing_lows = find_swing_lows(low, 3).dropna().values
    
    # Cluster nearby levels
    def cluster_levels(levels, threshold_pct=0.02):
        if len(levels) == 0:
            return []
        levels = sorted(levels)
        clusters = []
        current_cluster = [levels[0]]
        
        for level in levels[1:]:
            if (level - current_cluster[0]) / current_cluster[0] < threshold_pct:
                current_cluster.append(level)
            else:
                clusters.append(np.mean(current_cluster))
                current_cluster = [level]
        clusters.append(np.mean(current_cluster))
        
        return clusters[-num_levels:] if len(clusters) > num_levels else clusters
    
    resistance_levels = cluster_levels(swing_highs)
    support_levels = cluster_levels(swing_lows)
    
    return support_levels, resistance_levels


# =============================================================================
# SYSTEM 1: ZERO LINE RULE
# =============================================================================

class System1_ZeroLineRule:
    """
    Zero Line Rule for Trend Trading
    
    Rules:
    1. MACD above zero = Only look for BUY trades
    2. MACD below zero = Only look for SELL trades
    3. Avoid chop zone near zero line
    4. Only take crossovers far from zero line
    5. Wait for confirmation candles after crossover
    """
    
    def __init__(self, config: MACDConfig):
        self.config = config
    
    def detect_crossover(self, macd: pd.Series, signal: pd.Series) -> pd.Series:
        """Detect MACD/Signal line crossovers"""
        crossover = pd.Series(index=macd.index, data=0)
        
        # Bullish crossover: MACD crosses above signal
        bullish = (macd > signal) & (macd.shift(1) <= signal.shift(1))
        crossover[bullish] = 1
        
        # Bearish crossover: MACD crosses below signal
        bearish = (macd < signal) & (macd.shift(1) >= signal.shift(1))
        crossover[bearish] = -1
        
        return crossover
    
    def check_distance_rule(self, macd: pd.Series) -> pd.Series:
        """Check if MACD is far enough from zero line"""
        # Normalize distance relative to price movement
        macd_std = macd.rolling(50).std()
        normalized_distance = abs(macd) / macd_std.replace(0, np.nan)
        return normalized_distance > 1.0  # At least 1 std away from zero
    
    def check_chop_zone(self, macd: pd.Series) -> pd.Series:
        """Check if we're in the chop zone (near zero)"""
        macd_std = macd.rolling(50).std()
        normalized_position = abs(macd) / macd_std.replace(0, np.nan)
        return normalized_position < 0.5  # Within 0.5 std of zero = chop zone
    
    def generate_signals(self, df: pd.DataFrame) -> pd.DataFrame:
        """Generate System 1 signals"""
        df = df.copy()
        
        # Calculate MACD
        df['MACD'], df['Signal'], df['Histogram'] = calculate_macd(
            df['Close'], 
            self.config.fast_period, 
            self.config.slow_period, 
            self.config.signal_period
        )
        
        # Detect crossovers
        df['Crossover'] = self.detect_crossover(df['MACD'], df['Signal'])
        
        # Apply zero line rule
        df['Above_Zero'] = df['MACD'] > 0
        df['Below_Zero'] = df['MACD'] < 0
        
        # Apply distance rule
        df['Distance_OK'] = self.check_distance_rule(df['MACD'])
        
        # Avoid chop zone
        df['In_Chop'] = self.check_chop_zone(df['MACD'])
        
        # Generate signals
        df['Signal_S1'] = 0
        
        # Bullish signal: Crossover up + MACD above zero + Distance OK + Not in chop
        bullish = (
            (df['Crossover'] == 1) & 
            df['Above_Zero'] & 
            df['Distance_OK'] & 
            ~df['In_Chop']
        )
        
        # Wait for confirmation candles
        if self.config.confirmation_candles > 0:
            bullish = bullish.shift(self.config.confirmation_candles).fillna(False)
        
        df.loc[bullish, 'Signal_S1'] = 1
        
        # Bearish signal: Crossover down + MACD below zero + Distance OK + Not in chop
        bearish = (
            (df['Crossover'] == -1) & 
            df['Below_Zero'] & 
            df['Distance_OK'] & 
            ~df['In_Chop']
        )
        
        if self.config.confirmation_candles > 0:
            bearish = bearish.shift(self.config.confirmation_candles).fillna(False)
        
        df.loc[bearish, 'Signal_S1'] = -1
        
        return df


# =============================================================================
# SYSTEM 2: DIVERGENCE DETECTION
# =============================================================================

class System2_Divergence:
    """
    Divergence Detection for Reversal Trading
    
    Bearish Divergence:
    - Price makes a HIGHER HIGH
    - MACD makes a LOWER HIGH
    - Signals upcoming bearish reversal
    
    Bullish Divergence:
    - Price makes a LOWER LOW
    - MACD makes a HIGHER LOW
    - Signals upcoming bullish reversal
    """
    
    def __init__(self, config: MACDConfig):
        self.config = config
    
    def find_divergences(self, price_high: pd.Series, price_low: pd.Series, 
                         macd: pd.Series, histogram: pd.Series) -> Tuple[pd.Series, pd.Series]:
        """
        Detect bullish and bearish divergences
        
        Returns:
            bullish_div: Series with 1 where bullish divergence detected
            bearish_div: Series with 1 where bearish divergence detected
        """
        bullish_div = pd.Series(index=price_high.index, data=0)
        bearish_div = pd.Series(index=price_high.index, data=0)
        
        lookback = self.config.divergence_lookback
        min_swing = self.config.min_swing_size
        
        for i in range(lookback + min_swing, len(price_high)):
            # Look for bearish divergence (higher high in price, lower high in MACD)
            # Find recent swing highs in price
            window_price_high = price_high.iloc[i-lookback:i]
            window_macd = macd.iloc[i-lookback:i]
            
            # Current high vs previous high in the window
            if i > min_swing:
                prev_high_idx = window_price_high.idxmax()
                prev_high_price = window_price_high.max()
                curr_high_price = price_high.iloc[i]
                
                if prev_high_idx is not np.nan and not pd.isna(prev_high_price):
                    prev_macd_at_high = macd.loc[prev_high_idx] if prev_high_idx in macd.index else np.nan
                    curr_macd = macd.iloc[i]
                    
                    # Bearish divergence
                    if (curr_high_price > prev_high_price and 
                        not pd.isna(prev_macd_at_high) and
                        curr_macd < prev_macd_at_high):
                        bearish_div.iloc[i] = 1
            
            # Look for bullish divergence (lower low in price, higher low in MACD)
            window_price_low = price_low.iloc[i-lookback:i]
            
            if i > min_swing:
                prev_low_idx = window_price_low.idxmin()
                prev_low_price = window_price_low.min()
                curr_low_price = price_low.iloc[i]
                
                if prev_low_idx is not np.nan and not pd.isna(prev_low_price):
                    prev_macd_at_low = macd.loc[prev_low_idx] if prev_low_idx in macd.index else np.nan
                    curr_macd = macd.iloc[i]
                    
                    # Bullish divergence
                    if (curr_low_price < prev_low_price and 
                        not pd.isna(prev_macd_at_low) and
                        curr_macd > prev_macd_at_low):
                        bullish_div.iloc[i] = 1
        
        return bullish_div, bearish_div
    
    def generate_signals(self, df: pd.DataFrame) -> pd.DataFrame:
        """Generate System 2 signals"""
        df = df.copy()
        
        # Calculate MACD if not present
        if 'MACD' not in df.columns:
            df['MACD'], df['Signal'], df['Histogram'] = calculate_macd(
                df['Close'], 
                self.config.fast_period, 
                self.config.slow_period, 
                self.config.signal_period
            )
        
        # Detect divergences
        df['Bullish_Div'], df['Bearish_Div'] = self.find_divergences(
            df['High'], df['Low'], df['MACD'], df['Histogram']
        )
        
        # Signal generation
        df['Signal_S2'] = 0
        df.loc[df['Bullish_Div'] == 1, 'Signal_S2'] = 1
        df.loc[df['Bearish_Div'] == 1, 'Signal_S2'] = -1
        
        return df


# =============================================================================
# HISTOGRAM CONFIRMATION
# =============================================================================

class HistogramConfirmation:
    """
    MACD Histogram Confirmation Signals
    
    1. The Flip: First bar of opposite color indicates momentum shift
    2. Shrinking Tower: Bars getting smaller = losing momentum
    3. Zero Bounce: Histogram approaches zero and bounces = trend continuation
    """
    
    def __init__(self, config: MACDConfig):
        self.config = config
    
    def detect_flip(self, histogram: pd.Series) -> pd.Series:
        """Detect histogram color flips"""
        flip = pd.Series(index=histogram.index, data=0)
        
        # Green flip (first positive after negatives)
        green_flip = (histogram > 0) & (histogram.shift(1) <= 0)
        flip[green_flip] = 1
        
        # Red flip (first negative after positives)
        red_flip = (histogram < 0) & (histogram.shift(1) >= 0)
        flip[red_flip] = -1
        
        return flip
    
    def detect_shrinking_tower(self, histogram: pd.Series, periods: int = 3) -> pd.Series:
        """Detect shrinking histogram (losing momentum)"""
        shrinking = pd.Series(index=histogram.index, data=0)
        
        for i in range(periods, len(histogram)):
            hist_window = histogram.iloc[i-periods:i+1].abs()
            
            # Check if each bar is smaller than previous
            is_shrinking = all(hist_window.iloc[j] < hist_window.iloc[j-1] 
                              for j in range(1, len(hist_window)))
            
            if is_shrinking:
                # Shrinking in positive territory = bearish warning
                if histogram.iloc[i] > 0:
                    shrinking.iloc[i] = -1
                # Shrinking in negative territory = bullish warning
                else:
                    shrinking.iloc[i] = 1
        
        return shrinking
    
    def detect_zero_bounce(self, histogram: pd.Series, threshold_pct: float = 0.3) -> pd.Series:
        """Detect histogram bouncing off zero line"""
        zero_bounce = pd.Series(index=histogram.index, data=0)
        
        hist_std = histogram.rolling(50).std()
        
        for i in range(3, len(histogram)):
            curr = histogram.iloc[i]
            prev = histogram.iloc[i-1]
            prev2 = histogram.iloc[i-2]
            std = hist_std.iloc[i]
            
            if pd.isna(std) or std == 0:
                continue
            
            # Zero approach from positive territory, then bounce back up
            if (prev2 > 0 and prev > 0 and curr > 0 and
                abs(prev) < abs(prev2) * 0.5 and  # Approached zero
                abs(curr) > abs(prev)):  # Now bouncing away
                zero_bounce.iloc[i] = 1  # Bullish continuation
            
            # Zero approach from negative territory, then bounce back down
            if (prev2 < 0 and prev < 0 and curr < 0 and
                abs(prev) < abs(prev2) * 0.5 and
                abs(curr) > abs(prev)):
                zero_bounce.iloc[i] = -1  # Bearish continuation
        
        return zero_bounce
    
    def generate_confirmations(self, df: pd.DataFrame) -> pd.DataFrame:
        """Generate all histogram confirmation signals"""
        df = df.copy()
        
        if 'Histogram' not in df.columns:
            df['MACD'], df['Signal'], df['Histogram'] = calculate_macd(
                df['Close'],
                self.config.fast_period,
                self.config.slow_period,
                self.config.signal_period
            )
        
        df['Hist_Flip'] = self.detect_flip(df['Histogram'])
        df['Hist_Shrinking'] = self.detect_shrinking_tower(df['Histogram'])
        df['Hist_Zero_Bounce'] = self.detect_zero_bounce(df['Histogram'])
        
        # Combined confirmation score
        df['Hist_Confirm'] = 0
        
        # Bullish confirmations
        bullish = (
            (df['Hist_Flip'] == 1) |
            (df['Hist_Zero_Bounce'] == 1) |
            ((df['Hist_Shrinking'] == 1) & (df['Histogram'] < 0))  # Bearish momentum weakening
        )
        df.loc[bullish, 'Hist_Confirm'] = 1
        
        # Bearish confirmations
        bearish = (
            (df['Hist_Flip'] == -1) |
            (df['Hist_Zero_Bounce'] == -1) |
            ((df['Hist_Shrinking'] == -1) & (df['Histogram'] > 0))  # Bullish momentum weakening
        )
        df.loc[bearish, 'Hist_Confirm'] = -1
        
        return df


# =============================================================================
# SYSTEM 3: MULTI-TIMEFRAME ANALYSIS
# =============================================================================

class System3_MultiTimeframe:
    """
    Multi-Timeframe MACD Analysis
    
    Daily Chart: Determines overall trend bias (above/below zero)
    4-Hour Chart: Identifies setup (crossover or divergence)
    1-Hour Chart: Provides entry trigger (histogram confirmation with flip)
    
    ALL THREE TIMEFRAMES MUST AGREE
    """
    
    def __init__(self, config: MACDConfig):
        self.config = config
    
    def resample_to_timeframe(self, df: pd.DataFrame, timeframe: str) -> pd.DataFrame:
        """Resample data to specified timeframe"""
        resampled = df.resample(timeframe).agg({
            'Open': 'first',
            'High': 'max',
            'Low': 'min',
            'Close': 'last',
            'Volume': 'sum'
        }).dropna()
        return resampled
    
    def get_daily_bias(self, df: pd.DataFrame) -> pd.Series:
        """
        Daily timeframe: Determine trend bias
        MACD above zero = Bullish bias (+1)
        MACD below zero = Bearish bias (-1)
        """
        macd, _, _ = calculate_macd(
            df['Close'],
            self.config.fast_period,
            self.config.slow_period,
            self.config.signal_period
        )
        
        bias = pd.Series(index=macd.index, data=0)
        bias[macd > 0] = 1
        bias[macd < 0] = -1
        
        return bias
    
    def get_setup_timeframe(self, df: pd.DataFrame) -> Tuple[pd.DataFrame, pd.Series]:
        """
        4-Hour timeframe: Identify setups
        Returns system 1 or system 2 signals
        """
        # Apply System 1 and System 2
        system1 = System1_ZeroLineRule(self.config)
        system2 = System2_Divergence(self.config)
        
        df = system1.generate_signals(df)
        df = system2.generate_signals(df)
        
        # Combined setup signal
        setup = pd.Series(index=df.index, data=0)
        setup[(df['Signal_S1'] == 1) | (df['Signal_S2'] == 1)] = 1
        setup[(df['Signal_S1'] == -1) | (df['Signal_S2'] == -1)] = -1
        
        return df, setup
    
    def get_entry_trigger(self, df: pd.DataFrame) -> pd.Series:
        """
        1-Hour timeframe: Entry trigger
        Histogram confirmation (flip)
        """
        hist_confirm = HistogramConfirmation(self.config)
        df = hist_confirm.generate_confirmations(df)
        
        return df['Hist_Flip']
    
    def check_alignment(self, daily_bias: int, setup_signal: int, entry_trigger: int) -> int:
        """
        Check if all timeframes align
        Returns: 1 (bullish), -1 (bearish), 0 (no alignment)
        """
        if daily_bias == 1 and setup_signal == 1 and entry_trigger == 1:
            return 1  # All bullish
        elif daily_bias == -1 and setup_signal == -1 and entry_trigger == -1:
            return -1  # All bearish
        return 0  # No alignment


# =============================================================================
# PRICE STRUCTURE CONFIRMATION
# =============================================================================

class PriceStructure:
    """
    Confirm trades with price structure:
    - MACD signals at key support/resistance levels
    - Breakout confirmation after trendline breaks
    """
    
    def __init__(self, config: MACDConfig):
        self.config = config
    
    def is_near_support(self, price: float, support_levels: List[float], 
                        threshold_pct: float = 0.02) -> bool:
        """Check if price is near a support level"""
        for level in support_levels:
            if abs(price - level) / level < threshold_pct:
                return True
        return False
    
    def is_near_resistance(self, price: float, resistance_levels: List[float], 
                           threshold_pct: float = 0.02) -> bool:
        """Check if price is near a resistance level"""
        for level in resistance_levels:
            if abs(price - level) / level < threshold_pct:
                return True
        return False
    
    def confirm_with_structure(self, df: pd.DataFrame, signal: int, 
                               lookback: int = 50) -> bool:
        """
        Confirm signal with price structure
        
        Buy signals should occur at:
        - Support levels
        - After bullish breakouts
        
        Sell signals should occur at:
        - Resistance levels
        - After bearish breakouts
        """
        support_levels, resistance_levels = find_support_resistance(df, lookback)
        current_price = df['Close'].iloc[-1]
        
        if signal == 1:  # Buy signal
            return self.is_near_support(current_price, support_levels)
        elif signal == -1:  # Sell signal
            return self.is_near_resistance(current_price, resistance_levels)
        
        return False


# =============================================================================
# MAIN STRATEGY CLASS
# =============================================================================

class MACD3SystemStrategy:
    """
    Complete 3-System MACD Strategy Implementation
    
    Combines all three systems with histogram confirmation
    and price structure analysis.
    """
    
    def __init__(self, config: MACDConfig = None):
        self.config = config or MACDConfig()
        self.system1 = System1_ZeroLineRule(self.config)
        self.system2 = System2_Divergence(self.config)
        self.histogram = HistogramConfirmation(self.config)
        self.price_structure = PriceStructure(self.config)
        self.name = "MACD 3-System Strategy"
    
    def generate_signals(self, df: pd.DataFrame, use_price_structure: bool = True) -> pd.DataFrame:
        """
        Generate combined signals from all three systems
        
        Signal Priority:
        1. System 1 (Trend) with histogram confirmation = Highest
        2. System 2 (Divergence) with histogram confirmation = Medium
        3. Either system without confirmation = Lower
        """
        df = df.copy()
        
        # Apply System 1
        df = self.system1.generate_signals(df)
        
        # Apply System 2
        df = self.system2.generate_signals(df)
        
        # Apply Histogram Confirmation
        df = self.histogram.generate_confirmations(df)
        
        # Calculate ATR for stop loss
        df['ATR'] = calculate_atr(df['High'], df['Low'], df['Close'])
        
        # Combined signal generation
        df['Signal'] = 0
        df['Signal_Strength'] = 0
        df['Signal_Source'] = ''
        
        for i in range(60, len(df)):
            idx = df.index[i]
            s1 = df.loc[idx, 'Signal_S1']
            s2 = df.loc[idx, 'Signal_S2']
            hist = df.loc[idx, 'Hist_Confirm']
            
            signal = 0
            strength = 0
            source = ''
            
            # System 1 signals (trend)
            if s1 != 0:
                if s1 == hist:  # Confirmed by histogram
                    signal = s1
                    strength = 3  # Highest
                    source = 'S1+Hist'
                else:
                    signal = s1
                    strength = 2
                    source = 'S1'
            
            # System 2 signals (divergence)
            elif s2 != 0:
                if s2 == hist:  # Confirmed by histogram
                    signal = s2
                    strength = 3
                    source = 'S2+Hist'
                else:
                    signal = s2
                    strength = 2
                    source = 'S2'
            
            # Histogram flip alone can be entry trigger
            elif hist != 0 and df.loc[idx, 'MACD'] * hist > 0:  # Histogram aligned with MACD position
                signal = hist
                strength = 1
                source = 'Hist'
            
            # Price structure confirmation (optional boost)
            if use_price_structure and signal != 0:
                df_window = df.iloc[:i+1]
                if self.price_structure.confirm_with_structure(df_window, signal):
                    strength += 1  # Boost strength if at key level
                    source += '+Key'
            
            df.loc[idx, 'Signal'] = signal
            df.loc[idx, 'Signal_Strength'] = strength
            df.loc[idx, 'Signal_Source'] = source
        
        return df
    
    def calculate_position_size(self, capital: float, entry_price: float, 
                                stop_loss: float, risk_pct: float = None) -> int:
        """Calculate position size based on risk"""
        risk_pct = risk_pct or self.config.risk_pct
        risk_amount = capital * risk_pct
        risk_per_share = abs(entry_price - stop_loss)
        
        if risk_per_share <= 0:
            return 0
        
        shares = int(risk_amount / risk_per_share)
        max_shares = int(capital * self.config.max_position_pct / entry_price)
        
        return min(shares, max_shares)


# =============================================================================
# BACKTESTING ENGINE
# =============================================================================

def backtest_macd_strategy(df: pd.DataFrame, config: MACDConfig = None, 
                           capital: float = 100000) -> Dict:
    """
    Backtest the MACD 3-System Strategy with proper risk management
    
    Entry: At candle close
    Stop Loss: Recent swing high/low or ATR-based
    Target: 2x Risk
    Trail: Move stop to breakeven at 1x, trail with MACD cross
    """
    config = config or MACDConfig()
    strategy = MACD3SystemStrategy(config)
    
    # Generate signals
    df = strategy.generate_signals(df)
    
    trades = []
    current_capital = capital
    position = None
    
    for i in range(60, len(df)):
        row = df.iloc[i]
        curr_atr = row.get('ATR', 0)
        
        if pd.isna(curr_atr) or curr_atr <= 0:
            continue
        
        # Check exit for existing position
        if position is not None:
            exit_triggered = False
            exit_price = None
            exit_reason = None
            
            # Check stops and targets
            if position['direction'] == 'long':
                # Stop loss hit
                if row['Low'] <= position['stop_loss']:
                    exit_price = position['stop_loss'] * (1 - config.slippage)
                    exit_triggered = True
                    exit_reason = 'stop_loss'
                # Take profit hit
                elif row['High'] >= position['take_profit']:
                    exit_price = position['take_profit']
                    exit_triggered = True
                    exit_reason = 'take_profit'
                # Opposite MACD cross exit
                elif row.get('Crossover', 0) == -1:
                    exit_price = row['Close'] * (1 - config.slippage)
                    exit_triggered = True
                    exit_reason = 'macd_cross'
            
            else:  # Short position
                if row['High'] >= position['stop_loss']:
                    exit_price = position['stop_loss'] * (1 + config.slippage)
                    exit_triggered = True
                    exit_reason = 'stop_loss'
                elif row['Low'] <= position['take_profit']:
                    exit_price = position['take_profit']
                    exit_triggered = True
                    exit_reason = 'take_profit'
                elif row.get('Crossover', 0) == 1:
                    exit_price = row['Close'] * (1 + config.slippage)
                    exit_triggered = True
                    exit_reason = 'macd_cross'
            
            # Move stop to breakeven at 1x profit
            if not exit_triggered:
                if position['direction'] == 'long':
                    unrealized = row['Close'] - position['entry_price']
                    if unrealized >= position['initial_risk'] and not position.get('breakeven_set', False):
                        position['stop_loss'] = position['entry_price']
                        position['breakeven_set'] = True
                else:
                    unrealized = position['entry_price'] - row['Close']
                    if unrealized >= position['initial_risk'] and not position.get('breakeven_set', False):
                        position['stop_loss'] = position['entry_price']
                        position['breakeven_set'] = True
            
            if exit_triggered:
                # Calculate PnL
                if position['direction'] == 'long':
                    pnl = position['shares'] * (exit_price - position['entry_price'])
                    ret = (exit_price / position['entry_price'] - 1) * 100
                else:
                    pnl = position['shares'] * (position['entry_price'] - exit_price)
                    ret = (position['entry_price'] / exit_price - 1) * 100
                
                # Subtract commission
                pnl -= position['shares'] * exit_price * config.commission
                current_capital += pnl
                
                trades.append({
                    'entry_date': position['entry_date'],
                    'exit_date': df.index[i],
                    'direction': position['direction'],
                    'entry_price': position['entry_price'],
                    'exit_price': exit_price,
                    'stop_loss': position['original_sl'],
                    'take_profit': position['take_profit'],
                    'shares': position['shares'],
                    'pnl': pnl,
                    'return_pct': ret,
                    'exit_reason': exit_reason,
                    'signal_source': position['signal_source'],
                    'signal_strength': position['signal_strength']
                })
                
                position = None
        
        # Check for new entry
        if position is None:
            signal = row.get('Signal', 0)
            strength = row.get('Signal_Strength', 0)
            source = row.get('Signal_Source', '')
            
            # Only take signals with minimum strength
            if signal != 0 and strength >= 2:
                close = row['Close']
                
                # Find recent swing for stop loss
                lookback = min(20, i)
                
                if signal == 1:  # Long
                    entry_price = close * (1 + config.slippage)
                    # Stop below recent low
                    recent_low = df['Low'].iloc[i-lookback:i].min()
                    stop_loss = min(recent_low, entry_price - curr_atr * 2)
                    initial_risk = entry_price - stop_loss
                    take_profit = entry_price + initial_risk * config.risk_reward_ratio
                    direction = 'long'
                
                else:  # Short
                    entry_price = close * (1 - config.slippage)
                    # Stop above recent high
                    recent_high = df['High'].iloc[i-lookback:i].max()
                    stop_loss = max(recent_high, entry_price + curr_atr * 2)
                    initial_risk = stop_loss - entry_price
                    take_profit = entry_price - initial_risk * config.risk_reward_ratio
                    direction = 'short'
                
                # Calculate position size
                shares = strategy.calculate_position_size(
                    current_capital, entry_price, stop_loss
                )
                
                if shares > 0:
                    # Subtract commission
                    current_capital -= shares * entry_price * config.commission
                    
                    position = {
                        'entry_date': df.index[i],
                        'direction': direction,
                        'entry_price': entry_price,
                        'stop_loss': stop_loss,
                        'original_sl': stop_loss,
                        'take_profit': take_profit,
                        'initial_risk': initial_risk,
                        'shares': shares,
                        'signal_source': source,
                        'signal_strength': strength,
                        'breakeven_set': False
                    }
    
    # Calculate results
    trades_df = pd.DataFrame(trades) if trades else pd.DataFrame()
    total_return = (current_capital / capital - 1) * 100
    
    results = {
        'strategy': 'MACD 3-System',
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
        results['profit_factor'] = abs(wins['pnl'].sum() / losses['pnl'].sum()) if len(losses) > 0 and losses['pnl'].sum() != 0 else 0
        results['avg_trade'] = trades_df['return_pct'].mean()
        results['best_trade'] = trades_df['return_pct'].max()
        results['worst_trade'] = trades_df['return_pct'].min()
        
        # Exit reason breakdown
        results['exits'] = trades_df['exit_reason'].value_counts().to_dict()
        results['signal_sources'] = trades_df['signal_source'].value_counts().to_dict()
    else:
        results['win_rate'] = 0
        results['avg_win'] = 0
        results['avg_loss'] = 0
        results['profit_factor'] = 0
        results['avg_trade'] = 0
        results['best_trade'] = 0
        results['worst_trade'] = 0
        results['exits'] = {}
        results['signal_sources'] = {}
    
    results['trades_df'] = trades_df
    
    return results


# =============================================================================
# MAIN EXECUTION
# =============================================================================

def run_backtest():
    """Run comprehensive backtest of MACD 3-System Strategy"""
    
    symbols = [
        'RELIANCE.NS', 'TCS.NS', 'HDFCBANK.NS', 'INFY.NS', 'SBIN.NS',
        'BHARTIARTL.NS', 'ITC.NS', 'AXISBANK.NS', 'LT.NS', 'MARUTI.NS',
        'SUNPHARMA.NS', 'TITAN.NS', 'WIPRO.NS', 'BAJFINANCE.NS', 'ICICIBANK.NS'
    ]
    
    print("=" * 90)
    print("MACD 3-SYSTEM SWING TRADING STRATEGY BACKTEST")
    print("=" * 90)
    print("""
Strategy Components:
  SYSTEM 1: Zero Line Rule (Trend Trading)
    - Trade direction based on MACD position relative to zero
    - Distance rule + Chop zone avoidance
    - Wait for confirmation candles
    
  SYSTEM 2: Divergence Detection (Reversal Trading)  
    - Bearish: Price higher high, MACD lower high
    - Bullish: Price lower low, MACD higher low
    
  HISTOGRAM CONFIRMATION:
    - The Flip (first opposite color bar)
    - Shrinking Tower (momentum loss)
    - Zero Bounce (trend continuation)
    
Risk Management:
  - Entry: At candle close
  - Stop Loss: Recent swing or 2 ATR
  - Target: 2x Risk (R:R = 1:2)
  - Trail: Move stop to breakeven at 1x profit
    """)
    
    config = MACDConfig()
    all_results = []
    
    print("-" * 90)
    print(f"{'Symbol':<15} | {'Return':>8} | {'Trades':>6} | {'Win Rate':>8} | {'Profit F':>8} | {'Avg Trade':>9}")
    print("-" * 90)
    
    for symbol in symbols:
        try:
            # Download data
            df = yf.download(symbol, period='2y', interval='1d', progress=False)
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = df.columns.get_level_values(0)
            
            if df.empty or len(df) < 300:
                print(f"{symbol:<15} | {'Skip - not enough data'}")
                continue
            
            # Run backtest
            result = backtest_macd_strategy(df, config)
            result['symbol'] = symbol
            all_results.append(result)
            
            # Print results
            status = "OK" if result['total_return'] > 0 else "--"
            print(f"{symbol:<15} | {result['total_return']:>7.2f}% | {result['total_trades']:>6} | "
                  f"{result['win_rate']:>7.1%} | {result['profit_factor']:>8.2f} | "
                  f"{result['avg_trade']:>8.2f}% {status}")
            
        except Exception as e:
            print(f"{symbol:<15} | Error: {e}")
    
    # Summary statistics
    if all_results:
        print("\n" + "=" * 90)
        print("SUMMARY STATISTICS")
        print("=" * 90)
        
        returns = [r['total_return'] for r in all_results]
        win_rates = [r['win_rate'] for r in all_results]
        profit_factors = [r['profit_factor'] for r in all_results]
        total_trades = sum(r['total_trades'] for r in all_results)
        profitable = sum(1 for r in all_results if r['total_return'] > 0)
        
        print(f"\nTotal Symbols Tested: {len(all_results)}")
        print(f"Profitable Symbols:   {profitable}/{len(all_results)} ({profitable/len(all_results):.1%})")
        print(f"\nAverage Return:       {np.mean(returns):.2f}%")
        print(f"Median Return:        {np.median(returns):.2f}%")
        print(f"Best Symbol:          {max(returns):.2f}%")
        print(f"Worst Symbol:         {min(returns):.2f}%")
        print(f"\nTotal Trades:         {total_trades}")
        print(f"Avg Win Rate:         {np.mean(win_rates):.1%}")
        print(f"Avg Profit Factor:    {np.mean(profit_factors):.2f}")
        
        # Signal source breakdown
        print("\n" + "-" * 90)
        print("SIGNAL SOURCE ANALYSIS")
        print("-" * 90)
        
        all_sources = {}
        for r in all_results:
            for source, count in r.get('signal_sources', {}).items():
                all_sources[source] = all_sources.get(source, 0) + count
        
        for source, count in sorted(all_sources.items(), key=lambda x: -x[1]):
            print(f"  {source:<20}: {count:>5} trades")
        
        # Exit reason breakdown
        print("\n" + "-" * 90)
        print("EXIT REASON ANALYSIS")
        print("-" * 90)
        
        all_exits = {}
        for r in all_results:
            for reason, count in r.get('exits', {}).items():
                all_exits[reason] = all_exits.get(reason, 0) + count
        
        for reason, count in sorted(all_exits.items(), key=lambda x: -x[1]):
            print(f"  {reason:<20}: {count:>5} trades")
    
    # Strategy insights
    print("\n" + "=" * 90)
    print("KEY INSIGHTS")
    print("=" * 90)
    print("""
WHAT THE RESULTS TELL US:

1. ZERO LINE RULE (System 1)
   - Filters out counter-trend trades
   - Distance rule avoids choppy markets
   - Confirmation candles reduce whipsaws
   
2. DIVERGENCE (System 2)
   - Catches reversals before they happen
   - Works best at support/resistance levels
   - Requires histogram confirmation
   
3. HISTOGRAM CONFIRMATION
   - The Flip is most reliable entry trigger
   - Shrinking tower warns of trend exhaustion
   - Zero bounce confirms trend continuation
   
4. RISK MANAGEMENT IS KEY
   - 2% risk per trade limits drawdowns
   - 1:2 Risk/Reward improves expectancy
   - Trailing stop captures runners
   
5. REALISTIC EXPECTATIONS
   - 50-60% win rate with 1:2 R/R = profitable
   - Expect losing streaks
   - Edge comes from discipline, not perfection
""")
    
    return all_results


if __name__ == "__main__":
    results = run_backtest()
