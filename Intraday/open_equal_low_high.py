"""
OPEN = LOW / OPEN = HIGH STRATEGY
==================================

Popular intraday pattern in Indian markets:

1. Open = Low (O=L): 
   - Stock opens at the day's low and never trades below opening price
   - Shows strong buying pressure from open
   - Signal: GO LONG

2. Open = High (O=H):
   - Stock opens at the day's high and never trades above opening price
   - Shows strong selling pressure from open
   - Signal: GO SHORT

Detection Logic:
- Check after first 15-30 minutes if O=L or O=H condition holds
- If confirmed, enter in the direction of the move
- Exit at end of day or on stop loss

This tests the hypothesis: Does early directional conviction persist?
"""

import pandas as pd
import numpy as np
import yfinance as yf
from datetime import datetime, timedelta
import warnings
warnings.filterwarnings('ignore')


def download_intraday_data(symbol, days=60):
    """Download intraday data (1h candles, max 730 days for Yahoo)"""
    try:
        df = yf.download(symbol, period=f'{days}d', interval='1h', progress=False)
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        return df
    except Exception as e:
        print(f"Error downloading {symbol}: {e}")
        return None


def identify_OL_OH_days(df):
    """
    Identify days where Open = Low or Open = High
    
    Returns DataFrame with daily analysis
    """
    if df is None or df.empty:
        return None
    
    # Add date column
    df = df.copy()
    df['Date'] = df.index.date
    df['Hour'] = df.index.hour
    
    daily_analysis = []
    
    for date in df['Date'].unique():
        day_data = df[df['Date'] == date].copy()
        
        if len(day_data) < 3:  # Need at least 3 hours of data
            continue
        
        # Get first candle (opening)
        first_candle = day_data.iloc[0]
        day_open = first_candle['Open']
        
        # Get day's high and low
        day_high = day_data['High'].max()
        day_low = day_data['Low'].min()
        day_close = day_data.iloc[-1]['Close']
        
        # Check O=L condition (with small tolerance for tick size)
        tolerance = day_open * 0.001  # 0.1% tolerance
        
        is_OL = abs(day_open - day_low) <= tolerance
        is_OH = abs(day_open - day_high) <= tolerance
        
        # Calculate day's return
        day_return = (day_close / day_open - 1) * 100
        
        # Direction
        if is_OL and not is_OH:
            pattern = 'O=L'
            expected_direction = 'bullish'
            correct = day_return > 0
        elif is_OH and not is_OL:
            pattern = 'O=H'
            expected_direction = 'bearish'
            correct = day_return < 0
        elif is_OL and is_OH:
            pattern = 'O=L=H'  # Rare: open = high = low (flat day)
            expected_direction = 'neutral'
            correct = None
        else:
            pattern = 'None'
            expected_direction = None
            correct = None
        
        daily_analysis.append({
            'date': date,
            'open': day_open,
            'high': day_high,
            'low': day_low,
            'close': day_close,
            'day_return': day_return,
            'pattern': pattern,
            'expected': expected_direction,
            'correct': correct,
            'candles': len(day_data)
        })
    
    return pd.DataFrame(daily_analysis)


