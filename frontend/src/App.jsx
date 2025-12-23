import { useEffect, useMemo, useRef, useState, useCallback } from 'react';
import Sidebar from './components/Sidebar';
import ChartSection from './components/ChartSection';
import NewsPanel from './components/NewsPanel';
import InferencePanel from './components/InferencePanel';
import useSupabaseSession from './hooks/useSupabaseSession';
import { supabase } from './supabaseClient';

const RAW_API_BASE = import.meta.env.VITE_BACKEND_URL || 'http://127.0.0.1:8000';
const API_BASE = RAW_API_BASE.replace(/\/+$/, ''); // normalize to avoid double slashes
const INFER_API_BASE =
  import.meta.env.VITE_INFER_URL ||
  API_BASE.replace(/:8000$/, ':9000').replace(/\/+$/, '');
const BINANCE_WS = 'wss://stream.binance.com:9443/ws/btcusdt@aggTrade';
const BASE_CANDLE_SECONDS = 15;
const MAX_CANDLES = 5000;
const MAX_LOGS = 200;
const MAX_NEWS = 40;
const MAX_TRADES = 20;
const LEVERAGE_KEY = 'ai-trader-leverage';

function mapPriceToCandle(pricePayload) {
  if (!pricePayload) return null;
  return {
    time: Math.floor(new Date(pricePayload.timestamp).getTime() / 1000),
    open: Number(pricePayload.open),
    high: Number(pricePayload.high),
    low: Number(pricePayload.low),
    close: Number(pricePayload.close),
    volume: Number(pricePayload.volume ?? 0)
  };
}

