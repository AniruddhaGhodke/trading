"""
Backtesting utilities for trading strategies.
Provides common functions for performance calculation and visualization.
"""

import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
from typing import Dict, Tuple, Optional


def calculate_returns(prices: pd.Series) -> pd.Series:
    """Calculate daily returns from price series."""
    return prices.pct_change().fillna(0)


def calculate_cumulative_returns(returns: pd.Series) -> pd.Series:
    """Calculate cumulative returns from daily returns."""
    return (1 + returns).cumprod() - 1


def calculate_sharpe_ratio(returns: pd.Series, risk_free_rate: float = 0.0, periods_per_year: int = 252) -> float:
    """
    Calculate annualized Sharpe ratio.

    Args:
        returns: Daily returns series
        risk_free_rate: Annual risk-free rate (default 0)
        periods_per_year: Trading days per year (default 252)
    """
    if returns.std() == 0:
        return 0.0
    excess_returns = returns - risk_free_rate / periods_per_year
    return np.sqrt(periods_per_year) * excess_returns.mean() / returns.std()


def calculate_max_drawdown(cumulative_returns: pd.Series) -> Tuple[float, pd.Timestamp, pd.Timestamp]:
    """
    Calculate maximum drawdown and its period.

    Returns:
        Tuple of (max_drawdown, peak_date, trough_date)
    """
    wealth = 1 + cumulative_returns
    peak = wealth.expanding(min_periods=1).max()
    drawdown = (wealth - peak) / peak

    max_dd = drawdown.min()
    trough_date = drawdown.idxmin()
    peak_date = wealth[:trough_date].idxmax()

    return max_dd, peak_date, trough_date


def calculate_win_rate(trades: pd.DataFrame) -> float:
    """Calculate win rate from trades dataframe with 'pnl' column."""
    if len(trades) == 0:
        return 0.0
    return (trades['pnl'] > 0).mean()


def calculate_profit_factor(trades: pd.DataFrame) -> float:
    """Calculate profit factor (gross profit / gross loss)."""
    if len(trades) == 0:
        return 0.0
    gross_profit = trades[trades['pnl'] > 0]['pnl'].sum()
    gross_loss = abs(trades[trades['pnl'] < 0]['pnl'].sum())
    if gross_loss == 0:
        return float('inf') if gross_profit > 0 else 0.0
    return gross_profit / gross_loss


def calculate_cagr(cumulative_returns: pd.Series, periods_per_year: int = 252) -> float:
    """Calculate Compound Annual Growth Rate."""
    total_return = cumulative_returns.iloc[-1]
    n_periods = len(cumulative_returns)
    n_years = n_periods / periods_per_year
    if n_years <= 0 or total_return <= -1:
        return 0.0
    return (1 + total_return) ** (1 / n_years) - 1


class Backtester:
    """
    Simple backtester for trading strategies.

    Supports:
    - Long-only strategies
    - Transaction costs
    - Position sizing
    """

    def __init__(self,
                 initial_capital: float = 100000,
                 transaction_cost: float = 0.001,  # 0.1% per trade
                 slippage: float = 0.0005):  # 0.05% slippage
        self.initial_capital = initial_capital
        self.transaction_cost = transaction_cost
        self.slippage = slippage

    def run(self,
            prices: pd.Series,
            signals: pd.Series,
            position_size: float = 1.0) -> Dict:
        """
        Run backtest with given prices and signals.

        Args:
            prices: Price series (Close prices)
            signals: Signal series (1=buy, -1=sell, 0=hold)
            position_size: Fraction of capital to use (0-1)

        Returns:
            Dictionary with backtest results
        """
        # Align data
        prices = prices.copy()
        signals = signals.reindex(prices.index).fillna(0)

        # Initialize
        capital = self.initial_capital
        position = 0  # Number of shares
        cash = capital
        trades = []
        portfolio_values = []

        entry_price = 0
        entry_date = None

        for date, price in prices.items():
            signal = signals.loc[date]

            # Calculate current portfolio value
            portfolio_value = cash + position * price
            portfolio_values.append({'date': date, 'value': portfolio_value})

            # Process signal
            if signal == 1 and position == 0:  # Buy signal
                # Calculate position size
                trade_value = cash * position_size
                cost = trade_value * (self.transaction_cost + self.slippage)
                shares = (trade_value - cost) / price

                position = shares
                cash = cash - trade_value
                entry_price = price
                entry_date = date

            elif signal == -1 and position > 0:  # Sell signal
                # Sell all
                trade_value = position * price
                cost = trade_value * (self.transaction_cost + self.slippage)

                pnl = (price - entry_price) * position - cost
                trades.append({
                    'entry_date': entry_date,
                    'exit_date': date,
                    'entry_price': entry_price,
                    'exit_price': price,
                    'shares': position,
                    'pnl': pnl,
                    'return_pct': (price / entry_price - 1) * 100
                })

                cash = cash + trade_value - cost
                position = 0

        # Create results
        portfolio_df = pd.DataFrame(portfolio_values).set_index('date')
        trades_df = pd.DataFrame(trades) if trades else pd.DataFrame()

        # Calculate metrics
        returns = portfolio_df['value'].pct_change().fillna(0)
        cum_returns = calculate_cumulative_returns(returns)

        total_return = (portfolio_df['value'].iloc[-1] / self.initial_capital - 1) * 100
        sharpe = calculate_sharpe_ratio(returns)
        max_dd, peak_date, trough_date = calculate_max_drawdown(cum_returns)
        cagr = calculate_cagr(cum_returns) * 100

        results = {
            'total_return_pct': total_return,
            'cagr_pct': cagr,
            'sharpe_ratio': sharpe,
            'max_drawdown_pct': max_dd * 100,
            'num_trades': len(trades_df),
            'win_rate': calculate_win_rate(trades_df) if len(trades_df) > 0 else 0,
            'profit_factor': calculate_profit_factor(trades_df) if len(trades_df) > 0 else 0,
            'portfolio': portfolio_df,
            'trades': trades_df,
            'returns': returns,
            'cumulative_returns': cum_returns
        }

        return results