def backtest_OL_OH_strategy(df, daily_analysis, 
                             entry_after_hours=1,  # Enter after N hours
                             stop_loss_pct=1.0,    # Stop loss %
                             capital=100000,
                             slippage=0.001,
                             commission=0.001):
    """
    Backtest the O=L / O=H strategy
    
    Logic:
    1. After first N hours, check if O=L or O=H holds
    2. If O=L: Go long, expect price to continue up
    3. If O=H: Go short, expect price to continue down
    4. Exit at end of day or on stop loss
    """
    if df is None or daily_analysis is None:
        return None
    
    df = df.copy()
    df['Date'] = df.index.date
    
    trades = []
    current_capital = capital
    
    for _, day_info in daily_analysis.iterrows():
        date = day_info['date']
        pattern = day_info['pattern']
        
        if pattern not in ['O=L', 'O=H']:
            continue
        
        day_data = df[df['Date'] == date].copy()
        
        if len(day_data) < entry_after_hours + 2:
            continue
        
        # Check if pattern still holds after N hours
        first_n_hours = day_data.iloc[:entry_after_hours + 1]
        check_open = day_data.iloc[0]['Open']
        check_low = first_n_hours['Low'].min()
        check_high = first_n_hours['High'].max()
        
        tolerance = check_open * 0.001
        
        still_OL = abs(check_open - check_low) <= tolerance
        still_OH = abs(check_open - check_high) <= tolerance
        
        # Entry decision
        if pattern == 'O=L' and still_OL and not still_OH:
            direction = 'long'
        elif pattern == 'O=H' and still_OH and not still_OL:
            direction = 'short'
        else:
            continue  # Pattern broken, skip
        
        # Entry price (after N hours)
        entry_candle = day_data.iloc[entry_after_hours]
        entry_price = entry_candle['Close'] * (1 + slippage if direction == 'long' else 1 - slippage)
        
        # Calculate position size (2% risk)
        stop_dist = entry_price * stop_loss_pct / 100
        risk = current_capital * 0.02
        shares = int(risk / stop_dist) if stop_dist > 0 else 0
        shares = min(shares, int(current_capital * 0.25 / entry_price))
        
        if shares <= 0:
            continue
        
        # Set stop loss
        if direction == 'long':
            stop_loss = entry_price * (1 - stop_loss_pct / 100)
        else:
            stop_loss = entry_price * (1 + stop_loss_pct / 100)
        
        # Simulate through rest of day
        exit_price = None
        exit_reason = None
        
        for i in range(entry_after_hours + 1, len(day_data)):
            candle = day_data.iloc[i]
            
            if direction == 'long':
                if candle['Low'] <= stop_loss:
                    exit_price = stop_loss * (1 - slippage)
                    exit_reason = 'stop_loss'
                    break
            else:
                if candle['High'] >= stop_loss:
                    exit_price = stop_loss * (1 + slippage)
                    exit_reason = 'stop_loss'
                    break
        
        # If no stop hit, exit at end of day
        if exit_price is None:
            last_candle = day_data.iloc[-1]
            exit_price = last_candle['Close'] * (1 - slippage if direction == 'long' else 1 + slippage)
            exit_reason = 'eod'
        
        # Calculate P&L
        if direction == 'long':
            pnl = shares * (exit_price - entry_price)
            ret = (exit_price / entry_price - 1) * 100
        else:
            pnl = shares * (entry_price - exit_price)
            ret = (entry_price / exit_price - 1) * 100
        
        # Deduct commission
        pnl -= shares * (entry_price + exit_price) * commission
        
        current_capital += pnl
        
        trades.append({
            'date': date,
            'pattern': pattern,
            'direction': direction,
            'entry_price': entry_price,
            'exit_price': exit_price,
            'exit_reason': exit_reason,
            'shares': shares,
            'pnl': pnl,
            'return_pct': ret,
            'capital': current_capital
        })
    
    return pd.DataFrame(trades) if trades else None


def analyze_pattern_frequency(daily_analysis):
    """Analyze how often O=L and O=H patterns occur"""
    if daily_analysis is None or daily_analysis.empty:
        return None
    
    total_days = len(daily_analysis)
    ol_days = (daily_analysis['pattern'] == 'O=L').sum()
    oh_days = (daily_analysis['pattern'] == 'O=H').sum()
    olh_days = (daily_analysis['pattern'] == 'O=L=H').sum()
    none_days = (daily_analysis['pattern'] == 'None').sum()
    
    # Win rate for each pattern
    ol_data = daily_analysis[daily_analysis['pattern'] == 'O=L']
    oh_data = daily_analysis[daily_analysis['pattern'] == 'O=H']
    
    ol_win_rate = ol_data['correct'].mean() if len(ol_data) > 0 else 0
    oh_win_rate = oh_data['correct'].mean() if len(oh_data) > 0 else 0
    
    ol_avg_return = ol_data['day_return'].mean() if len(ol_data) > 0 else 0
    oh_avg_return = oh_data['day_return'].mean() if len(oh_data) > 0 else 0
    
    return {
        'total_days': total_days,
        'ol_days': ol_days,
        'ol_pct': ol_days / total_days * 100,
        'ol_win_rate': ol_win_rate * 100,
        'ol_avg_return': ol_avg_return,
        'oh_days': oh_days,
        'oh_pct': oh_days / total_days * 100,
        'oh_win_rate': oh_win_rate * 100,
        'oh_avg_return': oh_avg_return,
        'olh_days': olh_days,
        'none_days': none_days
    }


