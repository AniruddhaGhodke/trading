"""
MACD 3-SYSTEM REAL-TIME SIGNAL SCANNER
=======================================

Scans a universe of stocks and generates actionable trading signals based on
the MACD 3-System strategy.

Features:
1. Scans 100+ stocks in NSE/BSE
2. Applies all three MACD systems
3. Ranks signals by quality
4. Provides entry, stop loss, and target levels
5. Can run on schedule (daily/hourly)

Usage:
    python macd_signal_scanner.py                    # Scan all stocks
    python macd_signal_scanner.py --nifty50          # Scan only Nifty 50
    python macd_signal_scanner.py --watchlist        # Scan your watchlist
    python macd_signal_scanner.py --refresh 60       # Auto-refresh every 60 minutes

Output:
    - Console table with signals sorted by quality
    - CSV file with detailed signal information
    - Trade ideas with entry/SL/target levels
"""

import pandas as pd
import numpy as np
import yfinance as yf
from datetime import datetime, timedelta
from typing import Dict, List, Tuple, Optional
from dataclasses import dataclass, field
import warnings
import argparse
import time
import os
import sys

warnings.filterwarnings('ignore')


# =============================================================================
# STOCK UNIVERSE
# =============================================================================

# Nifty 50 stocks
NIFTY_50 = [
    'RELIANCE.NS', 'TCS.NS', 'HDFCBANK.NS', 'INFY.NS', 'ICICIBANK.NS',
    'HINDUNILVR.NS', 'SBIN.NS', 'BHARTIARTL.NS', 'ITC.NS', 'KOTAKBANK.NS',
    'LT.NS', 'HCLTECH.NS', 'AXISBANK.NS', 'ASIANPAINT.NS', 'MARUTI.NS',
    'SUNPHARMA.NS', 'TITAN.NS', 'BAJFINANCE.NS', 'DMART.NS', 'NTPC.NS',
    'TATAMOTORS.NS', 'ONGC.NS', 'NESTLEIND.NS', 'WIPRO.NS', 'POWERGRID.NS',
    'M&M.NS', 'ULTRACEMCO.NS', 'JSWSTEEL.NS', 'ADANIENT.NS', 'COALINDIA.NS',
    'TATASTEEL.NS', 'HINDALCO.NS', 'BAJAJFINSV.NS', 'TECHM.NS', 'HDFCLIFE.NS',
    'GRASIM.NS', 'DRREDDY.NS', 'SBILIFE.NS', 'CIPLA.NS', 'BRITANNIA.NS',
    'APOLLOHOSP.NS', 'EICHERMOT.NS', 'INDUSINDBK.NS', 'DIVISLAB.NS', 'TATACONSUM.NS',
    'BPCL.NS', 'HEROMOTOCO.NS', 'SHRIRAMFIN.NS', 'BAJAJ-AUTO.NS', 'ADANIPORTS.NS'
]

# Nifty Next 50
NIFTY_NEXT_50 = [
    'ADANIGREEN.NS', 'AMBUJACEM.NS', 'BANKBARODA.NS', 'BEL.NS', 'BERGEPAINT.NS',
    'BOSCHLTD.NS', 'CANBK.NS', 'CHOLAFIN.NS', 'COLPAL.NS', 'DLF.NS',
    'DABUR.NS', 'GAIL.NS', 'GODREJCP.NS', 'HAVELLS.NS', 'ICICIPRULI.NS',
    'ICICIGI.NS', 'INDUSTOWER.NS', 'IOC.NS', 'IRCTC.NS', 'JINDALSTEL.NS',
    'LICI.NS', 'LTIM.NS', 'LUPIN.NS', 'MARICO.NS', 'MOTHERSON.NS',
    'NAUKRI.NS', 'NHPC.NS', 'OFSS.NS', 'PAGEIND.NS', 'PAYTM.NS',
    'PFC.NS', 'PIDILITIND.NS', 'PNB.NS', 'RECLTD.NS', 'SBICARD.NS',
    'SIEMENS.NS', 'SRF.NS', 'TORNTPHARM.NS', 'TRENT.NS', 'TVSMOTOR.NS',
    'UPL.NS', 'VEDL.NS', 'ZOMATO.NS', 'ZYDUSLIFE.NS', 'POLYCAB.NS',
    'ABB.NS', 'ATGL.NS', 'MCDOWELL-N.NS', 'HINDPETRO.NS', 'SAIL.NS'
]

