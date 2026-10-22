"""
MACD 3-SYSTEM MULTI-TIMEFRAME STRATEGY V2
==========================================

Practical implementation using Weekly + Daily timeframes.

ISSUE WITH V1: 4H data only available for 60 days, too restrictive.

V2 APPROACH:
  - Weekly Chart: Overall trend bias (MACD above/below zero)
  - Daily Chart: Setup + Entry (crossover/divergence + histogram)
  - Use Weekly as FILTER, Daily for actual signals
  
This is more practical and still captures the essence of MTF analysis.
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
class MTFV2Config:
    """MTF V2 Configuration"""
    # MACD Parameters
    fast_period: int = 12
    slow_period: int = 26
    signal_period: int = 9
    
    # Signal requirements
    require_weekly_alignment: bool = True
    require_histogram_confirm: bool = True
    
    # Risk Management
    risk_pct: float = 0.02
    risk_reward: float = 2.0
    max_position_pct: float = 0.25
    
    # Costs
    slippage: float = 0.001
    commission: float = 0.001
    
    # Mode
    long_only: bool = True


# =============================================================================
# DATA & INDICATORS
# =============================================================================

def download_weekly_daily(symbol: str) -> Tuple[Optional[pd.DataFrame], Optional[pd.DataFrame]]:
    """Download Weekly and Daily data"""
    try:
        weekly = yf.download(symbol, period='2y', interval='1wk', progress=False)
        if isinstance(weekly.columns, pd.MultiIndex):
            weekly.columns = weekly.columns.get_level_values(0)
        
        daily = yf.download(symbol, period='2y', interval='1d', progress=False)
        if isinstance(daily.columns, pd.MultiIndex):
            daily.columns = daily.columns.get_level_values(0)
        
        if weekly.empty or len(weekly) < 20 or daily.empty or len(daily) < 100:
            return None, None
        
        return weekly, daily
    except:
        return None, None


def calculate_macd(prices: pd.Series, fast: int = 12, slow: int = 26, 
                   signal: int = 9) -> Tuple[pd.Series, pd.Series, pd.Series]:
    """Calculate MACD"""
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
# WEEKLY ANALYSIS
# =============================================================================

def analyze_weekly(weekly_df: pd.DataFrame, config: MTFV2Config) -> pd.DataFrame:
    """Analyze weekly timeframe for trend bias"""
    df = weekly_df.copy()
    
    df['MACD'], df['Signal'], df['Histogram'] = calculate_macd(
        df['Close'], config.fast_period, config.slow_period, config.signal_period
    )
    
    # Bias
    df['Bias'] = 0
    df.loc[df['MACD'] > 0, 'Bias'] = 1
    df.loc[df['MACD'] < 0, 'Bias'] = -1
    
    # Trend strength
    macd_std = df['MACD'].rolling(20).std()
    df['Trend_Strength'] = abs(df['MACD']) / macd_std.replace(0, np.nan)
    df['Strong_Trend'] = df['Trend_Strength'] > 1.0
    
    # Weekly momentum (histogram direction)
    df['Weekly_Momentum'] = 0
    df.loc[df['Histogram'] > df['Histogram'].shift(1), 'Weekly_Momentum'] = 1
    df.loc[df['Histogram'] < df['Histogram'].shift(1), 'Weekly_Momentum'] = -1
    
    return df


# =============================================================================
# DAILY ANALYSIS WITH WEEKLY FILTER
# =============================================================================

def analyze_daily_with_weekly(daily_df: pd.DataFrame, weekly_df: pd.DataFrame, 
                               config: MTFV2Config) -> pd.DataFrame:
    """Analyze daily with weekly filter applied"""
    df = daily_df.copy()
    
    # Calculate daily indicators
    df['MACD'], df['Signal'], df['Histogram'] = calculate_macd(
        df['Close'], config.fast_period, config.slow_period, config.signal_period
    )
    df['ATR'] = calculate_atr(df['High'], df['Low'], df['Close'])
    
    # Map weekly bias to each daily bar
    df['Weekly_Bias'] = 0
    df['Weekly_Strong'] = False
    df['Weekly_Momentum'] = 0
    
    for idx in df.index:
        date = idx.date() if hasattr(idx, 'date') else pd.Timestamp(idx).date()
        weekly_before = weekly_df[weekly_df.index.date <= date]
        if len(weekly_before) > 0:
            latest = weekly_before.iloc[-1]
            df.loc[idx, 'Weekly_Bias'] = latest['Bias']
            df.loc[idx, 'Weekly_Strong'] = latest['Strong_Trend']
            df.loc[idx, 'Weekly_Momentum'] = latest['Weekly_Momentum']
    
    # Daily crossover
    df['Crossover'] = 0
    bullish = (df['MACD'] > df['Signal']) & (df['MACD'].shift(1) <= df['Signal'].shift(1))
    bearish = (df['MACD'] < df['Signal']) & (df['MACD'].shift(1) >= df['Signal'].shift(1))
    df.loc[bullish, 'Crossover'] = 1
    df.loc[bearish, 'Crossover'] = -1
    
    # Histogram flip
    df['Hist_Flip'] = 0
    df.loc[(df['Histogram'] > 0) & (df['Histogram'].shift(1) <= 0), 'Hist_Flip'] = 1
    df.loc[(df['Histogram'] < 0) & (df['Histogram'].shift(1) >= 0), 'Hist_Flip'] = -1
    
    # MACD zone
    macd_std = df['MACD'].rolling(50).std()
    df['Above_Zero'] = df['MACD'] > 0
    df['Below_Zero'] = df['MACD'] < 0
    df['Far_From_Zero'] = abs(df['MACD']) > macd_std * 0.5
    
    # Divergence
    df['Bullish_Div'] = 0
    df['Bearish_Div'] = 0
    lookback = 20
    
    for i in range(lookback, len(df)):
        h = df['High'].iloc[i-lookback:i+1]
        l = df['Low'].iloc[i-lookback:i+1]
        m = df['MACD'].iloc[i-lookback:i+1]
        
        mid = lookback // 2
        # Bearish div
        if h.iloc[-1] > h.iloc[mid] and m.iloc[-1] < m.iloc[mid]:
            df.iloc[i, df.columns.get_loc('Bearish_Div')] = 1
        # Bullish div
        if l.iloc[-1] < l.iloc[mid] and m.iloc[-1] > m.iloc[mid]:
            df.iloc[i, df.columns.get_loc('Bullish_Div')] = 1
    
    # Generate MTF signals
    df['MTF_Signal'] = 0
    df['Signal_Type'] = ''
    df['Signal_Quality'] = 0
    
    for i in range(50, len(df)):
        idx = df.index[i]
        
        weekly_bias = df.loc[idx, 'Weekly_Bias']
        weekly_strong = df.loc[idx, 'Weekly_Strong']
        weekly_mom = df.loc[idx, 'Weekly_Momentum']
        crossover = df.loc[idx, 'Crossover']
        hist_flip = df.loc[idx, 'Hist_Flip']
        above_zero = df.loc[idx, 'Above_Zero']
        below_zero = df.loc[idx, 'Below_Zero']
        far_from_zero = df.loc[idx, 'Far_From_Zero']
        bull_div = df.loc[idx, 'Bullish_Div']
        bear_div = df.loc[idx, 'Bearish_Div']
        
        signal = 0
        sig_type = ''
        quality = 0
        
        # =====================================================================
        # LONG SIGNALS
        # =====================================================================
        
        # System 1: Trend continuation
        # Daily crossover up + Above zero + Far from zero
        if crossover == 1 and above_zero and far_from_zero:
            signal = 1
            sig_type = 'S1_Trend'
            quality = 2
            
            # Weekly alignment bonus
            if weekly_bias == 1:
                quality += 1
                sig_type = 'MTF_S1_Trend'
                
                if weekly_strong:
                    quality += 1
                    sig_type += '+Strong'
                
                if weekly_mom == 1:
                    quality += 1
                    sig_type += '+Mom'
            
            # Histogram confirmation
            if hist_flip == 1:
                quality += 1
        
        # System 2: Divergence reversal
        if signal == 0 and bull_div == 1:
            # Check histogram is improving
            if df['Histogram'].iloc[i] > df['Histogram'].iloc[i-1]:
                signal = 1
                sig_type = 'S2_Div'
                quality = 2
                
                if weekly_bias == 1:
                    quality += 1
                    sig_type = 'MTF_S2_Div'
                
                if hist_flip == 1:
                    quality += 1
        
        # =====================================================================
        # SHORT SIGNALS (if not long_only)
        # =====================================================================
        if signal == 0 and not config.long_only:
            # System 1: Trend continuation short
            if crossover == -1 and below_zero and far_from_zero:
                signal = -1
                sig_type = 'S1_Trend'
                quality = 2
                
                if weekly_bias == -1:
                    quality += 1
                    sig_type = 'MTF_S1_Trend'
                    
                    if weekly_strong:
                        quality += 1
                        sig_type += '+Strong'
                
                if hist_flip == -1:
                    quality += 1
            
            # System 2: Divergence short
            if signal == 0 and bear_div == 1:
                if df['Histogram'].iloc[i] < df['Histogram'].iloc[i-1]:
                    signal = -1
                    sig_type = 'S2_Div'
                    quality = 2
                    
                    if weekly_bias == -1:
                        quality += 1
                        sig_type = 'MTF_S2_Div'
        
        # Only accept signals with weekly alignment when required
        if config.require_weekly_alignment:
            if signal == 1 and weekly_bias != 1:
                signal = 0
                sig_type = ''
                quality = 0
            elif signal == -1 and weekly_bias != -1:
                signal = 0
                sig_type = ''
                quality = 0
        
        df.loc[idx, 'MTF_Signal'] = signal
        df.loc[idx, 'Signal_Type'] = sig_type
        df.loc[idx, 'Signal_Quality'] = quality
    
    return df


# =============================================================================
# BACKTESTING
# =============================================================================

def backtest_mtf_v2(symbol: str, config: MTFV2Config = None) -> Optional[Dict]:
    """Backtest MTF V2 strategy"""
    config = config or MTFV2Config()
    
    # Download data
    weekly, daily = download_weekly_daily(symbol)
    if weekly is None or daily is None:
        return None
    
    # Process weekly
    weekly = analyze_weekly(weekly, config)
    
    # Process daily with weekly filter
    df = analyze_daily_with_weekly(daily, weekly, config)
    
    # Backtest
    trades = []
    capital = 100000
    current_capital = capital
    position = None
    
    for i in range(50, len(df)):
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
                # Opposite signal exit
                elif row['MTF_Signal'] == -1 and row['Signal_Quality'] >= 3:
                    exit_price = row['Close'] * (1 - config.slippage)
                    exit_triggered = True
                    exit_reason = 'signal_reverse'
                # Trail stop
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
                elif row['MTF_Signal'] == 1 and row['Signal_Quality'] >= 3:
                    exit_price = row['Close'] * (1 + config.slippage)
                    exit_triggered = True
                    exit_reason = 'signal_reverse'
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
            quality = row.get('Signal_Quality', 0)
            sig_type = row.get('Signal_Type', '')
            
            # Take signals with quality >= 2
            if signal != 0 and quality >= 2:
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
    
    # Results
    trades_df = pd.DataFrame(trades) if trades else pd.DataFrame()
    total_return = (current_capital / capital - 1) * 100
    
    results = {
        'symbol': symbol,
        'strategy': 'MACD MTF V2',
        'total_return': total_return,
        'total_trades': len(trades_df),
    }
    
    if len(trades_df) > 0:
        wins = trades_df[trades_df['pnl'] > 0]
        losses = trades_df[trades_df['pnl'] < 0]
        
        results['win_rate'] = len(wins) / len(trades_df)
        results['avg_trade'] = trades_df['return_pct'].mean()
        results['profit_factor'] = abs(wins['pnl'].sum() / losses['pnl'].sum()) if len(losses) > 0 and losses['pnl'].sum() != 0 else float('inf')
        results['exits'] = trades_df['exit_reason'].value_counts().to_dict()
        results['signal_types'] = trades_df['signal_type'].value_counts().to_dict()
        
        # MTF vs non-MTF trades
        mtf_trades = trades_df[trades_df['signal_type'].str.contains('MTF', na=False)]
        non_mtf_trades = trades_df[~trades_df['signal_type'].str.contains('MTF', na=False)]
        
        results['mtf_trades'] = len(mtf_trades)
        results['mtf_win_rate'] = len(mtf_trades[mtf_trades['pnl'] > 0]) / len(mtf_trades) if len(mtf_trades) > 0 else 0
        results['non_mtf_trades'] = len(non_mtf_trades)
        results['non_mtf_win_rate'] = len(non_mtf_trades[non_mtf_trades['pnl'] > 0]) / len(non_mtf_trades) if len(non_mtf_trades) > 0 else 0
    else:
        results['win_rate'] = 0
        results['avg_trade'] = 0
        results['profit_factor'] = 0
        results['exits'] = {}
        results['signal_types'] = {}
        results['mtf_trades'] = 0
        results['mtf_win_rate'] = 0
        results['non_mtf_trades'] = 0
        results['non_mtf_win_rate'] = 0
    
    results['trades_df'] = trades_df
    
    return results


# =============================================================================
# MAIN
# =============================================================================

def run_mtf_v2_backtest():
    """Run MTF V2 backtest"""
    
    symbols = [
        'RELIANCE.NS', 'TCS.NS', 'HDFCBANK.NS', 'INFY.NS', 'SBIN.NS',
        'BHARTIARTL.NS', 'ITC.NS', 'AXISBANK.NS', 'LT.NS', 'MARUTI.NS',
        'SUNPHARMA.NS', 'TITAN.NS', 'WIPRO.NS', 'BAJFINANCE.NS', 'ICICIBANK.NS'
    ]
    
    print("=" * 100)
    print("MACD 3-SYSTEM MTF V2: WEEKLY + DAILY (PRACTICAL APPROACH)")
    print("=" * 100)
    print("""
