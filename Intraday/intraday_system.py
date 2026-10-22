"""
Intraday Trading System - High Confidence, Low Risk

Features:
- 1-hour candle analysis
- Max 1-2 trades per day
- 1% max risk per trade
- Multiple confirmation required

Strategies:
1. Opening Range Breakout (ORB)
2. VWAP Bounce/Rejection
3. Multi-Timeframe Trend Following
4. Support/Resistance Trading
"""

import pandas as pd
import numpy as np
import yfinance as yf
from datetime import datetime, timedelta
from typing import Dict, List, Tuple, Optional
import warnings
warnings.filterwarnings('ignore')


class IntradayIndicators:
    """Calculate intraday-specific indicators."""

    @staticmethod
    def vwap(high: pd.Series, low: pd.Series, close: pd.Series, volume: pd.Series) -> pd.Series:
        """
        Volume Weighted Average Price.
        Resets daily for intraday data.
        """
        typical_price = (high + low + close) / 3
        cumulative_tp_vol = (typical_price * volume).cumsum()
        cumulative_vol = volume.cumsum()
        return cumulative_tp_vol / cumulative_vol

    @staticmethod
    def opening_range(df: pd.DataFrame, hours: int = 1) -> Tuple[pd.Series, pd.Series]:
        """
        Calculate opening range high/low for first N hours.
        Returns (range_high, range_low) series.
        """
        df = df.copy()
        df['Date'] = df.index.date

        range_high = pd.Series(index=df.index, dtype=float)
        range_low = pd.Series(index=df.index, dtype=float)

        for date in df['Date'].unique():
            day_data = df[df['Date'] == date]
            if len(day_data) >= hours:
                or_high = day_data['High'].iloc[:hours].max()
                or_low = day_data['Low'].iloc[:hours].min()
                range_high.loc[day_data.index] = or_high
                range_low.loc[day_data.index] = or_low

        return range_high, range_low

    @staticmethod
    def pivot_points(high: float, low: float, close: float) -> Dict[str, float]:
        """Calculate pivot points and support/resistance levels."""
        pivot = (high + low + close) / 3
        r1 = 2 * pivot - low
        s1 = 2 * pivot - high
        r2 = pivot + (high - low)
        s2 = pivot - (high - low)
        r3 = high + 2 * (pivot - low)
        s3 = low - 2 * (high - pivot)

        return {
            'pivot': pivot,
            'r1': r1, 'r2': r2, 'r3': r3,
            's1': s1, 's2': s2, 's3': s3
        }

    @staticmethod
    def atr(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
        """Average True Range for volatility-based stops."""
        tr1 = high - low
        tr2 = abs(high - close.shift(1))
        tr3 = abs(low - close.shift(1))
        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        return tr.rolling(window=period).mean()

    @staticmethod
    def ema(series: pd.Series, period: int) -> pd.Series:
        """Exponential Moving Average."""
        return series.ewm(span=period, adjust=False).mean()

    @staticmethod
    def rsi(prices: pd.Series, period: int = 14) -> pd.Series:
        """Relative Strength Index."""
        delta = prices.diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
        rs = gain / loss
        return 100 - (100 / (1 + rs))


class RiskManager:
    """
    Position sizing and risk management.
    Ensures max 1% risk per trade.
    """

    def __init__(self, capital: float, max_risk_pct: float = 0.01):
        self.capital = capital
        self.max_risk_pct = max_risk_pct
        self.max_risk_amount = capital * max_risk_pct

    def calculate_position_size(self, entry_price: float, stop_loss: float) -> Dict:
        """
        Calculate position size based on stop-loss distance.

        Returns:
            Dict with shares, position_value, risk_amount, risk_pct
        """
        risk_per_share = abs(entry_price - stop_loss)

        if risk_per_share <= 0:
            return {'shares': 0, 'position_value': 0, 'risk_amount': 0, 'risk_pct': 0}

        # Max shares based on risk
        max_shares = self.max_risk_amount / risk_per_share

        # Also limit to 20% of capital per trade
        max_position_value = self.capital * 0.20
        max_shares_by_capital = max_position_value / entry_price

        shares = min(max_shares, max_shares_by_capital)
        shares = int(shares)  # Round down to whole shares

        position_value = shares * entry_price
        actual_risk = shares * risk_per_share

        return {
            'shares': shares,
            'position_value': position_value,
            'risk_amount': actual_risk,
            'risk_pct': actual_risk / self.capital * 100
        }

    def calculate_targets(self, entry_price: float, stop_loss: float,
                         reward_risk_ratio: float = 2.0) -> Dict:
        """Calculate profit targets based on R:R ratio."""
        risk = abs(entry_price - stop_loss)

        if entry_price > stop_loss:  # Long trade
            target1 = entry_price + risk * reward_risk_ratio
            target2 = entry_price + risk * (reward_risk_ratio + 1)
        else:  # Short trade
            target1 = entry_price - risk * reward_risk_ratio
            target2 = entry_price - risk * (reward_risk_ratio + 1)

        return {
            'entry': entry_price,
            'stop_loss': stop_loss,
            'target1': target1,
            'target2': target2,
            'risk': risk,
            'reward1': risk * reward_risk_ratio,
            'rr_ratio': reward_risk_ratio
        }


class OpeningRangeBreakout:
    """
    Opening Range Breakout Strategy.

    Rules:
    - Wait for first 1-2 hours to form the opening range
    - Buy breakout above range high with volume
    - Sell breakdown below range low with volume
    - Stop-loss at opposite end of range
    """

    def __init__(self, range_hours: int = 1, volume_factor: float = 1.2):
        self.range_hours = range_hours
        self.volume_factor = volume_factor
        self.name = f"ORB ({range_hours}h)"

    def generate_signals(self, df: pd.DataFrame) -> pd.DataFrame:
        """Generate ORB signals."""
        df = df.copy()

        # Calculate opening range
        range_high, range_low = IntradayIndicators.opening_range(df, self.range_hours)
        df['OR_High'] = range_high
        df['OR_Low'] = range_low
        df['OR_Range'] = range_high - range_low

        # Volume condition
        df['Vol_MA'] = df['Volume'].rolling(20).mean()
        df['High_Volume'] = df['Volume'] > df['Vol_MA'] * self.volume_factor

        # Initialize signals
        df['Signal'] = 0
        df['Entry'] = np.nan
        df['Stop_Loss'] = np.nan
        df['Signal_Type'] = ''

        # Get date for each row
        df['Date'] = df.index.date
        df['Hour'] = df.index.hour

        for date in df['Date'].unique():
            day_data = df[df['Date'] == date]

            if len(day_data) <= self.range_hours:
                continue

            # Only look at candles after opening range
            post_or = day_data.iloc[self.range_hours:]
            or_high = day_data['OR_High'].iloc[0]
            or_low = day_data['OR_Low'].iloc[0]

            if pd.isna(or_high) or pd.isna(or_low):
                continue

            breakout_found = False

            for idx in post_or.index:
                if breakout_found:
                    break

                row = df.loc[idx]

                # Bullish breakout
                # FIX: Entry at CLOSE price (realistic) instead of or_high (unrealistic)
                if row['Close'] > or_high and row['High_Volume']:
                    df.loc[idx, 'Signal'] = 1
                    df.loc[idx, 'Entry'] = row['Close']  # FIXED: was or_high
                    df.loc[idx, 'Stop_Loss'] = row['Close'] - (or_high - or_low)  # FIXED: ATR-like stop
                    df.loc[idx, 'Signal_Type'] = 'ORB_Long'
                    breakout_found = True

                # Bearish breakdown
                # FIX: Entry at CLOSE price (realistic) instead of or_low (unrealistic)
                elif row['Close'] < or_low and row['High_Volume']:
                    df.loc[idx, 'Signal'] = -1
                    df.loc[idx, 'Entry'] = row['Close']  # FIXED: was or_low
                    df.loc[idx, 'Stop_Loss'] = row['Close'] + (or_high - or_low)  # FIXED: ATR-like stop
                    df.loc[idx, 'Signal_Type'] = 'ORB_Short'
                    breakout_found = True

        return df


class VWAPStrategy:
    """
    VWAP Bounce/Rejection Strategy.

    Rules:
    - In uptrend (price > VWAP): Buy bounces off VWAP
    - In downtrend (price < VWAP): Sell rejections at VWAP
    - Requires RSI confirmation
    """

    def __init__(self, rsi_oversold: int = 40, rsi_overbought: int = 60):
        self.rsi_oversold = rsi_oversold
        self.rsi_overbought = rsi_overbought
        self.name = "VWAP Bounce"

    def generate_signals(self, df: pd.DataFrame) -> pd.DataFrame:
        """Generate VWAP-based signals."""
        df = df.copy()

        # Calculate VWAP (simplified - cumulative for the data period)
        typical_price = (df['High'] + df['Low'] + df['Close']) / 3
        df['VWAP'] = (typical_price * df['Volume']).cumsum() / df['Volume'].cumsum()

        # RSI
        df['RSI'] = IntradayIndicators.rsi(df['Close'], 14)

        # ATR for stops
        df['ATR'] = IntradayIndicators.atr(df['High'], df['Low'], df['Close'], 14)

        # Trend
        df['Above_VWAP'] = df['Close'] > df['VWAP']

        # Initialize
        df['Signal'] = 0
        df['Entry'] = np.nan
        df['Stop_Loss'] = np.nan
        df['Signal_Type'] = ''

        for i in range(2, len(df)):
            # Skip if already have signal today
            current_date = df.index[i].date() if hasattr(df.index[i], 'date') else None

            # Bullish: Price above VWAP, pulled back to VWAP, RSI not overbought
            if (df['Close'].iloc[i] > df['VWAP'].iloc[i] and
                df['Low'].iloc[i] <= df['VWAP'].iloc[i] * 1.002 and  # Touched VWAP
                df['Close'].iloc[i] > df['Open'].iloc[i] and  # Bullish candle
                df['RSI'].iloc[i] < self.rsi_overbought):

                df.iloc[i, df.columns.get_loc('Signal')] = 1
                df.iloc[i, df.columns.get_loc('Entry')] = df['Close'].iloc[i]
                df.iloc[i, df.columns.get_loc('Stop_Loss')] = df['VWAP'].iloc[i] - df['ATR'].iloc[i]
                df.iloc[i, df.columns.get_loc('Signal_Type')] = 'VWAP_Long'

            # Bearish: Price below VWAP, rallied to VWAP, RSI not oversold
            elif (df['Close'].iloc[i] < df['VWAP'].iloc[i] and
                  df['High'].iloc[i] >= df['VWAP'].iloc[i] * 0.998 and  # Touched VWAP
                  df['Close'].iloc[i] < df['Open'].iloc[i] and  # Bearish candle
                  df['RSI'].iloc[i] > self.rsi_oversold):

                df.iloc[i, df.columns.get_loc('Signal')] = -1
                df.iloc[i, df.columns.get_loc('Entry')] = df['Close'].iloc[i]
                df.iloc[i, df.columns.get_loc('Stop_Loss')] = df['VWAP'].iloc[i] + df['ATR'].iloc[i]
                df.iloc[i, df.columns.get_loc('Signal_Type')] = 'VWAP_Short'

        return df


class MultiTimeframeTrend:
    """
    Multi-Timeframe Trend Following.

    Rules:
    - Daily trend sets direction (EMA 20 > EMA 50 = bullish)
    - Hourly signals for entry
    - Only trade in direction of daily trend
    """

    def __init__(self, fast_ema: int = 9, slow_ema: int = 21):
        self.fast_ema = fast_ema
        self.slow_ema = slow_ema
        self.name = f"MTF Trend ({fast_ema}/{slow_ema})"

    def generate_signals(self, hourly_df: pd.DataFrame,
                        daily_df: pd.DataFrame = None) -> pd.DataFrame:
        """Generate multi-timeframe signals."""
        df = hourly_df.copy()

        # Hourly EMAs
        df['EMA_Fast'] = IntradayIndicators.ema(df['Close'], self.fast_ema)
        df['EMA_Slow'] = IntradayIndicators.ema(df['Close'], self.slow_ema)
        df['RSI'] = IntradayIndicators.rsi(df['Close'], 14)
        df['ATR'] = IntradayIndicators.atr(df['High'], df['Low'], df['Close'], 14)

        # Daily trend (if provided)
        if daily_df is not None:
            daily_df['EMA20'] = IntradayIndicators.ema(daily_df['Close'], 20)
            daily_df['EMA50'] = IntradayIndicators.ema(daily_df['Close'], 50)
            daily_df['Daily_Trend'] = np.where(daily_df['EMA20'] > daily_df['EMA50'], 1, -1)

            # Map daily trend to hourly
            df['Date'] = df.index.date
            daily_trend_map = daily_df['Daily_Trend'].to_dict()
            df['Daily_Trend'] = df['Date'].map(lambda x: daily_trend_map.get(x, 0))
        else:
            # Use longer hourly EMA as proxy for daily trend
            df['EMA_Long'] = IntradayIndicators.ema(df['Close'], 50)
            df['Daily_Trend'] = np.where(df['Close'] > df['EMA_Long'], 1, -1)

        # Initialize signals
        df['Signal'] = 0
        df['Entry'] = np.nan
        df['Stop_Loss'] = np.nan
        df['Signal_Type'] = ''

        for i in range(2, len(df)):
            daily_trend = df['Daily_Trend'].iloc[i]

            # Bullish setup: Daily uptrend + hourly EMA crossover
            if (daily_trend == 1 and
                df['EMA_Fast'].iloc[i] > df['EMA_Slow'].iloc[i] and
                df['EMA_Fast'].iloc[i-1] <= df['EMA_Slow'].iloc[i-1] and
                df['RSI'].iloc[i] < 70):

                df.iloc[i, df.columns.get_loc('Signal')] = 1
                df.iloc[i, df.columns.get_loc('Entry')] = df['Close'].iloc[i]
                df.iloc[i, df.columns.get_loc('Stop_Loss')] = df['Close'].iloc[i] - 1.5 * df['ATR'].iloc[i]
                df.iloc[i, df.columns.get_loc('Signal_Type')] = 'MTF_Long'

            # Bearish setup: Daily downtrend + hourly EMA crossover
            elif (daily_trend == -1 and
                  df['EMA_Fast'].iloc[i] < df['EMA_Slow'].iloc[i] and
                  df['EMA_Fast'].iloc[i-1] >= df['EMA_Slow'].iloc[i-1] and
                  df['RSI'].iloc[i] > 30):

                df.iloc[i, df.columns.get_loc('Signal')] = -1
                df.iloc[i, df.columns.get_loc('Entry')] = df['Close'].iloc[i]
                df.iloc[i, df.columns.get_loc('Stop_Loss')] = df['Close'].iloc[i] + 1.5 * df['ATR'].iloc[i]
                df.iloc[i, df.columns.get_loc('Signal_Type')] = 'MTF_Short'

        return df


class HighConfidenceFilter:
    """
    Filter signals to only keep high-confidence setups.
    Combines multiple strategies and requires confirmation.
    """

    def __init__(self, min_confirmations: int = 2):
        self.min_confirmations = min_confirmations
        self.name = f"High Confidence (min {min_confirmations} confirms)"

    def filter_signals(self, signals_list: List[pd.DataFrame]) -> pd.DataFrame:
        """
        Combine signals from multiple strategies.
        Only keep signals where multiple strategies agree.
        """
        if not signals_list:
            return pd.DataFrame()

        # Start with first DataFrame
        combined = signals_list[0].copy()
        combined['Confirmations'] = (combined['Signal'] != 0).astype(int)
        combined['Signal_Sources'] = combined['Signal_Type']

        # Add confirmations from other strategies
        for df in signals_list[1:]:
            for idx in combined.index:
                if idx in df.index:
                    if df.loc[idx, 'Signal'] != 0:
                        # Same direction?
                        if combined.loc[idx, 'Signal'] == 0:
                            combined.loc[idx, 'Signal'] = df.loc[idx, 'Signal']
                            combined.loc[idx, 'Entry'] = df.loc[idx, 'Entry']
                            combined.loc[idx, 'Stop_Loss'] = df.loc[idx, 'Stop_Loss']
                            combined.loc[idx, 'Signal_Type'] = df.loc[idx, 'Signal_Type']

                        if combined.loc[idx, 'Signal'] == df.loc[idx, 'Signal']:
                            combined.loc[idx, 'Confirmations'] += 1
                            combined.loc[idx, 'Signal_Sources'] += f"+{df.loc[idx, 'Signal_Type']}"

        # Filter to high-confidence only
        low_confidence = combined['Confirmations'] < self.min_confirmations
        combined.loc[low_confidence, 'Signal'] = 0
        combined.loc[low_confidence, 'Signal_Type'] = ''

        return combined


class IntradayBacktester:
    """
    Backtester specifically for intraday strategies.
    Handles daily trade limits and proper exit timing.
    """

    def __init__(self, capital: float = 100000, max_risk_pct: float = 0.01,
                 max_trades_per_day: int = 2):
        self.capital = capital
        self.risk_manager = RiskManager(capital, max_risk_pct)
        self.max_trades_per_day = max_trades_per_day

    def run(self, df: pd.DataFrame, exit_eod: bool = True) -> Dict:
        """
        Run backtest on signal DataFrame.

        Args:
            df: DataFrame with Signal, Entry, Stop_Loss columns
            exit_eod: Exit all positions at end of day
        """
        df = df.copy()

        capital = self.capital
        position = 0
        entry_price = 0
        stop_loss = 0
        trades = []
        daily_trades = {}
        equity_curve = []

        for i, (idx, row) in enumerate(df.iterrows()):
            current_date = idx.date() if hasattr(idx, 'date') else idx

            # Track daily trades
            if current_date not in daily_trades:
                daily_trades[current_date] = 0

            # Record equity
            if position != 0:
                current_equity = capital + position * (row['Close'] - entry_price)
            else:
                current_equity = capital
            equity_curve.append({'date': idx, 'equity': current_equity})

            # Check stop-loss
            if position > 0 and row['Low'] <= stop_loss:
                # Stop-loss hit (long)
                pnl = position * (stop_loss - entry_price)
                capital += pnl
                trades.append({
                    'entry_date': entry_date,
                    'exit_date': idx,
                    'type': 'long',
                    'entry_price': entry_price,
                    'exit_price': stop_loss,
                    'exit_reason': 'stop_loss',
                    'pnl': pnl,
                    'return_pct': (stop_loss / entry_price - 1) * 100
                })
                position = 0

            elif position < 0 and row['High'] >= stop_loss:
                # Stop-loss hit (short)
                pnl = abs(position) * (entry_price - stop_loss)
                capital += pnl
                trades.append({
                    'entry_date': entry_date,
                    'exit_date': idx,
                    'type': 'short',
                    'entry_price': entry_price,
                    'exit_price': stop_loss,
                    'exit_reason': 'stop_loss',
                    'pnl': pnl,
                    'return_pct': (entry_price / stop_loss - 1) * 100
                })
                position = 0

            # End of day exit
            if exit_eod and position != 0:
                next_date = df.index[i+1].date() if i+1 < len(df) and hasattr(df.index[i+1], 'date') else None
                if next_date and next_date != current_date:
                    exit_price = row['Close']
                    if position > 0:
                        pnl = position * (exit_price - entry_price)
                        ret_pct = (exit_price / entry_price - 1) * 100
                    else:
                        pnl = abs(position) * (entry_price - exit_price)
                        ret_pct = (entry_price / exit_price - 1) * 100

                    capital += pnl
                    trades.append({
                        'entry_date': entry_date,
                        'exit_date': idx,
                        'type': 'long' if position > 0 else 'short',
                        'entry_price': entry_price,
                        'exit_price': exit_price,
                        'exit_reason': 'eod',
                        'pnl': pnl,
                        'return_pct': ret_pct
                    })
                    position = 0

            # New signal
            if position == 0 and row['Signal'] != 0:
                if daily_trades[current_date] >= self.max_trades_per_day:
                    continue  # Max trades reached for today

                if pd.isna(row['Entry']) or pd.isna(row['Stop_Loss']):
                    continue

                entry_price = row['Entry'] if not pd.isna(row['Entry']) else row['Close']
                stop_loss = row['Stop_Loss']

                # Calculate position size
                pos_info = self.risk_manager.calculate_position_size(entry_price, stop_loss)

                if pos_info['shares'] > 0:
                    if row['Signal'] == 1:
                        position = pos_info['shares']
                    else:
                        position = -pos_info['shares']

                    entry_date = idx
                    daily_trades[current_date] += 1

        # Calculate results
        trades_df = pd.DataFrame(trades) if trades else pd.DataFrame()
        equity_df = pd.DataFrame(equity_curve).set_index('date')

        total_return = (capital / self.capital - 1) * 100

        if len(trades_df) > 0:
            win_rate = (trades_df['pnl'] > 0).mean()
            avg_win = trades_df[trades_df['pnl'] > 0]['pnl'].mean() if (trades_df['pnl'] > 0).any() else 0
            avg_loss = abs(trades_df[trades_df['pnl'] < 0]['pnl'].mean()) if (trades_df['pnl'] < 0).any() else 0
            profit_factor = avg_win * win_rate / (avg_loss * (1 - win_rate)) if avg_loss > 0 and win_rate < 1 else 0
        else:
            win_rate = 0
            profit_factor = 0

        return {
            'total_return_pct': total_return,
            'num_trades': len(trades_df),
            'win_rate': win_rate,
            'profit_factor': profit_factor,
            'final_capital': capital,
            'trades': trades_df,
            'equity': equity_df
        }


def download_intraday_data(symbol: str, period: str = '60d', interval: str = '1h') -> pd.DataFrame:
    """
    Download intraday data from Yahoo Finance.
    Note: yfinance has limits on intraday data history.
    """
    print(f"Downloading {interval} data for {symbol}...")
    data = yf.download(symbol, period=period, interval=interval, progress=False)

    if isinstance(data.columns, pd.MultiIndex):
        data.columns = data.columns.get_level_values(0)

    return data


def run_intraday_backtest(symbol: str = 'RELIANCE.NS'):
    """Run full intraday backtest with all strategies."""

    print("="*60)
    print(f"INTRADAY SYSTEM BACKTEST - {symbol}")
    print("="*60)

    # Download data
    df = download_intraday_data(symbol, period='60d', interval='1h')

    if df.empty:
        print("Error: No data downloaded")
        return None

    print(f"Data range: {df.index[0]} to {df.index[-1]}")
    print(f"Total candles: {len(df)}")

    # Initialize strategies
    orb = OpeningRangeBreakout(range_hours=1)
    vwap = VWAPStrategy()
    mtf = MultiTimeframeTrend(fast_ema=9, slow_ema=21)

    # Generate signals
    print("\nGenerating signals...")
    orb_signals = orb.generate_signals(df)
    vwap_signals = vwap.generate_signals(df)
    mtf_signals = mtf.generate_signals(df)

    # High confidence filter
    hc_filter = HighConfidenceFilter(min_confirmations=1)  # Start with 1, can increase
    combined_signals = hc_filter.filter_signals([orb_signals, vwap_signals, mtf_signals])

    # Count signals
    print(f"\nSignals generated:")
    print(f"  ORB: {(orb_signals['Signal'] != 0).sum()}")
    print(f"  VWAP: {(vwap_signals['Signal'] != 0).sum()}")
    print(f"  MTF: {(mtf_signals['Signal'] != 0).sum()}")
    print(f"  Combined (high confidence): {(combined_signals['Signal'] != 0).sum()}")

    # Backtest
    backtester = IntradayBacktester(capital=100000, max_risk_pct=0.01, max_trades_per_day=2)

    results = {}

    for name, signals in [('ORB', orb_signals), ('VWAP', vwap_signals),
                          ('MTF', mtf_signals), ('Combined', combined_signals)]:
        result = backtester.run(signals)
        results[name] = result

        print(f"\n{name} Strategy Results:")
        print(f"  Total Return: {result['total_return_pct']:.2f}%")
        print(f"  Trades: {result['num_trades']}")
        print(f"  Win Rate: {result['win_rate']:.1%}")
        print(f"  Profit Factor: {result['profit_factor']:.2f}")

    # Summary
    print("\n" + "="*60)
    print("SUMMARY")
    print("="*60)
    print(f"{'Strategy':<15} {'Return':<12} {'Trades':<10} {'Win Rate':<12} {'Profit Factor':<12}")
    print("-"*60)
    for name, res in results.items():
        print(f"{name:<15} {res['total_return_pct']:<12.2f} {res['num_trades']:<10} {res['win_rate']:<12.1%} {res['profit_factor']:<12.2f}")

    return results


if __name__ == "__main__":
    results = run_intraday_backtest('RELIANCE.NS')
