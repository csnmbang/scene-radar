#!/bin/bash
# Refresh the committed Beatport chart feed from a home connection.
#
# Beatport's Cloudflare blocks GitHub's servers, so the daily cloud run reads
# data/beatport_feed.json instead of fetching charts itself. This script keeps
# that file fresh: it fetches the charts here, commits ONLY that file, and
# pushes.
#
# It works in its own clone (default ~/.scene-radar-feed) rather than your
# working copy, so it can never rebase over uncommitted edits.
#
# Usage:  scripts/refresh_beatport_feed.sh
# Env:    FEED_REPO_DIR   clone location   (default ~/.scene-radar-feed)
#         FEED_REPO_URL   remote to clone  (default: this repo's origin)
set -euo pipefail

export PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"

SRC="$(cd "$(dirname "$0")/.." && pwd)"
DEST="${FEED_REPO_DIR:-$HOME/.scene-radar-feed}"
URL="${FEED_REPO_URL:-$(git -C "$SRC" remote get-url origin)}"

if [ ! -d "$DEST/.git" ]; then
  git clone --quiet "$URL" "$DEST"
fi
cd "$DEST"

# Start from the remote's tip every time; this clone holds nothing of value.
git fetch --quiet origin master
git reset --quiet --hard origin/master

uv run python refresh_beatport_feed.py

if git diff --quiet -- data/beatport_feed.json; then
  echo "Feed unchanged — nothing to commit."
  exit 0
fi

git add data/beatport_feed.json
git -c user.name="beatport-feed-bot" -c user.email="noreply@users.noreply.github.com" \
  commit --quiet -m "Beatport feed $(date +%F)"

# The cloud run commits to master too; rebase over it rather than fail.
for attempt in 1 2 3; do
  git pull --quiet --rebase origin master
  if git push --quiet origin master; then
    echo "Pushed Beatport feed for $(date +%F)."
    exit 0
  fi
  sleep $((attempt * 5))
done
echo "Could not push the feed after 3 attempts." >&2
exit 1
