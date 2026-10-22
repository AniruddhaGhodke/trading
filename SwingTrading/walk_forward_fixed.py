"""
WALK-FORWARD ANALYSIS - FIXED VERSION
======================================

Fixed issues:
1. Use strategies that generate signals in shorter windows
2. Proper warmup period handling
3. Better parameter ranges
"""

import pandas as pd
import numpy as np
import yfinance as yf
from itertools import product
import warnings
warnings.filterwarnings('ignore')


# =====================================================================
# INDICATORS
# =====================================================================

def ema(series, period):
    return series.ewm(span=period, adjust=False).mean()

def atr(high, low, close, period=14):
    tr = pd.concat([high - low, 
                    abs(high - close.shift(1)), 
                    abs(low - close.shift(1))], axis=1).max(axis=1)
    return tr.rolling(period).mean()

def rsi(prices, period=14):
    delta = prices.diff()
    gain = (delta.where(delta > 0, 0)).rolling(period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(period).mean()
    return 100 - (100 / (1 + gain / loss))


# =====================================================================
# STRATEGIES (Generate more signals)
# =====================================================================

def ema_crossover_signals(df, fast_period=10, slow_period=30):
    """EMA crossover - generates signals frequently"""
    df = df.copy()
    
    df['EMA_fast'] = ema(df['Close'], fast_period)
    df['EMA_slow'] = ema(df['Close'], slow_period)
    df['ATR'] = atr(df['High'], df['Low'], df['Close'])
    
    df['Signal'] = 0
    # Buy when fast crosses above slow
    buy_signal = (df['EMA_fast'] > df['EMA_slow']) & (df['EMA_fast'].shift(1) <= df['EMA_slow'].shift(1))
    # Sell when fast crosses below slow  
    sell_signal = (df['EMA_fast'] < df['EMA_slow']) & (df['EMA_fast'].shift(1) >= df['EMA_slow'].shift(1))
    
    df.loc[buy_signal, 'Signal'] = 1
    df.loc[sell_signal, 'Signal'] = -1
    
    return df


def rsi_reversal_signals(df, rsi_period=14, oversold=30, overbought=70):
    """RSI mean reversion - generates signals on extremes"""
    df = df.copy()
    
    df['RSI'] = rsi(df['Close'], rsi_period)
    df['ATR'] = atr(df['High'], df['Low'], df['Close'])
    
    df['Signal'] = 0
    # Buy when RSI crosses above oversold
    buy_signal = (df['RSI'] > oversold) & (df['RSI'].shift(1) <= oversold)
    # Sell when RSI crosses below overbought
    sell_signal = (df['RSI'] < overbought) & (df['RSI'].shift(1) >= overbought)
    
    df.loc[buy_signal, 'Signal'] = 1
    df.loc[sell_signal, 'Signal'] = -1
    
    return df


def breakout_signals(df, lookback=20):
    """Breakout strategy - buy new highs, sell new lows"""
    df = df.copy()
    
    df['High_N'] = df['High'].rolling(lookback).max()
    df['Low_N'] = df['Low'].rolling(lookback).min()
    df['ATR'] = atr(df['High'], df['Low'], df['Close'])
    
    df['Signal'] = 0
    # Buy when close breaks above N-day high
    df.loc[df['Close'] > df['High_N'].shift(1), 'Signal'] = 1
    # Sell when close breaks below N-day low
    df.loc[df['Close'] < df['Low_N'].shift(1), 'Signal'] = -1
    
    return df


# =====================================================================
# BACKTEST ENGINE
# =====================================================================

def backtest_period(df, signal_func, params, capital=100000):
    """Run backtest on a single period"""
    
    df = signal_func(df, **params)
    
    trades = []
    position = None
    current_capital = capital
    
    slippage = 0.001
    commission = 0.001
    atr_sl = 2.0
    atr_tp = 3.0
    
    # Start after indicator warmup
    warmup = max(params.get('slow_period', 30), params.get('lookback', 20), 30)
    
    for i in range(warmup, len(df)):
        row = df.iloc[i]
        curr_atr = row.get('ATR', 0)
        
        if pd.isna(curr_atr) or curr_atr <= 0:
            continue
        
        # Check exit
        if position is not None:
            exit_triggered = False
            
            if position['direction'] == 'long':
                if row['Low'] <= position['stop_loss']:
                    exit_price = position['stop_loss'] * (1 - slippage)
                    exit_triggered = True
                elif row['High'] >= position['take_profit']:
                    exit_price = position['take_profit']
                    exit_triggered = True
            else:
                if row['High'] >= position['stop_loss']:
                    exit_price = position['stop_loss'] * (1 + slippage)
                    exit_triggered = True
                elif row['Low'] <= position['take_profit']:
                    exit_price = position['take_profit']
                    exit_triggered = True
            
            if exit_triggered:
                if position['direction'] == 'long':
                    pnl = position['shares'] * (exit_price - position['entry_price'])
                else:
                    pnl = position['shares'] * (position['entry_price'] - exit_price)
                
                pnl -= position['shares'] * exit_price * commission
                current_capital += pnl
                trades.append(pnl)
                position = None
        
        # Check entry
        if position is None:
            signal = row.get('Signal', 0)
            
            if signal != 0:
                close = row['Close']
                sl_dist = curr_atr * atr_sl
                tp_dist = curr_atr * atr_tp
                
                if signal == 1:
                    entry_price = close * (1 + slippage)
                    stop_loss = entry_price - sl_dist
                    take_profit = entry_price + tp_dist
                    direction = 'long'
                else:
                    entry_price = close * (1 - slippage)
                    stop_loss = entry_price + sl_dist
                    take_profit = entry_price - tp_dist
                    direction = 'short'
                
                risk = current_capital * 0.02
                shares = int(risk / sl_dist) if sl_dist > 0 else 0
                shares = min(shares, int(current_capital * 0.25 / entry_price))
                
                if shares > 0:
                    current_capital -= shares * entry_price * commission
                    position = {
                        'direction': direction,
                        'entry_price': entry_price,
                        'stop_loss': stop_loss,
                        'take_profit': take_profit,
                        'shares': shares
                    }
    
    total_return = (current_capital / capital - 1) * 100
    n_trades = len(trades)
    win_rate = sum(1 for t in trades if t > 0) / n_trades if n_trades > 0 else 0
    
    return {
        'return': total_return,
        'trades': n_trades,
        'win_rate': win_rate
    }


# =====================================================================
# WALK-FORWARD ANALYSIS
# =====================================================================

def walk_forward_analysis(df, signal_func, param_grid, 
                          train_days=180, test_days=60):
    """
    Walk-Forward Analysis
    
    Uses longer windows to ensure enough signals are generated.
    """
    n_bars = len(df)
    
    min_required = train_days + test_days
    if n_bars < min_required:
        return None
    
    oos_results = []
    is_results = []
    param_history = []
    
    # Generate windows
    start = 0
    window_num = 0
    
    while start + train_days + test_days <= n_bars:
        train_start = start
        train_end = start + train_days
        test_start = train_end
        test_end = min(test_start + test_days, n_bars)
        
        train_df = df.iloc[train_start:train_end].copy()
        test_df = df.iloc[test_start:test_end].copy()
        
        if len(test_df) < 30:
            break
        
        # Find best params on training data
        best_score = -999
        best_params = None
        best_is_result = None
        
        param_names = list(param_grid.keys())
        param_values = list(param_grid.values())
        
        for values in product(*param_values):
            params = dict(zip(param_names, values))
            
            try:
                result = backtest_period(train_df, signal_func, params)
                
                # Score: return but penalize low trade counts
                score = result['return']
                if result['trades'] < 2:
                    score = -100  # Need at least 2 trades to be meaningful
                
                if score > best_score:
                    best_score = score
                    best_params = params
                    best_is_result = result
            except:
                continue
        
        if best_params is None or best_is_result is None:
            start += test_days
            continue
        
        # Test on OOS data with best params
        oos_result = backtest_period(test_df, signal_func, best_params)
        
        is_results.append(best_is_result)
        oos_results.append(oos_result)
        param_history.append({
            'window': window_num,
            'params': best_params,
            'is_return': best_is_result['return'],
            'is_trades': best_is_result['trades'],
            'oos_return': oos_result['return'],
            'oos_trades': oos_result['trades']
        })
        
        window_num += 1
        start += test_days
    
    if not oos_results:
        return None
    
    # Aggregate results
    total_oos = sum(r['return'] for r in oos_results)
    avg_oos = np.mean([r['return'] for r in oos_results])
    total_is = sum(r['return'] for r in is_results)
    avg_is = np.mean([r['return'] for r in is_results])
    total_oos_trades = sum(r['trades'] for r in oos_results)
    total_is_trades = sum(r['trades'] for r in is_results)
    
    # Walk-forward efficiency
    if avg_is != 0:
        wf_efficiency = (avg_oos / avg_is * 100)
    else:
        wf_efficiency = 0 if avg_oos == 0 else 100
    
    return {
        'n_windows': len(oos_results),
        'total_oos': total_oos,
        'avg_oos': avg_oos,
        'avg_is': avg_is,
        'wf_efficiency': wf_efficiency,
        'total_oos_trades': total_oos_trades,
        'total_is_trades': total_is_trades,
        'param_history': param_history
    }


# =====================================================================
# MAIN
# =====================================================================

def main():
    symbols = [
        'RELIANCE.NS', 'TCS.NS', 'HDFCBANK.NS', 'INFY.NS', 'SBIN.NS',
        'ITC.NS', 'AXISBANK.NS', 'LT.NS'
    ]
    
    # Strategies with parameters that generate signals
    strategies = {
        'EMA Crossover': {
            'func': ema_crossover_signals,
            'grid': {
                'fast_period': [5, 10, 15],
                'slow_period': [20, 30, 40]
            }
        },
        'RSI Reversal': {
            'func': rsi_reversal_signals,
            'grid': {
                'rsi_period': [10, 14, 20],
                'oversold': [25, 30, 35],
                'overbought': [65, 70, 75]
            }
        },
        'Breakout': {
            'func': breakout_signals,
            'grid': {
                'lookback': [10, 15, 20, 30]
            }
        }
    }
    
    print("=" * 75)
    print("WALK-FORWARD ANALYSIS - STRATEGY VALIDATION")
    print("=" * 75)
    print(f"\nTesting {len(strategies)} strategies on {len(symbols)} stocks")
    print("Train: 180 days | Test: 60 days | Rolling windows")
    print("Data: 2 years daily | Costs: 0.1% slippage + 0.1% commission")
    print()
    
    all_results = {}
    
    for strat_name, config in strategies.items():
        print(f"\n{'='*75}")
        print(f"Strategy: {strat_name}")
        print(f"Parameters tested: {config['grid']}")
        print(f"{'='*75}")
        
        results = []
        
        for symbol in symbols:
            try:
                print(f"  {symbol:<15}", end=" ")
                
                df = yf.download(symbol, period='2y', interval='1d', progress=False)
                if isinstance(df.columns, pd.MultiIndex):
                    df.columns = df.columns.get_level_values(0)
                
                if df.empty or len(df) < 300:
                    print("Skip (not enough data)")
                    continue
                
                result = walk_forward_analysis(
                    df, config['func'], config['grid'],
                    train_days=180, test_days=60
                )
                
                if result is None:
                    print("Skip (WFA failed)")
                    continue
                
                results.append({
                    'symbol': symbol,
                    **result
                })
                
                # Status
                if result['wf_efficiency'] > 70:
                    status = "EXCELLENT"
                elif result['wf_efficiency'] > 50:
                    status = "GOOD"
                elif result['wf_efficiency'] > 0:
                    status = "POOR"
                else:
                    status = "FAILED"
                
                print(f"IS={result['avg_is']:>6.2f}% ({result['total_is_trades']:>3} tr) | "
                      f"OOS={result['avg_oos']:>6.2f}% ({result['total_oos_trades']:>3} tr) | "
                      f"WFE={result['wf_efficiency']:>6.1f}% | {status}")
                
            except Exception as e:
                print(f"Error: {e}")
        
        all_results[strat_name] = results
    
    # Summary
    print("\n" + "=" * 75)
    print("SUMMARY: WALK-FORWARD ANALYSIS RESULTS")
    print("=" * 75)
    
    summary = []
    
    for strat_name, results in all_results.items():
        if not results:
            continue
        
        df_r = pd.DataFrame(results)
        
        avg_is = df_r['avg_is'].mean()
        avg_oos = df_r['avg_oos'].mean()
        avg_wfe = df_r['wf_efficiency'].mean()
        total_is_trades = df_r['total_is_trades'].sum()
        total_oos_trades = df_r['total_oos_trades'].sum()
        profitable = (df_r['avg_oos'] > 0).sum()
        
        summary.append({
            'Strategy': strat_name,
            'Avg IS': avg_is,
            'Avg OOS': avg_oos,
            'Avg WFE': avg_wfe,
            'IS Trades': total_is_trades,
            'OOS Trades': total_oos_trades,
            'Profitable': f"{profitable}/{len(df_r)}"
        })
        
        print(f"\n{strat_name}:")
        print(f"  Avg In-Sample Return: {avg_is:.2f}% ({total_is_trades} trades)")
        print(f"  Avg Out-of-Sample Return: {avg_oos:.2f}% ({total_oos_trades} trades)")
        print(f"  Walk-Forward Efficiency: {avg_wfe:.1f}%")
        print(f"  Profitable Stocks OOS: {profitable}/{len(df_r)}")
    
    # Comparison table
    print("\n" + "=" * 75)
    print("STRATEGY COMPARISON (Sorted by OOS Return)")
    print("=" * 75)
    
    summary_df = pd.DataFrame(summary).sort_values('Avg OOS', ascending=False)
    
    print(f"\n{'Strategy':<18} | {'IS Return':>10} | {'OOS Return':>11} | {'WFE':>8} | {'Verdict':>10}")
    print("-" * 70)
    
    for _, row in summary_df.iterrows():
        if row['Avg WFE'] > 70 and row['Avg OOS'] > 0:
            verdict = "EXCELLENT"
        elif row['Avg WFE'] > 50 and row['Avg OOS'] > 0:
            verdict = "GOOD"
        elif row['Avg OOS'] > 0:
            verdict = "MARGINAL"
        else:
            verdict = "AVOID"
        
        print(f"{row['Strategy']:<18} | {row['Avg IS']:>9.2f}% | "
              f"{row['Avg OOS']:>10.2f}% | {row['Avg WFE']:>7.1f}% | {verdict:>10}")
    
    # Key insights
    print("\n" + "=" * 75)
    print("KEY INSIGHTS")
    print("=" * 75)
    print("""
UNDERSTANDING THE RESULTS:

1. IN-SAMPLE (IS) = Performance during parameter optimization
   - Higher IS suggests parameters were found that worked historically
   - Too high IS (>5%) might indicate overfitting

2. OUT-OF-SAMPLE (OOS) = Performance on unseen data  
   - This is the TRUE test of strategy viability
   - Positive OOS = Strategy has real predictive power

3. WALK-FORWARD EFFICIENCY (WFE) = OOS / IS * 100
   - WFE > 70%: Excellent - strategy generalizes well
   - WFE 50-70%: Good - some edge persists
   - WFE < 50%: Poor - mostly overfit
   - WFE < 0%: Failed - strategy is broken

4. WHAT THE RESULTS TELL US:
   - If IS > 0 but OOS < 0: Strategy is OVERFIT to history
   - If IS ~ OOS: Strategy is ROBUST
   - If OOS > IS: Unusual but possible (lucky or adaptive)

5. ACTIONABLE CONCLUSIONS:
   - Only consider strategies with OOS > 0%
   - Prefer strategies with WFE > 50%
   - Paper trade for 3+ months before going live
   - Monitor for regime changes
""")
    
    # Parameter stability analysis
    print("\n" + "=" * 75)
    print("PARAMETER STABILITY ANALYSIS")
    print("=" * 75)
    
    for strat_name, results in all_results.items():
        if not results:
            continue
        
        print(f"\n{strat_name}:")
        
        # Collect all optimal params across all windows and stocks
        all_params = {}
        for r in results:
            for h in r['param_history']:
                for k, v in h['params'].items():
                    if k not in all_params:
                        all_params[k] = []
                    all_params[k].append(v)
        
        for param_name, values in all_params.items():
            unique_vals = list(set(values))
            most_common = max(set(values), key=values.count)
            stability = values.count(most_common) / len(values) * 100
            print(f"  {param_name}: Most common={most_common}, "
                  f"Stability={stability:.0f}%, Options={unique_vals}")
    
    return summary_df


if __name__ == "__main__":
    summary = main()
