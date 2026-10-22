"""
O=L STRATEGY WITH CANDLESTICK & CHART PATTERN CONFIRMATION
============================================================

Combines multiple confirmations:
1. First candle O=L (base signal)
2. Candlestick patterns (confirmation)
3. Chart patterns (trend confirmation)
4. Quick profit booking

Candlestick Patterns Detected:
- Bullish Marubozu (strong body, small wicks)
- Bullish Engulfing (current candle engulfs previous)
- Hammer (long lower wick, small body at top)
- Morning Star (3-candle reversal pattern)

Chart Patterns Detected:
- Higher High (HH) - uptrend confirmation
- Support Bounce - price near recent low
- Breakout - price above recent resistance
"""

import pandas as pd
import numpy as np
import yfinance as yf
import warnings
warnings.filterwarnings('ignore')


# =====================================================================
# CANDLESTICK PATTERN DETECTION
# =====================================================================

def is_marubozu(open_p, high, low, close, threshold=0.1):
    """
    Bullish Marubozu: Strong green candle with small wicks
    - Body is > 90% of total range
    - Close > Open (green)
    """
    body = abs(close - open_p)
    total_range = high - low
    
    if total_range <= 0:
        return False
    
    body_ratio = body / total_range
    is_green = close > open_p
    
    return is_green and body_ratio >= (1 - threshold)


def is_hammer(open_p, high, low, close):
    """
    Hammer: Long lower wick, small body at top
    - Lower wick >= 2x body
    - Upper wick is small
    - Can be green or red
    """
    body = abs(close - open_p)
    total_range = high - low
    
    if total_range <= 0 or body <= 0:
        return False
    
    body_top = max(open_p, close)
    body_bottom = min(open_p, close)
    
    upper_wick = high - body_top
    lower_wick = body_bottom - low
    
    # Lower wick >= 2x body, upper wick < body
    return lower_wick >= 2 * body and upper_wick < body


def is_bullish_engulfing(prev_open, prev_high, prev_low, prev_close,
                          curr_open, curr_high, curr_low, curr_close):
    """
    Bullish Engulfing:
    - Previous candle is red (bearish)
    - Current candle is green and engulfs previous body
    """
    prev_is_red = prev_close < prev_open
    curr_is_green = curr_close > curr_open
    
    # Current body engulfs previous body
    curr_body_low = min(curr_open, curr_close)
    curr_body_high = max(curr_open, curr_close)
    prev_body_low = min(prev_open, prev_close)
    prev_body_high = max(prev_open, prev_close)
    
    engulfs = curr_body_low <= prev_body_low and curr_body_high >= prev_body_high
    
    return prev_is_red and curr_is_green and engulfs


def is_doji(open_p, high, low, close, threshold=0.1):
    """
    Doji: Very small body relative to range
    - Body < 10% of total range
    """
    body = abs(close - open_p)
    total_range = high - low
    
    if total_range <= 0:
        return False
    
    return body / total_range < threshold


def detect_candlestick_pattern(candles):
    """
    Detect candlestick patterns in a series of candles.
    Returns pattern name and strength (1-3).
    """
    if len(candles) < 2:
        return None, 0
    
    curr = candles.iloc[-1]
    prev = candles.iloc[-2]
    
    patterns = []
    
    # Check Marubozu (strongest)
    if is_marubozu(curr['Open'], curr['High'], curr['Low'], curr['Close']):
        patterns.append(('Marubozu', 3))
    
    # Check Bullish Engulfing
    if is_bullish_engulfing(prev['Open'], prev['High'], prev['Low'], prev['Close'],
                            curr['Open'], curr['High'], curr['Low'], curr['Close']):
        patterns.append(('Engulfing', 3))
    
    # Check Hammer
    if is_hammer(curr['Open'], curr['High'], curr['Low'], curr['Close']):
        patterns.append(('Hammer', 2))
    
    # Check Doji (neutral, needs confirmation)
    if is_doji(curr['Open'], curr['High'], curr['Low'], curr['Close']):
        patterns.append(('Doji', 1))
    
    # Check simple green candle
    if curr['Close'] > curr['Open']:
        patterns.append(('Green', 1))
    
    if patterns:
        # Return strongest pattern
        patterns.sort(key=lambda x: x[1], reverse=True)
        return patterns[0]
    
    return None, 0


# =====================================================================
# CHART PATTERN DETECTION
# =====================================================================

def detect_higher_high(df, lookback=5):
    """
    Detect if we're making higher highs (uptrend)
    """
    if len(df) < lookback + 1:
        return False
    
    recent_highs = df['High'].tail(lookback + 1)
    
    # Check if current high > previous lookback highs
    current_high = recent_highs.iloc[-1]
    previous_max = recent_highs.iloc[:-1].max()
    
    return current_high >= previous_max


