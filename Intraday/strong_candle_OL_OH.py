"""
REFINED O=L / O=H STRATEGY - With Candle Strength Filter
=========================================================

Previous test showed O=L/O=H doesn't work. But we didn't filter for
candle STRENGTH.

Refined logic:
1. O=L (Open = Low): Price never went below open in first hour
   BUT also require: Close > Open (green candle) AND Close near High
   This is a STRONG bullish candle (like marubozu)

2. O=H (Open = High): Price never went above open in first hour  
   BUT also require: Close < Open (red candle) AND Close near Low
   This is a STRONG bearish candle

Only trade when we have STRONG conviction candles.
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


def analyze_strong_first_candle(df):
    """
    Analyze STRONG first candles only.
    
    Strong O=L (Bullish):
    - Open = Low (price never went below open)
    - Close > Open (green candle)
    - Close is in top 30% of candle range (close near high)
    
    Strong O=H (Bearish):
    - Open = High (price never went above open)
    - Close < Open (red candle)
    - Close is in bottom 30% of candle range (close near low)
    """
    if df is None or df.empty:
        return None
    
    df = df.copy()
    df['Date'] = df.index.date
    
    daily_analysis = []
    
    for date in df['Date'].unique():
        day_data = df[df['Date'] == date].copy()
        
        if len(day_data) < 4:
            continue
        
        # First candle
        fc = day_data.iloc[0]
        fc_open = fc['Open']
        fc_high = fc['High']
        fc_low = fc['Low']
        fc_close = fc['Close']
        
        # Candle range
        candle_range = fc_high - fc_low
        if candle_range <= 0:
            continue
        
        # Position of close within candle (0 = low, 1 = high)
        close_position = (fc_close - fc_low) / candle_range
        
        # Tolerance for O=L/O=H detection
        tolerance = fc_open * 0.001
        
        is_OL = abs(fc_open - fc_low) <= tolerance
        is_OH = abs(fc_open - fc_high) <= tolerance
        is_green = fc_close > fc_open
        is_red = fc_close < fc_open
        
        # Strong bullish: O=L + green + close in top 30%
        strong_bullish = is_OL and is_green and close_position >= 0.7
        
        # Strong bearish: O=H + red + close in bottom 30%
        strong_bearish = is_OH and is_red and close_position <= 0.3
        
        # Weak patterns (O=L or O=H but not strong)
        weak_OL = is_OL and not strong_bullish
        weak_OH = is_OH and not strong_bearish
        
        # Rest of day performance
        rest_of_day = day_data.iloc[1:]
        if len(rest_of_day) == 0:
            continue
            
        day_close = rest_of_day.iloc[-1]['Close']
        return_from_close = (day_close / fc_close - 1) * 100
        
        # Classification
        if strong_bullish:
            pattern = 'STRONG_OL'
            correct = return_from_close > 0
        elif strong_bearish:
            pattern = 'STRONG_OH'
            correct = return_from_close < 0
        elif weak_OL:
            pattern = 'WEAK_OL'
            correct = return_from_close > 0
        elif weak_OH:
            pattern = 'WEAK_OH'
            correct = return_from_close < 0
        else:
            pattern = 'None'
            correct = None
        
        daily_analysis.append({
            'date': date,
            'fc_open': fc_open,
            'fc_high': fc_high,
            'fc_low': fc_low,
            'fc_close': fc_close,
            'close_position': close_position,
            'is_green': is_green,
            'pattern': pattern,
            'return_rest_of_day': return_from_close,
            'correct': correct,
            'day_close': day_close
        })
    
    return pd.DataFrame(daily_analysis)


def backtest_strong_candles(df, analysis_df,
                             stop_loss_pct=1.5,
                             take_profit_pct=2.0,
                             capital=100000,
                             slippage=0.001,
                             commission=0.001):
    """Backtest only STRONG patterns"""
    
    if df is None or analysis_df is None:
        return None
    
    df = df.copy()
    df['Date'] = df.index.date
    
    trades = []
    current_capital = capital
    
    for _, day_info in analysis_df.iterrows():
        date = day_info['date']
        pattern = day_info['pattern']
        
        # Only trade STRONG patterns
        if pattern not in ['STRONG_OL', 'STRONG_OH']:
            continue
        
        day_data = df[df['Date'] == date].copy()
        
        if len(day_data) < 3:
            continue
        
        fc = day_data.iloc[0]
        entry_price = fc['Close']
        
        if pattern == 'STRONG_OL':
            direction = 'long'
            entry_price = entry_price * (1 + slippage)
            stop_loss = entry_price * (1 - stop_loss_pct / 100)
            take_profit = entry_price * (1 + take_profit_pct / 100)
        else:
            direction = 'short'
            entry_price = entry_price * (1 - slippage)
            stop_loss = entry_price * (1 + stop_loss_pct / 100)
            take_profit = entry_price * (1 - take_profit_pct / 100)
        
        # Position sizing
        risk_amount = current_capital * 0.02
        risk_per_share = abs(entry_price - stop_loss)
        shares = int(risk_amount / risk_per_share) if risk_per_share > 0 else 0
        shares = min(shares, int(current_capital * 0.25 / entry_price))
        
        if shares <= 0:
            continue
        
        # Simulate
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
            else:
                if candle['High'] >= stop_loss:
                    exit_price = stop_loss * (1 + slippage)
                    exit_reason = 'stop_loss'
                    break
                elif candle['Low'] <= take_profit:
                    exit_price = take_profit
                    exit_reason = 'take_profit'
                    break
        
        if exit_price is None:
            exit_price = day_data.iloc[-1]['Close']
            exit_price = exit_price * (1 - slippage if direction == 'long' else 1 + slippage)
            exit_reason = 'eod'
        
        # P&L
        if direction == 'long':
            pnl = shares * (exit_price - entry_price)
            ret = (exit_price / entry_price - 1) * 100
        else:
            pnl = shares * (entry_price - exit_price)
            ret = (entry_price / exit_price - 1) * 100
        
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
    
    print("=" * 85)
    print("REFINED O=L / O=H STRATEGY - STRONG CANDLES ONLY")
    print("=" * 85)
    print("""
