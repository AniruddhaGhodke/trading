"""
FIRST 1-HOUR CANDLE O=L / O=H STRATEGY
======================================

Corrected approach: Check the FIRST 1-hour candle of the day.

If first candle has:
- Open = Low: Strong buying from open -> GO LONG
- Open = High: Strong selling from open -> GO SHORT

This is tradeable because we detect the pattern after the first hour
and have the rest of the day to profit.
"""

import pandas as pd
import numpy as np
import yfinance as yf
import warnings
warnings.filterwarnings('ignore')


def download_intraday_data(symbol, days=60):
    """Download intraday data (1h candles)"""
    try:
        df = yf.download(symbol, period=f'{days}d', interval='1h', progress=False)
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        return df
    except Exception as e:
        print(f"Error downloading {symbol}: {e}")
        return None


def analyze_first_candle_pattern(df):
    """
    Analyze the FIRST 1-hour candle of each day.
    
    Check if Open = Low (bullish) or Open = High (bearish)
    Then see if the rest of the day continues in that direction.
    """
    if df is None or df.empty:
        return None
    
    df = df.copy()
    df['Date'] = df.index.date
    df['Hour'] = df.index.hour
    
    daily_analysis = []
    
    for date in df['Date'].unique():
        day_data = df[df['Date'] == date].copy()
        
        if len(day_data) < 4:  # Need at least 4 hours of data
            continue
        
        # FIRST candle of the day
        first_candle = day_data.iloc[0]
        first_open = first_candle['Open']
        first_high = first_candle['High']
        first_low = first_candle['Low']
        first_close = first_candle['Close']
        
        # Check O=L or O=H for FIRST CANDLE ONLY
        tolerance = first_open * 0.001  # 0.1% tolerance
        
        first_is_OL = abs(first_open - first_low) <= tolerance
        first_is_OH = abs(first_open - first_high) <= tolerance
        
        # Rest of day performance (after first candle)
        rest_of_day = day_data.iloc[1:]
        if len(rest_of_day) == 0:
            continue
            
        day_close = rest_of_day.iloc[-1]['Close']
        rest_high = rest_of_day['High'].max()
        rest_low = rest_of_day['Low'].min()
        
        # Returns
        from_first_close = (day_close / first_close - 1) * 100
        from_first_open = (day_close / first_open - 1) * 100
        
        # Pattern classification
        if first_is_OL and not first_is_OH:
            pattern = 'O=L'
            expected = 'bullish'
            correct = from_first_close > 0
        elif first_is_OH and not first_is_OL:
            pattern = 'O=H'
            expected = 'bearish'
            correct = from_first_close < 0
        else:
            pattern = 'None'
            expected = None
            correct = None
        
        daily_analysis.append({
            'date': date,
            'first_open': first_open,
            'first_high': first_high,
            'first_low': first_low,
            'first_close': first_close,
            'day_close': day_close,
            'pattern': pattern,
            'expected': expected,
            'return_from_close': from_first_close,
            'return_from_open': from_first_open,
            'correct': correct,
            'rest_high': rest_high,
            'rest_low': rest_low
        })
    
    return pd.DataFrame(daily_analysis)


def backtest_first_candle_strategy(df, analysis_df,
                                    stop_loss_pct=1.0,
                                    take_profit_pct=2.0,
                                    capital=100000,
                                    slippage=0.001,
                                    commission=0.001):
    """
    Backtest: Enter after first candle based on O=L or O=H pattern.
    """
    if df is None or analysis_df is None:
        return None
    
    df = df.copy()
    df['Date'] = df.index.date
    
    trades = []
    current_capital = capital
    
    for _, day_info in analysis_df.iterrows():
        date = day_info['date']
        pattern = day_info['pattern']
        
        if pattern not in ['O=L', 'O=H']:
            continue
        
        day_data = df[df['Date'] == date].copy()
        
        if len(day_data) < 3:
            continue
        
        # Entry at close of first candle
        first_candle = day_data.iloc[0]
        entry_price = first_candle['Close']
        
        if pattern == 'O=L':
            direction = 'long'
            entry_price = entry_price * (1 + slippage)
            stop_loss = entry_price * (1 - stop_loss_pct / 100)
            take_profit = entry_price * (1 + take_profit_pct / 100)
        else:  # O=H
            direction = 'short'
            entry_price = entry_price * (1 - slippage)
            stop_loss = entry_price * (1 + stop_loss_pct / 100)
            take_profit = entry_price * (1 - take_profit_pct / 100)
        
        # Position sizing (2% risk)
        risk_amount = current_capital * 0.02
        risk_per_share = abs(entry_price - stop_loss)
        shares = int(risk_amount / risk_per_share) if risk_per_share > 0 else 0
        shares = min(shares, int(current_capital * 0.25 / entry_price))
        
        if shares <= 0:
            continue
        
        # Simulate through rest of day
        exit_price = None
        exit_reason = None
        
        for i in range(1, len(day_data)):
            candle = day_data.iloc[i]
            
            if direction == 'long':
                if candle['Low'] <= stop_loss:
                    exit_price = stop_loss * (1 - slippage)
                    exit_reason = 'stop_loss'
                    break
                elif candle['High'] >= take_profit:
                    exit_price = take_profit
                    exit_reason = 'take_profit'
                    break
            else:  # short
                if candle['High'] >= stop_loss:
                    exit_price = stop_loss * (1 + slippage)
                    exit_reason = 'stop_loss'
                    break
                elif candle['Low'] <= take_profit:
                    exit_price = take_profit
                    exit_reason = 'take_profit'
                    break
        
        # Exit at end of day if no SL/TP hit
        if exit_price is None:
            exit_price = day_data.iloc[-1]['Close']
            if direction == 'long':
                exit_price = exit_price * (1 - slippage)
            else:
                exit_price = exit_price * (1 + slippage)
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
            'entry': entry_price,
            'exit': exit_price,
            'exit_reason': exit_reason,
            'pnl': pnl,
            'return_pct': ret
        })
    
    return pd.DataFrame(trades) if trades else None


