"""Bar-replay chart server (stdlib only).  Run:  python server.py  ->  http://127.0.0.1:8050

API
  GET /api/symbols   -> {"symbols": [...], "intervals": [...], "indicators": registry metadata}
  GET /api/bars?symbol=&start=&interval=5m&before=300&after=2000&ind=ema:20,rsi:14,macd:12:26:9
                     -> data_source.window(...) + "indicators": values aligned with "bars"
  GET /api/forming?symbol=&interval=&time=<bar open, epoch s>&tick=auto&ind=...
                     -> successive states of one candle + indicator values for each state
  GET /api/expiries?time=<epoch s>&n=4      -> next Nifty expiries at that time (lot size, real/synthetic)
  GET /api/chain?time=<epoch s>&expiry=YYYY-MM-DD&n=10
                     -> option chain known at that time (options.chain)
  GET /api/live?symbol=&interval=           -> live state: server time, market open, last completed bar,
                                               forming bar (Nifty and Nifty options only)
  GET /api/assist?expiry=YYYY-MM-DD         -> snapshot for assist.py: today's Nifty 1m, VIX, near-ATM options,
                                               bid/ask, trade book + positions (assist_data.snapshot)
  GET /api/alerts?time=<epoch s>&live=0|1   -> assist + practice-setup (BASEBREAK) alerts of that day (pullback.day_alerts); the client
                                               shows only alerts whose time <= its clock (live: bars closed by now)
  GET /api/decision?alert=<epoch s>&side=CE|PE&choice=take|skip&clock=<epoch s>&mode=replay|live&kind=BASEBREAK|PULLBACK
                                            -> logs the user's Take/Skip on a setup alert (pullback.log_decision)
Option contracts are symbols NIFTY_<YYYYMMDD>_<strike><CE|PE> (see options.py).
To add an endpoint, add a function to ROUTES.
"""
from __future__ import annotations

import json
from functools import lru_cache
import mimetypes
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pandas as pd

import data_source
import indicators
import live
import options

STATIC = Path(__file__).resolve().parent / "static"
HOST, PORT = "127.0.0.1", 8050


class ApiError(Exception):
    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status, self.detail = status, detail


def _int(q: dict, key: str, default: int, lo: int, hi: int) -> int:
    try:
        v = int(q.get(key, default))
    except ValueError:
        raise ApiError(400, f"{key} must be an integer")
    return min(max(v, lo), hi)


def api_symbols(q: dict) -> dict:
    return {"symbols": data_source.list_symbols(), "intervals": list(data_source.INTERVALS),
            "ticks": data_source.TICKS, "minutes": data_source.MINUTES,
            "indicators": indicators.registry_meta()}


def api_bars(q: dict) -> dict:
    for k in ("symbol", "start"):
        if not q.get(k):
            raise ApiError(400, f"missing parameter: {k}")
    interval = q.get("interval", "5m")
    if interval not in data_source.INTERVALS:
        raise ApiError(400, f"interval must be one of {list(data_source.INTERVALS)}")
    try:
        items = indicators.parse(q.get("ind", ""))
        w = data_source.window(q["symbol"], q["start"], interval,
                               _int(q, "before", 300, 0, 5000), _int(q, "after", 2000, 0, 20000))
        w["indicators"] = indicators.for_window(q["symbol"], interval, items, w["lo"], w["hi"])
        w["has_1m"] = data_source.has_1m(q["symbol"])
        return w
    except KeyError as e:
        raise ApiError(404, e.args[0])
    except ValueError as e:
        raise ApiError(400, str(e))


def api_forming(q: dict) -> dict:
    for k in ("symbol", "interval", "time"):
        if not q.get(k):
            raise ApiError(400, f"missing parameter: {k}")
    interval, tick = q["interval"], q.get("tick", "auto")
    if interval not in data_source.INTERVALS or tick not in data_source.TICKS:
        raise ApiError(400, "bad interval or tick")
    try:
        items = indicators.parse(q.get("ind", ""))
        f = data_source.forming(q["symbol"], interval, pd.Timestamp(int(q["time"]), unit="s"), tick)
        part = f.pop("_partials")
        f["indicators"] = indicators.for_partials(q["symbol"], interval, items, f["i"], part) if len(part) > 1 else {}
        return f
    except KeyError as e:
        raise ApiError(404, e.args[0])
    except ValueError as e:
        raise ApiError(400, str(e))


def _time(q: dict) -> pd.Timestamp:
    try:
        return pd.Timestamp(int(q["time"]), unit="s")
    except (KeyError, ValueError):
        raise ApiError(400, "time must be epoch seconds")


def api_expiries(q: dict) -> dict:
    return {"expiries": options.expiries_after(_time(q), _int(q, "n", 4, 1, 12)), "lot_now": options.LOT_NOW}


def api_chain(q: dict) -> dict:
    if not q.get("expiry"):
        raise ApiError(400, "missing parameter: expiry")
    try:
        return options.chain(_time(q), q["expiry"], _int(q, "n", 10, 1, 40))
    except KeyError as e:
        raise ApiError(404, e.args[0])
    except ValueError as e:
        raise ApiError(400, str(e))


