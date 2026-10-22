"""
Signal Generator - Live Market Monitoring

Monitors stocks and generates trading signals for:
1. Intraday opportunities (ORB, VWAP, etc.)
2. Long-term investment opportunities (Smart SIP, Stock Selection)

Can be run periodically to check for new signals.
"""

import pandas as pd
import numpy as np
import yfinance as yf
from datetime import datetime, timedelta
from typing import Dict, List, Optional
import warnings
warnings.filterwarnings('ignore')

# Import our strategy modules
from intraday_system import (
    IntradayIndicators, OpeningRangeBreakout, VWAPStrategy,
    MultiTimeframeTrend, RiskManager
)
from longterm_system import (
    MarketRegimeDetector, SmartSIP, StockRanker, MomentumScorer, ValueScorer
)


class SignalGenerator:
    """
    Unified signal generator for all trading strategies.
    """

    def __init__(self, capital: float = 100000):
        self.capital = capital
        self.risk_manager = RiskManager(capital, max_risk_pct=0.01)

        # Initialize strategies
        self.orb = OpeningRangeBreakout(range_hours=1)
        self.vwap = VWAPStrategy()
        self.mtf = MultiTimeframeTrend()
        self.regime_detector = MarketRegimeDetector()
        self.stock_ranker = StockRanker()

    def get_intraday_signals(self, symbol: str) -> Dict:
        """
        Check for intraday trading signals.

        Returns:
            Dict with signal information
        """
        # Download recent hourly data
        df = yf.download(symbol, period='5d', interval='1h', progress=False)

        if df.empty:
            return {'symbol': symbol, 'error': 'No data available'}

        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)

        signals = {
            'symbol': symbol,
            'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M'),
            'current_price': df['Close'].iloc[-1],
            'signals': []
        }

        # Check ORB
        orb_df = self.orb.generate_signals(df)
        if orb_df['Signal'].iloc[-1] != 0:
            signals['signals'].append({
                'strategy': 'ORB',
                'type': 'LONG' if orb_df['Signal'].iloc[-1] == 1 else 'SHORT',
                'entry': orb_df['Entry'].iloc[-1],
                'stop_loss': orb_df['Stop_Loss'].iloc[-1],
                'confidence': 'HIGH'
            })

        # Check VWAP
        vwap_df = self.vwap.generate_signals(df)
        if vwap_df['Signal'].iloc[-1] != 0:
            signals['signals'].append({
                'strategy': 'VWAP',
                'type': 'LONG' if vwap_df['Signal'].iloc[-1] == 1 else 'SHORT',
                'entry': vwap_df['Entry'].iloc[-1],
                'stop_loss': vwap_df['Stop_Loss'].iloc[-1],
                'confidence': 'MEDIUM'
            })

        # Check MTF
        mtf_df = self.mtf.generate_signals(df)
        if mtf_df['Signal'].iloc[-1] != 0:
            signals['signals'].append({
                'strategy': 'MTF Trend',
                'type': 'LONG' if mtf_df['Signal'].iloc[-1] == 1 else 'SHORT',
                'entry': mtf_df['Entry'].iloc[-1],
                'stop_loss': mtf_df['Stop_Loss'].iloc[-1],
                'confidence': 'MEDIUM'
            })

        # Add position sizing if signals exist
        for sig in signals['signals']:
            if not pd.isna(sig['entry']) and not pd.isna(sig['stop_loss']):
                pos_info = self.risk_manager.calculate_position_size(
                    sig['entry'], sig['stop_loss']
                )
                sig['position_size'] = pos_info['shares']
                sig['risk_amount'] = pos_info['risk_amount']
                sig['risk_pct'] = pos_info['risk_pct']

                # Calculate targets
                targets = self.risk_manager.calculate_targets(
                    sig['entry'], sig['stop_loss'], reward_risk_ratio=2.0
                )
                sig['target1'] = targets['target1']
                sig['target2'] = targets['target2']

        return signals

    def get_market_regime(self, index_symbol: str = '^NSEI') -> Dict:
        """
        Get current market regime based on Nifty 50 index.
        """
        df = yf.download(index_symbol, period='1y', interval='1d', progress=False)

        if df.empty:
            return {'error': 'Could not fetch index data'}

        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)

        regime_df = self.regime_detector.detect_regime(df['Close'])

        current = regime_df.iloc[-1]

        return {
            'index': index_symbol,
            'date': df.index[-1].strftime('%Y-%m-%d'),
            'close': df['Close'].iloc[-1],
            'regime': current['Regime'],
            'regime_score': current['Regime_Score'],
            'rsi': current['RSI'],
            'above_50_sma': current['Above_50'],
            'above_200_sma': current['Above_200'],
            'volatility': current['Volatility'] * 100,  # As percentage
            'sip_recommendation': self._get_sip_recommendation(current['Regime'])
        }

    def _get_sip_recommendation(self, regime: str) -> Dict:
        """Get SIP recommendation based on regime."""
        recommendations = {
            'Strong_Bear': {
                'action': 'INVEST MORE',
                'multiplier': 2.0,
                'reason': 'Great buying opportunity - market oversold'
            },
            'Bear': {
                'action': 'INVEST MORE',
                'multiplier': 1.5,
                'reason': 'Good value - accumulate quality stocks'
            },
            'Sideways': {
                'action': 'NORMAL',
                'multiplier': 1.0,
                'reason': 'Continue regular SIP'
            },
            'Bull': {
                'action': 'INVEST LESS',
                'multiplier': 0.75,
                'reason': 'Market slightly expensive - be cautious'
            },
            'Strong_Bull': {
                'action': 'WAIT',
                'multiplier': 0.5,
                'reason': 'Market overheated - consider taking profits'
            }
        }
        return recommendations.get(regime, recommendations['Sideways'])

    def get_stock_recommendations(self, symbols: List[str] = None) -> Dict:
        """
        Get stock recommendations for monthly investment.
        """
        if symbols is None:
            symbols = [
                'RELIANCE.NS', 'TCS.NS', 'HDFCBANK.NS', 'INFY.NS', 'ICICIBANK.NS',
                'HINDUNILVR.NS', 'SBIN.NS', 'BHARTIARTL.NS', 'ITC.NS', 'KOTAKBANK.NS',
                'LT.NS', 'AXISBANK.NS', 'ASIANPAINT.NS', 'MARUTI.NS', 'SUNPHARMA.NS',
                'TITAN.NS', 'BAJFINANCE.NS', 'WIPRO.NS', 'HCLTECH.NS', 'ULTRACEMCO.NS'
            ]

        # Download data for all stocks
        stock_data = {}
        for symbol in symbols:
            try:
                data = yf.download(symbol, period='2y', interval='1d', progress=False)
                if not data.empty:
                    if isinstance(data.columns, pd.MultiIndex):
                        data.columns = data.columns.get_level_values(0)
                    stock_data[symbol] = data
            except:
                continue

        # Rank stocks
        rankings = self.stock_ranker.rank_stocks(stock_data)

        return {
            'date': datetime.now().strftime('%Y-%m-%d'),
            'top_picks': rankings.head(5).to_dict('records'),
            'value_picks': rankings.nlargest(5, 'Value_Score').to_dict('records'),
            'momentum_picks': rankings.nlargest(5, 'Momentum_Score').to_dict('records'),
            'full_rankings': rankings.to_dict('records')
        }

    def scan_all_stocks(self, symbols: List[str]) -> Dict:
        """
        Scan multiple stocks for intraday signals.
        """
        all_signals = {
            'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M'),
            'long_signals': [],
            'short_signals': [],
            'no_signal': []
        }

        for symbol in symbols:
            try:
                result = self.get_intraday_signals(symbol)

                if 'error' in result:
                    continue

                if result['signals']:
                    for sig in result['signals']:
                        entry = {
                            'symbol': symbol,
                            'price': result['current_price'],
                            **sig
                        }
                        if sig['type'] == 'LONG':
                            all_signals['long_signals'].append(entry)
                        else:
                            all_signals['short_signals'].append(entry)
                else:
                    all_signals['no_signal'].append(symbol)

            except Exception as e:
                continue

        # Sort by confidence
        confidence_order = {'HIGH': 0, 'MEDIUM': 1, 'LOW': 2}
        all_signals['long_signals'].sort(key=lambda x: confidence_order.get(x.get('confidence', 'LOW'), 2))
        all_signals['short_signals'].sort(key=lambda x: confidence_order.get(x.get('confidence', 'LOW'), 2))

        return all_signals


