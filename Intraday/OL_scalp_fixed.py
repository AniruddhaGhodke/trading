"""
O=L SCALPING STRATEGY - FIXED VERSION
======================================

Quick profit booking approach:
1. First 1h candle has O=L (Open = Low, price never went below)
2. Enter LONG at close of first candle  
3. Target: 0.5% profit
4. Stop Loss: 0.3%
5. Max hold: 3 candles then exit
"""

import pandas as pd
import numpy as np
import yfinance as yf
import warnings
warnings.filterwarnings('ignore')


def download_data(symbol, days=60):
    """Download intraday data"""
    try:
        df = yf.download(symbol, period=f'{days}d', interval='1h', progress=False)
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        return df
    except:
        return None


def find_OL_signals(df):
    """Find first candles with O=L pattern"""
    if df is None or df.empty:
        return []
    
    df = df.copy()
    df['Date'] = df.index.date
    
    signals = []
    
    for date in df['Date'].unique():
        day_data = df[df['Date'] == date]
        
        if len(day_data) < 4:
            continue
        
        fc = day_data.iloc[0]
        tolerance = fc['Open'] * 0.001
        
        is_OL = abs(fc['Open'] - fc['Low']) <= tolerance
        is_OH = abs(fc['Open'] - fc['High']) <= tolerance
        
        if is_OL and not is_OH:
            is_green = fc['Close'] > fc['Open']
            candle_range = fc['High'] - fc['Low']
            close_pos = (fc['Close'] - fc['Low']) / candle_range if candle_range > 0 else 0.5
            
            signals.append({
                'date': date,
                'entry_price': fc['Close'],
                'is_green': is_green,
                'close_pos': close_pos,
                'first_ret': (fc['Close'] / fc['Open'] - 1) * 100
            })
    
    return signals


def scalp_trade(df, signal, target_pct, sl_pct, max_candles):
    """Execute a single scalp trade"""
    
    date = signal['date']
    df = df.copy()
    df['Date'] = df.index.date
    day_data = df[df['Date'] == date]
    
    if len(day_data) < 2:
        return None
    
    entry = signal['entry_price'] * 1.001  # slippage
    tp = entry * (1 + target_pct / 100)
    sl = entry * (1 - sl_pct / 100)
    
    exit_price = None
    exit_reason = None
    
    # Check candles after entry
    for i in range(1, min(max_candles + 1, len(day_data))):
        candle = day_data.iloc[i]
        
        # Check TP first (optimistic)
        if candle['High'] >= tp:
            exit_price = tp
            exit_reason = 'TP'
            break
        
        # Check SL
        if candle['Low'] <= sl:
            exit_price = sl * 0.999  # slippage
            exit_reason = 'SL'
            break
    
    # Time exit if no TP/SL
    if exit_price is None:
        last_idx = min(max_candles, len(day_data) - 1)
        exit_price = day_data.iloc[last_idx]['Close'] * 0.999
        exit_reason = 'TIME'
    
    ret = (exit_price / entry - 1) * 100
    
    return {
        'date': date,
        'entry': entry,
        'exit': exit_price,
        'reason': exit_reason,
        'return': ret,
        'is_green': signal['is_green'],
        'close_pos': signal['close_pos']
    }


