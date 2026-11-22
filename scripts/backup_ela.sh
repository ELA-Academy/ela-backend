#!/usr/bin/env bash
# ==============================================================================
# ELA Academy — Scoped Backup Script (Staging & Production ONLY)
#
# STRICT SCOPE:
#   - Backs up ONLY the PostgreSQL databases referenced in:
#       ~/ela-backend/production.env
#       ~/ela-backend-staging/staging.env
#   - Optionally archives ONLY ELA static uploads/documents.
#   - NEVER runs pg_dumpall and NEVER touches any other database or app on VPS.
# ==============================================================================

set -euo pipefail

BACKUP_ROOT="${ELA_BACKUP_DIR:-/var/backups/ela-school}"
RETENTION_DAYS="${ELA_BACKUP_RETENTION_DAYS:-14}"
TIMESTAMP="$(date -u +%Y%m%d_%H%M%S)"
RCLONE_REMOTE="${ELA_RCLONE_REMOTE:-}"  # Optional: e.g. "b2:ela-backups" or "r2:ela-backups"

mkdir -p "$BACKUP_ROOT/prod" "$BACKUP_ROOT/staging"
chmod 700 "$BACKUP_ROOT"

extract_db_url() {
    local env_file="$1"
    if [[ ! -f "$env_file" ]]; then
        return 1
    fi
    # Read DATABASE_URL, strip surrounding quotes and whitespace
    local raw
    raw="$(grep -E '^[[:space:]]*DATABASE_URL=' "$env_file" | tail -n 1 | cut -d'=' -f2-)"
    raw="${raw%\"}"
    raw="${raw#\"}"
    raw="${raw%\'}"
    raw="${raw#\'}"
    # Normalize SQLAlchemy driver prefix (postgresql+psycopg2:// -> postgresql://)
    raw="${raw/postgresql+psycopg2:\/\//postgresql:\/\/}"
    echo "$raw"
}

backup_env() {
    local label="$1"       # prod | staging
    local app_dir="$2"     # ~/ela-backend | ~/ela-backend-staging
    local env_file="$3"    # production.env | staging.env
    local target_dir="$BACKUP_ROOT/$label"

    local env_path="$app_dir/$env_file"
    if [[ ! -f "$env_path" ]]; then
        echo "[WARN] [$label] Env file not found at $env_path — skipping."
        return 0
    fi

    local db_url
    db_url="$(extract_db_url "$env_path" || true)"
    if [[ -z "$db_url" ]]; then
        echo "[WARN] [$label] DATABASE_URL not found in $env_path — skipping DB dump."
    else
        local dump_file="$target_dir/ela_${label}_db_${TIMESTAMP}.dump"
        echo "[INFO] [$label] Dumping ELA $label PostgreSQL database..."
        pg_dump --format=custom --no-owner --no-privileges --dbname="$db_url" --file="$dump_file"
        chmod 600 "$dump_file"
        echo "[OK]   [$label] DB backup created: $dump_file ($(du -h "$dump_file" | cut -f1))"
    fi

    # Archive ONLY ELA static documents & uploads (never other VPS directories)
    local static_dir="$app_dir/app/static"
    if [[ -d "$static_dir" ]]; then
        local files_archive="$target_dir/ela_${label}_static_${TIMESTAMP}.tar.gz"
        tar -czf "$files_archive" -C "$static_dir" .
        chmod 600 "$files_archive"
        echo "[OK]   [$label] Static files archived: $files_archive ($(du -h "$files_archive" | cut -f1))"
    fi

    # Prune old ELA backups beyond retention window
    find "$target_dir" -type f \( -name "ela_${label}_db_*.dump" -o -name "ela_${label}_static_*.tar.gz" \) -mtime +"$RETENTION_DAYS" -delete
}

backup_env "prod"    "${ELA_PROD_DIR:-$HOME/ela-backend}"         "production.env"
backup_env "staging" "${ELA_STAGING_DIR:-$HOME/ela-backend-staging}" "staging.env"

# Optional off-VPS sync (strongly recommended so a VPS disk failure cannot lose backups)
if [[ -n "$RCLONE_REMOTE" ]] && command -v rclone >/dev/null 2>&1; then
    echo "[INFO] Syncing ELA backups to off-server remote: $RCLONE_REMOTE"
    rclone sync "$BACKUP_ROOT" "$RCLONE_REMOTE" --transfers=4 --fast-list
    echo "[OK]   Off-server sync complete."
fi

echo "[DONE] ELA scoped backup finished at $(date -u +%Y-%m-%dT%H:%M:%SZ)"
