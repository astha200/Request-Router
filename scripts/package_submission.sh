#!/usr/bin/env bash
# Build a clean submission ZIP.
#
# Excludes credentials, virtualenvs, node_modules, the local database, build
# output and git history, then VERIFIES the archive contains no secrets before
# declaring success.
#
#   bash scripts/package_submission.sh

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

OUT_DIR="submission"
NAME="fireworks-router-submission"
STAGE="$(mktemp -d)/$NAME"
trap 'rm -rf "$(dirname "$STAGE")"' EXIT

echo "Staging a clean copy..."
mkdir -p "$STAGE"
# NOTE: rsync applies filter rules in order - includes must precede the
# broader excludes, or '.env.*' would swallow '.env.example'.
rsync -a \
  --include '.env.example' \
  --include '.gitignore' \
  --exclude '.git/' \
  --exclude '.env' \
  --exclude '.env.*' \
  --exclude '.venv/' --exclude 'venv/' \
  --exclude 'node_modules/' \
  --exclude 'dist/' --exclude '.next/' \
  --exclude '__pycache__/' --exclude '*.pyc' \
  --exclude '.pytest_cache/' \
  --exclude '*.db' --exclude '*.db-wal' --exclude '*.db-shm' \
  --exclude '*.tsbuildinfo' \
  --exclude '.DS_Store' \
  --exclude "$OUT_DIR/" \
  ./ "$STAGE/"

# --- verification: refuse to ship a secret ------------------------------------
echo "Verifying no secrets in the archive..."
FAIL=0

if find "$STAGE" -name '.env' -not -name '.env.example' | grep -q .; then
  echo "  FAIL  a .env file is present"; FAIL=1
fi

# Key-shaped strings, ignoring obvious placeholders (test fixtures, .env.example).
HITS=$(grep -rIoh --exclude='*.example' -E 'fw_[A-Za-z0-9_-]{16,}' "$STAGE" 2>/dev/null \
        | grep -vE '(test|example|placeholder|not_real|your_key|redacted)' || true)
if [ -n "$HITS" ]; then
  echo "  FAIL  a live Fireworks key appears to be present:"
  grep -rIl --exclude='*.example' -E 'fw_[A-Za-z0-9_-]{16,}' "$STAGE" 2>/dev/null \
    | xargs grep -lE 'fw_[A-Za-z0-9_-]{16,}' 2>/dev/null \
    | sed "s|$STAGE/||;s|^|        |"
  FAIL=1
fi

for junk in .venv node_modules .git router.db; do
  if [ -e "$STAGE/$junk" ] || [ -e "$STAGE/frontend/$junk" ]; then
    echo "  FAIL  '$junk' was not excluded"; FAIL=1
  fi
done

if [ "$FAIL" -ne 0 ]; then
  echo
  echo "ABORTED - archive not written."
  exit 1
fi
echo "  ok    no credentials, no virtualenv, no node_modules, no database"

# --- write the archive --------------------------------------------------------
mkdir -p "$OUT_DIR"
ARCHIVE="$REPO_ROOT/$OUT_DIR/$NAME.zip"
rm -f "$ARCHIVE"
( cd "$(dirname "$STAGE")" && zip -qr "$ARCHIVE" "$NAME" -x '*.DS_Store' )

SIZE=$(du -h "$ARCHIVE" | cut -f1)
COUNT=$(unzip -l "$ARCHIVE" | tail -1 | awk '{print $2}')

echo
echo "Wrote $OUT_DIR/$NAME.zip  ($SIZE, $COUNT files)"
echo
echo "Contents:"
unzip -l "$ARCHIVE" | awk 'NR>3 && $4 != "" {print "  " $4}' | head -45
echo
echo "Reviewer runs: unzip, then follow README Quickstart (needs their own FIREWORKS_API_KEY)."
