-- Schema initialization for landing + market_data namespaces.
CREATE SCHEMA IF NOT EXISTS landing;
CREATE SCHEMA IF NOT EXISTS market_data;

CREATE TABLE IF NOT EXISTS landing.raw_blobs_5m (
    bucket_5m TIMESTAMPTZ PRIMARY KEY,
    asset TEXT NOT NULL,
    s3_uri TEXT NOT NULL,
    object_size_bytes BIGINT,
    ingested_tick_span TSRANGE,
    metadata JSONB,
    created_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS market_data.btc_ticks (
    bucket_5m TIMESTAMPTZ NOT NULL,
    event_time TIMESTAMPTZ NOT NULL,
    trade_time TIMESTAMPTZ,
    symbol TEXT NOT NULL,
    trade_id BIGINT NOT NULL,
    price NUMERIC(18, 8) NOT NULL,
    quantity NUMERIC(24, 12) NOT NULL,
    buyer_order_id TEXT,
    seller_order_id TEXT,
    is_market_maker BOOLEAN NOT NULL DEFAULT false,
    ingested_at TIMESTAMPTZ DEFAULT now(),
    PRIMARY KEY (bucket_5m, symbol, trade_id)
);

SELECT create_hypertable('market_data.btc_ticks', 'bucket_5m', if_not_exists => TRUE);

CREATE INDEX IF NOT EXISTS idx_btc_ticks_symbol_event_time ON market_data.btc_ticks (symbol, event_time DESC);