export default function App() {
  const [candles, setCandles] = useState([]);
  const [rawStrategies, setRawStrategies] = useState([]);
  const [newsItems, setNewsItems] = useState([]);
  const [logs, setLogs] = useState([]);
  const [connected, setConnected] = useState(false);
  const [latency, setLatency] = useState(null);
  const [lastPrice, setLastPrice] = useState(null);
  const [inferResult, setInferResult] = useState(null);
  const [inferLoading, setInferLoading] = useState(false);
  const [inferError, setInferError] = useState('');
  const [predAnchor, setPredAnchor] = useState(null); // {price, ts}
  const [actualReturn, setActualReturn] = useState(null);
  const [sessionInfo, setSessionInfo] = useState(null);
  const [eventSource, setEventSource] = useState(null);
  const binanceSocketRef = useRef(null);
  const binanceBucketRef = useRef(null);
  const inferLockRef = useRef(false);
  const [leverage, setLeverage] = useState(1);
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [authError, setAuthError] = useState('');
  const [authLoading, setAuthLoading] = useState(false);
  const { token } = useSupabaseSession();

  const addLog = useCallback(
    message => {
      const ts = new Date().toISOString();
      const key = `${ts}-${Math.random().toString(16).slice(2)}`;
      setLogs(prev => [{ timestamp: ts, message, key }, ...prev].slice(0, MAX_LOGS));
    },
    []
  );

  const mapPredToStrategies = useCallback(pred => {
    if (!pred) return [];
    const items = [
      { key: 'trend_return_pct', name: 'Trend', description: 'Trend following' },
      { key: 'mean_revert_return_pct', name: 'Mean Revert', description: 'Mean reversion' },
      { key: 'breakout_return_pct', name: 'Breakout', description: 'Breakout' },
      { key: 'scalper_return_pct', name: 'Scalper', description: 'Scalping' },
      { key: 'long_hold_return_pct', name: 'Long Hold', description: 'Long hold' },
      { key: 'short_hold_return_pct', name: 'Short Hold', description: 'Short hold' },
    ];
    return items.map(item => ({
      name: item.name,
      description: item.description,
      return_pct: Number(pred[item.key]),
      total_pnl: Number(pred[item.key]),
      fees_paid: 0,
      confidence: 60,
      active: true,
      timeframe_sec: 600,
      open_side: pred[item.key] >= 0 ? 'long' : 'short',
      trade_count: null,
    }));
  }, []);

  const fetchNews = useCallback(
    async () => {
      if (!token) return;
      try {
        const params = new URLSearchParams({ limit: String(MAX_NEWS) });
        const res = await fetch(`${API_BASE}/news?${params.toString()}`, {
          headers: { Authorization: `Bearer ${token}` },
        });
        if (!res.ok) throw new Error('news failed');
        const data = await res.json();
        const items = Array.isArray(data.items) ? data.items : [];
        setNewsItems(items.slice(0, MAX_NEWS));
      } catch (err) {
        addLog(`News fetch failed: ${err.message || err}`);
      }
    },
    [API_BASE, token, addLog]
  );

  const runInference = useCallback(
    async () => {
      if (inferLockRef.current) return;
      inferLockRef.current = true;
      setInferError('');
      setInferLoading(true);
      addLog('Inference started');
      try {
        const res = await fetch(`${INFER_API_BASE}/predict`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({}),
        });
        if (!res.ok) throw new Error(`infer failed ${res.status}`);
        const data = await res.json();
        setInferResult(data);
        setRawStrategies(mapPredToStrategies(data.pred));
        if (lastPrice) {
          setPredAnchor({ price: lastPrice, ts: Date.now() });
          addLog(`Inference anchor price: ${lastPrice}`);
        } else {
          setPredAnchor(null);
        }
        addLog('Inference finished');
      } catch (err) {
        setInferError(err.message || 'Inference failed');
        addLog(`Inference failed: ${err.message || err}`);
      } finally {
        setInferLoading(false);
        inferLockRef.current = false;
      }
    },
    [INFER_API_BASE, mapPredToStrategies, addLog, lastPrice]
  );

  useEffect(() => {
    const stored = localStorage.getItem(LEVERAGE_KEY);
    if (stored) {
      const parsed = Number(stored);
      if (parsed && parsed > 0) {
        setLeverage(parsed);
      }
    }
  }, []);

  useEffect(() => {
    if (predAnchor && lastPrice) {
      const pct = ((lastPrice - predAnchor.price) / predAnchor.price) * 100;
      setActualReturn(pct);
    }
  }, [lastPrice, predAnchor]);

  useEffect(() => {
    // backend health check every 30s
    const check = async () => {
      try {
        const res = await fetch(`${API_BASE}/healthz`);
        if (res.ok) addLog('Backend health ok');
        else addLog(`Backend health failed ${res.status}`);
      } catch (e) {
        addLog(`Backend health error: ${e.message || e}`);
      }
    };
    check();
    const id = setInterval(check, 30000);
    return () => clearInterval(id);
  }, [API_BASE, addLog]);

  const applyLeverage = (strategies, lev) =>
    strategies.map(st => ({
      ...st,
      return_pct: Number.isFinite(st.return_pct) ? st.return_pct * lev : st.return_pct,
      total_pnl: Number.isFinite(st.total_pnl) ? st.total_pnl * lev : st.total_pnl,
      fees_paid: Number.isFinite(st.fees_paid) ? st.fees_paid * lev : st.fees_paid,
      leverage: lev,
    }));

  useEffect(() => {
    let isMounted = true;
    let sse;
    let binanceWs;

    const mapBackendCandle = item => {
      if (!item?.ts) return null;
      return {
        time: Math.floor(new Date(item.ts).getTime() / 1000),
        open: Number(item.open),
        high: Number(item.high),
        low: Number(item.low),
        close: Number(item.close),
        volume: Number(item.volume ?? 0),
      };
    };

    const startSession = async () => {
      if (!token) return;
      const startedAt = performance.now();
      try {
        const res = await fetch(`${API_BASE}/session/start`, {
          method: 'POST',
          headers: {
            Authorization: `Bearer ${token}`,
            'Content-Type': 'application/json',
          },
          body: JSON.stringify({}),
        });
        if (!res.ok) throw new Error('session start failed');
        const payload = await res.json();
        if (!isMounted) return;
        setSessionInfo(payload);
        setConnected(true);
        setLatency(Math.round(performance.now() - startedAt));
        addLog('Session started');

        // Bootstrap candles
        const allCandles = [];
        let cursor = null;
        do {
          const url = `${API_BASE}/bootstrap?session_id=${payload.session_id}&limit=${MAX_CANDLES}${
            cursor ? `&cursor=${cursor}` : ''
          }`;
          const pageRes = await fetch(url, {
            headers: { Authorization: `Bearer ${token}` },
          });
          if (!pageRes.ok) throw new Error('bootstrap failed');
          const page = await pageRes.json();
          const mapped = (page.items || []).map(mapBackendCandle).filter(Boolean);
          allCandles.push(...mapped);
          cursor = page.next_cursor;
          // Stop if already have enough
          if (allCandles.length >= MAX_CANDLES) break;
        } while (cursor);

        const sorted = allCandles
          .filter(Boolean)
          .sort((a, b) => a.time - b.time)
          .slice(-MAX_CANDLES);
        setCandles(sorted);
        if (sorted.length) setLastPrice(sorted[sorted.length - 1].close);

        // Start SSE gap stream (session id is enough; backend trusts session)
        const streamPath = payload.stream_url || `/stream/gap?session_id=${payload.session_id}`;
        const streamUrl = streamPath.startsWith('http')
          ? streamPath
          : `${API_BASE}${streamPath}`;
        sse = new EventSource(streamUrl);
        sse.onmessage = ev => {
          try {
            const data = JSON.parse(ev.data);
            const c = mapBackendCandle(data);
            if (!c) return;
            setCandles(prev => {
              const next = [...prev, c].sort((a, b) => a.time - b.time).slice(-MAX_CANDLES);
              setLastPrice(next[next.length - 1]?.close ?? null);
              return next;
            });
      } catch (e) {
        // ignore parse errors
      }
    };
    sse.onerror = () => {
      setConnected(false);
    };
        setEventSource(sse);

        // Start Binance live stream (front-end direct)
        try {
          binanceWs = new WebSocket(BINANCE_WS);
          binanceSocketRef.current = binanceWs;
          binanceWs.onmessage = ev => {
            try {
              const msg = JSON.parse(ev.data);
              const tradeTimeMs = msg.T || msg.E;
              const price = parseFloat(msg.p);
              const qty = parseFloat(msg.q);
              if (!tradeTimeMs || !price || !qty) return;
              const bucketStart = Math.floor(tradeTimeMs / 1000 / BASE_CANDLE_SECONDS) * BASE_CANDLE_SECONDS;
              const existing = binanceBucketRef.current;
              if (!existing || existing.time !== bucketStart) {
                if (existing) {
                  // flush previous bucket
                  setCandles(prev => {
                    const next = [...prev, existing].sort((a, b) => a.time - b.time).slice(-MAX_CANDLES);
                    setLastPrice(next[next.length - 1]?.close ?? null);
                    return next;
                  });
                }
                binanceBucketRef.current = {
                  time: bucketStart,
                  open: price,
                  high: price,
                  low: price,
                  close: price,
                  volume: qty,
                };
              } else {
                existing.high = Math.max(existing.high, price);
                existing.low = Math.min(existing.low, price);
                existing.close = price;
                existing.volume += qty;
                binanceBucketRef.current = existing;
              }
              // push current bucket to chart so live updates show immediately
              const currentBucket = binanceBucketRef.current;
              if (currentBucket) {
                setCandles(prev => {
                  const next = [...prev];
                  const idx = next.findIndex(c => c.time === currentBucket.time);
                  if (idx >= 0) {
                    next[idx] = { ...currentBucket };
                  } else {
                    next.push({ ...currentBucket });
                  }
                  next.sort((a, b) => a.time - b.time);
                  return next.slice(-MAX_CANDLES);
                });
                setLastPrice(currentBucket.close);
              }
            } catch {
              /* ignore */
            }
          };
          binanceWs.onerror = () => {
            // allow reconnect on refresh; no action here
          };
        } catch {
          // ignore ws init errors
        }

        // Fetch news once at start
        fetchNews();
      } catch (err) {
        if (!isMounted) return;
        setConnected(false);
      }
    };

    startSession();

    return () => {
      isMounted = false;
      if (sse) sse.close();
      if (eventSource) eventSource.close();
      if (binanceWs) binanceWs.close();
      binanceSocketRef.current = null;
      binanceBucketRef.current = null;
    };
  }, [token]);

  useEffect(() => {
    localStorage.setItem(LEVERAGE_KEY, String(leverage));
  }, [leverage]);

  const strategies = useMemo(() => applyLeverage(rawStrategies, leverage), [rawStrategies, leverage]);

  if (!token) {
    const handleLogin = async e => {
      e.preventDefault();
      setAuthError('');
      setAuthLoading(true);
      const { error } = await supabase.auth.signInWithPassword({ email, password });
      if (error) setAuthError(error.message);
      setAuthLoading(false);
    };

    const handleGithubLogin = async () => {
      setAuthError('');
      setAuthLoading(true);
      const { error } = await supabase.auth.signInWithOAuth({
        provider: 'github',
        options: {
          redirectTo: window.location.href,
        },
      });
      if (error) {
        setAuthError(error.message);
        setAuthLoading(false);
      }
    };

    return (
      <main className="main-container">
        <div style={{ padding: '2rem', color: '#e6edf3', maxWidth: 360 }}>
          <h3 style={{ marginBottom: '1rem' }}>Supabase Login</h3>
          <form onSubmit={handleLogin} style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
            <input
              type="email"
              placeholder="email"
              value={email}
              onChange={e => setEmail(e.target.value)}
              required
            />
            <input
              type="password"
              placeholder="password"
              value={password}
              onChange={e => setPassword(e.target.value)}
              required
            />
            <button type="submit" disabled={authLoading}>
              {authLoading ? 'Signing in…' : 'Sign in'}
            </button>
          </form>
          <button
            type="button"
            onClick={handleGithubLogin}
            disabled={authLoading}
            style={{ marginTop: 10 }}
          >
            {authLoading ? 'Signing in…' : 'Sign in with GitHub'}
          </button>
          {authError && <div style={{ marginTop: 10, color: '#ff7788' }}>{authError}</div>}
          <div style={{ marginTop: 10, fontSize: 12, color: '#9ea7b3' }}>
            Use your Supabase email/password account or GitHub OAuth.
          </div>
        </div>
      </main>
    );
  }

  return (
    <>
      <Sidebar connected={connected} latency={latency} logs={logs} />
      <main className="main-container">
        <ChartSection candles={candles} latestPrice={lastPrice} />
        <NewsPanel newsItems={newsItems} />
        <InferencePanel
          result={inferResult}
          loading={inferLoading}
          error={inferError}
          onInfer={runInference}
          actualReturn={actualReturn}
          anchorTs={predAnchor?.ts}
        />
      </main>
    </>
  );
}