def calculate_buy_and_hold(prices: pd.Series, initial_capital: float = 100000) -> Dict:
    """Calculate buy-and-hold benchmark returns."""
    returns = calculate_returns(prices)
    cum_returns = calculate_cumulative_returns(returns)

    total_return = (prices.iloc[-1] / prices.iloc[0] - 1) * 100
    sharpe = calculate_sharpe_ratio(returns)
    max_dd, _, _ = calculate_max_drawdown(cum_returns)
    cagr = calculate_cagr(cum_returns) * 100

    return {
        'total_return_pct': total_return,
        'cagr_pct': cagr,
        'sharpe_ratio': sharpe,
        'max_drawdown_pct': max_dd * 100,
        'returns': returns,
        'cumulative_returns': cum_returns
    }


def plot_backtest_results(results: Dict, benchmark: Optional[Dict] = None, title: str = "Strategy Performance"):
    """Plot backtest results with optional benchmark comparison."""
    fig, axes = plt.subplots(2, 2, figsize=(14, 10))

    # Plot 1: Cumulative Returns
    ax1 = axes[0, 0]
    ax1.plot(results['cumulative_returns'] * 100, label='Strategy', linewidth=2)
    if benchmark:
        ax1.plot(benchmark['cumulative_returns'] * 100, label='Buy & Hold', linewidth=2, alpha=0.7)
    ax1.set_title('Cumulative Returns')
    ax1.set_ylabel('Return (%)')
    ax1.legend()
    ax1.grid(True, alpha=0.3)

    # Plot 2: Drawdown
    ax2 = axes[0, 1]
    wealth = 1 + results['cumulative_returns']
    peak = wealth.expanding(min_periods=1).max()
    drawdown = (wealth - peak) / peak * 100
    ax2.fill_between(drawdown.index, drawdown.values, 0, alpha=0.3, color='red')
    ax2.plot(drawdown, color='red', linewidth=1)
    ax2.set_title('Drawdown')
    ax2.set_ylabel('Drawdown (%)')
    ax2.grid(True, alpha=0.3)

    # Plot 3: Monthly Returns Heatmap (if enough data)
    ax3 = axes[1, 0]
    if len(results['returns']) > 30:
        monthly = results['returns'].resample('ME').apply(lambda x: (1 + x).prod() - 1) * 100
        ax3.bar(range(len(monthly)), monthly.values, color=['green' if x > 0 else 'red' for x in monthly])
        ax3.set_title('Monthly Returns')
        ax3.set_ylabel('Return (%)')
        ax3.axhline(y=0, color='black', linewidth=0.5)
    else:
        ax3.text(0.5, 0.5, 'Not enough data for monthly view', ha='center', va='center')
    ax3.grid(True, alpha=0.3)

    # Plot 4: Performance Metrics Table
    ax4 = axes[1, 1]
    ax4.axis('off')

    metrics = [
        ['Metric', 'Strategy', 'Buy & Hold' if benchmark else ''],
        ['Total Return', f"{results['total_return_pct']:.2f}%", f"{benchmark['total_return_pct']:.2f}%" if benchmark else ''],
        ['CAGR', f"{results['cagr_pct']:.2f}%", f"{benchmark['cagr_pct']:.2f}%" if benchmark else ''],
        ['Sharpe Ratio', f"{results['sharpe_ratio']:.2f}", f"{benchmark['sharpe_ratio']:.2f}" if benchmark else ''],
        ['Max Drawdown', f"{results['max_drawdown_pct']:.2f}%", f"{benchmark['max_drawdown_pct']:.2f}%" if benchmark else ''],
        ['Num Trades', f"{results['num_trades']}", '-'],
        ['Win Rate', f"{results['win_rate']:.1%}", '-'],
        ['Profit Factor', f"{results['profit_factor']:.2f}", '-'],
    ]

    table = ax4.table(cellText=metrics, loc='center', cellLoc='center')
    table.auto_set_font_size(False)
    table.set_fontsize(10)
    table.scale(1.2, 1.5)

    # Color header row
    for j in range(3):
        table[(0, j)].set_facecolor('#4472C4')
        table[(0, j)].set_text_props(color='white', weight='bold')

    plt.suptitle(title, fontsize=14, fontweight='bold')
    plt.tight_layout()
    plt.show()

    return fig


def print_results_summary(results: Dict, name: str = "Strategy"):
    """Print formatted results summary."""
    print(f"\n{'='*50}")
    print(f"{name} Results")
    print('='*50)
    print(f"Total Return:    {results['total_return_pct']:>10.2f}%")
    print(f"CAGR:            {results['cagr_pct']:>10.2f}%")
    print(f"Sharpe Ratio:    {results['sharpe_ratio']:>10.2f}")
    print(f"Max Drawdown:    {results['max_drawdown_pct']:>10.2f}%")
    if 'num_trades' in results:
        print(f"Number of Trades:{results['num_trades']:>10}")
        print(f"Win Rate:        {results['win_rate']:>10.1%}")
        print(f"Profit Factor:   {results['profit_factor']:>10.2f}")
    print('='*50)
