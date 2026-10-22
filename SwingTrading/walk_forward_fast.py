"""
WALK-FORWARD ANALYSIS - FAST VERSION
=====================================

Optimized for speed with reduced parameter grid.
Tests the core concept: does OOS performance match IS performance?
"""

import pandas as pd
import numpy as np
import yfinance as yf
from itertools import product
from typing import Dict, List
import warnings
warnings.filterwarnings('ignore')


# =====================================================================
# CORE INDICATORS
# =====================================================================

def ema(series, period):
    return series.ewm(span=period, adjust=False).mean()

def sma(series, period):
    return series.rolling(period).mean()

def atr(high, low, close, period=14):
    tr = pd.concat([high - low, 
                    abs(high - close.shift(1)), 
                    abs(low - close.shift(1))], axis=1).max(axis=1)
    return tr.rolling(period).mean()


# =====================================================================
# STRATEGY: Momentum + Quality (Simplified)
# =====================================================================

def momentum_signals(df, momentum_period=126, vol_threshold=0.5):
    """Momentum strategy with configurable parameters"""
    df = df.copy()
    
    df['Momentum'] = df['Close'].pct_change(momentum_period)
    df['Volatility'] = df['Close'].pct_change().rolling(20).std()
    df['Vol_percentile'] = df['Volatility'].rolling(252).apply(
        lambda x: pd.Series(x).rank(pct=True).iloc[-1] if len(x) > 0 else 0.5
    )
    df['ATR'] = atr(df['High'], df['Low'], df['Close'])
    
    df['Signal'] = 0
    strong_momentum = df['Momentum'] > df['Momentum'].rolling(252).quantile(0.7)
    low_vol = df['Vol_percentile'] < vol_threshold
    df.loc[strong_momentum & low_vol, 'Signal'] = 1
    
    return df


def ema_crossover_signals(df, fast_period=20, slow_period=50):
    """Simple EMA crossover"""
    df = df.copy()
    
    df['EMA_fast'] = ema(df['Close'], fast_period)
    df['EMA_slow'] = ema(df['Close'], slow_period)
    df['ATR'] = atr(df['High'], df['Low'], df['Close'])
    
    df['Signal'] = 0
    df.loc[(df['EMA_fast'] > df['EMA_slow']) & 
           (df['EMA_fast'].shift(1) <= df['EMA_slow'].shift(1)), 'Signal'] = 1
    df.loc[(df['EMA_fast'] < df['EMA_slow']) & 
           (df['EMA_fast'].shift(1) >= df['EMA_slow'].shift(1)), 'Signal'] = -1
    
    return df


# =====================================================================
# BACKTEST ENGINE (Simplified)
# =====================================================================

def backtest_period(df, signal_func, params, capital=100000):
    """Fast backtest for a single period"""
    
    df = signal_func(df, **params)
    
    trades = []
    position = None
    current_capital = capital
    
    slippage = 0.001
    commission = 0.001
    atr_sl = 2.0
    atr_tp = 4.0
    
    for i in range(60, len(df)):
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
# WALK-FORWARD ANALYSIS (Simplified)
# =====================================================================

def walk_forward_analysis(df, signal_func, param_grid, 
                          train_days=126, test_days=42):
    """
    Walk-Forward Analysis with reduced complexity.
    """
    n_bars = len(df)
    
    if n_bars < train_days + test_days + 60:
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
        
        if len(test_df) < 20:
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
                score = result['return']
                if result['trades'] < 2:
                    score *= 0.5
                
                if score > best_score:
                    best_score = score
                    best_params = params
                    best_is_result = result
            except:
                continue
        
        if best_params is None:
            start += test_days
            continue
        
        # Test on OOS data
        oos_result = backtest_period(test_df, signal_func, best_params)
        
        is_results.append(best_is_result)
        oos_results.append(oos_result)
        param_history.append(best_params)
        
        window_num += 1
        start += test_days
    
    if not oos_results:
        return None
    
    # Aggregate
    total_oos = sum(r['return'] for r in oos_results)
    avg_oos = np.mean([r['return'] for r in oos_results])
    total_is = sum(r['return'] for r in is_results)
    avg_is = np.mean([r['return'] for r in is_results])
    
    wf_efficiency = (avg_oos / avg_is * 100) if avg_is != 0 else 0
    
    return {
        'n_windows': len(oos_results),
        'total_oos': total_oos,
        'avg_oos': avg_oos,
        'avg_is': avg_is,
        'wf_efficiency': wf_efficiency,
        'total_trades': sum(r['trades'] for r in oos_results),
        'param_history': param_history
    }


# =====================================================================
# MAIN
# =====================================================================

