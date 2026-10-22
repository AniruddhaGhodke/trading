"""Live Nifty + Nifty option data while the server runs in market hours (Angel One SmartAPI, data only).

  every minute  today's 1m candles (+ OI for options) for Nifty, India VIX, the near-ATM options of the nearest
                expiry and every contract a chart is showing -> OVERLAY (completed minutes only). New rows are also
                appended to the history files, so the daily recorder continues from them.
  every 2 s     LTP quotes -> FORMING: the current, unfinished minute per symbol.
data_source / options merge OVERLAY on top of the files; FORMING is only used for the live candle and the chain.

Times use Angel's corrected clock (this PC's clock can be minutes off; see angel_data._clock_offset).
Needs SwingSystem/.env. Without it, or outside market hours, the thread just idles.
"""
from __future__ import annotations

import sys
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

SWING = Path(__file__).resolve().parent.parent / "SwingSystem"
SPOT, VIX = "IDX_NSEI", "IDX_INDIAVIX"
INDEX_TOKENS = {SPOT: "99926000", VIX: "99926017"}
CHAIN_WIDTH = 5            # strikes each side of ATM recorded live (nearest expiry)
WATCH_TTL = 300            # a contract stays watched this many seconds after a chart last asked for it
QUOTE_EVERY = 2.0
REQUEST_GAP = 0.8          # seconds between candle requests (Angel's ~3/s limit is per account, shared with recorders)
OI_EVERY = 5               # minutes between open-interest refreshes
QUOTE_MAX_AGE = 60         # seconds: an older quote is not used as the current price
PERSIST_AFTER = pd.Timedelta(minutes=2)   # only minutes this old are written to the history files

OVERLAY: dict[str, pd.DataFrame] = {}
FORMING: dict[str, dict] = {}       # symbol -> {"time": minute start, open, high, low, close, "at": quote time}
WATCH: dict[str, float] = {}
QBARS: dict[str, dict] = {}         # symbol -> {minute: {"Open","High","Low","Close"}} finished quote minutes
STATE = {"enabled": False, "market": False, "error": None, "last_candles": None, "last_quote": None}
_offset = 0.0
_lock = threading.Lock()


def now() -> pd.Timestamp:
    """IST wall-clock, corrected for this PC's clock error once Angel One has been contacted."""
    return pd.Timestamp(datetime.fromtimestamp(time.time() + _offset)).floor("s")


def overlay(symbol: str) -> pd.DataFrame | None:
    """Today's completed minutes: exchange candles, plus quote-built bars for the minutes the (slower) candle loop
    hasn't fetched yet - so the chart has no gap; the candles replace them when they arrive."""
    ov, q = OVERLAY.get(symbol), QBARS.get(symbol)
    if not q or STATE.get("no_session"):     # holiday: quotes are the last session's frozen prices, not bars
        return ov
    last = ov.index[-1] if ov is not None and len(ov) else pd.Timestamp(0)
    rows = {m: b for m, b in list(q.items()) if m > last}
    if not rows:
        return ov
    qb = pd.DataFrame.from_dict(rows, orient="index").sort_index()
    qb["Volume"] = 0.0
    qb.index.name = "Datetime"
    return qb if ov is None else pd.concat([ov, qb])


def watch(symbol: str):
    WATCH[symbol] = time.monotonic()


def quote(symbol: str, t=None) -> dict | None:
    """The live minute of symbol if its last quote is fresh at time t (default: now): taken at or before t and at
    most QUOTE_MAX_AGE seconds earlier. A contract that left the watch list stops updating and so stops counting."""
    f = FORMING.get(symbol)
    if f is None:
        return None
    age = ((pd.Timestamp(t) if t is not None else now()) - f["at"]).total_seconds()
    return f if 0 <= age <= QUOTE_MAX_AGE else None


def ltp(symbol: str, t=None) -> float | None:
    f = quote(symbol, t)
    return None if f is None else f["close"]


def in_session(t: pd.Timestamp, grace_min: int = 2) -> bool:
    tod = t - t.normalize()
    return t.weekday() < 5 and pd.Timedelta(hours=9, minutes=14) <= tod <= pd.Timedelta(hours=15, minutes=30 + grace_min)


