import boto3
import logging
import pandas as pd
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import List, Dict, Any

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
logger = logging.getLogger(__name__)

s3 = boto3.client("s3")

BUCKET = "ybigta-mlops-landing-zone-324037321745"

RAW_PREFIX_BASE = "Binance/BTCUSDT/"
PROCESSED_PREFIX_BASE = "processed-Binance/BTCUSDT/"

TMP_DIR = Path("/tmp")


def handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """Main Lambda handler with error handling and statistics tracking."""
    now = datetime.now(timezone.utc)
    target_minutes = get_last_n_minutes(now, n=5)

    logger.info(f"Starting processing for {len(target_minutes)} time windows")
    logger.info(f"Target minutes: {[dt.strftime('%Y-%m-%d %H:%M') for dt in target_minutes]}")

    stats = {
        "total_windows": len(target_minutes),
        "processed_windows": 0,
        "skipped_windows": 0,
        "failed_windows": 0,
        "total_files_processed": 0,
        "total_files_failed": 0,
        "total_records_before": 0,
        "total_records_after": 0,
        "errors": []
    }

    for dt in target_minutes:
        raw_prefix = build_time_prefix(RAW_PREFIX_BASE, dt)
        processed_prefix = build_time_prefix(PROCESSED_PREFIX_BASE, dt)
        time_str = dt.strftime("%Y-%m-%d %H:%M")

        try:
            # List parquet files
            parquet_keys = list_parquet_files(BUCKET, raw_prefix)
            if not parquet_keys:
                logger.warning(f"No parquet files found under {raw_prefix}")
                stats["skipped_windows"] += 1
                continue

            logger.info(f"[{time_str}] Found {len(parquet_keys)} files under {raw_prefix}")

            # Load and combine all parquet files
            dfs = []
            failed_files = []
            for key in parquet_keys:
                try:
                    df = load_parquet_from_s3(BUCKET, key)
                    dfs.append(df)
                    stats["total_files_processed"] += 1
                    logger.debug(f"Successfully loaded {key}: {len(df)} records")
                except Exception as exc:
                    error_msg = f"Failed to load {key}: {str(exc)}"
                    logger.error(error_msg, exc_info=True)
                    failed_files.append(key)
                    stats["total_files_failed"] += 1
                    stats["errors"].append({"file": key, "error": str(exc), "time": time_str})

            if not dfs:
                logger.warning(f"[{time_str}] No files successfully loaded (all {len(parquet_keys)} failed)")
                stats["failed_windows"] += 1
                continue

            # Combine all dataframes
            try:
                df_all = pd.concat(dfs, ignore_index=True)
                stats["total_records_before"] += len(df_all)
                logger.info(f"[{time_str}] Combined {len(dfs)} files into {len(df_all)} total records")
            except Exception as exc:
                error_msg = f"[{time_str}] Failed to concatenate dataframes: {str(exc)}"
                logger.error(error_msg, exc_info=True)
                stats["failed_windows"] += 1
                stats["errors"].append({"operation": "concat", "error": str(exc), "time": time_str})
                continue

            # Clean trades
            try:
                df_clean = clean_trades(df_all)
                if df_clean.empty:
                    logger.warning(f"[{time_str}] Empty after cleaning, skipping upload")
                    stats["skipped_windows"] += 1
                    continue

                stats["total_records_after"] += len(df_clean)
                records_dropped = len(df_all) - len(df_clean)
                logger.info(
                    f"[{time_str}] Cleaned data: {len(df_clean)} records "
                    f"({records_dropped} dropped, {len(df_clean)/len(df_all)*100:.1f}% retained)"
                )
            except Exception as exc:
                error_msg = f"[{time_str}] Failed to clean trades: {str(exc)}"
                logger.error(error_msg, exc_info=True)
                stats["failed_windows"] += 1
                stats["errors"].append({"operation": "clean", "error": str(exc), "time": time_str})
                continue

            # Upload cleaned data
            output_key = f"{processed_prefix}batch-cleaned.parquet"
            try:
                upload_parquet_to_s3(df_clean, BUCKET, output_key)
                logger.info(f"[{time_str}] Successfully uploaded cleaned data to s3://{BUCKET}/{output_key}")
                stats["processed_windows"] += 1
            except Exception as exc:
                error_msg = f"[{time_str}] Failed to upload to {output_key}: {str(exc)}"
                logger.error(error_msg, exc_info=True)
                stats["failed_windows"] += 1
                stats["errors"].append({"operation": "upload", "file": output_key, "error": str(exc), "time": time_str})

        except Exception as exc:
            error_msg = f"[{time_str}] Unexpected error processing time window: {str(exc)}"
            logger.error(error_msg, exc_info=True)
            stats["failed_windows"] += 1
            stats["errors"].append({"operation": "general", "error": str(exc), "time": time_str})

    # Log summary
    logger.info("=" * 60)
    logger.info("Processing Summary:")
    logger.info(f"  Total time windows: {stats['total_windows']}")
    logger.info(f"  Successfully processed: {stats['processed_windows']}")
    logger.info(f"  Skipped (no files/empty): {stats['skipped_windows']}")
    logger.info(f"  Failed: {stats['failed_windows']}")
    logger.info(f"  Files processed: {stats['total_files_processed']}")
    logger.info(f"  Files failed: {stats['total_files_failed']}")
    logger.info(f"  Total records before cleaning: {stats['total_records_before']}")
    logger.info(f"  Total records after cleaning: {stats['total_records_after']}")
    if stats["errors"]:
        logger.warning(f"  Total errors: {len(stats['errors'])}")
        for err in stats["errors"][:5]:  # Log first 5 errors
            logger.warning(f"    - {err}")
    logger.info("=" * 60)

    return {
        "statusCode": 200 if stats["failed_windows"] == 0 else 207,  # 207 = Multi-Status
        "stats": stats
    }