def main():
    # Use fewer stocks for speed
    symbols = [
        'RELIANCE.NS', 'TCS.NS', 'HDFCBANK.NS', 'INFY.NS', 'SBIN.NS',
        'ITC.NS', 'AXISBANK.NS', 'LT.NS'
    ]
    
    # Strategies with smaller parameter grids
    strategies = {
        'Momentum': {
            'func': momentum_signals,
            'grid': {
                'momentum_period': [63, 126],
                'vol_threshold': [0.4, 0.6]
            }
        },
        'EMA Crossover': {
            'func': ema_crossover_signals,
            'grid': {
                'fast_period': [10, 20],
                'slow_period': [40, 60]
            }
        }
    }
    
    print("=" * 70)
    print("WALK-FORWARD ANALYSIS - STRATEGY VALIDATION")
    print("=" * 70)
    print(f"\nTesting {len(strategies)} strategies on {len(symbols)} stocks")
    print("Train: 126 days | Test: 42 days | Rolling windows")
    print()
    
    all_results = {}
    
    for strat_name, config in strategies.items():
        print(f"\n{'='*70}")
        print(f"Strategy: {strat_name}")
        print(f"{'='*70}")
        
        results = []
        
        for symbol in symbols:
            try:
                print(f"  {symbol}...", end=" ")
                
                df = yf.download(symbol, period='2y', interval='1d', progress=False)
                if isinstance(df.columns, pd.MultiIndex):
                    df.columns = df.columns.get_level_values(0)
                
                if df.empty or len(df) < 300:
                    print("Skip (not enough data)")
                    continue
                
                result = walk_forward_analysis(
                    df, config['func'], config['grid']
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
                
                print(f"OOS={result['avg_oos']:>6.2f}% | "
                      f"IS={result['avg_is']:>6.2f}% | "
                      f"WFE={result['wf_efficiency']:>5.0f}% | "
                      f"{status}")
                
            except Exception as e:
                print(f"Error: {e}")
        
        all_results[strat_name] = results
    
    # Summary
    print("\n" + "=" * 70)
    print("SUMMARY: WALK-FORWARD ANALYSIS RESULTS")
    print("=" * 70)
    
    summary = []
    
    for strat_name, results in all_results.items():
        if not results:
            continue
        
        df_r = pd.DataFrame(results)
        
        avg_oos = df_r['avg_oos'].mean()
        avg_wfe = df_r['wf_efficiency'].mean()
        profitable = (df_r['avg_oos'] > 0).sum()
        excellent = (df_r['wf_efficiency'] > 70).sum()
        
        summary.append({
            'Strategy': strat_name,
            'Avg OOS': avg_oos,
            'Avg WFE': avg_wfe,
            'Profitable': f"{profitable}/{len(df_r)}",
            'Excellent': f"{excellent}/{len(df_r)}"
        })
        
        print(f"\n{strat_name}:")
        print(f"  Avg OOS Return: {avg_oos:.2f}%")
        print(f"  Avg WF Efficiency: {avg_wfe:.1f}%")
        print(f"  Profitable Stocks: {profitable}/{len(df_r)}")
        print(f"  Excellent WFE (>70%): {excellent}/{len(df_r)}")
    
    # Comparison
    print("\n" + "=" * 70)
    print("COMPARISON TABLE")
    print("=" * 70)
    
    print(f"\n{'Strategy':<20} | {'OOS Return':>12} | {'WF Efficiency':>14} | {'Verdict':>10}")
    print("-" * 65)
    
    for row in summary:
        if row['Avg WFE'] > 70:
            verdict = "EXCELLENT"
        elif row['Avg WFE'] > 50:
            verdict = "GOOD"
        elif row['Avg OOS'] > 0:
            verdict = "MARGINAL"
        else:
            verdict = "AVOID"
        
        print(f"{row['Strategy']:<20} | {row['Avg OOS']:>10.2f}% | "
              f"{row['Avg WFE']:>12.1f}% | {verdict:>10}")
    
    # Interpretation
    print("\n" + "=" * 70)
    print("INTERPRETATION")
    print("=" * 70)
    print("""
Walk-Forward Efficiency (WFE) shows how well the strategy generalizes:

  WFE > 70%  : EXCELLENT - Strategy has real edge, likely to work live
  WFE 50-70% : GOOD      - Some edge exists, worth paper trading
  WFE 0-50%  : POOR      - Likely overfit, unreliable
  WFE < 0%   : FAILED    - No edge, avoid completely

If OOS returns are consistently negative:
  -> The strategy doesn't work, regardless of WFE

If OOS >> IS (WFE > 100%):
  -> Unusual, could be luck or adaptive params helping

KEY: Only trade strategies with WFE > 50% AND positive OOS returns
""")
    
    return summary


if __name__ == "__main__":
    summary = main()