REFINED LOGIC:

STRONG Bullish (O=L):
  - Open = Low (price never went below open) 
  - Close > Open (GREEN candle)
  - Close in TOP 30% of range (strong close)
  -> This is a powerful bullish marubozu-like candle

STRONG Bearish (O=H):
  - Open = High (price never went above open)
  - Close < Open (RED candle)  
  - Close in BOTTOM 30% of range (weak close)
  -> This is a powerful bearish marubozu-like candle

We ONLY trade strong patterns, ignoring weak ones.
""")
    
    all_pattern_stats = []
    all_results = []
    
    for symbol in symbols:
        print(f"\n{'-'*70}")
        print(f"{symbol}")
        
        df = download_intraday_data(symbol, days=60)
        
        if df is None or df.empty:
            print("  No data")
            continue
        
        analysis = analyze_strong_first_candle(df)
        
        if analysis is None or analysis.empty:
            print("  No analysis")
            continue
        
        # Count patterns
        total = len(analysis)
        strong_ol = analysis[analysis['pattern'] == 'STRONG_OL']
        strong_oh = analysis[analysis['pattern'] == 'STRONG_OH']
        weak_ol = analysis[analysis['pattern'] == 'WEAK_OL']
        weak_oh = analysis[analysis['pattern'] == 'WEAK_OH']
        
        # Win rates
        sol_wr = strong_ol['correct'].mean() * 100 if len(strong_ol) > 0 else 0
        soh_wr = strong_oh['correct'].mean() * 100 if len(strong_oh) > 0 else 0
        wol_wr = weak_ol['correct'].mean() * 100 if len(weak_ol) > 0 else 0
        woh_wr = weak_oh['correct'].mean() * 100 if len(weak_oh) > 0 else 0
        
        # Avg returns
        sol_ret = strong_ol['return_rest_of_day'].mean() if len(strong_ol) > 0 else 0
        soh_ret = strong_oh['return_rest_of_day'].mean() if len(strong_oh) > 0 else 0
        
        print(f"  Days: {total}")
        print(f"  STRONG O=L: {len(strong_ol)} ({len(strong_ol)/total*100:.1f}%) -> Continues UP: {sol_wr:.1f}% | Avg ret: {sol_ret:+.2f}%")
        print(f"  STRONG O=H: {len(strong_oh)} ({len(strong_oh)/total*100:.1f}%) -> Continues DOWN: {soh_wr:.1f}% | Avg ret: {-soh_ret:+.2f}%")
        print(f"  Weak O=L: {len(weak_ol)} -> UP: {wol_wr:.1f}%")
        print(f"  Weak O=H: {len(weak_oh)} -> DOWN: {woh_wr:.1f}%")
        
        all_pattern_stats.append({
            'symbol': symbol,
            'total': total,
            'strong_ol': len(strong_ol),
            'strong_ol_wr': sol_wr,
            'strong_ol_ret': sol_ret,
            'strong_oh': len(strong_oh),
            'strong_oh_wr': soh_wr,
            'strong_oh_ret': soh_ret,
            'weak_ol': len(weak_ol),
            'weak_ol_wr': wol_wr,
            'weak_oh': len(weak_oh),
            'weak_oh_wr': woh_wr
        })
        
        # Backtest
        trades_df = backtest_strong_candles(df, analysis)
        
        if trades_df is None or trades_df.empty:
            print("  No STRONG trades")
            all_results.append({
                'symbol': symbol,
                'trades': 0,
                'win_rate': 0,
                'total_return': 0
            })
            continue
        
        n_trades = len(trades_df)
        win_rate = (trades_df['pnl'] > 0).mean() * 100
        total_return = trades_df['pnl'].sum() / 100000 * 100
        
        print(f"  BACKTEST: {n_trades} trades | WR: {win_rate:.1f}% | Return: {total_return:+.2f}%")
        
        all_results.append({
            'symbol': symbol,
            'trades': n_trades,
            'win_rate': win_rate,
            'total_return': total_return
        })
    
    # Summary
    print("\n" + "=" * 85)
    print("SUMMARY: STRONG vs WEAK PATTERNS")
    print("=" * 85)
    
    if all_pattern_stats:
        stats_df = pd.DataFrame(all_pattern_stats)
        
        # Aggregate
        total_strong_ol = stats_df['strong_ol'].sum()
        total_strong_oh = stats_df['strong_oh'].sum()
        total_weak_ol = stats_df['weak_ol'].sum()
        total_weak_oh = stats_df['weak_oh'].sum()
        
        avg_sol_wr = stats_df[stats_df['strong_ol'] > 0]['strong_ol_wr'].mean()
        avg_soh_wr = stats_df[stats_df['strong_oh'] > 0]['strong_oh_wr'].mean()
        avg_wol_wr = stats_df[stats_df['weak_ol'] > 0]['weak_ol_wr'].mean()
        avg_woh_wr = stats_df[stats_df['weak_oh'] > 0]['weak_oh_wr'].mean()
        
        avg_sol_ret = stats_df[stats_df['strong_ol'] > 0]['strong_ol_ret'].mean()
        avg_soh_ret = stats_df[stats_df['strong_oh'] > 0]['strong_oh_ret'].mean()
        
        print(f"""
