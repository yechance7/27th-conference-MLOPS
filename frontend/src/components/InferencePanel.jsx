import React from 'react';

const fallbackStrategies = [
  { key: 'trend_return_pct', label: 'Trend' },
  { key: 'mean_revert_return_pct', label: 'Mean Revert' },
  { key: 'breakout_return_pct', label: 'Breakout' },
  { key: 'scalper_return_pct', label: 'Scalper' },
  { key: 'long_hold_return_pct', label: 'Long Hold' },
  { key: 'short_hold_return_pct', label: 'Short Hold' },
];

export default function InferencePanel({ result, loading, error, onInfer, actualReturn, anchorTs }) {
  const pred = result?.pred || {};
  const actual = Number.isFinite(actualReturn) ? actualReturn : null;
  return (
    <section className="inference-panel">
      <div className="section-header">
        <span>Model Inference</span>
        <div className="infer-actions">
          <button onClick={onInfer} disabled={loading}>
            {loading ? 'Running…' : 'Infer'}
          </button>
        </div>
      </div>
      {error && <div className="infer-error">{error}</div>}
      {actual !== null && (
        <div className="infer-meta">
          <div>Actual return since inference: <span className={actual >= 0 ? 'pos' : 'neg'}>{actual.toFixed(3)}%</span></div>
          {anchorTs && <div>Updated: {new Date(anchorTs).toLocaleTimeString()}</div>}
        </div>
      )}
      {result ? (
        <div className="infer-result">
          <div className="infer-grid">
            {fallbackStrategies.map(s => (
              <div className="infer-card" key={s.key}>
                <div className="infer-label">{s.label}</div>
                <div className={`infer-value ${Number(pred[s.key]) >= 0 ? 'pos' : 'neg'}`}>
                  {Number.isFinite(Number(pred[s.key])) ? `${Number(pred[s.key]).toFixed(3)}` : '—'}
                </div>
                {actual !== null && (
                  <div className="infer-actual">
                    <span className="infer-label">Δ vs actual</span>
                    <span className={`infer-value ${actual - Number(pred[s.key]) >= 0 ? 'pos' : 'neg'}`}>
                      {Number.isFinite(Number(pred[s.key])) ? (actual - Number(pred[s.key])).toFixed(3) : '—'}
                    </span>
                  </div>
                )}
              </div>
            ))}
          </div>
        </div>
      ) : (
        <div className="infer-placeholder">No inference yet. Click the button to run.</div>
      )}
    </section>
  );
}