# Popular mid-caps
MIDCAP_POPULAR = [
    'TATAELXSI.NS', 'PERSISTENT.NS', 'COFORGE.NS', 'MPHASIS.NS', 'LTTS.NS',
    'DEEPAKNTR.NS', 'ASTRAL.NS', 'VOLTAS.NS', 'CROMPTON.NS', 'BHARATFORG.NS',
    'JUBLFOOD.NS', 'MUTHOOTFIN.NS', 'IDFCFIRSTB.NS', 'FEDERALBNK.NS', 'RBLBANK.NS',
    'MANAPPURAM.NS', 'INDHOTEL.NS', 'OBEROIRLTY.NS', 'GODREJPROP.NS', 'PRESTIGE.NS',
    'AUROPHARMA.NS', 'ALKEM.NS', 'IPCALAB.NS', 'LAURUSLABS.NS', 'NATCOPHARM.NS',
    'TATAPOWER.NS', 'ADANIPOWER.NS', 'CUMMINSIND.NS', 'THERMAX.NS', 'AIAENG.NS'
]

# All stocks
ALL_STOCKS = NIFTY_50 + NIFTY_NEXT_50 + MIDCAP_POPULAR


# =============================================================================
# SIGNAL DATA STRUCTURE
# =============================================================================

@dataclass
class Signal:
    """Trading signal with all details"""
    symbol: str
    signal_type: str  # 'BUY' or 'SELL'
    signal_source: str  # 'S1_Trend', 'S2_Div', etc.
    quality: int  # 1-5 (higher = better)
    
    # Price levels
    current_price: float
    entry_price: float
    stop_loss: float
    target_1: float  # 1R target
    target_2: float  # 2R target
    
    # Risk metrics
    risk_pct: float  # Risk as % of entry
    reward_pct: float  # Reward as % of entry
    risk_reward: float
    
    # Technical details
    macd_value: float
    macd_signal: float
    histogram: float
    macd_position: str  # 'Above Zero', 'Below Zero', 'Chop Zone'
    
    # Additional info
    divergence: str  # 'Bullish', 'Bearish', 'None'
    histogram_momentum: str  # 'Strong', 'Weak', 'Flip'
    near_support: bool
    near_resistance: bool
    
    # Metadata
    timestamp: str = field(default_factory=lambda: datetime.now().strftime('%Y-%m-%d %H:%M'))
    
    def to_dict(self):
        return {
            'Symbol': self.symbol,
            'Signal': self.signal_type,
            'Source': self.signal_source,
            'Quality': self.quality,
            'Price': self.current_price,
            'Entry': self.entry_price,
            'Stop': self.stop_loss,
            'Target1': self.target_1,
            'Target2': self.target_2,
            'Risk%': self.risk_pct,
            'R:R': self.risk_reward,
            'MACD_Pos': self.macd_position,
            'Divergence': self.divergence,
            'At_Level': 'Support' if self.near_support else ('Resistance' if self.near_resistance else '-'),
            'Time': self.timestamp
        }


# =============================================================================
# CORE INDICATORS
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