def print_intraday_signals(signals: Dict):
    """Pretty print intraday signals."""
    print(f"\n{'='*60}")
    print(f"INTRADAY SIGNALS - {signals['symbol']}")
    print(f"{'='*60}")
    print(f"Time: {signals['timestamp']}")
    print(f"Current Price: Rs {signals['current_price']:.2f}")

    if not signals['signals']:
        print("\nNo active signals at this time.")
        return

    for sig in signals['signals']:
        print(f"\n--- {sig['strategy']} Signal ---")
        print(f"  Type: {sig['type']}")
        print(f"  Confidence: {sig['confidence']}")
        if not pd.isna(sig.get('entry')):
            print(f"  Entry: Rs {sig['entry']:.2f}")
        if not pd.isna(sig.get('stop_loss')):
            print(f"  Stop Loss: Rs {sig['stop_loss']:.2f}")
        if sig.get('target1') and not pd.isna(sig['target1']):
            print(f"  Target 1: Rs {sig['target1']:.2f}")
            print(f"  Target 2: Rs {sig['target2']:.2f}")
        if sig.get('position_size'):
            print(f"  Position Size: {sig['position_size']} shares")
            print(f"  Risk: Rs {sig['risk_amount']:.2f} ({sig['risk_pct']:.2f}%)")


