## Database Bootstrap Scripts

Use these helpers when you need to install TimescaleDB/pgvector on the private EC2 host and backfill data without direct internet access.

### 1. Prepare a bootstrap bundle

1. Download the required RPM packages (done on your laptop or a networked machine):
   - TimescaleDB for PostgreSQL 15 (e.g., `timescaledb-2.13.1-postgresql-15-0.el8.x86_64.rpm`)
   - pgvector for PostgreSQL 15 (e.g., `pgvector_15-0.5.1-1.el8.x86_64.rpm`)
   - **Shortcut:** pass `--rpm-preset al2023-pg15` to let the helper download the two RPMs that work on Amazon Linux 2023/PostgreSQL 15 automatically.\
     If you already have custom RPMs, keep using `--rpm-paths` instead.
2. Run the helper to assemble scripts + Python wheels + RPMs and push to S3:
   ```bash
   cd infra/database/scripts
   python publish_bootstrap_bundle.py \
     --bundle-dir build/db-bootstrap \
     --rpm-preset al2023-pg15 \
     --output-zip s3://<landing-bucket>/bootstrap/db_bootstrap.zip
   ```
   This command zips the install scripts (`setup_timescale.sh`, `backfill_s3_ticks.py`, migrations, Python deps) and includes the RPMs you specify. The resulting archive is uploaded to your S3 bucket so that the private EC2 instance (which already has an S3 Gateway VPC Endpoint) can download it without public internet.

### 2. On the EC2 host

1. Start an SSM Session (the instance has AmazonSSMManagedInstanceCore attached).
2. Download and extract the bundle:
   ```bash
   aws s3 cp s3://<landing-bucket>/bootstrap/db_bootstrap.zip /tmp/
   cd /home/ec2-user
   unzip /tmp/db_bootstrap.zip
   ```
3. Install TimescaleDB + pgvector using the provided RPMs, initialize PostgreSQL, run migrations, and perform the one-time backfill:
   ```bash
   cd db-bootstrap/scripts
   sudo chmod +x setup_timescale.sh
   sudo ./setup_timescale.sh market mlops <secure-password>
   sudo -u postgres psql -d market -f ../sql/001_init.sql
   python backfill_s3_ticks.py --s3-bucket ... --s3-prefix ... --table market_data.btc_ticks \
       --host 127.0.0.1 --port 5432 --dbname market --user mlops --password <secure-password>
   ```

After the backfill is complete, schedule your Airflow/SSM jobs to process new 5-minute slices as they appear in S3.
