import json
import random
import statistics
import threading
import time
from collections import deque
from datetime import datetime, timezone
from typing import List, Optional
from urllib import request, parse, error as urlerror

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
import uvicorn

app = FastAPI(title="Mock Trading Engine")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

current_price = 98000.0
price_buffer: deque = deque(maxlen=10000)
last_trade_time_ms: Optional[int] = None
use_mock = False
BASIS_SYMBOL = "BTCUSDT"
POSITION_NOTIONAL = 10.0  # $10 per cycle per strategy
BASE_CANDLE_SECONDS = 15  # aggregate raw trades into 15s bars
CYCLE_SECONDS = 600  # 10-minute cycle for position resets
FEE_RATE = 0.0004  # 4 bps per side (entry and exit)

STRATEGY_DEFS = [
    {
        "key": "trend",
        "name": "Trend Follow",
        "description": "Follow short/long MA crossover with momentum confirmation",
        "timeframe": 60,
    },
    {
        "key": "mean_revert",
        "name": "Mean Reversion",
        "description": "Fade extremes via RSI and snapback to range mid",
        "timeframe": 30,
    },
    {
        "key": "breakout",
        "name": "Breakout Momentum",
        "description": "Trade Donchian edge breaks with volatility expansion",
        "timeframe": 15,
    },
    {
        "key": "scalper",
        "name": "Volatility Scalper",
        "description": "Range scalp when vol is elevated but price is mid-range",
        "timeframe": BASE_CANDLE_SECONDS,
    },
    {
        "key": "long_hold",
        "name": "Long Bias",
        "description": "Always long; signal checked every 15s bar",
        "timeframe": BASE_CANDLE_SECONDS,
    },
    {
        "key": "short_hold",
        "name": "Short Bias",
        "description": "Always short; signal checked every 15s bar",
        "timeframe": BASE_CANDLE_SECONDS,
    },
]
STRATEGY_NAMES = [s["name"] for s in STRATEGY_DEFS]

NEWS_HEADLINES = [
    ("SEC approves new Bitcoin ETF derivative options", "Tiingo", "positive", 0.89),
    ("Ethereum gas fees drop to 5-year low", "CoinDesk", "neutral", 0.45),
    ("US CPI surprise pushes risk assets lower", "Bloomberg", "negative", 0.32),
    ("Major bank launches crypto custody service", "Reuters", "positive", 0.78),
    ("Bitcoin hash rate hits new all-time high", "The Block", "positive", 0.67),
]

LOG_TEMPLATES = [
    "Fetched {count} new trades from Binance",
    "Order book refreshed at depth {depth}",
    "AI switched to {strategy}",
    "Heartbeat OK, latency {latency} ms",
    "Position rebalanced at {price}",
]


def random_walk_candle():
    global current_price
    open_price = current_price
    drift = random.uniform(-180, 180)
    close_price = max(1000.0, open_price + drift)
    high = max(open_price, close_price) + random.uniform(0, 60)
    low = min(open_price, close_price) - random.uniform(0, 60)
    current_price = close_price
    now = datetime.now(timezone.utc)
    volume = random.uniform(50, 450)
    return {
        "open": round(open_price, 2),
        "high": round(high, 2),
        "low": round(low, 2),
        "close": round(close_price, 2),
        "timestamp": now.isoformat(),
        "volume": round(volume, 3),
    }


def clamp_score(value: float) -> float:
    return max(0.0, min(100.0, value))


def sma(values: List[float], period: int) -> Optional[float]:
    if period <= 0 or len(values) < period:
        return None
    return sum(values[-period:]) / period


def safe_pct_change(current: float, previous: float) -> float:
    if previous is None or previous == 0:
        return 0.0
    return (current - previous) / previous


