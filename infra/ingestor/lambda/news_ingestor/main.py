import json
import os
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List
from xml.etree import ElementTree as ET

import boto3
import urllib3


s3 = boto3.client("s3")
http = urllib3.PoolManager()

BUCKET = os.environ.get("LANDING_BUCKET_NAME") or os.environ.get("BUCKET_NAME")
NEWS_SOURCE = (os.environ.get("NEWS_SOURCE") or os.environ.get("NEWS_DATA_SOURCE") or "RSS").upper()
CRYPTOPANIC_API_KEY = os.environ.get("CRYPTOPANIC_API_KEY", "")


def lambda_handler(event: Dict[str, Any], _context: Any) -> Dict[str, Any]:
    """Entry point invoked by EventBridge to collect news and upload to S3."""
    if not BUCKET:
        raise RuntimeError("Landing bucket name missing (LANDING_BUCKET_NAME or BUCKET_NAME).")

    now = datetime.now(timezone.utc)
    items = fetch_cryptopanic() if NEWS_SOURCE == "CRYPTOPANIC" else fetch_rss_feeds()

    if not items:
        print(f"No items collected for source {NEWS_SOURCE}.")
        return {"status": "skipped", "count": 0}

    key = build_s3_key(now, NEWS_SOURCE)
    payload = {
        "collection_timestamp": now.isoformat(),
        "source_type": NEWS_SOURCE,
        "item_count": len(items),
        "data": items,
        "event": event,
    }

    s3.put_object(
        Bucket=BUCKET,
        Key=key,
        Body=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        ContentType="application/json",
    )

    print(f"Uploaded {len(items)} items to s3://{BUCKET}/{key}")
    return {"status": "success", "bucket": BUCKET, "key": key}


def build_s3_key(timestamp: datetime, source: str) -> str:
    """Build S3 key: Ext/{source}/YYYY/MM/DD/news-<ts>-<uuid>.json"""
    run_suffix = f"{timestamp.strftime('%H%M%S')}-{str(uuid.uuid4())[:8]}"
    return timestamp.strftime(f"Ext/{source}/%Y/%m/%d/news-{run_suffix}.json")


def fetch_cryptopanic() -> List[Dict[str, Any]]:
    """CryptoPanic API (hot BTC news)."""
    if not CRYPTOPANIC_API_KEY:
        print("CRYPTOPANIC_API_KEY missing; skipping CryptoPanic fetch.")
        return []

    url = (
        "https://cryptopanic.com/api/v1/posts/"
        f"?auth_token={CRYPTOPANIC_API_KEY}&currencies=BTC&kind=news&filter=hot"
    )

    try:
        response = http.request("GET", url, timeout=urllib3.Timeout(connect=3.0, read=5.0))
    except Exception as exc:  # noqa: BLE001
        print(f"CryptoPanic request failed: {exc}")
        return []

    if response.status != 200:
        print(f"CryptoPanic HTTP {response.status}: {response.data}")
        return []

    try:
        data = json.loads(response.data.decode("utf-8"))
    except Exception as exc:  # noqa: BLE001
        print(f"Failed to decode CryptoPanic payload: {exc}")
        return []

    results: List[Dict[str, Any]] = []
    for item in data.get("results", []):
        results.append(
            {
                "id": item.get("id"),
                "title": item.get("title"),
                "url": item.get("url"),
                "published_at": item.get("published_at"),
                "source": item.get("source", {}).get("title"),
                "currency": "BTC",
            }
        )
    return results


def fetch_rss_feeds() -> List[Dict[str, Any]]:
    """Parse public RSS feeds without extra dependencies."""
    rss_urls = [
        "https://cointelegraph.com/rss/tag/bitcoin",
        "https://www.coindesk.com/arc/outboundfeeds/rss/?outputType=xml",
    ]

    collected: List[Dict[str, Any]] = []
    for url in rss_urls:
        try:
            response = http.request("GET", url, timeout=urllib3.Timeout(connect=3.0, read=5.0))
        except Exception as exc:  # noqa: BLE001
            print(f"RSS fetch failed for {url}: {exc}")
            continue

        if response.status != 200:
            print(f"RSS HTTP {response.status} for {url}")
            continue

        try:
            root = ET.fromstring(response.data)
        except Exception as exc:  # noqa: BLE001
            print(f"RSS parse error for {url}: {exc}")
            continue

        channel = root.find("channel")
        if channel is None:
            print(f"No channel element found in RSS for {url}")
            continue

        for item in channel.findall("item")[:5]:
            title = (item.findtext("title") or "").strip()
            link = (item.findtext("link") or "").strip()
            pub_date = (item.findtext("pubDate") or "").strip()

            lower_title = title.lower()
            if "bitcoin" not in lower_title and "btc" not in lower_title:
                continue

            collected.append(
                {
                    "title": title or "No Title",
                    "url": link,
                    "published_at": pub_date,
                    "source_rss": url,
                }
            )

    return collected