TIMEFRAME HIERARCHY:
  - Weekly Chart: Trend bias FILTER (MACD above/below zero)
  - Daily Chart:  Setup + Entry (crossover/divergence + histogram)

SIGNAL GENERATION:
  - Daily crossover in direction of weekly bias
  - Weekly momentum alignment adds quality
  - Weekly strong trend adds quality
  
QUALITY LEVELS:
  5+: MTF + Strong weekly trend + Histogram confirm
  4:  MTF + Weekly momentum aligned
  3:  MTF aligned (weekly + daily same direction)
  2:  Daily signal only (no MTF alignment)
    """)
    
    all_results = []
    
    # Test with weekly alignment required
    print("\n" + "=" * 100)
    print("TEST 1: WEEKLY ALIGNMENT REQUIRED (Strict MTF)")
    print("=" * 100)
    
    config_strict = MTFV2Config(require_weekly_alignment=True)
    
    print(f"\n{'-'*100}")
    print(f"{'Symbol':<15} | {'Return':>8} | {'Trades':>6} | {'Win%':>6} | {'PF':>6} | {'MTF Trades':>10} | {'MTF Win%':>8}")
    print(f"{'-'*100}")
    
    strict_results = []
    for symbol in symbols:
        try:
            result = backtest_mtf_v2(symbol, config_strict)
            if result is None:
                print(f"{symbol:<15} | Skip")
                continue
            
            strict_results.append(result)
            status = "OK" if result['total_return'] > 0 else "--"
            pf = min(result['profit_factor'], 99.9) if result['profit_factor'] != float('inf') else 99.9
            
            print(f"{symbol:<15} | {result['total_return']:>7.2f}% | {result['total_trades']:>6} | "
                  f"{result['win_rate']:>5.1%} | {pf:>6.2f} | {result['mtf_trades']:>10} | "
                  f"{result['mtf_win_rate']:>7.1%} {status}")
        except Exception as e:
            print(f"{symbol:<15} | Error: {str(e)[:40]}")
    
    # Summary for strict
    if strict_results:
        returns = [r['total_return'] for r in strict_results]
        profitable = sum(1 for r in strict_results if r['total_return'] > 0)
        total_trades = sum(r['total_trades'] for r in strict_results)
        
        print(f"\n{'-'*100}")
        print(f"STRICT MTF Summary: Avg={np.mean(returns):.2f}%, Profitable={profitable}/{len(strict_results)}, Trades={total_trades}")
    
    # Test without weekly alignment (for comparison)
    print("\n" + "=" * 100)
    print("TEST 2: WEEKLY ALIGNMENT OPTIONAL (Relaxed)")
    print("=" * 100)
    
    config_relaxed = MTFV2Config(require_weekly_alignment=False)
    
    print(f"\n{'-'*100}")
    print(f"{'Symbol':<15} | {'Return':>8} | {'Trades':>6} | {'Win%':>6} | {'PF':>6} | {'MTF Trades':>10} | {'Non-MTF':>8}")
    print(f"{'-'*100}")
    
    relaxed_results = []
    for symbol in symbols:
        try:
            result = backtest_mtf_v2(symbol, config_relaxed)
            if result is None:
                print(f"{symbol:<15} | Skip")
                continue
            
            relaxed_results.append(result)
            status = "OK" if result['total_return'] > 0 else "--"
            pf = min(result['profit_factor'], 99.9) if result['profit_factor'] != float('inf') else 99.9
            
            print(f"{symbol:<15} | {result['total_return']:>7.2f}% | {result['total_trades']:>6} | "
                  f"{result['win_rate']:>5.1%} | {pf:>6.2f} | {result['mtf_trades']:>10} | "
                  f"{result['non_mtf_trades']:>8} {status}")
        except Exception as e:
            print(f"{symbol:<15} | Error: {str(e)[:40]}")
    
    # Summary for relaxed
    if relaxed_results:
        returns = [r['total_return'] for r in relaxed_results]
        profitable = sum(1 for r in relaxed_results if r['total_return'] > 0)
        total_trades = sum(r['total_trades'] for r in relaxed_results)
        
        print(f"\n{'-'*100}")
        print(f"RELAXED Summary: Avg={np.mean(returns):.2f}%, Profitable={profitable}/{len(relaxed_results)}, Trades={total_trades}")
    
    # Final comparison
    print("\n" + "=" * 100)
    print("COMPARISON: STRICT MTF vs RELAXED vs DAILY-ONLY (V3)")
    print("=" * 100)
    
    if strict_results and relaxed_results:
        strict_avg = np.mean([r['total_return'] for r in strict_results])
        relaxed_avg = np.mean([r['total_return'] for r in relaxed_results])
        strict_prof = sum(1 for r in strict_results if r['total_return'] > 0)
        relaxed_prof = sum(1 for r in relaxed_results if r['total_return'] > 0)
        strict_trades = sum(r['total_trades'] for r in strict_results)
        relaxed_trades = sum(r['total_trades'] for r in relaxed_results)
        
        print(f"""