def aggregate_candles_to_timeframe(candles: List[dict], timeframe_seconds: int) -> List[dict]:
    """Aggregate base candles (15s) into higher timeframe buckets."""
    if timeframe_seconds <= 1:
        return list(candles)
    buckets = {}
    for c in candles:
        ts = int(c.get("time") or 0)
        bucket = (ts // timeframe_seconds) * timeframe_seconds
        existing = buckets.get(bucket)
        if not existing:
            buckets[bucket] = {
                "time": bucket,
                "open": c["open"],
                "high": c["high"],
                "low": c["low"],
                "close": c["close"],
                "volume": c.get("volume", 0),
            }
        else:
            existing["high"] = max(existing["high"], c["high"])
            existing["low"] = min(existing["low"], c["low"])
            existing["close"] = c["close"]
            existing["volume"] = existing.get("volume", 0) + c.get("volume", 0)
    return [buckets[k] for k in sorted(buckets.keys())]


def compute_features_from_closes(closes: List[float]):
    if len(closes) < 5:
        return None
    closes = closes[-360:]
    last_close = closes[-1]
    fast_ma = sma(closes, 20)
    slow_ma = sma(closes, 60)
    rsi_val = compute_rsi(closes, 14)

    vol_window = closes[-60:]
    vol_pct = statistics.pstdev(vol_window) / last_close if len(vol_window) >= 2 and last_close else 0.0

    high_50 = max(closes[-50:]) if closes else last_close
    low_50 = min(closes[-50:]) if closes else last_close
    if high_50 == low_50:
        range_pos = 0.5
    else:
        range_pos = (last_close - low_50) / (high_50 - low_50)
    range_pos = max(0.0, min(1.0, range_pos))
    range_edge = max(range_pos, 1 - range_pos)
    range_center = 1 - range_edge

    mom_15 = safe_pct_change(last_close, closes[-15]) if len(closes) > 15 else 0.0
    mom_30 = safe_pct_change(last_close, closes[-30]) if len(closes) > 30 else mom_15

    return {
        "last_close": last_close,
        "fast_ma": fast_ma,
        "slow_ma": slow_ma,
        "rsi": rsi_val,
        "vol_pct": vol_pct,
        "high_50": high_50,
        "low_50": low_50,
        "range_pos": range_pos,
        "range_edge": range_edge,
        "range_center": range_center,
        "mom_15": mom_15,
        "mom_30": mom_30,
    }

def compute_rsi(values: List[float], period: int = 14) -> Optional[float]:
    if len(values) <= period:
        return None
    gains = 0.0
    losses = 0.0
    start = len(values) - period
    for idx in range(start, len(values)):
        delta = values[idx] - values[idx - 1]
        if delta >= 0:
            gains += delta
        else:
            losses -= delta
    if losses == 0:
        return 100.0
    rs = gains / losses
    return 100 - (100 / (1 + rs))


def compute_score(strategy_key: str, feats: Optional[dict]) -> float:
    if not feats:
        return 50.0
    last_close = feats["last_close"]
    fast_ma = feats["fast_ma"]
    slow_ma = feats["slow_ma"]
    rsi_val = feats["rsi"]
    range_center = feats["range_center"]
    range_edge = feats["range_edge"]
    range_pos = feats["range_pos"]
    mom_15 = feats["mom_15"]
    mom_30 = feats["mom_30"]
    vol_pct = feats["vol_pct"]

    if strategy_key == "trend":
        score = 20.0
        if fast_ma and slow_ma:
            score += max(0.0, (fast_ma - slow_ma) / slow_ma) * 6000.0
        score += max(0.0, mom_15) * 9000.0
        score += max(0.0, mom_30) * 7000.0
        score += min(vol_pct * 3000.0, 12.0)
        return clamp_score(score)

    if strategy_key == "mean_revert":
        score = 10.0
        if rsi_val is not None:
            score += max(0.0, abs(rsi_val - 50.0) - 8.0) * 2.6
        score += range_center * 35.0
        score -= min(vol_pct * 5000.0, 25.0)
        return clamp_score(score)

    if strategy_key == "breakout":
        score = 15.0
        score += range_edge * 50.0
        score += max(0.0, mom_15) * 12000.0
        score += max(0.0, mom_30) * 9000.0
        score += min(vol_pct * 12000.0, 40.0)
        return clamp_score(score)

    if strategy_key == "scalper":
        score = 15.0
        score += min(vol_pct * 15000.0, 55.0)
        score += max(0.0, 1 - abs(range_pos - 0.5) * 2) * 35.0
        score -= abs(mom_15) * 5000.0
        return clamp_score(score)

    if strategy_key in ("long_hold", "short_hold"):
        return 55.0

    return 50.0


def evaluate_strategies(candles: List[dict]) -> List[dict]:
    aggregated_by_key = {
        cfg["key"]: aggregate_candles_to_timeframe(candles, cfg.get("timeframe", 1)) for cfg in STRATEGY_DEFS
    }
    performance = simulate_strategy_performance(aggregated_by_key)

    def to_win_rate(score: float) -> float:
        return round(clamp_score(45.0 + score * 0.35), 1)

    scores = {}
    for cfg in STRATEGY_DEFS:
        series = aggregated_by_key.get(cfg["key"], [])
        closes = [c["close"] for c in series if c.get("close") is not None]
        feats = compute_features_from_closes(closes)
        scores[cfg["key"]] = compute_score(cfg["key"], feats)

    active_key = max(scores, key=scores.get) if scores else STRATEGY_DEFS[0]["key"]

    results = []
    for cfg in STRATEGY_DEFS:
        perf = performance.get(cfg["key"], {})
        score = scores.get(cfg["key"], 50.0)
        results.append(
            {
                "name": cfg["name"],
                "description": cfg["description"],
                "win_rate": to_win_rate(score),
                "active": cfg["key"] == active_key,
                "confidence": round(score, 2),
                "return_pct": perf.get("return_pct", 0.0),
                "total_pnl": perf.get("total_pnl", 0.0),
                "unrealized_pnl": perf.get("unrealized_pnl", 0.0),
                "trade_count": perf.get("trade_count", 0),
                "open_side": perf.get("open_side"),
                "fees_paid": perf.get("fees_paid", 0.0),
                "timeframe_sec": cfg.get("timeframe", 1),
            }
        )
    return results


def strategy_signal(
    strategy_key: str,
    last_close: float,
    fast_ma: Optional[float],
    slow_ma: Optional[float],
    rsi_val: Optional[float],
    range_pos: float,
    high_50: float,
    low_50: float,
    mom_15: float,
    vol_pct: float,
) -> Optional[str]:
    if strategy_key == "trend":
        if fast_ma and slow_ma:
            if fast_ma > slow_ma * 1.001 and mom_15 >= 0:
                return "long"
            if fast_ma < slow_ma * 0.999 and mom_15 <= 0:
                return "short"
    elif strategy_key == "mean_revert":
        if rsi_val is not None:
            if rsi_val > 65:
                return "short"
            if rsi_val < 35:
                return "long"
    elif strategy_key == "breakout":
        if last_close >= high_50 * 0.999:
            return "long"
        if last_close <= low_50 * 1.001:
            return "short"
        if abs(mom_15) > 0.004 and vol_pct > 0.003:
            return "long" if mom_15 > 0 else "short"
    elif strategy_key == "scalper":
        if vol_pct < 0.0015:
            return None
        if 0.35 <= range_pos <= 0.65:
            if mom_15 > 0:
                return "long"
            if mom_15 < 0:
                return "short"
    elif strategy_key == "long_hold":
        return "long"
    elif strategy_key == "short_hold":
        return "short"
    return None


def simulate_strategy_performance(candles: List[dict]) -> dict:
    """Hypothetical per-strategy PnL using simple signals; runs on recent window."""
def simulate_strategy_performance(candles_by_key: dict) -> dict:
    """Hypothetical per-strategy PnL using simple signals; runs on each strategy's timeframe."""

    def default_perf():
        return {
            "return_pct": 0.0,
            "total_pnl": 0.0,
            "unrealized_pnl": 0.0,
            "trade_count": 0,
            "open_side": None,
            "fees_paid": 0.0,
        }

    perf = {}
    for cfg in STRATEGY_DEFS:
        key = cfg["key"]
        series = candles_by_key.get(key, [])
        closes = [c["close"] for c in series if c.get("close") is not None]
        if len(closes) < 20:
            perf[key] = default_perf()
            continue
        closes = closes[-600:]
        side = None
        entry_price = None
        qty = None
        cumulative_pnl = 0.0
        trade_count = 0
        fees_paid = 0.0

        last_price = closes[-1]
        for idx in range(1, len(closes)):
            window = closes[: idx + 1]
            last_close = window[-1]
            fast_ma = sma(window, 20)
            slow_ma = sma(window, 60)
            rsi_val = compute_rsi(window, 14)
            high_50 = max(window[-50:]) if len(window) >= 50 else max(window)
            low_50 = min(window[-50:]) if len(window) >= 50 else min(window)
            if high_50 == low_50:
                range_pos = 0.5
            else:
                range_pos = (last_close - low_50) / (high_50 - low_50)
            range_pos = max(0.0, min(1.0, range_pos))
            mom_15 = safe_pct_change(last_close, window[-15]) if len(window) > 15 else 0.0
            vol_window = window[-60:] if len(window) >= 2 else window
            vol_pct = statistics.pstdev(vol_window) / last_close if len(vol_window) >= 2 and last_close else 0.0

            signal = strategy_signal(
                key,
                last_close,
                fast_ma,
                slow_ma,
                rsi_val,
                range_pos,
                high_50,
                low_50,
                mom_15,
                vol_pct,
            )

            if side and signal != side:
                if entry_price and qty:
                    if side == "long":
                        pnl = (last_close - entry_price) * qty
                    else:
                        pnl = (entry_price - last_close) * qty
                    close_fee = POSITION_NOTIONAL * FEE_RATE
                    pnl -= close_fee
                    cumulative_pnl += pnl
                    trade_count += 1
                    fees_paid += close_fee
                side = None
                entry_price = None
                qty = None

            if signal and side is None:
                qty = POSITION_NOTIONAL / last_close if last_close else 0.0
                side = signal
                entry_price = last_close
                open_fee = POSITION_NOTIONAL * FEE_RATE
                cumulative_pnl -= open_fee
                fees_paid += open_fee

            last_price = last_close

        unreal = 0.0
        if side and entry_price and qty and last_price:
            if side == "long":
                unreal = (last_price - entry_price) * qty
            else:
                unreal = (entry_price - last_price) * qty
        total_pnl = cumulative_pnl + unreal
        return_pct = (total_pnl / POSITION_NOTIONAL * 100) if POSITION_NOTIONAL else 0.0
        perf[key] = {
            "return_pct": round(return_pct, 2),
            "total_pnl": round(total_pnl, 2),
            "unrealized_pnl": round(unreal, 2),
            "trade_count": trade_count,
            "open_side": side,
            "fees_paid": round(fees_paid, 4),
        }
    return perf


def random_news():
    title, source, sentiment, score = random.choice(NEWS_HEADLINES)
    return {
        "title": title,
        "source": source,
        "sentiment_label": sentiment,
        "score": score,
        "timestamp": datetime.utcnow().isoformat(),
    }


def random_log(active_strategy: Optional[str] = None):
    template = random.choice(LOG_TEMPLATES)
    strategy_name = active_strategy or random.choice(STRATEGY_NAMES)
    message = template.format(
        count=random.randint(10, 150),
        depth=random.choice([10, 20, 50, 100]),
        strategy=strategy_name,
        latency=random.randint(10, 120),
        price=round(current_price, 2),
    )
    return {
        "timestamp": datetime.utcnow().strftime("%H:%M:%S"),
        "message": message,
    }


class DualPositionManager:
    """Run independent 10-minute cycles for long and short."""

    def __init__(self):
        self.last_cycle: Optional[int] = None
        self.last_candle: Optional[dict] = None
        self.modes = {
            "long": {
                "side": "long",
                "entry_price": None,
                "entry_qty": None,
                "entry_time": None,
                "last_pnl": 0.0,
                "prev_cycle_pnl": 0.0,
                "cumulative_pnl": 0.0,
                "trade_count": 0,
            },
            "short": {
                "side": "short",
                "entry_price": None,
                "entry_qty": None,
                "entry_time": None,
                "last_pnl": 0.0,
                "prev_cycle_pnl": 0.0,
                "cumulative_pnl": 0.0,
                "trade_count": 0,
            },
        }
        self.trades = deque(maxlen=100)

    def _record_trade(self, mode: str, action: str, price: float, pnl: float, qty: float):
        ret_pct = (pnl / POSITION_NOTIONAL) * 100 if POSITION_NOTIONAL else 0.0
        self.trades.appendleft(
            {
                "timestamp": datetime.utcnow().strftime("%H:%M:%S"),
                "side": mode,
                "action": action,
                "price": round(price, 2),
                "qty": round(qty, 6),
                "pnl": round(pnl, 2),
                "return_pct": round(ret_pct, 2),
            }
        )

    def _close(self, mode: str, candle: dict):
        state = self.modes[mode]
        if state["entry_price"] is None or state["entry_qty"] is None:
            return
        close_price = candle["close"]
        if mode == "long":
            pnl = (close_price - state["entry_price"]) * state["entry_qty"]
        else:
            pnl = (state["entry_price"] - close_price) * state["entry_qty"]
        state["last_pnl"] = pnl
        state["prev_cycle_pnl"] = pnl
        state["cumulative_pnl"] += pnl
        state["trade_count"] += 1
        self._record_trade(mode, "close", close_price, pnl, state["entry_qty"])
        state["entry_price"] = None
        state["entry_qty"] = None
        state["entry_time"] = None

    def _open(self, mode: str, candle: dict):
        price = candle["close"]
        qty = POSITION_NOTIONAL / price if price else 0.0
        state = self.modes[mode]
        state["entry_price"] = price
        state["entry_qty"] = qty
        state["entry_time"] = candle["time"]
        self._record_trade(mode, "open", price, 0.0, qty)

    def update(self, candle: dict):
        cycle = candle["time"] // CYCLE_SECONDS
        if self.last_cycle is None:
            self.last_cycle = cycle
            for mode in self.modes.keys():
                self._open(mode, candle)
            self.last_candle = candle
            return
        if cycle != self.last_cycle:
            # close using the last candle of the previous cycle
            closing_candle = self.last_candle or candle
            for mode in self.modes.keys():
                self._close(mode, closing_candle)
            self.last_cycle = cycle
            for mode in self.modes.keys():
                self._open(mode, candle)
        # track last seen candle for the next close
        self.last_candle = candle

    def snapshot(self):
        latest_price = self.last_candle["close"] if self.last_candle else None
        strategies = []
        for mode, state in self.modes.items():
            trades = state["trade_count"]
            cum_ret = (state["cumulative_pnl"] / (POSITION_NOTIONAL * trades)) * 100 if trades else 0.0
            last_ret = (state["last_pnl"] / POSITION_NOTIONAL) * 100 if POSITION_NOTIONAL else 0.0
            unreal_pnl = 0.0
            if state["entry_price"] and state["entry_qty"] and latest_price:
                if mode == "long":
                    unreal_pnl = (latest_price - state["entry_price"]) * state["entry_qty"]
                else:
                    unreal_pnl = (state["entry_price"] - latest_price) * state["entry_qty"]
            current_ret = (unreal_pnl / POSITION_NOTIONAL) * 100 if POSITION_NOTIONAL else 0.0
            prev_ret = (state["prev_cycle_pnl"] / POSITION_NOTIONAL) * 100 if POSITION_NOTIONAL else 0.0
            strategies.append(
                {
                    "name": f"{mode.capitalize()} Cycle",
                    "side": mode,
                    "entry_price": round(state["entry_price"], 2) if state["entry_price"] else None,
                    "entry_time": datetime.utcfromtimestamp(state["entry_time"]).isoformat() + "Z" if state["entry_time"] else None,
                    "size": round(state["entry_qty"], 6) if state["entry_qty"] else 0.0,
                    "notional": POSITION_NOTIONAL,
                    "current_return_pct": round(current_ret, 2),
                    "current_pnl": round(unreal_pnl, 2),
                    "last_pnl": round(state["last_pnl"], 2),
                    "last_return_pct": round(last_ret, 2),
                    "prev_cycle_return_pct": round(prev_ret, 2),
                    "prev_cycle_pnl": round(state["prev_cycle_pnl"], 2),
                    "cumulative_pnl": round(state["cumulative_pnl"], 2),
                    "cumulative_return_pct": round(cum_ret, 2),
                    "trade_count": trades,
                }
            )
        return {
            "strategies": strategies,
            "recent_trades": list(self.trades),
        }


position_manager = DualPositionManager()


def fetch_binance_trades(start_time_ms: Optional[int] = None) -> List[dict]:
    """Fetch aggregated trades from Binance. Uses public REST; no auth required."""
    base_url = "https://api.binance.com/api/v3/aggTrades"
    params = {"symbol": BASIS_SYMBOL, "limit": 1000}
    if start_time_ms:
        params["startTime"] = start_time_ms
    url = f"{base_url}?{parse.urlencode(params)}"
    req = request.Request(url, headers={"User-Agent": "ai-trader-hts"})
    with request.urlopen(req, timeout=10) as resp:
        if resp.status != 200:
            raise HTTPException(status_code=502, detail=f"Binance returned {resp.status}")
        data = resp.read()
        trades = json.loads(data.decode("utf-8"))
        return trades


def trades_to_candles(trades: List[dict], bucket_seconds: int = BASE_CANDLE_SECONDS) -> List[dict]:
    """Aggregate trades into N-second candles (default 15s)."""
    bucket_seconds = max(1, bucket_seconds)
    buckets = {}
    for tr in trades:
        ts_ms = tr.get("T")
        if ts_ms is None:
            continue
        sec = ts_ms // 1000
        price = float(tr["p"])
        qty = float(tr["q"])
        bucket_start = (sec // bucket_seconds) * bucket_seconds
        bucket = buckets.get(bucket_start)
        if not bucket:
            buckets[bucket_start] = {
                "time": bucket_start,
                "open": price,
                "high": price,
                "low": price,
                "close": price,
                "volume": qty,
            }
        else:
            bucket["high"] = max(bucket["high"], price)
            bucket["low"] = min(bucket["low"], price)
            bucket["close"] = price
            bucket["volume"] += qty
    return [buckets[k] for k in sorted(buckets.keys())]


def seed_history_from_binance():
    global last_trade_time_ms, use_mock
    try:
        trades = fetch_binance_trades()
        candles = trades_to_candles(trades)
        if not candles:
            return
        latest_trade_ms = max((tr.get("T") for tr in trades if tr.get("T") is not None), default=None)
        for c in candles:
            price_buffer.append(c)
        if latest_trade_ms is not None:
            last_trade_time_ms = max(last_trade_time_ms or 0, int(latest_trade_ms))
        print(f"[engine] Seeded {len(candles)} candles from Binance.")
    except Exception as exc:
        use_mock = True
        print(f"[engine] Binance seed failed, falling back to mock data: {exc}")


def poll_binance_forever():
    global last_trade_time_ms, use_mock
    while True:
        try:
            trades = fetch_binance_trades(last_trade_time_ms + 1 if last_trade_time_ms else None)
            candles = trades_to_candles(trades)
            if candles:
                latest_trade_ms = max((tr.get("T") for tr in trades if tr.get("T") is not None), default=None)
                for c in candles:
                    price_buffer.append(c)
                if latest_trade_ms is not None:
                    last_trade_time_ms = max(last_trade_time_ms or 0, int(latest_trade_ms))
                # Update current_price baseline for mock calculations
                last_close = candles[-1]["close"]
                if last_close:
                    global current_price
                    current_price = last_close
        except urlerror.URLError:
            use_mock = True
        except Exception as exc:
            print(f"[engine] Poll error: {exc}")
        finally:
            time.sleep(BASE_CANDLE_SECONDS)


@app.on_event("startup")
def _startup():
    seed_history_from_binance()
    thread = threading.Thread(target=poll_binance_forever, daemon=True)
    thread.start()


@app.get("/api/status")
def get_status():
    if price_buffer:
        latest = price_buffer[-1]
        candle = {
            "open": round(latest["open"], 2),
            "high": round(latest["high"], 2),
            "low": round(latest["low"], 2),
            "close": round(latest["close"], 2),
            "timestamp": datetime.utcfromtimestamp(latest["time"]).replace(tzinfo=timezone.utc).isoformat(),
            "volume": round(latest.get("volume", 0), 4),
            "source": "binance",
        }
    else:
        candle = random_walk_candle()
        candle["source"] = "mock"
        if price_buffer.maxlen:
            # also push into buffer so history endpoint has something
            sec = int(datetime.fromisoformat(candle["timestamp"]).timestamp())
            price_buffer.append(
                {
                    "time": sec,
                    "open": candle["open"],
                    "high": candle["high"],
                    "low": candle["low"],
                    "close": candle["close"],
                    "volume": candle["volume"],
                }
            )
    candles_snapshot = list(price_buffer)
    position_manager.update(
        {
            "time": int(datetime.fromisoformat(candle["timestamp"]).timestamp()),
            "open": candle["open"],
            "high": candle["high"],
            "low": candle["low"],
            "close": candle["close"],
        }
    )
    strategies = evaluate_strategies(candles_snapshot)
    active_strategy = next((s["name"] for s in strategies if s["active"]), None)
    news = random_news()
    log = random_log(active_strategy)
    return {
        "price": candle,
        "strategies": strategies,
        "news": news,
        "log": log,
        "server_time": time.time(),
        "position_state": position_manager.snapshot(),
    }


@app.get("/api/history")
def get_history(limit: int = 3000):
    if limit <= 0:
        raise HTTPException(status_code=400, detail="limit must be positive")
    candles = list(price_buffer)[-limit:]
    if not candles:
        return {"candles": [], "source": "mock"}
    return {
        "candles": [
            {
                "open": round(c["open"], 2),
                "high": round(c["high"], 2),
                "low": round(c["low"], 2),
                "close": round(c["close"], 2),
                "volume": round(c.get("volume", 0), 4),
                "timestamp": datetime.utcfromtimestamp(c["time"]).replace(tzinfo=timezone.utc).isoformat(),
            }
            for c in candles
        ],
        "source": "binance" if not use_mock else "mock",
    }


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8000, reload=False)