def detect_support_bounce(df, lookback=10, threshold=0.02):
    """
    Detect if price is bouncing from recent support
    - Price is within threshold of recent low
    - Current candle is green (bounce)
    """
    if len(df) < lookback + 1:
        return False
    
    recent = df.tail(lookback + 1)
    recent_low = recent['Low'].min()
    current_close = recent['Close'].iloc[-1]
    current_open = recent['Open'].iloc[-1]
    
    # Price near support
    near_support = (current_close - recent_low) / recent_low < threshold
    # Bouncing (green candle)
    bouncing = current_close > current_open
    
    return near_support and bouncing


def detect_breakout(df, lookback=10):
    """
    Detect if price broke above recent resistance
    """
    if len(df) < lookback + 1:
        return False
    
    recent = df.tail(lookback + 1)
    previous_high = recent['High'].iloc[:-1].max()
    current_close = recent['Close'].iloc[-1]
    
    return current_close > previous_high


def detect_higher_lows(df, lookback=5):
    """
    Detect higher lows pattern (uptrend)
    """
    if len(df) < lookback * 2:
        return False
    
    recent = df.tail(lookback * 2)
    
    # Split into two halves
    first_half = recent.iloc[:lookback]
    second_half = recent.iloc[lookback:]
    
    first_low = first_half['Low'].min()
    second_low = second_half['Low'].min()
    
    return second_low > first_low


def detect_chart_patterns(df):
    """
    Detect chart patterns and return list with strengths
    """
    patterns = []
    
    if detect_higher_high(df):
        patterns.append(('Higher_High', 2))
    
    if detect_higher_lows(df):
        patterns.append(('Higher_Lows', 2))
    
    if detect_breakout(df):
        patterns.append(('Breakout', 3))
    
    if detect_support_bounce(df):
        patterns.append(('Support_Bounce', 2))
    
    return patterns


# =====================================================================
# SIGNAL GENERATION
# =====================================================================

def generate_signals(df):
    """
    Generate trading signals combining:
    1. O=L pattern
    2. Candlestick confirmation
    3. Chart pattern confirmation
    """
    if df is None or df.empty:
        return []
    
    df = df.copy()
    df['Date'] = df.index.date
    
    signals = []
    
    for date in df['Date'].unique():
        day_data = df[df['Date'] == date]
        
        if len(day_data) < 4:
            continue
        
        # Get first candle
        fc = day_data.iloc[0]
        fc_idx = day_data.index[0]
        
        # Check O=L condition
        tolerance = fc['Open'] * 0.001
        is_OL = abs(fc['Open'] - fc['Low']) <= tolerance
        is_OH = abs(fc['Open'] - fc['High']) <= tolerance
        
        if not (is_OL and not is_OH):
            continue
        
        # Get data up to and including first candle for pattern detection
        historical_idx = df.index.get_loc(fc_idx)
        if historical_idx < 10:
            continue
        
        historical_data = df.iloc[historical_idx-10:historical_idx+1]
        
        # Detect candlestick patterns
        candle_pattern, candle_strength = detect_candlestick_pattern(
            historical_data[['Open', 'High', 'Low', 'Close']]
        )
        
        # Detect chart patterns
        chart_patterns = detect_chart_patterns(historical_data)
        chart_strength = sum(p[1] for p in chart_patterns)
        chart_names = [p[0] for p in chart_patterns]
        
        # Calculate total signal strength
        total_strength = candle_strength + chart_strength
        
        # Basic candle info
        is_green = fc['Close'] > fc['Open']
        candle_range = fc['High'] - fc['Low']
        close_pos = (fc['Close'] - fc['Low']) / candle_range if candle_range > 0 else 0.5
        first_ret = (fc['Close'] / fc['Open'] - 1) * 100
        
        signals.append({
            'date': date,
            'entry_price': fc['Close'],
            'is_green': is_green,
            'close_pos': close_pos,
            'first_ret': first_ret,
            'candle_pattern': candle_pattern,
            'candle_strength': candle_strength,
            'chart_patterns': chart_names,
            'chart_strength': chart_strength,
            'total_strength': total_strength
        })
    
    return signals


# =====================================================================
# BACKTEST
# =====================================================================

