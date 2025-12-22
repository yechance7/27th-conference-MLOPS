import { useEffect, useMemo, useRef, useState } from 'react';
import { createChart } from 'lightweight-charts';

const TIMEFRAMES = [
  { key: '15s', label: '15s', seconds: 15 },
  { key: '1m', label: '1m', seconds: 60 },
  { key: '5m', label: '5m', seconds: 300 },
  { key: '10m', label: '10m', seconds: 600 },
  { key: '1h', label: '1h', seconds: 3600 },
  { key: '1d', label: '1d', seconds: 86400 },
  { key: '7d', label: '7d', seconds: 604800 },
  { key: '1mo', label: '1mo', seconds: 2592000 },
];

const MA_PERIODS = [5, 30, 120];

function aggregateCandles(candles, timeframeSeconds) {
  if (!candles.length) return [];
  const buckets = new Map();
  candles.forEach(c => {
    const bucket = Math.floor(c.time / timeframeSeconds) * timeframeSeconds;
    const existing = buckets.get(bucket);
    if (!existing) {
      buckets.set(bucket, {
        time: bucket,
        open: c.open,
        high: c.high,
        low: c.low,
        close: c.close,
        volume: c.volume ?? 0,
      });
    } else {
      existing.high = Math.max(existing.high, c.high);
      existing.low = Math.min(existing.low, c.low);
      existing.close = c.close;
      existing.volume += c.volume ?? 0;
    }
  });
  return Array.from(buckets.values()).sort((a, b) => a.time - b.time);
}

function computeSMA(bars, period) {
  if (!bars.length) return [];
  const result = [];
  let sum = 0;
  for (let i = 0; i < bars.length; i++) {
    sum += bars[i].close;
    if (i >= period) {
      sum -= bars[i - period].close;
    }
    if (i >= period - 1) {
      result.push({ time: bars[i].time, value: sum / period });
    }
  }
  return result;
}

function normalizeRange(range) {
  if (!range || range.from === undefined || range.to === undefined) return null;
  let from = Number(range.from);
  let to = Number(range.to);
  if (!Number.isFinite(from) || !Number.isFinite(to)) return null;
  if (from > to) {
    const swap = from;
    from = to;
    to = swap;
  }
  if (to - from < 1) {
    to = from + 1;
  }
  return { from, to };
}

function formatTickLabel(date, timeframeSeconds) {
  const pad = v => String(v).padStart(2, '0');
  const sec = date.getUTCSeconds();
  const min = date.getUTCMinutes();
  const hour = date.getUTCHours();
  const day = date.getUTCDate();
  const month = date.getUTCMonth() + 1;

  if (timeframeSeconds <= 15) {
    if (sec % 15 !== 0) return '';
    return `${pad(hour)}:${pad(min)}:${pad(sec)}`;
  }
  if (timeframeSeconds <= 60) {
    if (sec !== 0) return '';
    return `${pad(hour)}:${pad(min)}:00`;
  }
  if (timeframeSeconds <= 300) {
    if (min % 5 !== 0 || sec !== 0) return '';
    return `${pad(hour)}:${pad(min)}`;
  }
  if (timeframeSeconds <= 600) {
    if (min % 10 !== 0 || sec !== 0) return '';
    return `${pad(hour)}:${pad(min)}`;
  }
  if (timeframeSeconds <= 3600) {
    if (min !== 0 || sec !== 0) return '';
    return `${pad(hour)}:00`;
  }
  if (timeframeSeconds <= 86400) {
    if (hour !== 0 || min !== 0 || sec !== 0) return '';
    return `${month}/${day}`;
  }
  if (timeframeSeconds <= 604800) {
    if (hour !== 0 || min !== 0 || sec !== 0) return '';
    return `${month}/${day}`;
  }
  // monthly and above
  if (day !== 1) return '';
  return `${date.getUTCFullYear()}/${pad(month)}`;
}

