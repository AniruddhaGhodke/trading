"""
O=L SCALPING STRATEGY - Quick Profit Booking
=============================================

Smart approach:
1. First 1h candle is O=L (strong buying from open)
2. Enter at close of first candle
3. Target: Quick 0.5% to 1% profit
4. Exit: Hit target, hit stop, or after 2-3 candles
5. Don't wait for whole day - book profits early!

The reversal might happen later, but we're already out with profits.
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


def identify_OL_candles(df):
    """Identify first candles with O=L pattern"""
    if df is None or df.empty:
        return None
    
    df = df.copy()
    df['Date'] = df.index.date
    
    signals = []
    
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
        fc_volume = fc['Volume']
        
        # Check O=L
        tolerance = fc_open * 0.001  # 0.1% tolerance
        is_OL = abs(fc_open - fc_low) <= tolerance
        is_OH = abs(fc_open - fc_high) <= tolerance
        
        # Calculate candle strength
        candle_range = fc_high - fc_low
        if candle_range > 0:
            close_position = (fc_close - fc_low) / candle_range
        else:
            close_position = 0.5
        
        is_green = fc_close > fc_open
        first_candle_return = (fc_close / fc_open - 1) * 100
        
        if is_OL and not is_OH:
            signals.append({
                'date': date,
                'fc_open': fc_open,
                'fc_high': fc_high,
                'fc_low': fc_low,
                'fc_close': fc_close,
                'fc_volume': fc_volume,
                'is_green': is_green,
                'close_position': close_position,
                'first_candle_return': first_candle_return,
                'pattern': 'O=L'
            })
    
    return pd.DataFrame(signals) if signals else None


def scalp_backtest(df, signals_df,
                   target_pct=0.5,        # Take profit at 0.5%
                   stop_loss_pct=0.3,     # Stop loss at 0.3%
                   max_candles=3,         # Exit after max 3 candles if no TP/SL
                   require_green=False,   # Require green first candle?
                   require_strong=False,  # Require close in top 50%?
                   min_volume_mult=1.0,   # Volume must be >= X times average
                   capital=100000,
                   slippage=0.001,
                   commission=0.001):
    """
    Scalping backtest - quick entry and exit
    """
    if df is None or signals_df is None or signals_df.empty:
        return None
    
    df = df.copy()
    df['Date'] = df.index.date
    
    # Calculate average volume for volume filter
    df['Avg_Volume'] = df['Volume'].rolling(20).mean()
    
    trades = []
    current_capital = capital
    
    for _, signal in signals_df.iterrows():
        date = signal['date']
        
        # Apply filters
        if require_green and not signal['is_green']:
            continue
        
        if require_strong and signal['close_position'] < 0.5:
            continue
        
        day_data = df[df['Date'] == date].copy()
        
        if len(day_data) < 3:
            continue
        
        # Volume filter
        first_candle_vol = day_data.iloc[0]['Volume']
        avg_vol = day_data.iloc[0].get('Avg_Volume', first_candle_vol)
        if pd.notna(avg_vol) and avg_vol > 0:
            vol_ratio = first_candle_vol / avg_vol
        else:
            vol_ratio = 1.0
        
        if vol_ratio < min_volume_mult:
            continue
        
        # Entry at close of first candle
        entry_price = signal['fc_close'] * (1 + slippage)
        
        # Targets
        take_profit = entry_price * (1 + target_pct / 100)
        stop_loss = entry_price * (1 - stop_loss_pct / 100)
        
        # Position sizing (2% risk)
        risk_amount = current_capital * 0.02
        risk_per_share = entry_price - stop_loss
        shares = int(risk_amount / risk_per_share) if risk_per_share > 0 else 0
        shares = min(shares, int(current_capital * 0.25 / entry_price))
        
        if shares <= 0:
            continue
        
        # Simulate through next candles (max_candles)
        exit_price = None
        exit_reason = None
        exit_candle = 0
        
        candles_to_check = min(max_candles, len(day_data) - 1)
        
        for i in range(1, candles_to_check + 1):
            candle = day_data.iloc[i]
            
            # Check if target hit (use high of candle)
            if candle['High'] >= take_profit:
                exit_price = take_profit
                exit_reason = 'take_profit'
                exit_candle = i
                break
            
            # Check if stop hit (use low of candle)
            if candle['Low'] <= stop_loss:
                exit_price = stop_loss * (1 - slippage)
                exit_reason = 'stop_loss'
                exit_candle = i
                break
        
        # If no TP/SL hit, exit at close of last checked candle
        if exit_price is None:
            exit_candle = candles_to_check
            exit_price = day_data.iloc[exit_candle]['Close'] * (1 - slippage)
            exit_reason = 'time_exit'
        
        # Calculate P&L
        pnl = shares * (exit_price - entry_price)
        ret = (exit_price / entry_price - 1) * 100
        
        # Deduct commission
        pnl -= shares * (entry_price + exit_price) * commission
        current_capital += pnl
        
        trades.append({
            'date': date,
            'entry': entry_price,
            'exit': exit_price,
            'exit_reason': exit_reason,
            'exit_candle': exit_candle,
            'pnl': pnl,
            'return_pct': ret,
            'vol_ratio': vol_ratio,
            'is_green': signal['is_green'],
            'first_candle_ret': signal['first_candle_return']
        })
    
    return pd.DataFrame(trades) if trades else None


def run_scalp_analysis():
    """Run scalping analysis with different parameters"""
    
    symbols = [
        'RELIANCE.NS', 'TCS.NS', 'HDFCBANK.NS', 'INFY.NS', 'SBIN.NS',
        'BHARTIARTL.NS', 'ITC.NS', 'AXISBANK.NS', 'LT.NS', 'MARUTI.NS',
        'SUNPHARMA.NS', 'TITAN.NS', 'WIPRO.NS', 'BAJFINANCE.NS', 'ICICIBANK.NS'
    ]
    
    print("=" * 85)
    print("O=L SCALPING STRATEGY - QUICK PROFIT BOOKING")
    print("=" * 85)
    print("""