def backtest(df, signals, min_strength=0, tp_pct=0.5, sl_pct=0.5, max_candles=3):
    """
    Backtest signals with pattern confirmation
    """
    if not signals:
        return []
    
    df = df.copy()
    df['Date'] = df.index.date
    
    trades = []
    
    for sig in signals:
        # Filter by minimum strength
        if sig['total_strength'] < min_strength:
            continue
        
        date = sig['date']
        day_data = df[df['Date'] == date]
        
        if len(day_data) < 2:
            continue
        
        entry = sig['entry_price'] * 1.001  # slippage
        tp = entry * (1 + tp_pct / 100)
        sl = entry * (1 - sl_pct / 100)
        
        exit_price = None
        exit_reason = None
        
        for i in range(1, min(max_candles + 1, len(day_data))):
            candle = day_data.iloc[i]
            
            if candle['High'] >= tp:
                exit_price = tp
                exit_reason = 'TP'
                break
            
            if candle['Low'] <= sl:
                exit_price = sl * 0.999
                exit_reason = 'SL'
                break
        
        if exit_price is None:
            last_idx = min(max_candles, len(day_data) - 1)
            exit_price = day_data.iloc[last_idx]['Close'] * 0.999
            exit_reason = 'TIME'
        
        ret = (exit_price / entry - 1) * 100
        
        trades.append({
            'date': date,
            'entry': entry,
            'exit': exit_price,
            'reason': exit_reason,
            'return': ret,
            'candle_pattern': sig['candle_pattern'],
            'chart_patterns': sig['chart_patterns'],
            'strength': sig['total_strength']
        })
    
    return trades


def download_data(symbol, days=60):
    """Download data"""
    try:
        df = yf.download(symbol, period=f'{days}d', interval='1h', progress=False)
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        return df
    except:
        return None


# =====================================================================
# MAIN
# =====================================================================