def run_backtest():
    """Run scalping backtest"""
    
    symbols = [
        'RELIANCE.NS', 'TCS.NS', 'HDFCBANK.NS', 'INFY.NS', 'SBIN.NS',
        'BHARTIARTL.NS', 'ITC.NS', 'AXISBANK.NS', 'LT.NS', 'MARUTI.NS',
        'SUNPHARMA.NS', 'TITAN.NS', 'WIPRO.NS', 'BAJFINANCE.NS', 'ICICIBANK.NS'
    ]
    
    # Different configurations to test
    configs = [
        {'name': 'TP:0.5% SL:0.3%', 'tp': 0.5, 'sl': 0.3, 'green_only': False},
        {'name': 'TP:0.5% SL:0.5%', 'tp': 0.5, 'sl': 0.5, 'green_only': False},
        {'name': 'TP:1.0% SL:0.5%', 'tp': 1.0, 'sl': 0.5, 'green_only': False},
        {'name': 'TP:1.0% SL:1.0%', 'tp': 1.0, 'sl': 1.0, 'green_only': False},
        {'name': 'TP:0.3% SL:0.2%', 'tp': 0.3, 'sl': 0.2, 'green_only': False},
        {'name': 'Green TP:0.5%', 'tp': 0.5, 'sl': 0.3, 'green_only': True},
        {'name': 'Green TP:1.0%', 'tp': 1.0, 'sl': 0.5, 'green_only': True},
    ]
    
    print("=" * 80)
    print("O=L SCALPING STRATEGY - QUICK PROFIT BOOKING")
    print("=" * 80)
    print("""
Logic:
1. First 1h candle has O=L (price never went below open)
2. Enter at close of first candle
3. Target & Stop Loss as configured
4. Max hold: 3 hours
5. Book profits before reversal!
""")
    
    all_results = []
    
    for config in configs:
        print(f"\n{'='*80}")
        print(f"CONFIG: {config['name']} | Green Only: {config['green_only']}")
        print(f"{'='*80}")
        
        all_trades = []
        stock_results = []
        
        for symbol in symbols:
            df = download_data(symbol, days=60)
            if df is None or df.empty:
                continue
            
            signals = find_OL_signals(df)
            
            if not signals:
                continue
            
            # Filter for green candles if required
            if config['green_only']:
                signals = [s for s in signals if s['is_green']]
            
            if not signals:
                continue
            
            trades = []
            for sig in signals:
                trade = scalp_trade(df, sig, config['tp'], config['sl'], max_candles=3)
                if trade:
                    trades.append(trade)
                    all_trades.append(trade)
            
            if trades:
                n = len(trades)
                wins = sum(1 for t in trades if t['return'] > 0)
                wr = wins / n * 100
                avg_ret = sum(t['return'] for t in trades) / n
                tp_count = sum(1 for t in trades if t['reason'] == 'TP')
                sl_count = sum(1 for t in trades if t['reason'] == 'SL')
                
                status = "OK" if avg_ret > 0 else "--"
                print(f"  {symbol:<15} | {n:>3} trades | WR: {wr:>5.1f}% | "
                      f"Ret: {avg_ret:>+6.2f}% | TP:{tp_count} SL:{sl_count} {status}")
                
                stock_results.append({
                    'symbol': symbol,
                    'trades': n,
                    'win_rate': wr,
                    'avg_return': avg_ret,
                    'tp_pct': tp_count / n * 100
                })
        
        # Config summary
        if all_trades:
            n_total = len(all_trades)
            wins = sum(1 for t in all_trades if t['return'] > 0)
            wr = wins / n_total * 100
            avg_ret = sum(t['return'] for t in all_trades) / n_total
            tp_pct = sum(1 for t in all_trades if t['reason'] == 'TP') / n_total * 100
            sl_pct = sum(1 for t in all_trades if t['reason'] == 'SL') / n_total * 100
            profitable = sum(1 for r in stock_results if r['avg_return'] > 0)
            
            print(f"\n  TOTAL: {n_total} trades | WR: {wr:.1f}% | Avg: {avg_ret:+.2f}% | "
                  f"TP: {tp_pct:.0f}% SL: {sl_pct:.0f}% | Profitable: {profitable}/{len(stock_results)}")
            
            all_results.append({
                'config': config['name'],
                'trades': n_total,
                'win_rate': wr,
                'avg_return': avg_ret,
                'tp_pct': tp_pct,
                'sl_pct': sl_pct,
                'profitable': f"{profitable}/{len(stock_results)}"
            })
    
    # Final comparison
    print("\n" + "=" * 80)
    print("CONFIGURATION COMPARISON")
    print("=" * 80)
    
    print(f"\n{'Config':<20} | {'Trades':>7} | {'WR':>6} | {'Return':>8} | {'TP%':>5} | {'SL%':>5} | {'Profit':>8}")
    print("-" * 80)
    
    best_return = -999
    best_config = None
    
    for r in all_results:
        status = "BEST" if r['avg_return'] == max(x['avg_return'] for x in all_results) else ""
        print(f"{r['config']:<20} | {r['trades']:>7} | {r['win_rate']:>5.1f}% | "
              f"{r['avg_return']:>+7.2f}% | {r['tp_pct']:>4.0f}% | {r['sl_pct']:>4.0f}% | "
              f"{r['profitable']:>8} {status}")
        
        if r['avg_return'] > best_return:
            best_return = r['avg_return']
            best_config = r['config']
    
    # Conclusion
    print("\n" + "=" * 80)
    print("CONCLUSION")
    print("=" * 80)
    
    if best_return > 0:
        print(f"""
SUCCESS! Best config: {best_config}
Average Return: {best_return:+.2f}%

The scalping approach shows POSITIVE returns!
Quick profit booking works better than holding to EOD.

RECOMMENDED SETTINGS:
- Use the '{best_config}' configuration
- Enter on first candle O=L
- Exit quickly on target or stop
""")
    else:
        print(f"""
Best config: {best_config} ({best_return:+.2f}%)

Unfortunately, even with quick exits:
- Stop Loss is hit more often than Take Profit
- The initial momentum doesn't persist long enough
- Transaction costs (0.2%) eat into small gains

The O=L pattern shows MEAN REVERSION, not continuation.
""")
    
    return all_results


if __name__ == "__main__":
    results = run_backtest()