STRATEGY LOGIC:
1. First 1h candle has O=L (price never went below open)
2. Enter LONG at close of first candle
3. Target: 0.5% profit (quick scalp)
4. Stop Loss: 0.3%
5. Max hold: 3 candles (3 hours) then exit
6. Book profits BEFORE the reversal!

Testing multiple configurations...
""")
    
    # Test different configurations
    configs = [
        {'name': 'Basic O=L', 'require_green': False, 'require_strong': False, 'min_vol': 1.0, 'target': 0.5, 'sl': 0.3},
        {'name': 'Green O=L', 'require_green': True, 'require_strong': False, 'min_vol': 1.0, 'target': 0.5, 'sl': 0.3},
        {'name': 'Strong Green O=L', 'require_green': True, 'require_strong': True, 'min_vol': 1.0, 'target': 0.5, 'sl': 0.3},
        {'name': 'High Volume O=L', 'require_green': False, 'require_strong': False, 'min_vol': 1.5, 'target': 0.5, 'sl': 0.3},
        {'name': 'Green + Volume', 'require_green': True, 'require_strong': False, 'min_vol': 1.2, 'target': 0.5, 'sl': 0.3},
        {'name': 'Wider Target 1%', 'require_green': True, 'require_strong': False, 'min_vol': 1.0, 'target': 1.0, 'sl': 0.5},
        {'name': 'Tight Scalp 0.3%', 'require_green': True, 'require_strong': False, 'min_vol': 1.0, 'target': 0.3, 'sl': 0.2},
    ]
    
    all_config_results = []
    
    for config in configs:
        print(f"\n{'='*85}")
        print(f"CONFIG: {config['name']}")
        print(f"  Target: {config['target']}% | SL: {config['sl']}% | Green: {config['require_green']} | Strong: {config['require_strong']} | Min Vol: {config['min_vol']}x")
        print(f"{'='*85}")
        
        config_trades = []
        config_stats = []
        
        for symbol in symbols:
            df = download_intraday_data(symbol, days=60)
            
            if df is None or df.empty:
                continue
            
            signals = identify_OL_candles(df)
            
            if signals is None or signals.empty:
                continue
            
            trades = scalp_backtest(
                df, signals,
                target_pct=config['target'],
                stop_loss_pct=config['sl'],
                max_candles=3,
                require_green=config['require_green'],
                require_strong=config['require_strong'],
                min_volume_mult=config['min_vol']
            )
            
            if trades is None or trades.empty:
                print(f"  {symbol:<15} | No trades")
                continue
            
            n_trades = len(trades)
            n_wins = (trades['pnl'] > 0).sum()
            win_rate = n_wins / n_trades * 100
            total_return = trades['pnl'].sum() / 100000 * 100
            avg_return = trades['return_pct'].mean()
            
            # Exit reason breakdown
            tp_count = (trades['exit_reason'] == 'take_profit').sum()
            sl_count = (trades['exit_reason'] == 'stop_loss').sum()
            time_count = (trades['exit_reason'] == 'time_exit').sum()
            
            status = "OK" if total_return > 0 else "--"
            print(f"  {symbol:<15} | {n_trades:>3} trades | WR: {win_rate:>5.1f}% | "
                  f"Ret: {total_return:>+6.2f}% | TP:{tp_count} SL:{sl_count} Time:{time_count} {status}")
            
            config_trades.append(trades)
            config_stats.append({
                'symbol': symbol,
                'trades': n_trades,
                'win_rate': win_rate,
                'total_return': total_return,
                'avg_return': avg_return,
                'tp_pct': tp_count / n_trades * 100,
                'sl_pct': sl_count / n_trades * 100
            })
        
        # Config summary
        if config_stats:
            stats_df = pd.DataFrame(config_stats)
            
            total_trades = stats_df['trades'].sum()
            avg_wr = stats_df['win_rate'].mean()
            avg_return = stats_df['total_return'].mean()
            profitable = (stats_df['total_return'] > 0).sum()
            avg_tp_pct = stats_df['tp_pct'].mean()
            avg_sl_pct = stats_df['sl_pct'].mean()
            
            all_config_results.append({
                'config': config['name'],
                'trades': total_trades,
                'win_rate': avg_wr,
                'avg_return': avg_return,
                'profitable': f"{profitable}/{len(stats_df)}",
                'tp_pct': avg_tp_pct,
                'sl_pct': avg_sl_pct
            })
            
            print(f"\n  SUMMARY: {total_trades} trades | WR: {avg_wr:.1f}% | "
                  f"Avg Return: {avg_return:+.2f}% | Profitable: {profitable}/{len(stats_df)}")
            print(f"  Exit breakdown: TP: {avg_tp_pct:.1f}% | SL: {avg_sl_pct:.1f}% | Time: {100-avg_tp_pct-avg_sl_pct:.1f}%")
    
    # Final comparison
    print("\n" + "=" * 85)
    print("CONFIGURATION COMPARISON")
    print("=" * 85)
    
    print(f"\n{'Configuration':<20} | {'Trades':>7} | {'Win Rate':>9} | {'Return':>8} | {'Profitable':>11} | {'TP Hit':>7}")
    print("-" * 85)
    
    for r in all_config_results:
        status = "BEST" if r['avg_return'] > 0 else ""
        print(f"{r['config']:<20} | {r['trades']:>7} | {r['win_rate']:>8.1f}% | "
              f"{r['avg_return']:>+7.2f}% | {r['profitable']:>11} | {r['tp_pct']:>6.1f}% {status}")
    
    # Find best config
    if all_config_results:
        best = max(all_config_results, key=lambda x: x['avg_return'])
        
        print("\n" + "=" * 85)
        print("CONCLUSION")
        print("=" * 85)
        
        if best['avg_return'] > 0:
            print(f"""
BEST CONFIGURATION: {best['config']}
  - Average Return: {best['avg_return']:+.2f}%
  - Win Rate: {best['win_rate']:.1f}%
  - Take Profit Hit: {best['tp_pct']:.1f}%
  - Profitable Stocks: {best['profitable']}

This configuration shows POSITIVE returns!
The scalping approach works better than waiting for EOD.
""")
        else:
            print(f"""
Best configuration: {best['config']} ({best['avg_return']:+.2f}%)

Unfortunately, even with quick profit booking, the strategy doesn't
generate consistent profits after costs.

The issue is:
- Take Profit is hit only {best['tp_pct']:.1f}% of the time
- Stop Loss or Time Exit happen more often
- Transaction costs eat into small gains
""")
    
    return all_config_results


if __name__ == "__main__":
    results = run_scalp_analysis()