def api_live(q: dict) -> dict:
    sym, interval = q.get("symbol", ""), q.get("interval", "5m")
    if sym != options.SPOT and not options.parse(sym):
        raise ApiError(400, "live data is only for Nifty (IDX_NSEI) and Nifty options")
    if interval not in data_source.INTERVALS:
        raise ApiError(400, "bad interval")
    if options.parse(sym):
        live.watch(sym)
    t = live.now()
    out = {**live.status(), "symbol": sym, "interval": interval, "last_complete": None, "forming": None,
           "quote": live.quote(sym, t) is not None}
    try:
        df = data_source.load_interval(sym, interval)
    except KeyError as e:
        raise ApiError(404, e.args[0])
    # Complete = the bar's nominal end has passed (a resampled bin with only some minutes so far is not complete).
    done = df[df.index + pd.Timedelta(minutes=data_source.MINUTES[interval]) <= t]
    if len(done):
        out["last_complete"] = int(done.index[-1].value // 10**9)
    # The unfinished bar: completed 1m bars of the current bin + the live (quote) minute.
    if interval == "1D":
        b = t.normalize()
    else:
        o = t.normalize() + pd.Timedelta(minutes=555)
        iv = pd.Timedelta(minutes=data_source.MINUTES[interval])
        b = o + ((t - o) // iv) * iv
    part = data_source.load(sym, "1m") if data_source.has_1m(sym) else df.iloc[0:0]
    part = part[part.index >= b][data_source._COLS]
    f = live.quote(sym, t)
    if f is not None and f["time"] >= b and f["time"] not in part.index:
        part = pd.concat([part, pd.DataFrame([{"Open": f["open"], "High": f["high"], "Low": f["low"],
                                               "Close": f["close"], "Volume": 0.0}], index=[f["time"]])])
    if len(part) and live.in_session(t, 0):
        out["forming"] = {"time": int(b.value // 10**9), "open": float(part["Open"].iloc[0]),
                          "high": float(part["High"].max()), "low": float(part["Low"].min()),
                          "close": float(part["Close"].iloc[-1]), "volume": float(part["Volume"].sum())}
    return out


def api_assist(q: dict) -> dict:
    """Everything assist.py needs in one call (it never logs in to Angel One itself)."""
    import assist_data
    return assist_data.snapshot(live.now(), expiry=q.get("expiry"), live_mode=True)


def api_alerts(q: dict) -> dict:
    import pullback
    t = _time(q)
    if q.get("live") == "1" or t.normalize() == live.now().normalize():
        return {"alerts": pullback.day_alerts(t.normalize(), until=live.now())}
    return {"alerts": _day_alerts_cached(t.normalize())}


@lru_cache(maxsize=64)
def _day_alerts_cached(day):
    import pullback
    return pullback.day_alerts(day)


def api_decision(q: dict) -> dict:
    import pullback
    if q.get("choice") not in ("take", "skip") or q.get("side") not in ("CE", "PE"):
        raise ApiError(400, "choice must be take|skip and side CE|PE")
    alert = _time({"time": q.get("alert")})
    clock = _time({"time": q.get("clock", q.get("alert"))})
    kind = q.get("kind", "PULLBACK")
    if kind not in ("PULLBACK", "BASEBREAK"):
        raise ApiError(400, "kind must be PULLBACK|BASEBREAK")
    return pullback.log_decision(alert, q["side"], q["choice"], clock, q.get("mode", "replay"), kind)


ROUTES = {"/api/symbols": api_symbols, "/api/bars": api_bars, "/api/forming": api_forming,
          "/api/expiries": api_expiries, "/api/chain": api_chain, "/api/live": api_live,
          "/api/assist": api_assist,
          "/api/alerts": api_alerts, "/api/decision": api_decision}


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        url = urlparse(self.path)
        if url.path in ROUTES:
            q = {k: v[-1] for k, v in parse_qs(url.query).items()}
            try:
                self._send(200, json.dumps(ROUTES[url.path](q)).encode(), "application/json")
            except ApiError as e:
                self._send(e.status, json.dumps({"detail": e.detail}).encode(), "application/json")
            return
        rel = "index.html" if url.path == "/" else url.path.removeprefix("/static/")
        f = (STATIC / rel).resolve()
        if not f.is_file() or STATIC not in f.parents:
            self._send(404, b"not found", "text/plain")
            return
        ctype = mimetypes.guess_type(f.name)[0] or "application/octet-stream"
        self._send(200, f.read_bytes(), ctype)

    def _send(self, status: int, body: bytes, ctype: str):
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):  # quieter console: only errors
        if args and str(args[1])[0] in "45":
            sys.stderr.write(f"{self.address_string()} {fmt % args}\n")


class Server(ThreadingHTTPServer):
    # On Windows SO_REUSEADDR lets a second server bind the same port silently (an old copy keeps
    # answering). Without it, starting twice fails loudly instead.
    allow_reuse_address = False


if __name__ == "__main__":
    try:
        httpd = Server((HOST, PORT), Handler)
    except OSError:
        sys.exit(f"Port {PORT} is already in use - is Chart Replay already running? Close it first.")
    if "--no-live" not in sys.argv:
        live.start()     # live Nifty + option data during market hours (idles otherwise)
    print(f"Chart Replay -> http://{HOST}:{PORT}   (Ctrl+C to stop)")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
