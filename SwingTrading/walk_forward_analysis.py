"""
WALK-FORWARD ANALYSIS (WFA)
===========================

Walk-Forward Analysis is the GOLD STANDARD for validating trading strategies.
It prevents overfitting by testing on truly out-of-sample data.

How it works:
1. Divide data into rolling windows
2. TRAIN: Optimize parameters on training window (e.g., 6 months)
3. TEST: Apply optimized parameters to test window (e.g., 2 months)
4. ROLL: Move window forward and repeat
5. AGGREGATE: Combine all out-of-sample results

Why it's better than simple backtesting:
- Parameters are optimized BEFORE each test period
- Simulates real trading (you can't see the future)
- Reveals if strategy adapts to changing markets
- Shows parameter stability (good strategy = stable params)
"""

import pandas as pd
import numpy as np
import yfinance as yf
from itertools import product
from typing import Dict, List, Tuple, Callable
from dataclasses import dataclass
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

def rsi(prices, period=14):
    delta = prices.diff()
    gain = (delta.where(delta > 0, 0)).rolling(period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(period).mean()
    return 100 - (100 / (1 + gain / loss))

def adx(high, low, close, period=14):
    plus_dm = high.diff()
    minus_dm = low.diff().abs() * -1
    plus_dm[plus_dm < 0] = 0
    minus_dm[minus_dm > 0] = 0
    
    tr = pd.concat([high - low, 
                    abs(high - close.shift(1)), 
                    abs(low - close.shift(1))], axis=1).max(axis=1)
    
    atr_val = tr.rolling(period).mean()
    plus_di = 100 * (plus_dm.rolling(period).mean() / atr_val)
    minus_di = 100 * (minus_dm.abs().rolling(period).mean() / atr_val)
    
    dx = 100 * abs(plus_di - minus_di) / (plus_di + minus_di)
    adx_val = dx.rolling(period).mean()
    
    return adx_val, plus_di, minus_di


# =====================================================================
# STRATEGY DEFINITIONS (Parameterized)
# =====================================================================

@dataclass
class StrategyParams:
    """Container for strategy parameters"""
    name: str
    params: Dict

def momentum_quality_signals(df, momentum_period=126, vol_lookback=20, vol_threshold=0.5):
    """Momentum + Quality strategy with configurable parameters"""
    df = df.copy()
    
    df['Momentum'] = df['Close'].pct_change(momentum_period)
    df['Volatility'] = df['Close'].pct_change().rolling(vol_lookback).std()
    df['Vol_percentile'] = df['Volatility'].rolling(252).apply(
        lambda x: pd.Series(x).rank(pct=True).iloc[-1] if len(x) > 0 else 0.5
    )
    df['ATR'] = atr(df['High'], df['Low'], df['Close'])
    
    df['Signal'] = 0
    strong_momentum = df['Momentum'] > df['Momentum'].rolling(252).quantile(0.7)
    low_vol = df['Vol_percentile'] < vol_threshold
    df.loc[strong_momentum & low_vol, 'Signal'] = 1
    
    return df

def regime_filtered_signals(df, adx_threshold=25, ema_fast=20, ema_slow=50):
    """Regime-filtered trend following"""
    df = df.copy()
    
    df['EMA_fast'] = ema(df['Close'], ema_fast)
    df['EMA_slow'] = ema(df['Close'], ema_slow)
    df['ADX'], df['Plus_DI'], df['Minus_DI'] = adx(df['High'], df['Low'], df['Close'])
    df['ATR'] = atr(df['High'], df['Low'], df['Close'])
    
    strong_trend = df['ADX'] > adx_threshold
    uptrend = df['EMA_fast'] > df['EMA_slow']
    downtrend = df['EMA_fast'] < df['EMA_slow']
    
    df['Signal'] = 0
    df.loc[strong_trend & uptrend & (df['Plus_DI'] > df['Minus_DI']), 'Signal'] = 1
    df.loc[strong_trend & downtrend & (df['Minus_DI'] > df['Plus_DI']), 'Signal'] = -1
    
    return df

def mean_reversion_signals(df, adx_max=20, rsi_low=25, rsi_high=75, bb_period=20, bb_std=2):
    """Mean reversion with filters"""
    df = df.copy()
    
    df['RSI'] = rsi(df['Close'])
    df['ADX'], _, _ = adx(df['High'], df['Low'], df['Close'])
    df['ATR'] = atr(df['High'], df['Low'], df['Close'])
    
    df['BB_mid'] = sma(df['Close'], bb_period)
    df['BB_std'] = df['Close'].rolling(bb_period).std()
    df['BB_upper'] = df['BB_mid'] + bb_std * df['BB_std']
    df['BB_lower'] = df['BB_mid'] - bb_std * df['BB_std']
    
    range_bound = df['ADX'] < adx_max
    oversold = (df['RSI'] < rsi_low) & (df['Close'] < df['BB_lower'])
    overbought = (df['RSI'] > rsi_high) & (df['Close'] > df['BB_upper'])
    
    df['Signal'] = 0
    df.loc[range_bound & oversold, 'Signal'] = 1
    df.loc[range_bound & overbought, 'Signal'] = -1
    
    return df


# =====================================================================
# BACKTEST ENGINE (Single Period)
# =====================================================================

def backtest_period(df, signal_func, params, capital=100000, 
                    atr_sl=2.0, atr_tp=4.0, slippage=0.001, commission=0.001):
    """
    Run backtest on a specific period with given parameters.
    Returns performance metrics.
    """
    # Generate signals with parameters
    df = signal_func(df, **params)
    
    trades = []
    current_capital = capital
    position = None
    
    warmup = 60  # Skip first 60 bars for indicator warmup
    
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
                    exit_reason = 'stop_loss'
                elif row['High'] >= position['take_profit']:
                    exit_price = position['take_profit']
                    exit_triggered = True
                    exit_reason = 'take_profit'
            else:
                if row['High'] >= position['stop_loss']:
                    exit_price = position['stop_loss'] * (1 + slippage)
                    exit_triggered = True
                    exit_reason = 'stop_loss'
                elif row['Low'] <= position['take_profit']:
                    exit_price = position['take_profit']
                    exit_triggered = True
                    exit_reason = 'take_profit'
            
            if exit_triggered:
                if position['direction'] == 'long':
                    pnl = position['shares'] * (exit_price - position['entry_price'])
                else:
                    pnl = position['shares'] * (position['entry_price'] - exit_price)
                
                pnl -= position['shares'] * exit_price * commission
                current_capital += pnl
                
                trades.append({'pnl': pnl, 'reason': exit_reason})
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
                
                # Position sizing (2% risk)
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
    
    # Calculate results
    total_return = (current_capital / capital - 1) * 100
    n_trades = len(trades)
    
    if n_trades > 0:
        wins = sum(1 for t in trades if t['pnl'] > 0)
        win_rate = wins / n_trades
        total_pnl = sum(t['pnl'] for t in trades)
        win_pnl = sum(t['pnl'] for t in trades if t['pnl'] > 0)
        loss_pnl = abs(sum(t['pnl'] for t in trades if t['pnl'] < 0))
        profit_factor = win_pnl / loss_pnl if loss_pnl > 0 else 0
    else:
        win_rate = 0
        profit_factor = 0
    
    return {
        'return': total_return,
        'trades': n_trades,
        'win_rate': win_rate,
        'profit_factor': profit_factor,
        'final_capital': current_capital
    }


# =====================================================================
# PARAMETER OPTIMIZATION (Grid Search)
# =====================================================================

def optimize_parameters(df, signal_func, param_grid, capital=100000):
    """
    Find best parameters on training data using grid search.
    
    Args:
        df: Training data
        signal_func: Strategy signal function
        param_grid: Dict of parameter names -> list of values to try
    
    Returns:
        Best parameters and their performance
    """
    best_result = {'return': -999}
    best_params = None
    all_results = []
    
    # Generate all parameter combinations
    param_names = list(param_grid.keys())
    param_values = list(param_grid.values())
    
    for values in product(*param_values):
        params = dict(zip(param_names, values))
        
        try:
            result = backtest_period(df, signal_func, params, capital)
            result['params'] = params
            all_results.append(result)
            
            # Optimize for risk-adjusted return
            # Penalize low trade counts (unreliable)
            score = result['return']
            if result['trades'] < 3:
                score = score * 0.5  # Penalize too few trades
            
            if score > best_result.get('score', -999):
                best_result = result
                best_result['score'] = score
                best_params = params
                
        except Exception as e:
            continue
    
    return best_params, best_result, all_results


# =====================================================================
# WALK-FORWARD ANALYSIS ENGINE
# =====================================================================

class WalkForwardAnalysis:
    """
    Walk-Forward Analysis Framework
    
    Splits data into rolling windows, optimizes on training, tests on out-of-sample.
    """
    
    def __init__(self, train_days=126, test_days=42, step_days=42):
        """
        Args:
            train_days: Days in training window (default 6 months = 126)
            test_days: Days in test window (default 2 months = 42)
            step_days: Days to step forward (default same as test)
        """
        self.train_days = train_days
        self.test_days = test_days
        self.step_days = step_days
    
    def run(self, df, signal_func, param_grid, capital=100000, verbose=True):
        """
        Run walk-forward analysis.
        
        Returns:
            Dict with aggregated OOS results and parameter stability analysis
        """
        n_bars = len(df)
        min_required = self.train_days + self.test_days + 60  # +60 for warmup
        
        if n_bars < min_required:
            return {'error': f'Need {min_required} bars, have {n_bars}'}
        
        windows = []
        oos_results = []
        is_results = []
        optimal_params_history = []
        
        # Generate rolling windows
        start = 0
        window_num = 0
        
        while start + self.train_days + self.test_days <= n_bars:
            train_start = start
            train_end = start + self.train_days
            test_start = train_end
            test_end = min(test_start + self.test_days, n_bars)
            
            windows.append({
                'window': window_num,
                'train_start': train_start,
                'train_end': train_end,
                'test_start': test_start,
                'test_end': test_end
            })
            
            window_num += 1
            start += self.step_days
        
        if verbose:
            print(f"\nWalk-Forward Analysis")
            print(f"=" * 60)
            print(f"Train window: {self.train_days} days | Test window: {self.test_days} days")
            print(f"Number of windows: {len(windows)}")
            print(f"-" * 60)
        
        # Process each window
        for w in windows:
            train_df = df.iloc[w['train_start']:w['train_end']].copy()
            test_df = df.iloc[w['test_start']:w['test_end']].copy()
            
            # Need enough data in test window
            if len(test_df) < 20:
                continue
            
            # Optimize on training data
            best_params, is_result, _ = optimize_parameters(
                train_df, signal_func, param_grid, capital
            )
            
            if best_params is None:
                continue
            
            # Test on out-of-sample data
            oos_result = backtest_period(test_df, signal_func, best_params, capital)
            
            # Store results
            is_results.append(is_result)
            oos_results.append(oos_result)
            optimal_params_history.append({
                'window': w['window'],
                'params': best_params,
                'is_return': is_result['return'],
                'oos_return': oos_result['return']
            })
            
            if verbose:
                print(f"Window {w['window']:>2}: IS={is_result['return']:>7.2f}% | "
                      f"OOS={oos_result['return']:>7.2f}% | "
                      f"Trades={oos_result['trades']:>2} | "
                      f"Params={best_params}")
        
        if not oos_results:
            return {'error': 'No valid windows processed'}
        
        # Aggregate results
        total_oos_return = sum(r['return'] for r in oos_results)
        avg_oos_return = np.mean([r['return'] for r in oos_results])
        total_is_return = sum(r['return'] for r in is_results)
        avg_is_return = np.mean([r['return'] for r in is_results])
        
        # Walk-forward efficiency (OOS / IS)
        wf_efficiency = (avg_oos_return / avg_is_return * 100) if avg_is_return != 0 else 0
        
        # Parameter stability analysis
        param_stability = self._analyze_param_stability(optimal_params_history)
        
        # Aggregate metrics
        total_trades = sum(r['trades'] for r in oos_results)
        avg_win_rate = np.mean([r['win_rate'] for r in oos_results if r['trades'] > 0])
        
        results = {
            'n_windows': len(windows),
            'n_valid_windows': len(oos_results),
            'total_oos_return': total_oos_return,
            'avg_oos_return': avg_oos_return,
            'total_is_return': total_is_return,
            'avg_is_return': avg_is_return,
            'wf_efficiency': wf_efficiency,
            'total_trades': total_trades,
            'avg_win_rate': avg_win_rate,
            'param_stability': param_stability,
            'param_history': optimal_params_history,
            'oos_results': oos_results,
            'is_results': is_results
        }
        
        if verbose:
            self._print_summary(results)
        
        return results
    
    def _analyze_param_stability(self, history):
        """Analyze how stable optimal parameters are across windows"""
        if not history:
            return {}
        
        stability = {}
        param_names = list(history[0]['params'].keys())
        
        for name in param_names:
            values = [h['params'][name] for h in history]
            stability[name] = {
                'values': values,
                'unique': len(set(values)),
                'most_common': max(set(values), key=values.count),
                'stability_pct': values.count(max(set(values), key=values.count)) / len(values) * 100
            }
        
        return stability
    
    def _print_summary(self, results):
        """Print summary of WFA results"""
        print(f"\n" + "=" * 60)
        print("WALK-FORWARD ANALYSIS SUMMARY")
        print("=" * 60)
        
        print(f"\nPerformance:")
        print(f"  Total OOS Return: {results['total_oos_return']:.2f}%")
        print(f"  Avg OOS Return/Window: {results['avg_oos_return']:.2f}%")
        print(f"  Total IS Return: {results['total_is_return']:.2f}%")
        print(f"  Avg IS Return/Window: {results['avg_is_return']:.2f}%")
        
        print(f"\nWalk-Forward Efficiency: {results['wf_efficiency']:.1f}%")
        print(f"  (Good: >50%, Excellent: >70%)")
        
        if results['wf_efficiency'] > 70:
            print(f"  -> EXCELLENT: Strategy generalizes well!")
        elif results['wf_efficiency'] > 50:
            print(f"  -> GOOD: Strategy has some edge")
        elif results['wf_efficiency'] > 0:
            print(f"  -> POOR: Strategy may be overfit")
        else:
            print(f"  -> FAILED: Strategy doesn't work OOS")
        
        print(f"\nParameter Stability:")
        for name, stats in results['param_stability'].items():
            print(f"  {name}: {stats['most_common']} ({stats['stability_pct']:.0f}% stable)")
        
        print(f"\nTrades: {results['total_trades']} total | "
              f"Avg Win Rate: {results['avg_win_rate']:.1%}")


# =====================================================================
# MAIN - RUN WFA ON ALL STRATEGIES
# =====================================================================

def run_walk_forward_analysis():
    """Run WFA on multiple strategies and stocks"""
    
    symbols = [
        'RELIANCE.NS', 'TCS.NS', 'HDFCBANK.NS', 'INFY.NS', 'SBIN.NS',
        'BHARTIARTL.NS', 'ITC.NS', 'AXISBANK.NS', 'LT.NS', 'MARUTI.NS',
        'SUNPHARMA.NS', 'TITAN.NS', 'WIPRO.NS', 'BAJFINANCE.NS', 'ICICIBANK.NS'
    ]
    
    # Strategy configurations with parameter grids
    strategies = {
        'Momentum + Quality': {
            'signal_func': momentum_quality_signals,
            'param_grid': {
                'momentum_period': [63, 126, 189],  # 3, 6, 9 months
                'vol_lookback': [10, 20, 30],
                'vol_threshold': [0.3, 0.5, 0.7]
            }
        },
        'Regime Filtered Trend': {
            'signal_func': regime_filtered_signals,
            'param_grid': {
                'adx_threshold': [20, 25, 30],
                'ema_fast': [10, 20, 30],
                'ema_slow': [40, 50, 60]
            }
        },
        'Mean Reversion': {
            'signal_func': mean_reversion_signals,
            'param_grid': {
                'adx_max': [15, 20, 25],
                'rsi_low': [20, 25, 30],
                'rsi_high': [70, 75, 80]
            }
        }
    }
    
    print("=" * 80)
    print("WALK-FORWARD ANALYSIS - COMPREHENSIVE STRATEGY VALIDATION")
    print("=" * 80)
    print("""
This analysis validates strategies using rolling out-of-sample testing.
A strategy that passes WFA is more likely to work in live trading.

Key metrics:
- WF Efficiency > 70%: Excellent (strategy generalizes well)
- WF Efficiency > 50%: Good (some real edge exists)
- WF Efficiency < 50%: Poor (likely overfit)
- WF Efficiency < 0%: Failed (no edge, avoid)
""")
    
    wfa = WalkForwardAnalysis(train_days=126, test_days=42, step_days=42)
    
    all_strategy_results = {}
    
    for strategy_name, config in strategies.items():
        print(f"\n{'='*80}")
        print(f"STRATEGY: {strategy_name}")
        print(f"{'='*80}")
        print(f"Parameter Grid: {config['param_grid']}")
        
        strategy_results = []
        
        for symbol in symbols:
            print(f"\n--- {symbol} ---")
            
            try:
                df = yf.download(symbol, period='2y', interval='1d', progress=False)
                if isinstance(df.columns, pd.MultiIndex):
                    df.columns = df.columns.get_level_values(0)
                
                if df.empty or len(df) < 300:
                    print(f"  Skipped: Not enough data ({len(df)} bars)")
                    continue
                
                result = wfa.run(
                    df, 
                    config['signal_func'], 
                    config['param_grid'],
                    verbose=False
                )
                
                if 'error' in result:
                    print(f"  Error: {result['error']}")
                    continue
                
                strategy_results.append({
                    'symbol': symbol,
                    'oos_return': result['avg_oos_return'],
                    'is_return': result['avg_is_return'],
                    'wf_efficiency': result['wf_efficiency'],
                    'trades': result['total_trades'],
                    'win_rate': result['avg_win_rate'],
                    'n_windows': result['n_valid_windows']
                })
                
                # Determine status
                if result['wf_efficiency'] > 70:
                    status = "EXCELLENT"
                elif result['wf_efficiency'] > 50:
                    status = "GOOD"
                elif result['wf_efficiency'] > 0:
                    status = "POOR"
                else:
                    status = "FAILED"
                
                print(f"  OOS: {result['avg_oos_return']:>6.2f}% | "
                      f"IS: {result['avg_is_return']:>6.2f}% | "
                      f"WFE: {result['wf_efficiency']:>5.1f}% | "
                      f"Trades: {result['total_trades']:>3} | "
                      f"{status}")
                
            except Exception as e:
                print(f"  Error: {e}")
                continue
        
        all_strategy_results[strategy_name] = strategy_results
    
    # Print final summary
    print("\n" + "=" * 80)
    print("FINAL WALK-FORWARD ANALYSIS SUMMARY")
    print("=" * 80)
    
    summary = []
    
    for strategy_name, results in all_strategy_results.items():
        if not results:
            continue
        
        df_results = pd.DataFrame(results)
        
        avg_oos = df_results['oos_return'].mean()
        avg_wfe = df_results['wf_efficiency'].mean()
        total_trades = df_results['trades'].sum()
        profitable = (df_results['oos_return'] > 0).sum()
        excellent = (df_results['wf_efficiency'] > 70).sum()
        
        summary.append({
            'Strategy': strategy_name,
            'Avg OOS Return': avg_oos,
            'Avg WF Efficiency': avg_wfe,
            'Total Trades': total_trades,
            'Profitable Stocks': f"{profitable}/{len(df_results)}",
            'Excellent WFE': f"{excellent}/{len(df_results)}"
        })
        
        print(f"\n{strategy_name}")
        print("-" * 60)
        print(f"  Avg OOS Return: {avg_oos:.2f}%")
        print(f"  Avg WF Efficiency: {avg_wfe:.1f}%")
        print(f"  Total Trades: {total_trades}")
        print(f"  Profitable Stocks: {profitable}/{len(df_results)}")
        print(f"  Excellent WFE (>70%): {excellent}/{len(df_results)}")
    
    # Comparison table
    print("\n" + "=" * 80)
    print("STRATEGY COMPARISON (Walk-Forward Validated)")
    print("=" * 80)
    
    summary_df = pd.DataFrame(summary).sort_values('Avg OOS Return', ascending=False)
    
    print(f"\n{'Strategy':<25} | {'OOS Return':<12} | {'WF Efficiency':<14} | {'Verdict':<10}")
    print("-" * 70)
    
    for _, row in summary_df.iterrows():
        if row['Avg WF Efficiency'] > 70:
            verdict = "EXCELLENT"
        elif row['Avg WF Efficiency'] > 50:
            verdict = "GOOD"
        elif row['Avg OOS Return'] > 0:
            verdict = "MARGINAL"
        else:
            verdict = "AVOID"
        
        print(f"{row['Strategy']:<25} | {row['Avg OOS Return']:>10.2f}% | "
              f"{row['Avg WF Efficiency']:>12.1f}% | {verdict:<10}")
    
    # Key insights
    print("\n" + "=" * 80)
    print("KEY INSIGHTS FROM WALK-FORWARD ANALYSIS")
    print("=" * 80)
    print("""
1. WALK-FORWARD EFFICIENCY (WFE) IS CRITICAL
   - WFE shows how much of in-sample edge persists out-of-sample
   - Low WFE = Strategy is overfit to historical data
   - High WFE = Strategy has real, persistent edge

2. PARAMETER STABILITY MATTERS
   - If optimal parameters change every window, strategy is fragile
   - Robust strategies have stable parameters across time

3. FEWER TRADES CAN BE BETTER
   - More selective = higher quality signals
   - But too few trades = unreliable statistics

4. COMPARE IS vs OOS RETURNS
   - IS >> OOS: Overfitting problem
   - IS ~ OOS: Robust strategy
   - IS < OOS: Rare but possible (underfit or lucky)

5. WHAT TO DO NEXT
   - Only trade strategies with WFE > 50%
   - Use the most stable parameters
   - Paper trade before going live
   - Monitor for regime changes
""")
    
    return summary_df


if __name__ == "__main__":
    summary = run_walk_forward_analysis()
