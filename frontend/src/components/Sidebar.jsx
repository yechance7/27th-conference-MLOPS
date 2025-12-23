export default function Sidebar({ connected, latency, logs }) {
  return (
    <aside className="sidebar">
      <div className="app-title">
        <span>⚡ AI TRADER</span>
      </div>

      <div className="server-status">
        <span>Python Engine</span>
        <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
          <span style={{ color: connected ? '#00ff88' : '#ff7788' }}>
            {connected ? 'Connected' : 'Offline'}
          </span>
          <span className={`status-dot ${connected ? '' : 'offline'}`} />
        </div>
      </div>
      <div className="server-status">
        <span>Latency</span>
        <span>{latency !== null ? `${latency} ms` : '…'}</span>
      </div>

      <div className="log-section">
        <div className="section-header">1. System Logs</div>
        <div className="log-list">
          {logs.map(entry => {
            const timeStr = new Date(entry.timestamp).toLocaleTimeString();
            return (
              <div className="log-item" key={entry.key || `${entry.timestamp}-${entry.message}`}>
                <span className="log-time">{timeStr}</span>
                <span>{entry.message}</span>
              </div>
            );
          })}
          {logs.length === 0 && <div className="log-item">No logs yet.</div>}
        </div>
      </div>
    </aside>
  );
}
