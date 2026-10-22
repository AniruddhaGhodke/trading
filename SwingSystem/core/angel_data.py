"""
Angel One SmartAPI - DATA ONLY (no order placement).

Credentials come from SwingSystem/.env (never hard-code or print them):
  ANGEL_API_KEY, ANGEL_CLIENT_CODE, ANGEL_PIN, ANGEL_TOTP_SECRET

Broker candles are RAW prices (not split/dividend adjusted); callers should run
core.data.fix_corporate_jumps on them, as for Yahoo data.
"""

import json
import time
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
import pyotp
import requests
from SmartApi import SmartConnect

import config

ENV_FILE = config.ROOT / ".env"
ANGEL_DIR = config.DATA_DIR / "angel"
SCRIP_MASTER_URL = "https://margincalculator.angelone.in/OpenAPI_File/files/OpenAPIScripMaster.json"

# Max days per getCandleData request (SmartAPI docs) and our names for intervals.
# 1m uses 20 days, not the documented 30: a request returns at most ~8000 candles, and 22 trading days x 375
# = 8250 silently lost the first chunk day's 09:15-13:24 (verified 2026-10-14 on ^NSEI).
INTERVALS = {
    "1m": ("ONE_MINUTE", 20), "3m": ("THREE_MINUTE", 60), "5m": ("FIVE_MINUTE", 100),
    "10m": ("TEN_MINUTE", 100), "15m": ("FIFTEEN_MINUTE", 200), "30m": ("THIRTY_MINUTE", 200),
    "1h": ("ONE_HOUR", 400), "1d": ("ONE_DAY", 2000),
}
INDEX_TOKENS = {"^NSEI": "99926000", "^INDIAVIX": "99926017"}     # Nifty 50, India VIX
REQUEST_GAP = 0.4                        # seconds between candle requests (rate limit ~3/s)


def _env() -> dict:
    if not ENV_FILE.exists():
        raise SystemExit(f"Missing {ENV_FILE}")
    out = {}
    for line in ENV_FILE.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip().strip('"').strip("'")
    return out


class Angel:
    def __init__(self):
        self._offset = _clock_offset()
        self._login()
        self.tokens = _token_map()
        self._last_call = 0.0
        self.gap = REQUEST_GAP          # seconds between requests; raise it when another process shares the limit

    def _login(self):
        env = _env()
        self.api = SmartConnect(api_key=env["ANGEL_API_KEY"])
        # TOTP depends on the clock; use internet time in case the PC clock is off
        # (this PC was 150 s slow, which made every code invalid)
        totp = pyotp.TOTP(env["ANGEL_TOTP_SECRET"].replace(" ", "")).at(time.time() + self._offset)
        resp = self.api.generateSession(env["ANGEL_CLIENT_CODE"], env["ANGEL_PIN"], totp)
        if not resp or not resp.get("status"):
            msg = (resp or {}).get("message", "no response")
            raise SystemExit(f"Angel One login failed: {msg} (errorcode {(resp or {}).get('errorcode')})")

    def token(self, symbol: str) -> str:
        if symbol in INDEX_TOKENS:
            return INDEX_TOKENS[symbol]
        tok = self.tokens.get(symbol.replace(".NS", ""))
        if tok is None:
            raise KeyError(f"No NSE-EQ token for {symbol}")
        return tok

    def quotes(self, symbols: list[str]) -> pd.DataFrame:
        """Live OHLC + LTP for symbols (open/high/low of the latest session, 'close' = previous close).
        'session' = date of the last trade, so callers can tell a live session from a stale one
        (weekends/holidays return the previous session's OHLC)."""
        rows = []
        toks = {self.token(s): s for s in symbols}
        items = list(toks)
        for k in range(0, len(items), 50):               # API limit: 50 tokens per request
            for attempt in range(3):
                try:
                    r = self.api.getMarketData("FULL", {"NSE": items[k:k + 50]})
                except Exception as e:
                    r = {"status": False, "message": str(e)}
                if r and r.get("status"):
                    for x in (r.get("data") or {}).get("fetched", []):
                        rows.append({"symbol": toks.get(str(x["symbolToken"])), "ltp": x["ltp"], "open": x["open"],
                                     "high": x["high"], "low": x["low"], "prev_close": x["close"],
                                     "session": pd.to_datetime(x.get("exchTradeTime"), format="%d-%b-%Y %H:%M:%S",
                                                               errors="coerce").normalize()})
                    break
                if "token" in str((r or {}).get("message", "")).lower():
                    time.sleep(31)
                    self._login()
                else:
                    time.sleep(2)
        return pd.DataFrame(rows).set_index("symbol") if rows else pd.DataFrame()

    def _candles_once(self, token: str, interval: str, start: datetime, end: datetime, exchange: str = "NSE",
                      oi: bool = False) -> list:
        """One getCandleData (or getOIData if oi) request, with rate limiting and re-login."""
        for attempt in range(5):
            wait = self.gap - (time.time() - self._last_call)
            if wait > 0:
                time.sleep(wait)
            self._last_call = time.time()
            try:
                r = (self.api.getOIData if oi else self.api.getCandleData)({
                    "exchange": exchange, "symboltoken": token, "interval": INTERVALS[interval][0],
                    "fromdate": start.strftime("%Y-%m-%d %H:%M"), "todate": end.strftime("%Y-%m-%d %H:%M"),
                })
            except Exception as e:          # network / rate-limit exceptions from the SDK
                r = {"status": False, "message": str(e)}
            if r and r.get("status"):
                return r.get("data") or []
            if (r or {}).get("errorcode") == "AB1012" and exchange != "NSE":
                return []               # "Invalid Bad Request": seen for untraded far-OTM option strikes
            msg = str((r or {}).get("message", ""))
            if "token" in msg.lower() or (r or {}).get("errorcode") in ("AG8001", "AG8002", "AG8003"):
                print("  (session invalid - logging in again)", flush=True)
                time.sleep(31)          # session expired/invalidated: wait for a fresh TOTP window, log in again
                self._login()
                continue
            time.sleep(1.5 * (attempt + 1))
        raise RuntimeError(f"getCandleData failed: {r.get('message') if r else 'no response'}")

    def candles(self, symbol: str, interval: str, start: datetime, end: datetime | None = None) -> pd.DataFrame:
        """OHLCV in IST (tz-naive), fetched in chunks within the per-request limits."""
        end = end or datetime.now()
        step = timedelta(days=INTERVALS[interval][1])
        token, rows, cur = self.token(symbol), [], start
        while cur < end:
            nxt = min(cur + step, end)
            rows += self._candles_once(token, interval, cur, nxt)
            cur = nxt + timedelta(minutes=1)
        if not rows:
            return pd.DataFrame(columns=["Open", "High", "Low", "Close", "Volume"])
        df = pd.DataFrame(rows, columns=["ts", "Open", "High", "Low", "Close", "Volume"])
        df.index = pd.to_datetime(df.pop("ts")).dt.tz_convert("Asia/Kolkata").dt.tz_localize(None)
        return df[~df.index.duplicated()].sort_index().astype(float)