def find_swing_points(high: pd.Series, low: pd.Series, 
                      lookback: int = 5) -> Tuple[List[float], List[float]]:
    """Find recent swing highs and lows"""
    resistance = []
    support = []
    
    for i in range(lookback, len(high) - lookback):
        # Swing high
        is_high = all(high.iloc[i] > high.iloc[i-j] and high.iloc[i] > high.iloc[i+j] 
                     for j in range(1, lookback+1) if i+j < len(high))
        if is_high:
            resistance.append(high.iloc[i])
        
        # Swing low
        is_low = all(low.iloc[i] < low.iloc[i-j] and low.iloc[i] < low.iloc[i+j] 
                    for j in range(1, lookback+1) if i+j < len(low))
        if is_low:
            support.append(low.iloc[i])
    
    return sorted(set(support))[-5:], sorted(set(resistance))[-5:]


def is_near_level(price: float, levels: List[float], threshold: float = 0.03) -> bool:
    """Check if price is within threshold of any level"""
    for level in levels:
        if abs(price - level) / level < threshold:
            return True
    return False


# =============================================================================
# SIGNAL DETECTION
# =============================================================================

def detect_macd_crossover(macd: pd.Series, signal: pd.Series, 
                          confirmation: int = 2) -> int:
    """
    Detect confirmed MACD crossover
    Returns: 1 (bullish), -1 (bearish), 0 (none)
    """
    if len(macd) < confirmation + 2:
        return 0
    
    # Check for crossover N bars ago
    idx = -confirmation - 1
    
    # Bullish: MACD crossed above signal
    if (macd.iloc[idx] > signal.iloc[idx] and 
        macd.iloc[idx-1] <= signal.iloc[idx-1] and
        macd.iloc[-1] > signal.iloc[-1]):  # Still above
        return 1
    
    # Bearish: MACD crossed below signal
    if (macd.iloc[idx] < signal.iloc[idx] and 
        macd.iloc[idx-1] >= signal.iloc[idx-1] and
        macd.iloc[-1] < signal.iloc[-1]):  # Still below
        return -1
    
    return 0


def detect_divergence(high: pd.Series, low: pd.Series, macd: pd.Series,
                       lookback: int = 20) -> str:
    """Detect price/MACD divergence"""
    if len(high) < lookback + 10:
        return 'None'
    
    # Get recent window
    h = high.iloc[-lookback:]
    l = low.iloc[-lookback:]
    m = macd.iloc[-lookback:]
    
    # Find peaks and troughs
    h_max_idx = h.idxmax()
    l_min_idx = l.idxmin()
    
    # Bearish divergence: Higher price high, lower MACD high
    recent_h = h.iloc[-5:].max()
    older_h = h.iloc[:-5].max()
    recent_m_at_h = m.iloc[-5:].max()
    older_m_at_h = m.iloc[:-5].max()
    
    if recent_h > older_h and recent_m_at_h < older_m_at_h:
        return 'Bearish'
    
    # Bullish divergence: Lower price low, higher MACD low
    recent_l = l.iloc[-5:].min()
    older_l = l.iloc[:-5].min()
    recent_m_at_l = m.iloc[-5:].min()
    older_m_at_l = m.iloc[:-5].min()
    
    if recent_l < older_l and recent_m_at_l > older_m_at_l:
        return 'Bullish'
    
    return 'None'


def get_histogram_momentum(histogram: pd.Series) -> str:
    """Analyze histogram momentum"""
    if len(histogram) < 5:
        return 'Unknown'
    
    curr = histogram.iloc[-1]
    prev = histogram.iloc[-2]
    prev2 = histogram.iloc[-3]
    
    # Flip detection
    if curr > 0 and prev <= 0:
        return 'Bullish Flip'
    if curr < 0 and prev >= 0:
        return 'Bearish Flip'
    
    # Momentum strength
    if curr > 0:
        if curr > prev > prev2:
            return 'Strong Bullish'
        elif curr < prev:
            return 'Weakening Bullish'
        else:
            return 'Bullish'
    else:
        if curr < prev < prev2:
            return 'Strong Bearish'
        elif curr > prev:
            return 'Weakening Bearish'
        else:
            return 'Bearish'