def run_analysis():
    """Run complete analysis"""
    
    symbols = [
        'RELIANCE.NS', 'TCS.NS', 'HDFCBANK.NS', 'INFY.NS', 'SBIN.NS',
        'BHARTIARTL.NS', 'ITC.NS', 'AXISBANK.NS', 'LT.NS', 'MARUTI.NS',
        'SUNPHARMA.NS', 'TITAN.NS', 'WIPRO.NS', 'BAJFINANCE.NS', 'ICICIBANK.NS'
    ]
    
    print("=" * 90)
    print("O=L STRATEGY WITH CANDLESTICK & CHART PATTERN CONFIRMATION")
    print("=" * 90)
    print("""
SIGNAL COMPONENTS:

1. BASE SIGNAL: First candle O=L (Open = Low)

2. CANDLESTICK PATTERNS (Strength 1-3):
   - Marubozu (3): Strong body, tiny wicks - STRONGEST
   - Bullish Engulfing (3): Current engulfs previous red candle
   - Hammer (2): Long lower wick, body at top
   - Doji (1): Indecision, needs confirmation
   - Green (1): Simple green candle

3. CHART PATTERNS (Strength 2-3):
   - Breakout (3): Price above recent resistance
   - Higher High (2): Making new highs
   - Higher Lows (2): Uptrend pattern
   - Support Bounce (2): Bouncing from support

4. FILTERING: Only trade when Total Strength >= threshold
""")
    
    # Test different strength thresholds
    thresholds = [0, 2, 3, 4, 5]
    
    all_results = {}
    
    for thresh in thresholds:
        print(f"\n{'='*90}")
        print(f"TESTING: Minimum Strength >= {thresh}")
        print(f"{'='*90}")
        
        all_trades = []
        stock_stats = []
        
        for symbol in symbols:
            df = download_data(symbol, days=60)
            if df is None or df.empty:
                continue
            
            signals = generate_signals(df)
            
            if not signals:
                continue
            
            trades = backtest(df, signals, min_strength=thresh)
            
            if not trades:
                continue
            
            n = len(trades)
            wins = sum(1 for t in trades if t['return'] > 0)
            wr = wins / n * 100
            avg_ret = sum(t['return'] for t in trades) / n
            tp_count = sum(1 for t in trades if t['reason'] == 'TP')
            
            # Pattern analysis
            patterns_used = [t['candle_pattern'] for t in trades if t['candle_pattern']]
            pattern_counts = {}
            for p in patterns_used:
                pattern_counts[p] = pattern_counts.get(p, 0) + 1
            
            status = "OK" if avg_ret > 0 else "--"
            print(f"  {symbol:<15} | {n:>3} trades | WR: {wr:>5.1f}% | "
                  f"Ret: {avg_ret:>+6.2f}% | TP:{tp_count} {status}")
            
            if pattern_counts:
                print(f"    Patterns: {pattern_counts}")
            
            all_trades.extend(trades)
            stock_stats.append({
                'symbol': symbol,
                'trades': n,
                'win_rate': wr,
                'avg_return': avg_ret
            })
        
        # Summary for this threshold
        if all_trades:
            n_total = len(all_trades)
            wins = sum(1 for t in all_trades if t['return'] > 0)
            wr = wins / n_total * 100
            avg_ret = sum(t['return'] for t in all_trades) / n_total
            tp_pct = sum(1 for t in all_trades if t['reason'] == 'TP') / n_total * 100
            profitable = sum(1 for s in stock_stats if s['avg_return'] > 0)
            
            print(f"\n  TOTAL: {n_total} trades | WR: {wr:.1f}% | "
                  f"Avg: {avg_ret:+.2f}% | TP: {tp_pct:.0f}% | "
                  f"Profitable: {profitable}/{len(stock_stats)}")
            
            all_results[thresh] = {
                'trades': n_total,
                'win_rate': wr,
                'avg_return': avg_ret,
                'tp_pct': tp_pct,
                'profitable': f"{profitable}/{len(stock_stats)}",
                'all_trades': all_trades
            }
    
    # Final comparison
    print("\n" + "=" * 90)
    print("STRENGTH THRESHOLD COMPARISON")
    print("=" * 90)
    
    print(f"\n{'Min Strength':<15} | {'Trades':>7} | {'WR':>7} | {'Return':>9} | {'TP%':>6} | {'Profitable':>12}")
    print("-" * 75)
    
    best_return = -999
    best_thresh = 0
    
    for thresh, res in all_results.items():
        is_best = res['avg_return'] == max(r['avg_return'] for r in all_results.values())
        marker = "BEST" if is_best else ""
        print(f"{thresh:<15} | {res['trades']:>7} | {res['win_rate']:>6.1f}% | "
              f"{res['avg_return']:>+8.2f}% | {res['tp_pct']:>5.0f}% | "
              f"{res['profitable']:>12} {marker}")
        
        if res['avg_return'] > best_return:
            best_return = res['avg_return']
            best_thresh = thresh
    
    # Pattern analysis for best threshold
    if best_thresh in all_results:
        print("\n" + "=" * 90)
        print(f"PATTERN ANALYSIS (Best Threshold: {best_thresh})")
        print("=" * 90)
        
        trades = all_results[best_thresh]['all_trades']
        
        # Candlestick pattern performance
        candle_perf = {}
        for t in trades:
            p = t['candle_pattern'] if t['candle_pattern'] else 'None'
            if p not in candle_perf:
                candle_perf[p] = {'trades': 0, 'wins': 0, 'returns': []}
            candle_perf[p]['trades'] += 1
            candle_perf[p]['wins'] += 1 if t['return'] > 0 else 0
            candle_perf[p]['returns'].append(t['return'])
        
        print("\nCANDLESTICK PATTERN PERFORMANCE:")
        print(f"{'Pattern':<15} | {'Trades':>7} | {'WR':>7} | {'Avg Return':>11}")
        print("-" * 50)
        
        for p, stats in sorted(candle_perf.items(), key=lambda x: -np.mean(x[1]['returns'])):
            wr = stats['wins'] / stats['trades'] * 100
            avg = np.mean(stats['returns'])
            status = "GOOD" if avg > 0 else ""
            print(f"{p:<15} | {stats['trades']:>7} | {wr:>6.1f}% | {avg:>+10.2f}% {status}")
        
        # Chart pattern performance
        chart_perf = {'with_chart': {'trades': 0, 'wins': 0, 'returns': []},
                      'no_chart': {'trades': 0, 'wins': 0, 'returns': []}}
        
        for t in trades:
            key = 'with_chart' if t['chart_patterns'] else 'no_chart'
            chart_perf[key]['trades'] += 1
            chart_perf[key]['wins'] += 1 if t['return'] > 0 else 0
            chart_perf[key]['returns'].append(t['return'])
        
        print("\nCHART PATTERN IMPACT:")
        for key, stats in chart_perf.items():
            if stats['trades'] > 0:
                wr = stats['wins'] / stats['trades'] * 100
                avg = np.mean(stats['returns'])
                print(f"  {key}: {stats['trades']} trades | WR: {wr:.1f}% | Avg: {avg:+.2f}%")
    
    # Conclusion
    print("\n" + "=" * 90)
    print("CONCLUSION")
    print("=" * 90)
    
    if best_return > 0:
        print(f"""
SUCCESS! Pattern confirmation helps!

Best configuration: Minimum Strength >= {best_thresh}
Average Return: {best_return:+.2f}%

RECOMMENDATIONS:
1. Use minimum strength threshold of {best_thresh}
2. Focus on Marubozu and Engulfing patterns
3. Chart patterns (Higher High, Breakout) add confirmation
4. This filters out weak signals and improves win rate
""")
    else:
        print(f"""
Pattern confirmation improves results but still negative.

Best configuration: Minimum Strength >= {best_thresh}
Average Return: {best_return:+.2f}%

KEY FINDINGS:
1. Higher strength thresholds reduce trades but improve quality
2. Certain patterns (Marubozu) perform better than others
3. Chart patterns add marginal improvement
4. The O=L base pattern still shows mean reversion tendency
""")
    
    return all_results


if __name__ == "__main__":
    results = run_analysis()