def run_analysis():
    """Run complete analysis"""
    
    symbols = [
        'RELIANCE.NS', 'TCS.NS', 'HDFCBANK.NS', 'INFY.NS', 'SBIN.NS',
        'BHARTIARTL.NS', 'ITC.NS', 'AXISBANK.NS', 'LT.NS', 'MARUTI.NS',
        'SUNPHARMA.NS', 'TITAN.NS', 'WIPRO.NS', 'BAJFINANCE.NS', 'ICICIBANK.NS'
    ]
    
    print("=" * 80)
    print("FIRST 1-HOUR CANDLE O=L / O=H STRATEGY")
    print("=" * 80)
    print("""
Logic:
1. Check if FIRST 1-hour candle has Open = Low or Open = High
2. O=L (first candle bullish): Go LONG expecting continuation
3. O=H (first candle bearish): Go SHORT expecting continuation
4. Entry: After first hour at close of first candle
5. Exit: Stop loss, take profit, or end of day
""")
    
    all_pattern_stats = []
    all_results = []
    
    for symbol in symbols:
        print(f"\n{'-'*60}")
        print(f"{symbol}")
        
        df = download_intraday_data(symbol, days=60)
        
        if df is None or df.empty:
            print("  No data")
            continue
        
        # Analyze first candle patterns
        analysis = analyze_first_candle_pattern(df)
        
        if analysis is None or analysis.empty:
            print("  No valid analysis")
            continue
        
        # Pattern stats
        total_days = len(analysis)
        ol_days = analysis[analysis['pattern'] == 'O=L']
        oh_days = analysis[analysis['pattern'] == 'O=H']
        
        ol_count = len(ol_days)
        oh_count = len(oh_days)
        
        # Win rates (does price continue in expected direction?)
        ol_win_rate = ol_days['correct'].mean() * 100 if ol_count > 0 else 0
        oh_win_rate = oh_days['correct'].mean() * 100 if oh_count > 0 else 0
        
        # Average returns when pattern occurs
        ol_avg_return = ol_days['return_from_close'].mean() if ol_count > 0 else 0
        oh_avg_return = oh_days['return_from_close'].mean() if oh_count > 0 else 0
        
        print(f"  Days analyzed: {total_days}")
        print(f"  First candle O=L: {ol_count} days ({ol_count/total_days*100:.1f}%)")
        print(f"    -> Price continues UP: {ol_win_rate:.1f}% | Avg return: {ol_avg_return:+.2f}%")
        print(f"  First candle O=H: {oh_count} days ({oh_count/total_days*100:.1f}%)")
        print(f"    -> Price continues DOWN: {oh_win_rate:.1f}% | Avg return (inverted): {-oh_avg_return:+.2f}%")
        
        all_pattern_stats.append({
            'symbol': symbol,
            'total_days': total_days,
            'ol_count': ol_count,
            'ol_pct': ol_count/total_days*100,
            'ol_win_rate': ol_win_rate,
            'ol_avg_return': ol_avg_return,
            'oh_count': oh_count,
            'oh_pct': oh_count/total_days*100,
            'oh_win_rate': oh_win_rate,
            'oh_avg_return': oh_avg_return
        })
        
        # Run backtest
        trades_df = backtest_first_candle_strategy(df, analysis)
        
        if trades_df is None or trades_df.empty:
            print("  No trades")
            continue
        
        n_trades = len(trades_df)
        n_wins = (trades_df['pnl'] > 0).sum()
        win_rate = n_wins / n_trades * 100
        total_pnl = trades_df['pnl'].sum()
        total_return = total_pnl / 100000 * 100
        avg_return = trades_df['return_pct'].mean()
        
        # By pattern
        ol_trades = trades_df[trades_df['pattern'] == 'O=L']
        oh_trades = trades_df[trades_df['pattern'] == 'O=H']
        
        ol_wr = (ol_trades['pnl'] > 0).mean() * 100 if len(ol_trades) > 0 else 0
        oh_wr = (oh_trades['pnl'] > 0).mean() * 100 if len(oh_trades) > 0 else 0
        
        print(f"  BACKTEST: {n_trades} trades | WR: {win_rate:.1f}% | Return: {total_return:.2f}%")
        print(f"    O=L trades: {len(ol_trades)} | WR: {ol_wr:.1f}%")
        print(f"    O=H trades: {len(oh_trades)} | WR: {oh_wr:.1f}%")
        
        all_results.append({
            'symbol': symbol,
            'trades': n_trades,
            'win_rate': win_rate,
            'total_return': total_return,
            'avg_return': avg_return,
            'ol_trades': len(ol_trades),
            'ol_wr': ol_wr,
            'oh_trades': len(oh_trades),
            'oh_wr': oh_wr
        })
    
    # Summary
    print("\n" + "=" * 80)
    print("SUMMARY: FIRST CANDLE O=L / O=H PATTERN")
    print("=" * 80)
    
    if all_pattern_stats:
        stats_df = pd.DataFrame(all_pattern_stats)
        
        avg_ol_pct = stats_df['ol_pct'].mean()
        avg_ol_wr = stats_df['ol_win_rate'].mean()
        avg_ol_ret = stats_df['ol_avg_return'].mean()
        
        avg_oh_pct = stats_df['oh_pct'].mean()
        avg_oh_wr = stats_df['oh_win_rate'].mean()
        avg_oh_ret = stats_df['oh_avg_return'].mean()
        
        print(f"\nPATTERN ANALYSIS (First 1-hour candle):")
        print(f"\n  O=L (Open = Low in first hour):")
        print(f"    Frequency: {avg_ol_pct:.1f}% of days")
        print(f"    Price continues UP: {avg_ol_wr:.1f}%")
        print(f"    Avg return rest of day: {avg_ol_ret:+.2f}%")
        
        print(f"\n  O=H (Open = High in first hour):")
        print(f"    Frequency: {avg_oh_pct:.1f}% of days")
        print(f"    Price continues DOWN: {avg_oh_wr:.1f}%")
        print(f"    Avg return rest of day: {avg_oh_ret:+.2f}%")
    
    if all_results:
        results_df = pd.DataFrame(all_results)
        
        print(f"\nBACKTEST RESULTS:")
        print(f"\n{'Symbol':<15} | {'Return':>8} | {'Trades':>7} | {'WR':>6} | {'O=L WR':>7} | {'O=H WR':>7}")
        print("-" * 65)
        
        for _, r in results_df.iterrows():
            status = "OK" if r['total_return'] > 0 else "--"
            print(f"{r['symbol']:<15} | {r['total_return']:>7.2f}% | {r['trades']:>7} | "
                  f"{r['win_rate']:>5.1f}% | {r['ol_wr']:>6.1f}% | {r['oh_wr']:>6.1f}% {status}")
        
        avg_return = results_df['total_return'].mean()
        total_trades = results_df['trades'].sum()
        avg_wr = results_df['win_rate'].mean()
        profitable = (results_df['total_return'] > 0).sum()
        
        print("-" * 65)
        print(f"{'AVERAGE':<15} | {avg_return:>7.2f}% | {total_trades:>7} | {avg_wr:>5.1f}%")
        print(f"\nProfitable: {profitable}/{len(results_df)} stocks")
        
        # Verdict
        print("\n" + "=" * 80)
        print("CONCLUSION")
        print("=" * 80)
        
        if avg_ol_wr > 55:
            ol_verdict = "HAS EDGE - First candle O=L predicts bullish day"
        elif avg_ol_wr > 45:
            ol_verdict = "NEUTRAL - No significant edge"
        else:
            ol_verdict = "REVERSAL - First candle O=L predicts BEARISH day!"
        
        if avg_oh_wr > 55:
            oh_verdict = "HAS EDGE - First candle O=H predicts bearish day"
        elif avg_oh_wr > 45:
            oh_verdict = "NEUTRAL - No significant edge"
        else:
            oh_verdict = "REVERSAL - First candle O=H predicts BULLISH day!"
        
        print(f"""
FIRST CANDLE PATTERN RESULTS:

1. O=L (First candle bullish):
   {ol_verdict}
   Win rate: {avg_ol_wr:.1f}%

2. O=H (First candle bearish):
   {oh_verdict}
   Win rate: {avg_oh_wr:.1f}%

3. TRADING PERFORMANCE:
   Average return: {avg_return:.2f}%
   Win rate: {avg_wr:.1f}%
   Profitable stocks: {profitable}/{len(results_df)}
""")
        
        if avg_return > 0:
            print("   VERDICT: Strategy shows POSITIVE returns!")
        elif avg_return > -0.5:
            print("   VERDICT: Strategy is MARGINAL - breaks even after costs")
        else:
            print("   VERDICT: Strategy LOSES money - no edge exists")
    
    return results_df if all_results else None


if __name__ == "__main__":
    results = run_analysis()