def get_macd_zone(macd_value: float, macd_std: float) -> str:
    """Determine MACD position relative to zero"""
    if pd.isna(macd_std) or macd_std == 0:
        return 'Unknown'
    
    normalized = abs(macd_value) / macd_std
    
    if normalized < 0.3:
        return 'Chop Zone'
    elif macd_value > 0:
        return 'Above Zero'
    else:
        return 'Below Zero'


# =============================================================================
# MAIN SCANNER
# =============================================================================

class MACDSignalScanner:
    """Real-time MACD 3-System Signal Scanner"""
    
    def __init__(self, symbols: List[str] = None, long_only: bool = True):
        self.symbols = symbols or NIFTY_50
        self.long_only = long_only
        self.signals: List[Signal] = []
        
    def download_data(self, symbol: str, period: str = '6mo', 
                      interval: str = '1d') -> Optional[pd.DataFrame]:
        """Download OHLCV data for a symbol"""
        try:
            df = yf.download(symbol, period=period, interval=interval, progress=False)
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = df.columns.get_level_values(0)
            return df if not df.empty and len(df) > 60 else None
        except Exception as e:
            return None
    
    def analyze_stock(self, symbol: str, df: pd.DataFrame) -> Optional[Signal]:
        """Analyze a single stock and generate signal if criteria met"""
        
        # Calculate indicators
        macd_line, signal_line, histogram = calculate_macd(df['Close'])
        atr_val = calculate_atr(df['High'], df['Low'], df['Close'])
        
        # Get latest values
        curr_price = df['Close'].iloc[-1]
        curr_macd = macd_line.iloc[-1]
        curr_signal = signal_line.iloc[-1]
        curr_hist = histogram.iloc[-1]
        curr_atr = atr_val.iloc[-1]
        
        if pd.isna(curr_atr) or curr_atr <= 0:
            return None
        
        # Get MACD zone
        macd_std = macd_line.rolling(50).std().iloc[-1]
        macd_zone = get_macd_zone(curr_macd, macd_std)
        
        # Detect crossover (with 2 bar confirmation)
        crossover = detect_macd_crossover(macd_line, signal_line, 2)
        
        # Detect divergence
        divergence = detect_divergence(df['High'], df['Low'], macd_line, 20)
        
        # Get histogram momentum
        hist_momentum = get_histogram_momentum(histogram)
        
        # Find support/resistance
        support_levels, resistance_levels = find_swing_points(df['High'], df['Low'], 5)
        near_support = is_near_level(curr_price, support_levels, 0.03)
        near_resistance = is_near_level(curr_price, resistance_levels, 0.03)
        
        # =================================================================
        # SIGNAL GENERATION LOGIC
        # =================================================================
        
        signal_type = None
        signal_source = ''
        quality = 0
        
        # SYSTEM 1: Zero Line Rule with Crossover
        if crossover == 1 and macd_zone == 'Above Zero':
            signal_type = 'BUY'
            signal_source = 'S1_Trend'
            quality = 2
            
            # Quality boosts
            if 'Bullish' in hist_momentum:
                quality += 1
                signal_source += '+Hist'
            if near_support:
                quality += 1
                signal_source += '+Level'
        
        elif crossover == -1 and macd_zone == 'Below Zero' and not self.long_only:
            signal_type = 'SELL'
            signal_source = 'S1_Trend'
            quality = 2
            
            if 'Bearish' in hist_momentum:
                quality += 1
                signal_source += '+Hist'
            if near_resistance:
                quality += 1
                signal_source += '+Level'
        
        # SYSTEM 2: Divergence
        if signal_type is None:
            if divergence == 'Bullish' and 'Bullish' in hist_momentum:
                signal_type = 'BUY'
                signal_source = 'S2_Div'
                quality = 3
                
                if near_support:
                    quality += 1
                    signal_source += '+Level'
            
            elif divergence == 'Bearish' and 'Bearish' in hist_momentum and not self.long_only:
                signal_type = 'SELL'
                signal_source = 'S2_Div'
                quality = 3
                
                if near_resistance:
                    quality += 1
                    signal_source += '+Level'
        
        # HISTOGRAM FLIP at key level (System 3 simplified)
        if signal_type is None:
            if hist_momentum == 'Bullish Flip' and macd_zone == 'Above Zero' and near_support:
                signal_type = 'BUY'
                signal_source = 'S3_Flip+Level'
                quality = 2
            
            elif hist_momentum == 'Bearish Flip' and macd_zone == 'Below Zero' and near_resistance and not self.long_only:
                signal_type = 'SELL'
                signal_source = 'S3_Flip+Level'
                quality = 2
        
        # No signal generated
        if signal_type is None or quality < 2:
            return None
        
        # =================================================================
        # CALCULATE ENTRY/STOP/TARGET
        # =================================================================
        
        lookback = 20
        
        if signal_type == 'BUY':
            entry_price = curr_price * 1.001  # Slight slippage
            recent_low = df['Low'].iloc[-lookback:].min()
            stop_loss = min(recent_low * 0.998, entry_price - curr_atr * 2)
            risk = entry_price - stop_loss
            target_1 = entry_price + risk * 1.5
            target_2 = entry_price + risk * 2.0
        else:
            entry_price = curr_price * 0.999
            recent_high = df['High'].iloc[-lookback:].max()
            stop_loss = max(recent_high * 1.002, entry_price + curr_atr * 2)
            risk = stop_loss - entry_price
            target_1 = entry_price - risk * 1.5
            target_2 = entry_price - risk * 2.0
        
        risk_pct = abs(risk / entry_price) * 100
        reward_pct = abs(target_2 - entry_price) / entry_price * 100
        risk_reward = reward_pct / risk_pct if risk_pct > 0 else 0
        
        # Create signal
        return Signal(
            symbol=symbol,
            signal_type=signal_type,
            signal_source=signal_source,
            quality=quality,
            current_price=round(curr_price, 2),
            entry_price=round(entry_price, 2),
            stop_loss=round(stop_loss, 2),
            target_1=round(target_1, 2),
            target_2=round(target_2, 2),
            risk_pct=round(risk_pct, 2),
            reward_pct=round(reward_pct, 2),
            risk_reward=round(risk_reward, 2),
            macd_value=round(curr_macd, 4),
            macd_signal=round(curr_signal, 4),
            histogram=round(curr_hist, 4),
            macd_position=macd_zone,
            divergence=divergence,
            histogram_momentum=hist_momentum,
            near_support=near_support,
            near_resistance=near_resistance
        )
    
    def scan(self, show_progress: bool = True) -> List[Signal]:
        """Scan all symbols and return signals"""
        self.signals = []
        total = len(self.symbols)
        
        print(f"\n{'='*70}")
        print(f"MACD 3-SYSTEM SIGNAL SCANNER")
        print(f"{'='*70}")
        print(f"Scanning {total} stocks... ({datetime.now().strftime('%Y-%m-%d %H:%M:%S')})")
        print(f"Mode: {'Long-Only' if self.long_only else 'Long/Short'}")
        print(f"{'='*70}\n")
        
        for i, symbol in enumerate(self.symbols):
            if show_progress:
                progress = (i + 1) / total * 100
                print(f"\rProgress: {progress:.0f}% ({i+1}/{total}) - {symbol:<20}", end='', flush=True)
            
            df = self.download_data(symbol)
            if df is None:
                continue
            
            signal = self.analyze_stock(symbol, df)
            if signal:
                self.signals.append(signal)
        
        # Sort by quality (descending)
        self.signals.sort(key=lambda x: x.quality, reverse=True)
        
        if show_progress:
            print(f"\n\nScan complete! Found {len(self.signals)} signals.\n")
        
        return self.signals
    
    def print_signals(self):
        """Print signals in a formatted table"""
        if not self.signals:
            print("No signals found matching criteria.")
            return
        
        print(f"\n{'='*120}")
        print(f"{'TRADING SIGNALS':^120}")
        print(f"{'='*120}")
        
        # Header
        print(f"{'Symbol':<15} {'Signal':<6} {'Quality':<8} {'Source':<20} {'Price':>10} {'Entry':>10} {'Stop':>10} {'Target':>10} {'R:R':>6} {'Level':<12}")
        print(f"{'-'*120}")
        
        for sig in self.signals:
            level = 'Support' if sig.near_support else ('Resistance' if sig.near_resistance else '-')
            quality_stars = '*' * sig.quality
            
            print(f"{sig.symbol:<15} {sig.signal_type:<6} {quality_stars:<8} {sig.signal_source:<20} "
                  f"{sig.current_price:>10.2f} {sig.entry_price:>10.2f} {sig.stop_loss:>10.2f} "
                  f"{sig.target_2:>10.2f} {sig.risk_reward:>6.2f} {level:<12}")
        
        print(f"{'-'*120}")
        print(f"Total signals: {len(self.signals)} | Quality: ***** = Best, ** = Minimum")
        print(f"{'='*120}\n")
    
    def print_detailed_signals(self, top_n: int = 10):
        """Print detailed information for top signals"""
        if not self.signals:
            print("No signals found.")
            return
        
        print(f"\n{'='*80}")
        print(f"{'TOP TRADE IDEAS (Detailed)':^80}")
        print(f"{'='*80}\n")
        
        for i, sig in enumerate(self.signals[:top_n], 1):
            print(f"{'-'*80}")
            print(f"#{i} {sig.symbol} - {sig.signal_type} (Quality: {'*' * sig.quality})")
            print(f"{'-'*80}")
            print(f"   Signal Source:    {sig.signal_source}")
            print(f"   Current Price:    Rs.{sig.current_price:,.2f}")
            print(f"   Entry Price:      Rs.{sig.entry_price:,.2f}")
            print(f"   Stop Loss:        Rs.{sig.stop_loss:,.2f} ({sig.risk_pct:.1f}% risk)")
            print(f"   Target 1 (1.5R):  Rs.{sig.target_1:,.2f}")
            print(f"   Target 2 (2R):    Rs.{sig.target_2:,.2f}")
            print(f"   Risk:Reward:      1:{sig.risk_reward:.1f}")
            print(f"")
            print(f"   MACD Analysis:")
            print(f"   - MACD Position:  {sig.macd_position}")
            print(f"   - Divergence:     {sig.divergence}")
            print(f"   - Histogram:      {sig.histogram_momentum}")
            print(f"   - Near Support:   {'Yes' if sig.near_support else 'No'}")
            print(f"   - Near Resistance:{'Yes' if sig.near_resistance else 'No'}")
            print(f"")
            
            # Trading notes
            print(f"   Trading Notes:")
            if 'S1' in sig.signal_source:
                print(f"   - Trend trade: MACD crossover confirmed with price in trend zone")
            if 'S2' in sig.signal_source:
                print(f"   - Divergence trade: Price/MACD divergence detected (reversal signal)")
            if 'Flip' in sig.signal_source:
                print(f"   - Histogram flip at key level (momentum shift)")
            if sig.near_support:
                print(f"   - Price near support level - good risk:reward for longs")
            if sig.near_resistance:
                print(f"   - Price near resistance - be cautious with longs")
            print(f"")
    
    def save_to_csv(self, filename: str = None):
        """Save signals to CSV file"""
        if not self.signals:
            print("No signals to save.")
            return
        
        filename = filename or f"macd_signals_{datetime.now().strftime('%Y%m%d_%H%M')}.csv"
        
        data = [sig.to_dict() for sig in self.signals]
        df = pd.DataFrame(data)
        df.to_csv(filename, index=False)
        
        print(f"Signals saved to: {filename}")
        return filename
    
    def get_watchlist_signals(self, watchlist: List[str]) -> List[Signal]:
        """Scan only watchlist stocks"""
        original_symbols = self.symbols
        self.symbols = [s if '.NS' in s else f"{s}.NS" for s in watchlist]
        signals = self.scan()
        self.symbols = original_symbols
        return signals


