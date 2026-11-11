#!/usr/bin/env bash
# Daily snapshot of the server bot's SQLite database, pulled to THIS machine (run it on the laptop,
# scheduled by deploy/com.stockbot.backup.plist). Nothing is stored on the VM.
#
# Uses SQLite's online backup inside the running container, so the copy is consistent without stopping
# the bot (a tar of a live database file can catch a half-written page). The snapshot is streamed over
# ssh, gzipped, checked on this machine, and the temp file on the VM is removed. Keeps KEEP_DAYS days.
set -euo pipefail

HOST=${HOST:-oracle-expense}
BACKUP_DIR=${BACKUP_DIR:-$HOME/backups/stock-bot}
CONTAINER=${CONTAINER:-stock-bot}
KEEP_DAYS=${KEEP_DAYS:-30}

mkdir -p "$BACKUP_DIR"
name="stockbot-$(date +%F).db"
snap=/tmp/stockbot-snap.db
tmp="$BACKUP_DIR/.$name.gz.part"
trap 'rm -f "$tmp"' EXIT

ssh -o BatchMode=yes -o ConnectTimeout=20 "$HOST" "docker exec -i $CONTAINER python - $snap" <<'EOF'
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
ssh -o BatchMode=yes "$HOST" "docker exec $CONTAINER sh -c 'cat $snap && rm -f $snap' | gzip" > "$tmp"

# Verify what actually landed here before it replaces anything.
check=$(gunzip -c "$tmp" | { f=$(mktemp); cat > "$f"; sqlite3 "$f" "pragma integrity_check; select count(*) from seen_items;" ; rm -f "$f"; })
[ "$(echo "$check" | head -1)" = "ok" ] || { echo "$(date '+%F %T') ERROR: downloaded copy failed its check: $check" >&2; exit 1; }

mv "$tmp" "$BACKUP_DIR/$name.gz"
find "$BACKUP_DIR" -name 'stockbot-*.db.gz' -mtime +"$KEEP_DAYS" -delete
echo "$(date '+%F %T') backup ok: $name.gz ($(du -h "$BACKUP_DIR/$name.gz" | cut -f1)), $(echo "$check" | tail -1) seen items, $(ls "$BACKUP_DIR"/stockbot-*.db.gz | wc -l | tr -d ' ') kept"
