export default function NewsPanel({ newsItems }) {
  return (
    <section
      className="news-panel"
      style={{ maxHeight: '100%', overflowY: 'auto' }}
    >
      <div className="section-header" style={{ marginBottom: 10 }}>
        Live News
      </div>
      {newsItems.length === 0 && (
        <div className="news-item" style={{ borderLeftColor: '#555' }}>
          <div className="news-title">Waiting for headlines…</div>
          <div className="news-meta">
            <span>Source: -</span>
          </div>
        </div>
      )}
      {newsItems.map(item => {
        const published = item.published_at || item.timestamp || '-';
        const source = item.source || (item.link ? new URL(item.link).hostname : 'supabase');

        return (
          <div
            className="news-item"
            key={`${item.title}-${item.timestamp || item.published_at || item.link || Math.random()}`}
            style={{ borderLeftColor: '#555' }}
          >
            <div className="news-title">{item.title}</div>
            <div className="news-meta">
              <span>{published}</span>
              {item.link && (
                <a href={item.link} target="_blank" rel="noreferrer" style={{ marginLeft: 8 }}>
                  {source || 'Link'}
                </a>
              )}
            </div>
          </div>
        );
      })}
    </section>
  );
}