# ---------- helpers ----------

def get_last_n_minutes(now: datetime, n: int) -> List[datetime]:
    return [
        (now - timedelta(minutes=i + 1)).replace(second=0, microsecond=0)
        for i in range(n)
    ]


def build_time_prefix(base: str, dt: datetime) -> str:
    return (
        f"{base}"
        f"{dt.year:04d}/{dt.month:02d}/{dt.day:02d}/"
        f"{dt.hour:02d}/{dt.minute:02d}/"
    )


def list_parquet_files(bucket: str, prefix: str) -> List[str]:
    """List all parquet files under the given prefix."""
    keys = []
    try:
        paginator = s3.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
            for obj in page.get("Contents", []):
                key = obj["Key"]
                if key.endswith(".parquet"):
                    keys.append(key)
    except Exception as exc:
        logger.error(f"Failed to list files in s3://{bucket}/{prefix}: {str(exc)}", exc_info=True)
        raise
    return keys


def load_parquet_from_s3(bucket: str, key: str) -> pd.DataFrame:
    """Download and load a parquet file from S3."""
    local_path = TMP_DIR / f"input_{key.replace('/', '_')}.parquet"
    try:
        logger.debug(f"Downloading s3://{bucket}/{key}")
        s3.download_file(bucket, key, str(local_path))
        if not local_path.exists():
            raise FileNotFoundError(f"Downloaded file not found at {local_path}")
        df = pd.read_parquet(local_path)
        logger.debug(f"Loaded {len(df)} records from {key}")
        return df
    except Exception as exc:
        logger.error(f"Failed to load parquet from s3://{bucket}/{key}: {str(exc)}", exc_info=True)
        # Clean up on error
        if local_path.exists():
            local_path.unlink()
        raise
    finally:
        # Clean up temp file
        if local_path.exists():
            try:
                local_path.unlink()
            except Exception:
                pass


def upload_parquet_to_s3(df: pd.DataFrame, bucket: str, key: str):
    """Upload a DataFrame as parquet to S3."""
    local_path = TMP_DIR / f"output_{key.replace('/', '_')}.parquet"
    try:
        logger.debug(f"Writing {len(df)} records to local parquet file")
        df.to_parquet(local_path, index=False)
        if not local_path.exists():
            raise FileNotFoundError(f"Parquet file not created at {local_path}")
        
        file_size = local_path.stat().st_size
        logger.debug(f"Uploading {file_size} bytes to s3://{bucket}/{key}")
        s3.upload_file(str(local_path), bucket, key)
        logger.debug(f"Successfully uploaded to s3://{bucket}/{key}")
    except Exception as exc:
        logger.error(f"Failed to upload parquet to s3://{bucket}/{key}: {str(exc)}", exc_info=True)
        raise
    finally:
        # Clean up temp file
        if local_path.exists():
            try:
                local_path.unlink()
            except Exception:
                pass


def clean_trades(df: pd.DataFrame) -> pd.DataFrame:
    """Clean and validate trade data."""
    if df.empty:
        logger.warning("Input DataFrame is empty")
        return df

    initial_count = len(df)
    df = df.copy()

    try:
        # Type conversion with error handling
        if "price" not in df.columns or "quantity" not in df.columns:
            raise ValueError("Missing required columns: 'price' or 'quantity'")
        
        df["price"] = pd.to_numeric(df["price"], errors="coerce")
        df["quantity"] = pd.to_numeric(df["quantity"], errors="coerce")

        # Filter invalid values
        before_filter = len(df)
        df = df[df["price"] > 0]
        df = df[df["quantity"] > 0]
        invalid_count = before_filter - len(df)
        if invalid_count > 0:
            logger.warning(f"Filtered out {invalid_count} records with invalid price/quantity")

        # Remove duplicates
        before_dedup = len(df)
        df = df.drop_duplicates(subset=["trade_id"])
        dup_count = before_dedup - len(df)
        if dup_count > 0:
            logger.info(f"Removed {dup_count} duplicate records based on trade_id")

        # Convert timestamps
        if "trade_time" in df.columns:
            df["trade_time"] = pd.to_datetime(df["trade_time"], utc=True, errors="coerce")
        if "event_time" in df.columns:
            df["event_time"] = pd.to_datetime(df["event_time"], utc=True, errors="coerce")

        # Remove rows with invalid timestamps
        if "trade_time" in df.columns:
            before_time_filter = len(df)
            df = df[df["trade_time"].notna()]
            invalid_time_count = before_time_filter - len(df)
            if invalid_time_count > 0:
                logger.warning(f"Filtered out {invalid_time_count} records with invalid trade_time")

        # Sort by trade time
        if "trade_time" in df.columns and not df.empty:
            df = df.sort_values("trade_time")

        final_count = len(df)
        logger.debug(
            f"Cleaning complete: {initial_count} -> {final_count} records "
            f"({initial_count - final_count} removed)"
        )

    except Exception as exc:
        logger.error(f"Error during data cleaning: {str(exc)}", exc_info=True)
        raise

    return df
