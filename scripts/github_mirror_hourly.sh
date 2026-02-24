#!/usr/bin/env bash
set -euo pipefail

REPO_DIR="${REPO_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
BRANCH="${MIRROR_BRANCH:-main}"
REMOTE_NAME="${MIRROR_REMOTE_NAME:-origin}"
GIT_USER_NAME="${GIT_USER_NAME:-Frontier: Sol 2000 Mirror Bot}"
GIT_USER_EMAIL="${GIT_USER_EMAIL:-mirror-bot@localhost}"
GITHUB_REMOTE_URL="${GITHUB_REMOTE_URL:-}"

cd "$REPO_DIR"

if ! git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  git init -b "$BRANCH"
fi

git config user.name "$GIT_USER_NAME"
git config user.email "$GIT_USER_EMAIL"

if [[ -n "$GITHUB_REMOTE_URL" ]]; then
  if git remote get-url "$REMOTE_NAME" >/dev/null 2>&1; then
    git remote set-url "$REMOTE_NAME" "$GITHUB_REMOTE_URL"
  else
    git remote add "$REMOTE_NAME" "$GITHUB_REMOTE_URL"
  fi
fi

if ! git remote get-url "$REMOTE_NAME" >/dev/null 2>&1; then
  echo "Missing git remote '$REMOTE_NAME'. Set GITHUB_REMOTE_URL or add remote manually." >&2
  exit 1
fi

git add -A

if git diff --cached --quiet; then
  echo "No changes detected at $(date -Iseconds)."
  exit 0
fi

git commit -m "hourly mirror: $(date -Iseconds)"
git push "$REMOTE_NAME" "HEAD:$BRANCH"

echo "Mirror push complete at $(date -Iseconds)."
