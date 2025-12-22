export default function NewsPanel({ newsItems }) {
  return (
    <section className="news-panel">
      <div className="section-header" style={{ marginBottom: 10 }}>
        Live News (Sentiment Analysis)
      </div>
      {newsItems.length === 0 && (
        <div className="news-item" style={{ borderLeftColor: '#555' }}>
          <div className="news-title">Waiting for headlines…</div>
          <div className="news-meta">
            <span>Source: -</span>
            <span className="news-sentiment neutral">Neutral</span>
          </div>
        </div>
      )}
      {newsItems.map(item => {
        const sentimentLabel = item.sentiment_label || item.sentiment || 'neutral';
        const sentimentClass =
          sentimentLabel.toLowerCase() === 'negative'
            ? 'negative'
            : sentimentLabel.toLowerCase() === 'neutral'
            ? 'neutral'
            : '';
        const scoreText =
          typeof item.score === 'number' ? ` (${item.score.toFixed(2)})` : '';

        return (
          <div
            className="news-item"
            key={`${item.title}-${item.timestamp}`}
            style={{ borderLeftColor: sentimentClass === 'negative' ? '#ff0055' : sentimentClass === 'neutral' ? '#555' : 'var(--accent-green)' }}
          >
            <div className="news-title">{item.title}</div>
            <div className="news-meta">
              <span>Source: {item.source}</span>
              <span className={`news-sentiment ${sentimentClass}`}>
                {sentimentLabel.charAt(0).toUpperCase() + sentimentLabel.slice(1)}
                {scoreText}
              </span>
            </div>
          </div>
        );
      })}
    </section>
  );
}
