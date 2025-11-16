# Database Schema — Bitcoin Time-Series + Vector Metadata

These notes sketch how to organize the PostgreSQL + TimescaleDB + `pgvector` schema that will be deployed inside `infra/database/`.

## 1. Core Hypertables

### `landing.raw_blobs_5m`
Stores references to unstructured payloads (Parquet batches, JSON blobs, etc.) that land in S3. The 5‑minute bucket is the primary key for orchestration.

| Column | Type | Notes |
| --- | --- | --- |
| `bucket_5m` | `TIMESTAMPTZ PRIMARY KEY` | `time_bucket('5 minutes', min_tick_ts)`; canonical slice id. |
| `asset` | `TEXT NOT NULL` | e.g., `BTCUSDT`. |
| `s3_uri` | `TEXT NOT NULL` | Pointer to the raw object (landing zone). |
| `object_size_bytes` | `BIGINT` | Size hint for downstream jobs. |
| `ingested_tick_span` | `TSRANGE` | Range of tick timestamps covered by the blob. |
| `metadata` | `JSONB` | Hashes, schema version, producer info. |
| `created_at` | `TIMESTAMPTZ DEFAULT now()` | Registry timestamp. |

- Use a regular table (not hypertable) because there is at most one row per 5‑minute bucket per asset.  
- Unique index `(asset, bucket_5m)` ensures deterministic joins with hypertables.

### `market_data.btc_ticks`
| Column | Type | Notes |
| --- | --- | --- |
| `bucket_5m` | `TIMESTAMPTZ NOT NULL` | Floor of `event_time` to 5‑minute boundary. Acts as the hypertable time column. |
| `event_time` | `TIMESTAMPTZ NOT NULL` | Exchange-reported event time. |
| `trade_time` | `TIMESTAMPTZ` | Matching engine completion time (optional). |
| `symbol` | `TEXT NOT NULL` | e.g., `BTCUSDT`. |
| `trade_id` | `BIGINT NOT NULL` | Exchange sequence. |
| `price` | `NUMERIC(18,8)` | Executed price. |
| `quantity` | `NUMERIC(24,12)` | Filled amount. |
| `buyer_order_id` | `TEXT` | Raw buyer id; nullable. |
| `seller_order_id` | `TEXT` | Raw seller id; nullable. |
| `is_market_maker` | `BOOLEAN` | `true` if maker sells aggressor. |
| `ingested_at` | `TIMESTAMPTZ DEFAULT now()` | Server-side ingest time. |

- Primary key: `(bucket_5m, symbol, trade_id)` ensures deduplication while keeping the bucket as the clustering column.
- Hypertable: `SELECT create_hypertable('market_data.btc_ticks', 'bucket_5m', chunk_time_interval => INTERVAL '7 days');`
- Indexes: `(symbol, event_time DESC)` for time-series queries inside a symbol, `(bucket_5m)` for incremental sweeps.
- Compression: enable Timescale columnar compression on chunks older than 30 days (segment by `symbol`, order by `event_time DESC`).

*Note*: We intentionally store the raw 1–10 ms granularity; analysts can derive 5 m/1 m/100 ms aggregates via SQL/continuous aggregates if required, but the canonical key remains the 5‑minute bucket.

## 2. Vector Store Tables

### `signals.btc_embeddings`
Stores per-bucket feature vectors for downstream ML/retrieval.

| Column | Type | Notes |
| --- | --- | --- |
| `bucket` | `TIMESTAMPTZ PRIMARY KEY` | Matches `btc_ohlcv_100ms.bucket`. |
| `exchange` | `TEXT NOT NULL` | Source venue. |
| `feature_vector` | `vector(256)` | Embedding produced by feature pipeline (dimension configurable). |
| `label_snapshot` | `JSONB` | Optional labels (future returns, vol). |
| `created_at` | `TIMESTAMPTZ DEFAULT now()` | Insertion timestamp. |

- Index: `CREATE INDEX ON signals.btc_embeddings USING ivfflat (feature_vector) WITH (lists = 200);`
- Use `ANALYZE` after bulk loads to optimize vector search.

## 3. Metadata Tables

### `ingestion.stream_offsets`
Tracks last processed offset per data source.

| Column | Type | Notes |
| --- | --- | --- |
| `source` | `TEXT PRIMARY KEY` | e.g., `binance_ws`, `historical_csv`. |
| `last_sequence` | `BIGINT` | Highest processed sequence. |
| `last_ts` | `TIMESTAMPTZ` | Timestamp of the sequence. |
| `state` | `JSONB` | Arbitrary decoder state. |
| `updated_at` | `TIMESTAMPTZ DEFAULT now()` | Maintenance timestamp. |

### `ops.retention_jobs`
Defines compression/backfill policies.

| Column | Type | Notes |
| --- | --- | --- |
| `job_name` | `TEXT PRIMARY KEY` | Human-readable id. |
| `hypertable` | `TEXT NOT NULL` | Target hypertable name. |
| `policy` | `JSONB NOT NULL` | e.g., `{ "compress_after": "7 days", "drop_after": "90 days" }`. |
| `enabled` | `BOOLEAN DEFAULT TRUE` | Toggle. |
| `created_at` | `TIMESTAMPTZ DEFAULT now()` | Metadata. |

## 4. Schema Relationships
- `landing.raw_blobs_5m.bucket_5m` is the parent key for every downstream artifact. Ingestion jobs insert or upsert one row per 5‑minute slice and record the S3 object that contains all raw/“semi-structured” payloads for that window.
- `market_data.btc_ticks.bucket_5m` is derived from `event_time` and matches the parent landing registry, enabling fast joins between raw files and structured ticks.
- Downstream aggregates (1 m, 5 m, user-defined) can be built as Timescale continuous aggregates sourcing from `market_data.btc_ticks`; there is no fixed 100 ms aggregate baked into the schema.
- `signals.btc_embeddings.bucket` should reference whichever aggregate bucket the embedding job cares about (commonly `5 minutes`); declare a `FOREIGN KEY` to that aggregate view/table for referential integrity.
- `ingestion.stream_offsets` ties ingestion workers to specific sequences, enabling idempotent replays.

## 5. Extension Requirements
```sql
CREATE EXTENSION IF NOT EXISTS timescaledb;
CREATE EXTENSION IF NOT EXISTS timescaledb_toolkit;
CREATE EXTENSION IF NOT EXISTS vector;
```

## 6. Storage Notes
- Set `timescaledb.compress` on `btc_ticks` and `btc_ohlcv_100ms` with segment-by `exchange`.
- WAL archiving should cover all schemas; consider logical replication slots if downstream analytics need live data.

These definitions can be codified as migration files (Sqitch/Flyway) under `infra/database/migrations/` once the base schema is finalized.
