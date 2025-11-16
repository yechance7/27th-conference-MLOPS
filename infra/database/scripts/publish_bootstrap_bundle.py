#!/usr/bin/env python3
"""
Helper script to package database bootstrap artifacts (scripts + wheels) and upload them to S3.

1. Create a “bundle” directory with subfolders for scripts and python wheels.
2. Zip the bundle and copy it to your landing bucket (or a dedicated bootstrap bucket).
3. On the private EC2 host, run `aws s3 cp` (vpc endpoint) to retrieve the bundle.

Example usage:
  python publish_bootstrap_bundle.py \
    --bundle-dir build/db-bootstrap \
    --output-zip s3://ybigta-mlops-landing-zone-324037321745/bootstrap/db_bootstrap.zip
"""
import argparse
import os
import shutil
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path
from typing import Dict, List, Optional

SCRIPTS = [
    "setup_timescale.sh",
    "backfill_s3_ticks.py",
]
SQL_FILES = [
    "../migrations/001_init.sql",
]
PYTHON_REQUIREMENTS = [
    "pandas==2.1.4",
    "boto3==1.34.69",
    "psycopg2-binary==2.9.9",
]
RPM_PRESETS: Dict[str, List[Dict[str, str]]] = {
    "al2023-pg15": [
        {
            "name": "timescaledb",
            "filename": "timescaledb-2.13.1-postgresql-15-0.el8.x86_64.rpm",
            "url": "https://packagecloud.io/timescale/timescaledb/packages/el/8/timescaledb-2.13.1-postgresql-15-0.el8.x86_64.rpm/download.rpm",
            "description": "TimescaleDB 2.13.1 build for PostgreSQL 15 (EL8/AL2023 compatible).",
        },
        {
            "name": "pgvector",
            "filename": "pgvector_15-0.5.1-1.el8.x86_64.rpm",
            "url": "https://download.postgresql.org/pub/repos/yum/15/redhat/rhel-8-x86_64/pgvector_15-0.5.1-1.el8.x86_64.rpm",
            "description": "pgvector 0.5.1 build for PostgreSQL 15 (EL8/AL2023 compatible).",
        },
    ]
}


def ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def run(cmd: List[str]) -> None:
    subprocess.run(cmd, check=True)


def build_bundle(bundle_dir: Path, scripts_root: Path) -> Path:
    ensure_dir(bundle_dir)
    scripts_target = bundle_dir / "scripts"
    ensure_dir(scripts_target)
    for script in SCRIPTS:
        src = scripts_root / script
        shutil.copy2(src, scripts_target / Path(script).name)
    sql_target = bundle_dir / "sql"
    ensure_dir(sql_target)
    for sql in SQL_FILES:
        src = (scripts_root / sql).resolve()
        shutil.copy2(src, sql_target / Path(sql).name)
    wheels_dir = bundle_dir / "wheels"
    ensure_dir(wheels_dir)
    tmp_req = bundle_dir / "requirements.txt"
    tmp_req.write_text("\n".join(PYTHON_REQUIREMENTS))
    run(
        [
            sys.executable,
            "-m",
            "pip",
            "download",
            "--no-deps",
            "--requirement",
            str(tmp_req),
            "--dest",
            str(wheels_dir),
        ]
    )
    return bundle_dir


def download_rpm_catalog(preset: str, target_dir: Path) -> List[Path]:
    entries = RPM_PRESETS[preset]
    downloaded: List[Path] = []
    for entry in entries:
        dest = target_dir / entry["filename"]
        print(f"Downloading {entry['name']} ({entry['description']})")
        with urllib.request.urlopen(entry["url"]) as response, open(dest, "wb") as fh:
            shutil.copyfileobj(response, fh)
        downloaded.append(dest)
    return downloaded


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bundle-dir", required=True, help="folder to assemble bundle")
    parser.add_argument(
        "--output-zip",
        required=True,
        help="S3 URI or local path for the final zip (e.g., s3://bucket/path/db_bootstrap.zip)",
    )
    parser.add_argument(
        "--rpm-paths",
        nargs="*",
        default=[],
        help="Optional local RPM files (TimescaleDB/pgvector) to include under bundle/rpms/",
    )
    parser.add_argument(
        "--rpm-preset",
        choices=sorted(RPM_PRESETS.keys()),
        help="Auto-download a known RPM set (e.g., Amazon Linux 2023 + PostgreSQL 15).",
    )
    parser.add_argument("--skip-upload", action="store_true", help="just create zip locally")
    args = parser.parse_args()

    bundle_dir = Path(args.bundle_dir)
    scripts_root = Path(__file__).resolve().parent
    bundle_path = build_bundle(bundle_dir, scripts_root)

    rpm_sources: List[Path] = [Path(p).resolve() for p in args.rpm_paths]
    tmp_rpm_dir: Optional[tempfile.TemporaryDirectory] = None
    try:
        if args.rpm_preset:
            tmp_rpm_dir = tempfile.TemporaryDirectory(prefix="rpm-preset-")
            preset_dir = Path(tmp_rpm_dir.name)
            rpm_sources.extend(download_rpm_catalog(args.rpm_preset, preset_dir))
        if rpm_sources:
            rpm_target = bundle_path / "rpms"
            ensure_dir(rpm_target)
            for rpm in rpm_sources:
                if not rpm.exists():
                    raise FileNotFoundError(f"RPM not found: {rpm}")
                shutil.copy2(rpm, rpm_target / rpm.name)
    finally:
        if tmp_rpm_dir is not None:
            tmp_rpm_dir.cleanup()

    zip_path = Path(args.bundle_dir + ".zip")
    if zip_path.exists():
        zip_path.unlink()
    run(["zip", "-r", str(zip_path), "-C", str(bundle_dir), "."])

    if args.skip-upload:
        print(f"Bundle created: {zip_path}")
        return

    if args.output-zip.startswith("s3://"):
        run(["aws", "s3", "cp", str(zip_path), args.output-zip])
        print(f"Uploaded bundle to {args.output-zip}")
    else:
        shutil.copy2(zip_path, args.output-zip)
        print(f"Copied bundle to {args.output-zip}")


if __name__ == "__main__":
    main()