# =============================================================================
# MAIN EXECUTION
# =============================================================================

def main():
    parser = argparse.ArgumentParser(description='MACD 3-System Signal Scanner')
    parser.add_argument('--nifty50', action='store_true', help='Scan only Nifty 50')
    parser.add_argument('--nifty100', action='store_true', help='Scan Nifty 100')
    parser.add_argument('--all', action='store_true', help='Scan all 130+ stocks')
    parser.add_argument('--watchlist', nargs='+', help='Custom watchlist of symbols')
    parser.add_argument('--refresh', type=int, default=0, help='Auto-refresh interval in minutes (0=once)')
    parser.add_argument('--long-short', action='store_true', help='Enable short signals (default: long-only)')
    parser.add_argument('--save', action='store_true', help='Save signals to CSV')
    parser.add_argument('--top', type=int, default=10, help='Show top N detailed signals')
    
    args = parser.parse_args()
    
    # Select stock universe
    if args.watchlist:
        symbols = [s if '.NS' in s else f"{s}.NS" for s in args.watchlist]
    elif args.nifty50:
        symbols = NIFTY_50
    elif args.nifty100:
        symbols = NIFTY_50 + NIFTY_NEXT_50
    elif args.all:
        symbols = ALL_STOCKS
    else:
        symbols = NIFTY_50  # Default
    
    # Create scanner
    scanner = MACDSignalScanner(symbols=symbols, long_only=not args.long_short)
    
    # Run scan
    while True:
        signals = scanner.scan()
        
        # Display results
        scanner.print_signals()
        scanner.print_detailed_signals(top_n=args.top)
        
        # Save if requested
        if args.save:
            scanner.save_to_csv()
        
        # Check for refresh
        if args.refresh <= 0:
            break
        
        print(f"\nNext scan in {args.refresh} minutes... (Press Ctrl+C to stop)")
        try:
            time.sleep(args.refresh * 60)
        except KeyboardInterrupt:
            print("\nScanner stopped.")
            break
    
    # Summary
    print("\n" + "="*70)
    print("SCANNER SUMMARY")
    print("="*70)
    print(f"""
Stocks Scanned: {len(symbols)}
Signals Found:  {len(scanner.signals)}
Mode:           {'Long-Only' if scanner.long_only else 'Long/Short'}

Signal Breakdown:
""")
    
    if scanner.signals:
        sources = {}
        for sig in scanner.signals:
            base_source = sig.signal_source.split('+')[0]
            sources[base_source] = sources.get(base_source, 0) + 1
        
        for source, count in sorted(sources.items(), key=lambda x: -x[1]):
            print(f"  {source:<15}: {count} signals")
        
        print(f"""
Quality Distribution:
  *****  (5): {sum(1 for s in scanner.signals if s.quality == 5)} signals
  ****   (4): {sum(1 for s in scanner.signals if s.quality == 4)} signals
  ***    (3): {sum(1 for s in scanner.signals if s.quality == 3)} signals
  **     (2): {sum(1 for s in scanner.signals if s.quality == 2)} signals
""")
    
    print("="*70)
    print("\nUsage Tips:")
    print("  - Focus on Quality 4-5 signals (more confirmations)")
    print("  - Divergence signals (S2) are typically higher quality")
    print("  - Signals at key levels (+Level) have better R:R")
    print("  - Always verify with your own chart analysis")
    print("  - Use 2% max risk per trade")
    print("="*70)
    
    return scanner.signals


if __name__ == "__main__":
    signals = main()
