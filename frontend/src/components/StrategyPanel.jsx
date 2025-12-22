export default function StrategyPanel({ strategies, leverage, onChangeLeverage }) {
  const fmtPct = value =>
    typeof value === 'number' && !Number.isNaN(value) ? `${value.toFixed(2)}%` : '—';
  const fmtPnL = value =>
    typeof value === 'number' && !Number.isNaN(value) ? `${value >= 0 ? '+' : ''}${value.toFixed(2)}` : '—';
  const fmtSide = side => side ?? 'flat';
  const valueClass = value =>
    typeof value === 'number' && !Number.isNaN(value) ? (value >= 0 ? 'pos' : 'neg') : '';
  const fmtTf = sec => {
    if (!sec || Number.isNaN(sec)) return '—';
    if (sec < 60) return `${sec}s`;
    if (sec % 3600 === 0) return `${sec / 3600}h`;
    if (sec % 60 === 0) return `${sec / 60}m`;
    return `${sec}s`;
  };

  return (
    <section className="strategy-panel">
      <div className="section-header">
        <span>Strategy Selector & Hypothetical PnL</span>
        <div className="lev-control">
          <label htmlFor="leverage">Leverage</label>
          <select
            id="leverage"
            value={leverage}
            onChange={e => onChangeLeverage?.(Number(e.target.value))}
          >
            {[1, 2, 3, 4, 10].map(val => (
              <option key={val} value={val}>
                {val}x
              </option>
            ))}
          </select>
        </div>
      </div>

      <div className="ai-dashboard">
        {strategies.map(strategy => (
          <div
            className={`strategy-card ${strategy.active ? 'active' : ''}`}
            key={strategy.name}
          >
            <div className="st-name">{strategy.name}</div>
            <div className="st-desc">{strategy.description}</div>
            <div className="confidence-bar">
              <div
                className="confidence-fill"
                style={{ width: `${Math.round(strategy.confidence)}%` }}
              />
            </div>
            <div
              className="strategy-confidence"
              style={{ color: strategy.active ? 'var(--accent-green)' : 'var(--text-sub)' }}
            >
              Confidence: {Math.round(strategy.confidence)}%
            </div>
            <div className="strategy-metrics">
              <div className="metric-row">
                <span className="metric-label">TF</span>
                <span className="metric-value">{fmtTf(strategy.timeframe_sec)}</span>
              </div>
              <div className="metric-row">
                <span className="metric-label">Return</span>
                <span className={`metric-value ${valueClass(strategy.return_pct)}`}>
                  {fmtPct(strategy.return_pct)}
                </span>
              </div>
              <div className="metric-row">
                <span className="metric-label">PnL</span>
                <span className={`metric-value ${valueClass(strategy.total_pnl)}`}>
                  {fmtPnL(strategy.total_pnl)}
                </span>
              </div>
              <div className="metric-row">
                <span className="metric-label">Trades</span>
                <span className="metric-value">{strategy.trade_count ?? '—'}</span>
              </div>
              <div className="metric-row">
                <span className="metric-label">Fees</span>
                <span className="metric-value">{fmtPnL(strategy.fees_paid ?? 0)}</span>
              </div>
              <div className="metric-row">
                <span className="metric-label">Position</span>
                <span className="metric-value">{fmtSide(strategy.open_side)}</span>
              </div>
            </div>
          </div>
        ))}
        {strategies.length === 0 && (
          <div className="strategy-card" style={{ opacity: 0.5 }}>
            <div className="st-name">Loading…</div>
            <div className="st-desc">Waiting for strategy signals.</div>
            <div className="confidence-bar">
              <div className="confidence-fill" style={{ width: '0%' }} />
            </div>
          </div>
        )}
      </div>
    </section>
  );
}
