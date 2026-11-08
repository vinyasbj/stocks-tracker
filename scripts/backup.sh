#!/usr/bin/env bash
# Nightly snapshot of the bot's SQLite database. Runs from cron on the server:
#   45 2 * * * ~/stock-bot/scripts/backup.sh >> ~/backups/stock-bot/backup.log 2>&1
#
# Uses SQLite's online backup inside the running container, so the copy is consistent without stopping
# the bot (a tar of a live database file can catch a half-written page). Keeps KEEP_DAYS days on the VM.
# When ~/stock-bot/.backup.env sets BACKUP_PAR_URL (an OCI Object Storage pre-authenticated request
# that permits object writes, ending in /o/), each snapshot is also uploaded off the VM.
# If the upload fails the local snapshot is kept and the script exits non-zero.
set -euo pipefail
cd "$(dirname "$0")/.."

BACKUP_DIR=${BACKUP_DIR:-$HOME/backups/stock-bot}
CONTAINER=${CONTAINER:-stock-bot}
KEEP_DAYS=${KEEP_DAYS:-30}
BACKUP_PAR_URL=${BACKUP_PAR_URL:-}
# shellcheck disable=SC1091
[ -f .backup.env ] && . ./.backup.env

mkdir -p "$BACKUP_DIR"
name="stockbot-$(date +%F).db"
snap=/tmp/stockbot-snap.db

docker exec -i "$CONTAINER" python - "$snap" <<'EOF'
import os, sqlite3, sys
out = sys.argv[1]
if os.path.exists(out):
    os.remove(out)
src = sqlite3.connect(os.environ.get("DB_PATH", "/app/data/stockbot.db"))
dst = sqlite3.connect(out)
src.backup(dst)
ok = dst.execute("PRAGMA quick_check").fetchone()[0]
dst.close()
src.close()
if ok != "ok":
    sys.exit(f"snapshot failed its integrity check: {ok}")
EOF
docker cp -q "$CONTAINER:$snap" "$BACKUP_DIR/$name"
docker exec "$CONTAINER" rm -f "$snap"
gzip -f "$BACKUP_DIR/$name"
find "$BACKUP_DIR" -name 'stockbot-*.db.gz' -mtime +"$KEEP_DAYS" -delete
echo "$(date '+%F %T') backup ok: $name.gz ($(du -h "$BACKUP_DIR/$name.gz" | cut -f1)), $(ls "$BACKUP_DIR"/stockbot-*.db.gz | wc -l | tr -d ' ') kept"

if [ -n "$BACKUP_PAR_URL" ]; then
  # The URL is a credential: never echo it. curl's own errors don't include it.
  if curl -fsS --retry 3 --max-time 120 -T "$BACKUP_DIR/$name.gz" "${BACKUP_PAR_URL%/}/$name.gz" -o /dev/null; then
    echo "$(date '+%F %T') uploaded $name.gz off the VM"
  else
    echo "$(date '+%F %T') ERROR: upload of $name.gz failed; the local copy is kept" >&2
    exit 1
  fi
fi