PATTERN COMPARISON:

                        | Count | Win Rate | Avg Return |
  --------------------------------------------------------
  STRONG O=L (bullish)  | {total_strong_ol:>5} | {avg_sol_wr:>7.1f}% | {avg_sol_ret:>+9.2f}% |
  STRONG O=H (bearish)  | {total_strong_oh:>5} | {avg_soh_wr:>7.1f}% | {-avg_soh_ret:>+9.2f}% |
  --------------------------------------------------------
  Weak O=L              | {total_weak_ol:>5} | {avg_wol_wr:>7.1f}% |    N/A     |
  Weak O=H              | {total_weak_oh:>5} | {avg_woh_wr:>7.1f}% |    N/A     |
""")
        
        print("\nKEY INSIGHT:")
        if avg_sol_wr > 50:
            print(f"  STRONG O=L WORKS! Win rate: {avg_sol_wr:.1f}%")
        else:
            print(f"  STRONG O=L still reverses. Win rate: {avg_sol_wr:.1f}%")
        
        if avg_soh_wr > 50:
            print(f"  STRONG O=H WORKS! Win rate: {avg_soh_wr:.1f}%")
        else:
            print(f"  STRONG O=H still reverses. Win rate: {avg_soh_wr:.1f}%")
    
    # Trading results
    if all_results:
        results_df = pd.DataFrame(all_results)
        
        print("\n" + "=" * 85)
        print("BACKTEST RESULTS (STRONG PATTERNS ONLY)")
        print("=" * 85)
        
        print(f"\n{'Symbol':<15} | {'Trades':>7} | {'Win Rate':>9} | {'Return':>9}")
        print("-" * 50)
        
        for _, r in results_df.iterrows():
            status = "OK" if r['total_return'] > 0 else "--"
            print(f"{r['symbol']:<15} | {r['trades']:>7} | {r['win_rate']:>8.1f}% | {r['total_return']:>+8.2f}% {status}")
        
        total_trades = results_df['trades'].sum()
        avg_return = results_df['total_return'].mean()
        avg_wr = results_df[results_df['trades'] > 0]['win_rate'].mean()
        profitable = (results_df['total_return'] > 0).sum()
        
        print("-" * 50)
        print(f"{'TOTAL':<15} | {total_trades:>7} | {avg_wr:>8.1f}% | {avg_return:>+8.2f}%")
        print(f"\nProfitable: {profitable}/{len(results_df)} stocks")
        
        # Final verdict
        print("\n" + "=" * 85)
        print("FINAL VERDICT")
        print("=" * 85)
        
        if avg_return > 0.5 and avg_wr > 50:
            verdict = "STRATEGY HAS EDGE! Consider paper trading."
        elif avg_return > 0:
            verdict = "MARGINAL EDGE. Might work with lower costs."
        elif avg_return > -0.5:
            verdict = "BREAK-EVEN. No significant edge after costs."
        else:
            verdict = "NO EDGE. Strategy loses money."
        
        print(f"\n{verdict}")
        print(f"\nStrong candles occur rarely ({total_strong_ol + total_strong_oh} times in 60 days across 15 stocks)")
        print(f"This means ~{(total_strong_ol + total_strong_oh) / 15 / 60 * 100:.1f}% of days have a tradeable signal")
    
    return results_df if all_results else None


if __name__ == "__main__":
    results = run_analysis()