| Metric          | Strict MTF    | Relaxed       | Daily-Only V3 |
|-----------------|---------------|---------------|---------------|
| Avg Return      | {strict_avg:>12.2f}% | {relaxed_avg:>12.2f}% | +0.39%        |
| Profitable      | {strict_prof}/{len(strict_results)} ({strict_prof/len(strict_results)*100:.0f}%)     | {relaxed_prof}/{len(relaxed_results)} ({relaxed_prof/len(relaxed_results)*100:.0f}%)     | 8/15 (53%)    |
| Total Trades    | {strict_trades:>13} | {relaxed_trades:>13} | 45            |
""")
    
    print("""
KEY FINDINGS:

1. STRICT MTF (Weekly required):
   - Fewer trades (weekly filter is restrictive)
   - May miss good setups during weekly trend transitions
   
2. RELAXED (Weekly optional):
   - More trades
   - Can compare MTF vs non-MTF trade performance
   
3. RECOMMENDATIONS:
   - Use weekly as BIAS FILTER, not hard requirement
   - Enter on daily signals that align with weekly
   - Increase position size when weekly confirms
   - Reduce size or skip when weekly is against
""")
    
    return strict_results, relaxed_results


if __name__ == "__main__":
    strict, relaxed = run_mtf_v2_backtest()