def run_backtest():
    """Run complete backtest on multiple stocks"""
    
    symbols = [
        'RELIANCE.NS', 'TCS.NS', 'HDFCBANK.NS', 'INFY.NS', 'SBIN.NS',
        'BHARTIARTL.NS', 'ITC.NS', 'AXISBANK.NS', 'LT.NS', 'MARUTI.NS',
        'SUNPHARMA.NS', 'TITAN.NS', 'WIPRO.NS', 'BAJFINANCE.NS', 'ICICIBANK.NS'
    ]
    
    print("=" * 80)
    print("OPEN = LOW / OPEN = HIGH STRATEGY BACKTEST")
    print("=" * 80)
    print("""
Hypothesis: If a stock opens at its day's low (O=L) and maintains that 
throughout the first hour, it shows strong buying conviction -> GO LONG

Similarly, if Open = High (O=H) -> GO SHORT

Testing on 60 days of 1-hour intraday data.
""")
    
    all_results = []
    all_pattern_stats = []
    
    for symbol in symbols:
        print(f"\n{'-'*60}")
        print(f"Processing {symbol}...")
        
        # Download data
        df = download_intraday_data(symbol, days=60)
        
        if df is None or df.empty:
            print(f"  No data for {symbol}")
            continue
        
        print(f"  Downloaded {len(df)} hourly candles")
        
        # Identify O=L and O=H days
        daily_analysis = identify_OL_OH_days(df)
        
        if daily_analysis is None or daily_analysis.empty:
            print(f"  No valid days for analysis")
            continue
        
        # Pattern frequency analysis
        pattern_stats = analyze_pattern_frequency(daily_analysis)
        pattern_stats['symbol'] = symbol
        all_pattern_stats.append(pattern_stats)
        
        print(f"  Total trading days: {pattern_stats['total_days']}")
        print(f"  O=L days: {pattern_stats['ol_days']} ({pattern_stats['ol_pct']:.1f}%)")
        print(f"  O=H days: {pattern_stats['oh_days']} ({pattern_stats['oh_pct']:.1f}%)")
        
        if pattern_stats['ol_days'] > 0:
            print(f"  O=L Win Rate: {pattern_stats['ol_win_rate']:.1f}% | Avg Day Return: {pattern_stats['ol_avg_return']:.2f}%")
        if pattern_stats['oh_days'] > 0:
            print(f"  O=H Win Rate: {pattern_stats['oh_win_rate']:.1f}% | Avg Day Return: {pattern_stats['oh_avg_return']:.2f}%")
        
        # Run backtest
        trades_df = backtest_OL_OH_strategy(df, daily_analysis)
        
        if trades_df is None or trades_df.empty:
            print(f"  No trades generated")
            continue
        
        # Calculate results
        total_return = (trades_df['capital'].iloc[-1] / 100000 - 1) * 100
        n_trades = len(trades_df)
        n_wins = (trades_df['pnl'] > 0).sum()
        win_rate = n_wins / n_trades * 100
        avg_return = trades_df['return_pct'].mean()
        
        # By pattern
        ol_trades = trades_df[trades_df['pattern'] == 'O=L']
        oh_trades = trades_df[trades_df['pattern'] == 'O=H']
        
        result = {
            'symbol': symbol,
            'total_return': total_return,
            'n_trades': n_trades,
            'win_rate': win_rate,
            'avg_return': avg_return,
            'ol_trades': len(ol_trades),
            'ol_win_rate': (ol_trades['pnl'] > 0).mean() * 100 if len(ol_trades) > 0 else 0,
            'oh_trades': len(oh_trades),
            'oh_win_rate': (oh_trades['pnl'] > 0).mean() * 100 if len(oh_trades) > 0 else 0
        }
        all_results.append(result)
        
        print(f"  BACKTEST RESULTS:")
        print(f"    Total Trades: {n_trades}")
        print(f"    Win Rate: {win_rate:.1f}%")
        print(f"    Total Return: {total_return:.2f}%")
        print(f"    Avg Trade Return: {avg_return:.2f}%")
    
    # Summary
    print("\n" + "=" * 80)
    print("SUMMARY: O=L / O=H STRATEGY RESULTS")
    print("=" * 80)
    
    if all_results:
        results_df = pd.DataFrame(all_results)
        
        print(f"\n{'Symbol':<15} | {'Return':>8} | {'Trades':>7} | {'Win Rate':>9} | {'O=L WR':>8} | {'O=H WR':>8}")
        print("-" * 70)
        
        for _, row in results_df.iterrows():
            print(f"{row['symbol']:<15} | {row['total_return']:>7.2f}% | {row['n_trades']:>7} | "
                  f"{row['win_rate']:>8.1f}% | {row['ol_win_rate']:>7.1f}% | {row['oh_win_rate']:>7.1f}%")
        
        # Aggregate stats
        print("\n" + "-" * 70)
        avg_return = results_df['total_return'].mean()
        total_trades = results_df['n_trades'].sum()
        avg_win_rate = results_df['win_rate'].mean()
        profitable = (results_df['total_return'] > 0).sum()
        
        print(f"{'AVERAGE':<15} | {avg_return:>7.2f}% | {total_trades:>7} | {avg_win_rate:>8.1f}%")
        print(f"\nProfitable stocks: {profitable}/{len(results_df)}")
        
        # Pattern analysis
        print("\n" + "=" * 80)
        print("PATTERN FREQUENCY ANALYSIS")
        print("=" * 80)
        
        pattern_df = pd.DataFrame(all_pattern_stats)
        
        avg_ol_pct = pattern_df['ol_pct'].mean()
        avg_oh_pct = pattern_df['oh_pct'].mean()
        avg_ol_wr = pattern_df['ol_win_rate'].mean()
        avg_oh_wr = pattern_df['oh_win_rate'].mean()
        
        print(f"\nOpen = Low (O=L):")
        print(f"  Frequency: {avg_ol_pct:.1f}% of trading days")
        print(f"  Day closes higher (win): {avg_ol_wr:.1f}%")
        
        print(f"\nOpen = High (O=H):")
        print(f"  Frequency: {avg_oh_pct:.1f}% of trading days")
        print(f"  Day closes lower (win): {avg_oh_wr:.1f}%")
        
        # Insights
        print("\n" + "=" * 80)
        print("KEY INSIGHTS")
        print("=" * 80)
        
        if avg_ol_wr > 50:
            ol_verdict = "WORKS - Bullish days tend to continue"
        else:
            ol_verdict = "DOESN'T WORK - No edge"
        
        if avg_oh_wr > 50:
            oh_verdict = "WORKS - Bearish days tend to continue"
        else:
            oh_verdict = "DOESN'T WORK - No edge"
        
        print(f"""
1. O=L PATTERN: {ol_verdict}
   - Occurs {avg_ol_pct:.1f}% of days
   - Day ends higher {avg_ol_wr:.1f}% of time

2. O=H PATTERN: {oh_verdict}
   - Occurs {avg_oh_pct:.1f}% of days
   - Day ends lower {avg_oh_wr:.1f}% of time

3. TRADING RESULTS:
   - Average return: {avg_return:.2f}%
   - Win rate: {avg_win_rate:.1f}%
   - Profitable on {profitable}/{len(results_df)} stocks

4. CONCLUSION:
""")
        
        if avg_return > 0 and avg_win_rate > 50:
            print("   -> STRATEGY HAS EDGE! Worth testing further.")
        elif avg_return > -0.5:
            print("   -> MARGINAL - Breaks even after costs. Not recommended.")
        else:
            print("   -> NO EDGE - Strategy loses money after costs.")
    
    return results_df if all_results else None


if __name__ == "__main__":
    results = run_backtest()
