import json
import os
import uuid
from datetime import datetime, timezone
from html.parser import HTMLParser
from typing import Any, Dict, List
from urllib.parse import unquote_plus

import boto3
import urllib3


s3 = boto3.client("s3")
http = urllib3.PoolManager()

BUCKET = os.environ["LANDING_BUCKET_NAME"]
DEST_PREFIX = os.environ.get("DEST_PREFIX", "ExtContent")
SOURCE_PREFIX = os.environ.get("SOURCE_PREFIX", "Ext/RSS/")
MAX_ARTICLE_BYTES = int(os.environ.get("NEWS_MAX_ARTICLE_BYTES", "524288"))
MAX_ARTICLE_CHARS = int(os.environ.get("NEWS_MAX_ARTICLE_CHARS", "4000"))


def lambda_handler(event: Dict[str, Any], _context: Any) -> Dict[str, Any]:
    """Triggered by S3 put of RSS/CryptoPanic payloads; fetch article text and write enriched JSON."""
    processed = 0
    outputs = []

    for record in event.get("Records", []):
        bucket = record.get("s3", {}).get("bucket", {}).get("name")
        key = unquote_plus(record.get("s3", {}).get("object", {}).get("key", ""))
        if not bucket or not key or not key.startswith(SOURCE_PREFIX):
            continue
        payload = read_payload(bucket, key)
        if not payload:
            continue

        source_type = payload.get("source_type", "RSS")
        items = payload.get("data", [])
        enriched = enrich_items(items)

        now = datetime.now(timezone.utc)
        out_key = build_dest_key(key, source_type, now)
        out_body = {
            "collection_timestamp": now.isoformat(),
            "source_type": source_type,
            "item_count": len(enriched),
            "data": enriched,
            "origin_key": key,
        }
        s3.put_object(
            Bucket=BUCKET,
            Key=out_key,
            Body=json.dumps(out_body, ensure_ascii=False).encode("utf-8"),
            ContentType="application/json",
        )
        processed += 1
        outputs.append(out_key)

    return {"status": "ok", "processed": processed, "output_keys": outputs}


def read_payload(bucket: str, key: str) -> Dict[str, Any]:
    try:
        obj = s3.get_object(Bucket=bucket, Key=key)
        body = obj["Body"].read()
        return json.loads(body)
    except Exception as exc:  # noqa: BLE001
        print(f"Failed to read {bucket}/{key}: {exc}")
        return {}


def enrich_items(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    enriched: List[Dict[str, Any]] = []
    for item in items:
        url = item.get("url") or ""
        doc = dict(item)
        if url:
            content = fetch_article_content(url)
            if content:
                doc["content"] = content
        enriched.append(doc)
    return enriched


def build_dest_key(src_key: str, source_type: str, timestamp: datetime) -> str:
    # Mirror date path; if not present, fallback to current date
    date_part = extract_date_path(src_key) or timestamp.strftime("%Y/%m/%d")
    suffix = f"{timestamp.strftime('%H%M%S')}-{uuid.uuid4().hex[:8]}"
    return f"{DEST_PREFIX}/{source_type}/{date_part}/content-{suffix}.json"


def extract_date_path(key: str) -> str:
    parts = key.split("/")
    # Expecting Ext/{source}/YYYY/MM/DD/...
    if len(parts) >= 5 and parts[-4].isdigit() and parts[-3].isdigit() and parts[-2].isdigit():
        return "/".join(parts[-4:-1])
    if len(parts) >= 6 and parts[-5].isdigit() and parts[-4].isdigit() and parts[-3].isdigit():
        return "/".join(parts[-5:-2])
    return ""


class _HTMLTextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self._texts: List[str] = []
        self._skip = False

    def handle_starttag(self, tag: str, attrs: List[Any]) -> None:
        if tag in {"script", "style"}:
            self._skip = True

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style"}:
            self._skip = False

    def handle_data(self, data: str) -> None:
        if self._skip:
            return
        text = data.strip()
        if text:
            self._texts.append(text)

    def get_text(self) -> str:
        return " ".join(self._texts)


def fetch_article_content(url: str) -> str:
    try:
        resp = http.request(
            "GET",
            url,
            preload_content=False,
            timeout=urllib3.Timeout(connect=3.0, read=10.0),
            headers={"User-Agent": "news-content-fetcher/1.0"},
        )
    except Exception as exc:  # noqa: BLE001
        print(f"Content fetch failed for {url}: {exc}")
        return ""

    try:
        if resp.status != 200:
            print(f"Content fetch HTTP {resp.status} for {url}")
            return ""
        content_type = resp.headers.get("Content-Type", "")
        if "text/html" not in content_type:
            return ""
        raw = resp.read(MAX_ARTICLE_BYTES)
        html = raw.decode("utf-8", errors="ignore")
    finally:
        resp.release_conn()

    parser = _HTMLTextExtractor()
    try:
        parser.feed(html)
    except Exception as exc:  # noqa: BLE001
        print(f"HTML parse failed for {url}: {exc}")
        return ""

    text = parser.get_text()
    if len(text) > MAX_ARTICLE_CHARS:
        return text[:MAX_ARTICLE_CHARS]
    return text