export default function ChartSection({ candles, latestPrice }) {
  const priceContainerRef = useRef(null);
  const volumeContainerRef = useRef(null);
  const priceChartRef = useRef(null);
  const volumeChartRef = useRef(null);
  const candleSeriesRef = useRef(null);
  const maSeriesRef = useRef([]);
  const volumeSeriesRef = useRef(null);
  const stickToRightRef = useRef(true);
  const aggregatedRef = useRef([]);
  const syncingRef = useRef(false);
  const [timeframe, setTimeframe] = useState(TIMEFRAMES[0]);

  const aggregated = useMemo(
    () => aggregateCandles([...candles].sort((a, b) => a.time - b.time), timeframe.seconds),
    [candles, timeframe]
  );

  const movingAverages = useMemo(
    () => MA_PERIODS.map(period => ({ period, data: computeSMA(aggregated, period) })),
    [aggregated]
  );

  useEffect(() => {
    if (!priceContainerRef.current || !volumeContainerRef.current) return;

    const commonOptions = {
      layout: { background: { color: '#0f1117' }, textColor: '#e6edf3' },
      grid: { vertLines: { color: '#1b1f2a' }, horzLines: { color: '#1b1f2a' } },
      crosshair: { mode: 0 },
      rightPriceScale: { borderColor: '#30363d' },
      timeScale: { borderColor: '#30363d', timeVisible: true, secondsVisible: true },
    };

    const priceChart = createChart(priceContainerRef.current, {
      ...commonOptions,
      width: priceContainerRef.current.clientWidth,
      height: priceContainerRef.current.clientHeight,
    });

    const volumeChart = createChart(volumeContainerRef.current, {
      ...commonOptions,
      width: volumeContainerRef.current.clientWidth,
      height: volumeContainerRef.current.clientHeight,
    });

    const candleSeries = priceChart.addCandlestickSeries({
      upColor: '#00ff88',
      borderUpColor: '#00ff88',
      wickUpColor: '#00ff88',
      downColor: '#ff0055',
      borderDownColor: '#ff0055',
      wickDownColor: '#ff0055',
    });

    const maSeries = MA_PERIODS.map((period, idx) =>
      priceChart.addLineSeries({
        color: ['#58a6ff', '#f2cc8f', '#b48ead'][idx % 3],
        lineWidth: 2,
        priceLineVisible: false,
        lastValueVisible: false,
        title: `MA ${period}`,
      })
    );

    const volumeSeries = volumeChart.addHistogramSeries({
      color: '#2f81f7',
      priceFormat: { type: 'volume' },
    });

    // Sync ranges between charts
    const clampRange = range => {
      if (!aggregatedRef.current.length) return null;
      const normalized = normalizeRange(range);
      if (!normalized) return null;
      const lastIndex = aggregatedRef.current.length - 1;
      const minAllowed = -10;
      const maxAllowed = lastIndex + 10;
      let { from, to } = normalized;
      if (from < minAllowed) from = minAllowed;
      if (to > maxAllowed) to = maxAllowed;
      if (to <= from) to = from + 1;
      stickToRightRef.current = to >= lastIndex - 1;
      return { from, to };
    };

    const syncRange = (source, target) => range => {
      if (!range) return;
      const clamped = clampRange(range);
      if (!clamped) return;
      if (syncingRef.current) return;
      syncingRef.current = true;
      if (clamped.from !== range.from || clamped.to !== range.to) {
        source.timeScale().setVisibleLogicalRange(clamped);
      }
      target.timeScale().setVisibleLogicalRange(clamped);
      syncingRef.current = false;
    };

    const priceRangeHandler = syncRange(priceChart, volumeChart);
    const volumeRangeHandler = syncRange(volumeChart, priceChart);
    priceChart.timeScale().subscribeVisibleLogicalRangeChange(priceRangeHandler);
    volumeChart.timeScale().subscribeVisibleLogicalRangeChange(volumeRangeHandler);

    const handleResize = () => {
      if (priceContainerRef.current && volumeContainerRef.current) {
        priceChart.applyOptions({
          width: priceContainerRef.current.clientWidth,
          height: priceContainerRef.current.clientHeight,
        });
        volumeChart.applyOptions({
          width: volumeContainerRef.current.clientWidth,
          height: volumeContainerRef.current.clientHeight,
        });
      }
    };

    window.addEventListener('resize', handleResize);

    priceChartRef.current = priceChart;
    volumeChartRef.current = volumeChart;
    candleSeriesRef.current = candleSeries;
    maSeriesRef.current = maSeries;
    volumeSeriesRef.current = volumeSeries;

    return () => {
      window.removeEventListener('resize', handleResize);
      priceChart.timeScale().unsubscribeVisibleLogicalRangeChange(priceRangeHandler);
      volumeChart.timeScale().unsubscribeVisibleLogicalRangeChange(volumeRangeHandler);
      priceChart.remove();
      volumeChart.remove();
    };
  }, []);

  useEffect(() => {
    const priceChart = priceChartRef.current;
    const volumeChart = volumeChartRef.current;
    if (!priceChart || !volumeChart || !candleSeriesRef.current || !volumeSeriesRef.current) return;

    aggregatedRef.current = aggregated;
    candleSeriesRef.current.setData(aggregated);
    volumeSeriesRef.current.setData(
      aggregated.map(bar => ({
        time: bar.time,
        value: bar.volume ?? 0,
        color: bar.close >= bar.open ? '#00ff88' : '#ff0055',
      }))
    );

    maSeriesRef.current.forEach((series, idx) => {
      const data = movingAverages[idx]?.data ?? [];
      series.setData(data);
    });

    const timeScale = priceChart.timeScale();
    const range = timeScale.getVisibleLogicalRange();
    const lastIndex = aggregated.length - 1;
    const shouldStick = stickToRightRef.current || !range || range.to >= lastIndex - 1;
    if (shouldStick) {
      stickToRightRef.current = true;
      timeScale.scrollToRealTime();
      volumeChart.timeScale().scrollToRealTime();
    }
  }, [aggregated, movingAverages]);

  useEffect(() => {
    const priceChart = priceChartRef.current;
    const volumeChart = volumeChartRef.current;
    if (!priceChart || !volumeChart) return;
    const formatter = time => {
      const date = new Date((time ?? 0) * 1000);
      return formatTickLabel(date, timeframe.seconds);
    };
    priceChart.applyOptions({
      timeScale: {
        borderColor: '#30363d',
        tickMarkFormatter: formatter,
      },
    });
    volumeChart.applyOptions({
      timeScale: {
        borderColor: '#30363d',
        tickMarkFormatter: formatter,
      },
    });
  }, [timeframe]);

  return (
    <section className="chart-area">
      <div className="chart-header">
        <div>
          <h2>
            BTC/USDT <span>Binance Futures ({timeframe.label})</span>
          </h2>
          <div className="chart-price">{latestPrice ? `$${latestPrice.toLocaleString()}` : '—'}</div>
        </div>
        <div className="timeframe-group">
          {TIMEFRAMES.map(tf => (
            <button
              key={tf.key}
              className={`timeframe-btn ${tf.key === timeframe.key ? 'active' : ''}`}
              onClick={() => setTimeframe(tf)}
            >
              {tf.label}
            </button>
          ))}
        </div>
      </div>
      <div className="chart-shell">
        <div className="chart-stack">
          <div ref={priceContainerRef} className="chart-surface price-pane" />
          <div ref={volumeContainerRef} className="chart-surface volume-pane" />
        </div>
        {aggregated.length === 0 && <div className="chart-placeholder">Waiting for data...</div>}
      </div>
    </section>
  );
}