def _keep_awake(on: bool):
    """Windows: stop idle sleep / standby while the market is open (19-Oct: standby on battery froze the live
    data for 43 min). Can't prevent a closed lid or a critical-battery sleep - keep the laptop on the charger."""
    if sys.platform != "win32":
        return
    import ctypes
    ES_CONTINUOUS, ES_SYSTEM_REQUIRED, ES_DISPLAY_REQUIRED = 0x80000000, 0x00000001, 0x00000002
    flags = ES_CONTINUOUS | (ES_SYSTEM_REQUIRED | ES_DISPLAY_REQUIRED if on else 0)
    ctypes.windll.kernel32.SetThreadExecutionState(flags)


def status() -> dict:
    return {**STATE, "now": int(now().value // 10**9), "watching": sorted(_watched())}


def _watched() -> list[str]:
    cut = time.monotonic() - WATCH_TTL
    return [s for s, t in list(WATCH.items()) if t >= cut]


# ---------------------------------------------------------------- polling thread
class _Live(threading.Thread):
    daemon = True

    def __init__(self):
        super().__init__(name="live")
        self.angel = None
        self.tokens: dict[str, str] = {}
        self.last_candle_min = None
        self.day = None
        self.last_quotes = 0.0
        self.persisted: dict[str, pd.Timestamp] = {}

    def run(self):
        global _offset
        if not (SWING / ".env").exists():
            STATE["error"] = "no SwingSystem/.env - live data disabled"
            return
        STATE["enabled"] = True
        awake = None
        while True:
            try:
                t = now()
                if in_session(t) != awake:     # SetThreadExecutionState is per thread: call it from this one
                    awake = in_session(t)
                    _keep_awake(awake)
                if not awake:
                    STATE["market"] = False
                    time.sleep(20)
                    continue
                if self.angel is None:
                    self._login()
                    _offset = self.angel._offset
                    continue
                self._quotes()
                t = now()
                m = t.floor("min")
                if m != self.last_candle_min and t.second >= 3:   # give the exchange a moment to close the minute
                    self.last_candle_min = m
                    self._candles(t)
                    if m.normalize() != self.day:
                        self.day = m.normalize()
                        import data_source
                        data_source.invalidate(new_day=True)
                STATE["error"] = None
                time.sleep(QUOTE_EVERY)
            except Exception as e:      # keep the server alive whatever the broker does
                STATE["error"] = f"{type(e).__name__}: {str(e)[:160]}"
                time.sleep(15)

    def _login(self):
        if str(SWING) not in sys.path:
            sys.path.insert(0, str(SWING))
        from core.angel_data import Angel
        self.angel = Angel()
        self.angel.gap = REQUEST_GAP

    # -- which contracts
    def _token(self, symbol: str) -> str | None:
        if symbol in INDEX_TOKENS:
            return INDEX_TOKENS[symbol]
        if symbol not in self.tokens:
            import options
            e, K, typ = options.parse(symbol)
            c = options._contracts()
            hit = c[(c["expiry"] == e) & (c["strike"] == K) & (c["type"] == typ)]
            if not len(hit):
                from core.angel_options import contracts
                c = contracts()
                hit = c[(c["expiry"] == e) & (c["strike"] == K) & (c["type"] == typ)]
            self.tokens[symbol] = str(hit["token"].iloc[0]) if len(hit) else None
        return self.tokens[symbol]

    def _symbols(self, t: pd.Timestamp) -> list[str]:
        import options
        out = [SPOT, VIX]
        S = ltp(SPOT)
        exps = options.expiries_after(t, 1)
        if S and exps:
            atm = round(S / options.STEP) * options.STEP
            for k in range(-CHAIN_WIDTH, CHAIN_WIDTH + 1):
                for typ in ("CE", "PE"):
                    out.append(options.make(exps[0]["expiry"], atm + k * options.STEP, typ))
        out += [s for s in _watched() if s not in out]
        return [s for s in out if self._token(s)]

    # -- quotes -> forming minute
    def _quotes(self):
        self.last_quotes = time.monotonic()
        syms = self._symbols(now())
        by_ex: dict[str, list[str]] = {"NSE": [], "NFO": []}
        tok2sym = {}
        for s in syms:
            ex = "NSE" if s in INDEX_TOKENS else "NFO"
            by_ex[ex].append(self._token(s))
            tok2sym[(ex, self._token(s))] = s
        req = {k: v[:50] for k, v in by_ex.items() if v}
        try:
            r = self.angel.api.getMarketData("LTP", req)
        except Exception as e:
            r = {"status": False, "message": str(e)}
        if not r or not r.get("status"):
            msg = str((r or {}).get("message", ""))
            if "token" in msg.lower() or (r or {}).get("errorcode") in ("AG8001", "AG8002", "AG8003"):
                time.sleep(31)              # session invalidated (e.g. another login): fresh TOTP window, log in again
                self.angel._login()
            raise RuntimeError(f"quotes: {msg}")
        at = now()
        m = at.floor("min")
        today = m.normalize()
        fresh, rolled = False, False
        for x in (r.get("data") or {}).get("fetched", []):
            s = tok2sym.get((x.get("exchange"), str(x.get("symbolToken"))))
            if s is None:
                continue
            p = float(x["ltp"])
            f = FORMING.get(s)
            if f is not None and f["time"] != m and f["time"] < m:
                if f["time"].normalize() != m.normalize():
                    QBARS[s] = {}
                QBARS.setdefault(s, {})[f["time"]] = {"Open": f["open"], "High": f["high"], "Low": f["low"], "Close": f["close"]}
                rolled = True
            if f is None or f["time"] != m:
                FORMING[s] = {"time": m, "open": p, "high": p, "low": p, "close": p, "at": at}
            else:
                f.update(high=max(f["high"], p), low=min(f["low"], p), close=p, at=at)
            fresh = True
        if rolled:
            import data_source
            data_source.invalidate()
        STATE["last_quote"] = int(now().value // 10**9) if fresh else STATE["last_quote"]
        STATE["market"] = fresh and today.weekday() < 5 and not STATE.get("no_session")

    # -- candles -> overlay (+ history files)
    def _candles(self, t: pd.Timestamp):
        import data_source
        open_ = t.normalize().to_pydatetime().replace(hour=9, minute=15)
        changed, failed = False, []
        for s in self._symbols(t):
            if time.monotonic() - self.last_quotes >= QUOTE_EVERY:
                self._quotes()          # keep the live candle moving while ~45 candle requests run
            ex = "NSE" if s in INDEX_TOKENS else "NFO"
            tok = self._token(s)
            old = OVERLAY.get(s)
            old = old[old.index >= t.normalize()] if old is not None else None
            # Only the last few minutes after the first fetch of the day (the last stored minute is re-fetched).
            start = max(open_, (old.index[-1] - pd.Timedelta(minutes=2)).to_pydatetime()) if old is not None and len(old) else open_
            try:
                df = self._fetch(s, ex, tok, start, open_, t, old)
            except Exception as e:      # rate limit / network: skip this symbol this minute
                failed.append(s)
                # Seen on 19-Oct right after the PC woke from standby (catch-up burst), not from another process
                rate = "exceeding access rate" in str(e)
                STATE["error"] = (f"{len(failed)} symbol(s) skipped this minute - Angel rate limit" if rate
                                  else f"{s}: {str(e)[:100]}")
                continue
            if df is None:
                continue
            with _lock:
                OVERLAY[s] = df
            changed = True
            self._persist(s, df)
        STATE["last_candles"] = int(now().value // 10**9)
        # 20-Oct-2026 (Dussehra): quotes kept returning the last session's prices, so quote-built bars looked live.
        # No exchange candle for Nifty by 09:20 = no session today (holiday) -> no fake bars, market closed.
        sp = OVERLAY.get(SPOT)
        has_today = sp is not None and len(sp) and sp.index[-1] >= t.normalize()
        STATE["no_session"] = (not has_today) and t - t.normalize() >= pd.Timedelta(hours=9, minutes=20)
        if STATE["no_session"]:
            STATE["error"] = "no exchange candles today - market closed (holiday?)"
            data_source.invalidate()
        elif not failed:
            STATE["error"] = None
        if changed:
            data_source.invalidate()

    def _fetch(self, s, ex, tok, start, open_, t, old) -> pd.DataFrame | None:
        """New 1m candles of one symbol merged onto its overlay. Option OI (a second request) every OI_EVERY min."""
        rows = self.angel._candles_once(tok, "1m", start, t.to_pydatetime(), exchange=ex)
        if not rows:
            return None
        df = pd.DataFrame(rows, columns=["ts", "Open", "High", "Low", "Close", "Volume"])
        df.index = pd.to_datetime(df.pop("ts")).dt.tz_convert("Asia/Kolkata").dt.tz_localize(None)
        df = df[~df.index.duplicated()].astype(float)
        df = df[df.index + pd.Timedelta("1min") <= t]           # completed minutes only
        if old is not None and len(old):
            df = pd.concat([old, df])
            df = df[~df.index.duplicated(keep="last")].sort_index()
        df.index.name = "Datetime"
        if time.monotonic() - self.last_quotes >= QUOTE_EVERY:
            self._quotes()
        if ex == "NFO":
            if "OI" not in df:
                df["OI"] = float("nan")
            if old is None or not len(old) or t.minute % OI_EVERY == 0:
                oi = self.angel._candles_once(tok, "1m", open_, t.to_pydatetime(), exchange=ex, oi=True)
                if oi:
                    o = pd.DataFrame(oi)
                    o.index = pd.to_datetime(o.pop("time")).dt.tz_convert("Asia/Kolkata").dt.tz_localize(None)
                    df["OI"] = o[~o.index.duplicated()]["oi"].reindex(df.index).combine_first(df["OI"])
        return df

    def _persist(self, s: str, df: pd.DataFrame):
        """Append new completed rows to the history file - only if the file is up to date to the last session
        (never leave a gap that the resumable recorders would not fill)."""
        import options
        if s in INDEX_TOKENS:
            p = SWING / "data" / "angel" / "1m" / f"{s}.csv"
        else:
            e, K, typ = options.parse(s)
            p = options.real_path(e, K, typ)
        last = self.persisted.get(s)
        if last is None:
            if p.is_file():
                with open(p, "rb") as fh:
                    fh.seek(max(0, p.stat().st_size - 300))
                    tail = fh.read().decode(errors="ignore").strip().splitlines()[-1]
                last = pd.Timestamp(tail.split(",")[0])
                if (df.index[0].normalize() - last.normalize()).days > 4:
                    return              # history is behind: leave it to the recorder
            else:
                if s in INDEX_TOKENS:
                    return
                last = pd.Timestamp(0)
        new = df[(df.index > last) & (df.index + pd.Timedelta("1min") <= now() - PERSIST_AFTER)]
        if not len(new):
            return
        p.parent.mkdir(parents=True, exist_ok=True)
        cols = ["Open", "High", "Low", "Close", "Volume"] + (["OI"] if s not in INDEX_TOKENS else [])
        new[cols].to_csv(p, mode="a", header=not p.is_file())
        self.persisted[s] = new.index[-1]


_thread: _Live | None = None
_cache: dict[str, tuple[float, object]] = {}
DEPTH_EVERY = 10           # seconds a FULL quote (bid/ask) is reused
ACCOUNT_EVERY = 30         # seconds the trade book / positions are reused


def _cached(key: str, max_age: float, fetch):
    """fetch() at most once per max_age seconds, sharing the live thread's Angel One session (no second login).
    A failed call (None or exception) is retried next time and returns None - never the last good value, so a
    caller can't mistake an old trade book for the current one. None also while the live thread is not logged in."""
    hit = _cache.get(key)
    if hit and time.monotonic() - hit[0] < max_age:
        return hit[1]
    angel = _thread.angel if _thread is not None else None
    if angel is None:
        return None
    try:
        val = fetch(angel.api)
    except Exception:
        val = None
    if val is not None:
        _cache[key] = (time.monotonic(), val)
    return val


def depth(symbols: list[str]) -> dict:
    """symbol -> {ltp, bid, ask, prev_close, open} from a FULL quote (best bid/ask). Nifty and Nifty options only."""
    if _thread is None or _thread.angel is None:
        return {}
    req: dict[str, list[str]] = {}
    tok2sym = {}
    for s in symbols:
        tok = _thread._token(s)
        if tok:
            ex = "NSE" if s in INDEX_TOKENS else "NFO"
            req.setdefault(ex, []).append(tok)
            tok2sym[(ex, tok)] = s

    def fetch(api):
        r = api.getMarketData("FULL", req)
        out = {}
        for x in ((r or {}).get("data") or {}).get("fetched", []):
            s = tok2sym.get((x.get("exchange"), str(x.get("symbolToken"))))
            d = x.get("depth") or {}
            bid = next((b["price"] for b in d.get("buy", []) if b.get("price")), None)
            ask = next((a["price"] for a in d.get("sell", []) if a.get("price")), None)
            out[s] = {"ltp": x.get("ltp"), "bid": bid, "ask": ask, "prev_close": x.get("close"), "open": x.get("open")}
        return out
    return _cached("depth:" + ",".join(sorted(symbols)), DEPTH_EVERY, fetch) or {}


def account() -> dict | None:
    """Today's fills (trade book) and positions - read only."""
    def fetch(api):
        tb, pos = api.tradeBook(), api.position()
        if not (tb or {}).get("status") or not (pos or {}).get("status"):
            return None
        return {"trades": tb.get("data") or [], "positions": pos.get("data") or []}
    return _cached("account", ACCOUNT_EVERY, fetch)


def start():
    global _thread
    if _thread is None:
        _thread = _Live()
        _thread.start()
