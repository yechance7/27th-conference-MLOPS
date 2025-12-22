import { useEffect, useMemo, useState } from 'react';
import Sidebar from './components/Sidebar';
import ChartSection from './components/ChartSection';
import NewsPanel from './components/NewsPanel';
import StrategyPanel from './components/StrategyPanel';

const API_BASE = 'http://127.0.0.1:8000/api';
const STATUS_URL = `${API_BASE}/status`;
const HISTORY_URL = `${API_BASE}/history`;
const MAX_CANDLES = 5000;
const MAX_LOGS = 200;
const MAX_NEWS = 6;
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
  const [leverage, setLeverage] = useState(1);

  useEffect(() => {
    const stored = localStorage.getItem(LEVERAGE_KEY);
    if (stored) {
      const parsed = Number(stored);
      if (parsed && parsed > 0) {
        setLeverage(parsed);
      }
    }
  }, []);

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
    let intervalId = null;

    const fetchHistory = async () => {
      try {
        const res = await fetch(`${HISTORY_URL}?limit=${MAX_CANDLES}`);
        if (!res.ok) throw new Error('history failed');
        const data = await res.json();
        if (!isMounted) return;
        if (Array.isArray(data.candles)) {
          const mapped = data.candles
            .map(mapPriceToCandle)
            .filter(Boolean)
            .sort((a, b) => a.time - b.time)
            .slice(-MAX_CANDLES);
          setCandles(mapped);
          if (mapped.length) setLastPrice(mapped[mapped.length - 1].close);
        }
      } catch {
        // ignore; will rely on live stream
      }
    };

    const fetchStatus = async () => {
      const startedAt = performance.now();
      try {
        const response = await fetch(STATUS_URL);
        if (!response.ok) {
          throw new Error('Failed to fetch status');
        }
        const payload = await response.json();
        if (!isMounted) return;

        setConnected(true);
        setLatency(Math.round(performance.now() - startedAt));

        if (payload?.price) {
          const candle = mapPriceToCandle(payload.price);
          if (candle) {
            setLastPrice(candle.close);
            setCandles(prev => {
              const existingIndex = prev.findIndex(item => item.time === candle.time);
              if (existingIndex !== -1) {
                const copy = [...prev];
                copy[existingIndex] = candle;
                return copy;
              }
              const next = [...prev, candle];
              return next.slice(-MAX_CANDLES);
            });
          }
        }

        if (payload?.strategies) {
          setRawStrategies(payload.strategies);
        }

        if (payload?.news) {
          setNewsItems(prev => {
            const next = [payload.news, ...prev];
            return next.slice(0, MAX_NEWS);
          });
        }

        if (payload?.log) {
          setLogs(prev => {
            const exists = prev.some(
              item => item.timestamp === payload.log.timestamp && item.message === payload.log.message
            );
            if (exists) return prev;
            const next = [payload.log, ...prev];
            return next.slice(0, MAX_LOGS);
          });
        }

      } catch (error) {
        if (!isMounted) return;
        setConnected(false);
      }
    };

    (async () => {
      await fetchHistory();
      await fetchStatus();
      intervalId = setInterval(fetchStatus, 500);
    })();

    return () => {
      isMounted = false;
      if (intervalId) clearInterval(intervalId);
    };
  }, []);

  useEffect(() => {
    localStorage.setItem(LEVERAGE_KEY, String(leverage));
  }, [leverage]);

  const strategies = useMemo(
    () => applyLeverage(rawStrategies, leverage),
    [rawStrategies, leverage]
  );

  return (
    <>
      <Sidebar connected={connected} latency={latency} logs={logs} />
      <main className="main-container">
        <ChartSection candles={candles} latestPrice={lastPrice} />
        <NewsPanel newsItems={newsItems} />
        <StrategyPanel
          strategies={strategies}
          leverage={leverage}
          onChangeLeverage={setLeverage}
        />
      </main>
    </>
  );
}