def backfill(interval: str = "5m", start: datetime = datetime(2018, 1, 1), symbols: list[str] | None = None,
             quiet: bool = False):
    """
    Download raw candles for the universe + Nifty into data/angel/<interval>/<symbol>.csv.
    Resumable: each symbol continues from its last stored bar. Prints one line per symbol.
    """
    from core import data as core_data
    a = Angel()
    symbols = symbols or (core_data.load_universe() + ["^NSEI"])
    out_dir = ANGEL_DIR / interval
    out_dir.mkdir(parents=True, exist_ok=True)
    for n, s in enumerate(symbols, 1):
        p = out_dir / f"{s.replace('^', 'IDX_')}.csv"
        old = pd.read_csv(p, index_col=0, parse_dates=True) if p.exists() else None
        frm = (old.index[-1] + timedelta(minutes=1)) if old is not None and len(old) else start
        t0 = time.time()
        try:
            new = a.candles(s, interval, frm)
        except Exception as e:
            print(f"[{n}/{len(symbols)}] {s}: ERROR {str(e)[:80]}", flush=True)
            continue
        df = pd.concat([old, new]) if old is not None else new
        df = df[~df.index.duplicated()].sort_index()
        df.index.name = "Datetime"
        df.to_csv(p)
        if not quiet:
            print(f"[{n}/{len(symbols)}] {s}: +{len(new)} bars, total {len(df)} "
                  f"({df.index[0].date()} -> {df.index[-1].date()}) {time.time() - t0:.0f}s", flush=True)
    if quiet:
        print(f"Angel One {interval}: history updated for {len(symbols)} symbols")


def _clock_offset() -> float:
    """Seconds to ADD to the local clock to match internet time (0 if it can't be determined)."""
    import email.utils
    for url in ("https://www.google.com", "https://www.cloudflare.com", "https://www.microsoft.com"):
        try:
            r = requests.head(url, timeout=10)
            server = email.utils.parsedate_to_datetime(r.headers["Date"]).timestamp() + 0.5  # Date has 1s resolution
        except Exception:
            continue
        off = server - time.time()
        if abs(off) > 20:
            print(f"(note: PC clock is {-off:+.0f}s off internet time - corrected for TOTP; consider syncing Windows time)")
        return off
    return 0.0


def _token_map() -> dict:
    """NSE equity symbol (e.g. 'RELIANCE') -> SmartAPI token, from the daily scrip master."""
    ANGEL_DIR.mkdir(parents=True, exist_ok=True)
    p = ANGEL_DIR / "scrip_master.json"
    if not p.exists() or datetime.fromtimestamp(p.stat().st_mtime).date() != datetime.now().date():
        # ~36 MB; Angel's server often drops the connection mid-download. Retry, write atomically, and fall
        # back to the previous day's file (index/equity tokens don't change) rather than blocking live data.
        tries = 1 if p.exists() else 3                     # with a fallback file, don't hold up live data
        for attempt in range(tries):
            try:
                r = requests.get(SCRIP_MASTER_URL, timeout=60)
                r.raise_for_status()
                json.loads(r.content)                      # reject a truncated body
                tmp = p.with_suffix(".tmp")
                tmp.write_bytes(r.content)
                tmp.replace(p)
                break
            except Exception as e:
                print(f"(scrip master download failed, attempt {attempt + 1}/{tries}: {type(e).__name__})")
                if attempt == tries - 1:
                    if not p.exists():
                        raise
                    print("(using the previous scrip master file)")
    out = {}
    for x in json.loads(p.read_text()):
        if x.get("exch_seg") == "NSE" and x.get("symbol", "").endswith("-EQ"):
            out[x["symbol"][:-3]] = x["token"]
    return out
