#!/bin/bash
# Simple helper to install PostgreSQL 15 + TimescaleDB on Amazon Linux 2023 EC2 hosts.
# Usage:
#   chmod +x setup_timescale.sh
#   sudo ./setup_timescale.sh <db_name> <db_user> <db_password>
set -euo pipefail

DB_NAME=${1:-market}
DB_USER=${2:-mlops}
DB_PASSWORD=${3:-changeme123}

echo "[*] Installing PostgreSQL 15 + extensions..."
dnf update -y
dnf install -y postgresql15-server postgresql15-contrib

echo "[*] Detecting postgresql-setup binary..."
if [ -x /usr/pgsql-15/bin/postgresql-15-setup ]; then
  SETUP_BIN="/usr/pgsql-15/bin/postgresql-15-setup"
  SETUP_MODE="pgdg15"
elif command -v postgresql-setup >/dev/null 2>&1; then
  SETUP_BIN="$(command -v postgresql-setup)"
  SETUP_MODE="al2023"
else
  echo "[!] Could not find postgresql-setup binary." >&2
  exit 1
fi

echo "[*] Initializing database cluster using ${SETUP_BIN} (${SETUP_MODE})..."
if [ "${SETUP_MODE}" = "pgdg15" ]; then
  # PGDG style: /usr/pgsql-15/bin/postgresql-15-setup initdb
  "${SETUP_BIN}" initdb
else
  # Amazon Linux 2023 style: postgresql-setup --initdb
  "${SETUP_BIN}" --initdb
fi

# Detect data directory
if [ -d /var/lib/pgsql/15/data ]; then
  CONF_DIR="/var/lib/pgsql/15/data"
elif [ -d /var/lib/pgsql/data ]; then
  CONF_DIR="/var/lib/pgsql/data"
else
  echo "[!] Could not find PostgreSQL data directory." >&2
  exit 1
fi

POSTGRESQL_CONF="${CONF_DIR}/postgresql.conf"
HBA_CONF="${CONF_DIR}/pg_hba.conf"

echo "[*] Enabling timescaledb extension in postgresql.conf"
if ! grep -q "shared_preload_libraries" "${POSTGRESQL_CONF}"; then
  echo "shared_preload_libraries = 'timescaledb'" >> "${POSTGRESQL_CONF}"
else
  sed -i "s/shared_preload_libraries.*/shared_preload_libraries = 'timescaledb'/g" "${POSTGRESQL_CONF}"
fi

echo "[*] Allowing local connections via md5 auth"
cat <<'EOF' > "${HBA_CONF}"
local   all             all                                     md5
host    all             all             127.0.0.1/32            md5
host    all             all             ::1/128                 md5
EOF

echo "[*] Enabling and starting PostgreSQL service..."
if systemctl list-unit-files | grep -q '^postgresql-15\.service'; then
  PG_SERVICE="postgresql-15"
else
  PG_SERVICE="postgresql"
fi
systemctl enable --now "${PG_SERVICE}"

echo "[*] Creating database/user..."
sudo -u postgres psql <<SQL
DO \$\$
BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = '${DB_USER}') THEN
    CREATE ROLE ${DB_USER} LOGIN PASSWORD '${DB_PASSWORD}';
  END IF;
END\$\$;

DO \$\$
BEGIN
  IF NOT EXISTS (SELECT FROM pg_database WHERE datname = '${DB_NAME}') THEN
    CREATE DATABASE ${DB_NAME} OWNER ${DB_USER};
  END IF;
END\$\$;
SQL

echo "[*] Installing timescaledb + pgvector extensions (may fail if packages not installed)"
set +e
sudo -u postgres psql -d "${DB_NAME}" -c "CREATE EXTENSION IF NOT EXISTS timescaledb;"
sudo -u postgres psql -d "${DB_NAME}" -c "CREATE EXTENSION IF NOT EXISTS vector;"
set -e

echo "[*] Completed. Connect via: psql -h localhost -U ${DB_USER} -d ${DB_NAME}"