def print_market_regime(regime: Dict):
    """Pretty print market regime."""
    print(f"\n{'='*60}")
    print("MARKET REGIME ANALYSIS")
    print(f"{'='*60}")
    print(f"Date: {regime['date']}")
    print(f"Nifty Close: {regime['close']:.2f}")
    print(f"\nRegime: {regime['regime']} (Score: {regime['regime_score']})")
    print(f"RSI: {regime['rsi']:.1f}")
    print(f"Volatility: {regime['volatility']:.1f}%")
    print(f"Above 50 SMA: {'Yes' if regime['above_50_sma'] else 'No'}")
    print(f"Above 200 SMA: {'Yes' if regime['above_200_sma'] else 'No'}")

    rec = regime['sip_recommendation']
    print(f"\n--- SIP RECOMMENDATION ---")
    print(f"Action: {rec['action']}")
    print(f"Investment Multiplier: {rec['multiplier']}x")
    print(f"Reason: {rec['reason']}")


def print_stock_recommendations(recs: Dict):
    """Pretty print stock recommendations."""
    print(f"\n{'='*60}")
    print("STOCK RECOMMENDATIONS FOR MONTHLY INVESTMENT")
    print(f"{'='*60}")
    print(f"Date: {recs['date']}")

    print("\n--- TOP 5 PICKS (Momentum + Value) ---")
    for stock in recs['top_picks']:
        print(f"  {stock['Rank']}. {stock['Symbol']} - Score: {stock['Combined_Score']:.1f}")

    print("\n--- VALUE PICKS (Oversold) ---")
    for stock in recs['value_picks'][:3]:
        print(f"  - {stock['Symbol']} - Value Score: {stock['Value_Score']:.1f}")

    print("\n--- MOMENTUM PICKS (Strong Trend) ---")
    for stock in recs['momentum_picks'][:3]:
        print(f"  - {stock['Symbol']} - Momentum Score: {stock['Momentum_Score']:.1f}")


def print_scan_results(scan: Dict):
    """Pretty print scan results."""
    print(f"\n{'='*60}")
    print(f"MARKET SCAN - {scan['timestamp']}")
    print(f"{'='*60}")

    if scan['long_signals']:
        print(f"\n--- LONG SIGNALS ({len(scan['long_signals'])}) ---")
        for sig in scan['long_signals'][:5]:  # Top 5
            print(f"  {sig['symbol']}: {sig['strategy']} [{sig['confidence']}]")
            print(f"    Price: Rs {sig['price']:.2f}, Entry: Rs {sig.get('entry', 'N/A')}")

    if scan['short_signals']:
        print(f"\n--- SHORT SIGNALS ({len(scan['short_signals'])}) ---")
        for sig in scan['short_signals'][:5]:
            print(f"  {sig['symbol']}: {sig['strategy']} [{sig['confidence']}]")
            print(f"    Price: Rs {sig['price']:.2f}, Entry: Rs {sig.get('entry', 'N/A')}")

    print(f"\nStocks with no signal: {len(scan['no_signal'])}")


def run_daily_analysis():
    """Run complete daily analysis."""
    print("\n" + "="*70)
    print("DAILY MARKET ANALYSIS - " + datetime.now().strftime('%Y-%m-%d %H:%M'))
    print("="*70)

    generator = SignalGenerator(capital=100000)

    # 1. Market Regime
    print("\n[1/3] Checking market regime...")
    try:
        regime = generator.get_market_regime('^NSEI')
        if 'error' not in regime:
            print_market_regime(regime)
    except Exception as e:
        print(f"Error checking market regime: {e}")

    # 2. Stock Recommendations (for monthly investment)
    print("\n[2/3] Generating stock recommendations...")
    try:
        recs = generator.get_stock_recommendations()
        print_stock_recommendations(recs)
    except Exception as e:
        print(f"Error generating recommendations: {e}")

    # 3. Intraday signals for top stocks
    print("\n[3/3] Checking intraday signals for top stocks...")
    top_stocks = ['RELIANCE.NS', 'TCS.NS', 'HDFCBANK.NS', 'INFY.NS', 'SBIN.NS']

    for symbol in top_stocks:
        try:
            signals = generator.get_intraday_signals(symbol)
            if signals.get('signals'):
                print_intraday_signals(signals)
        except Exception as e:
            print(f"Error checking {symbol}: {e}")

    print("\n" + "="*70)
    print("ANALYSIS COMPLETE")
    print("="*70)


if __name__ == "__main__":
    run_daily_analysis()
