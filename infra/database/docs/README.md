# Database Layer Overview

This folder documents the PostgreSQL + TimescaleDB + `pgvector` deployment that stores raw Bitcoin trade ticks (as-is, bucketed by 5‑minute slices) and downstream embeddings.

## Data Flow
1. **Raw ingestion**  
   - Exchange feeds (websocket dumps or historical CSV replays) land in S3 as Parquet batches containing the fields  
     `(event_time, trade_time, symbol, trade_id, price, quantity, buyer_order_id, seller_order_id, is_market_maker)`.  
   - An ETL job inside the VPC fetches slices via the S3 gateway endpoint, derives a 5분 버킷(`bucket_5m = time_bucket('5 minutes', event_time)`), and writes the rows directly into Timescale using `COPY` or batched inserts.

2. **Temporal layout**  
   - The canonical primary key for “unstructured” batches (raw S3 objects, derived blobs) is a 5‑minute bucket. Every file, parquet partition, or vector snapshot is tagged with `bucket_5m = time_bucket('5 minutes', ts)` so downstream ETL can reason about consistent windows.  
   - Within each 5‑minute slice, the system still ingests the native 100 ms ticks for research-grade resolution. Continuous aggregates and feature jobs read the 100 ms hypertables but always attach the parent 5‑minute key to keep joins cheap.
3. **Hypertable storage (`market_data.btc_ticks`)**  
   - The hypertable uses the 5분 버킷(`bucket_5m`) as its time column and the primary key `(bucket_5m, symbol, trade_id)` to guarantee deduplication.  
   - Native 틱 해상도(수 ms)를 그대로 저장하되, 조인/슬라이스는 항상 5분 키를 기준으로 수행합니다. 필요하다면 Timescale continuous aggregates로 1 m/30 s 등의 뷰를 추가하면 됩니다.
4. **Landing/metadata registry (`landing.raw_blobs_5m`)**  
   - Each 5‑minute slice of “unstructured” data (raw parquet, JSON, zipped blobs) is written to the landing-zone bucket and registered in `landing.raw_blobs_5m`.  
   - This registry becomes the primary key for Airflow, Glue, or Spark jobs that need to fan out across slices without scanning the raw S3 prefixes.

5. **Feature / embedding generation (`signals.btc_embeddings`)**  
   - A feature pipeline (PyTorch/NumPy job) reads recent candles, computes engineered features or embeddings (dimension configurable), and stores them in a vector column.  
   - `pgvector` indexes (`ivfflat`) enable fast nearest-neighbour searches for similar micro-structures.

6. **Ops metadata**  
   - `ingestion.stream_offsets` tracks per-source offsets so ETL jobs resume exactly.  
   - `ops.retention_jobs` records compression/retention policies applied via Timescale background workers.

## Storage Characteristics
- All hypertables are chunked daily, with retention policies (e.g., drop raw ticks after 90 days, keep aggregates forever).
- WAL archiving to S3 ensures point-in-time recovery; the EC2 instance requires IAM permissions limited to the backup bucket.
- Database/ETL EC2 instances run in a private subnet with no public IP. They reach S3 through a **Gateway VPC Endpoint** (`com.amazonaws.<region>.s3`) and talk to EC2 collector nodes over VPC-internal security groups, so there is no internet egress path from the database tier. Add Interface VPC Endpoints for CloudWatch Logs/SSM if those services are required.

## Bootstrap Procedure
1. **Provision EC2**  
   Run `terraform -chdir=infra/database/terraform apply -var-file=terraform.tfvars` to launch the private `t3.medium` host, the S3 Gateway VPC Endpoint, and the IAM role with read access to the landing bucket.
2. **Install TimescaleDB**  
   SSH/SSM into the host and execute:
   ```bash
   cd ~/repo/infra/database/scripts
   sudo chmod +x setup_timescale.sh
   sudo ./setup_timescale.sh market mlops <secure-password>
   ```
   This installs PostgreSQL 15 + Timescale, enables the extensions, and creates the initial database/user.
3. **Initial backfill from S3**  
   Install Python deps (`pip install -r infra/database/scripts/requirements.txt`) and run:
   ```bash
   python infra/database/scripts/backfill_s3_ticks.py \
     --s3-bucket ybigta-mlops-landing-zone-324037321745 \
     --s3-prefix Binance/BTCUSDT/ \
     --table market_data.btc_ticks \
     --dsn postgresql://mlops:<password>@<db-host>:5432/market
   ```
   This script streams existing Parquet batches from S3, 계산한 `bucket_5m` 값을 함께 저장하며 Timescale에 `COPY` 합니다. Run it once to seed historical data, then schedule an Airflow/Lambda job that processes 새로운 5분 버킷만 골라 같은 경로로 적재하도록 구성하세요 (`landing.raw_blobs_5m` 테이블을 참조하거나 S3 이벤트를 사용).

## Next Steps
- Add Terraform modules (VPC endpoint, security groups, EC2 bootstrap) to this directory.
- Define SQL migration files (e.g., `migrations/001_init.sql`) that create the schemas/tables described in `SCHEMA.md`.

Use this README as the high-level reference for anyone extending the database layer.
