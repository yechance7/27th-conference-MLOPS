#!/usr/bin/env python3
"""
One-time bootstrap script: read historical Parquet batches from S3, derive 5-minute buckets,
and COPY into TimescaleDB.

Usage (requires explicit password, otherwise the script aborts):

  python backfill_s3_ticks.py \\
      --s3-bucket ybigta-mlops-landing-zone-324037321745 \\
      --s3-prefix Binance/BTCUSDT/ \\
      --table market_data.btc_ticks \\
      --host <db-host-or-ip> \\
      --port 5432 \\
      --dbname market \\
      --user mlops \\
      --password <your-password>
"""
import argparse
import io
import logging
import sys
from typing import Iterator

import boto3
import pandas as pd
import psycopg2

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
)
LOGGER = logging.getLogger("backfill")


def iter_parquet_keys(bucket: str, prefix: str) -> Iterator[str]:
    s3 = boto3.client("s3")
    paginator = s3.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        for obj in page.get("Contents", []):
            key = obj["Key"]
            if key.endswith(".parquet"):
                yield key


def prepare_dataframe(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    if "vent_time" in df.columns and "event_time" not in df.columns:
        df = df.rename(columns={"vent_time": "event_time"})
    df["event_time"] = pd.to_datetime(df["event_time"], utc=True)
    df["trade_time"] = pd.to_datetime(df["trade_time"], utc=True)
    df["bucket_5m"] = df["event_time"].dt.floor("5min")
    ordered = df[
        [
            "bucket_5m",
            "event_time",
            "trade_time",
            "symbol",
            "trade_id",
            "price",
            "quantity",
            "buyer_order_id",
            "seller_order_id",
            "is_market_maker",
        ]
    ].copy()
    for col in ["bucket_5m", "event_time", "trade_time"]:
        ordered[col] = ordered[col].dt.strftime("%Y-%m-%dT%H:%M:%S.%f%z")
    return ordered


def copy_dataframe(conn, table: str, df: pd.DataFrame) -> None:
    buffer = io.StringIO()
    df.to_csv(buffer, index=False, header=False)
    buffer.seek(0)
    with conn.cursor() as cur:
        cur.copy_expert(f"COPY {table} FROM STDIN WITH (FORMAT csv)", buffer)
    conn.commit()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--s3-bucket", required=True)
    parser.add_argument("--s3-prefix", required=True)
    parser.add_argument("--table", required=True)
    parser.add_argument("--host", required=True)
    parser.add_argument("--port", type=int, default=5432)
    parser.add_argument("--dbname", required=True)
    parser.add_argument("--user", required=True)
    parser.add_argument("--password", required=True, help="DB password (script exits if omitted)")
    parser.add_argument("--limit", type=int, default=0, help="optional max number of objects")
    args = parser.parse_args()

if not args.password:
    LOGGER.error("DB password is required; aborting for safety.")
    sys.exit(1)

print("WARNING: This script will load historical data from S3 into TimescaleDB.")
print("It is intended to run once. Proceeding may overwrite/duplicate data.")
confirmation = input("Type 'I UNDERSTAND' to continue: ").strip()
if confirmation != "I UNDERSTAND":
    LOGGER.error("Confirmation phrase not provided; aborting without changes.")
    sys.exit(1)

    s3 = boto3.client("s3")
    conn = psycopg2.connect(
        host=args.host,
        port=args.port,
        dbname=args.dbname,
        user=args.user,
        password=args.password,
        sslmode="prefer",
    )

    processed = 0
    for key in iter_parquet_keys(args.s3_bucket, args.s3_prefix):
        LOGGER.info("Processing %s", key)
        obj = s3.get_object(Bucket=args.s3_bucket, Key=key)
        body = io.BytesIO(obj["Body"].read())
        df = pd.read_parquet(body)
        if df.empty:
            LOGGER.info("Skipping empty file %s", key)
            continue
        prepared = prepare_dataframe(df)
        copy_dataframe(conn, args.table, prepared)
        processed += 1
        if args.limit and processed >= args.limit:
            break

    conn.close()
    LOGGER.info("Completed backfill of %s Parquet files", processed)


if __name__ == "__main__":
    main()
