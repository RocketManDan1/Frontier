#!/usr/bin/env bash
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SYSTEMD_DIR="$REPO_DIR/ops/systemd"

chmod +x "$REPO_DIR/scripts/github_mirror_hourly.sh"

if [[ ! -f "$REPO_DIR/.mirror.env" ]]; then
  cat > "$REPO_DIR/.mirror.env" <<'EOF'
# Required if origin is not already configured:
# GITHUB_REMOTE_URL=https://<token>@github.com/<owner>/<repo>.git

# Optional overrides:
MIRROR_BRANCH=main
MIRROR_REMOTE_NAME=origin
GIT_USER_NAME=Frontier: Sol 2000 Mirror Bot
GIT_USER_EMAIL=mirror-bot@localhost
EOF
  echo "Created $REPO_DIR/.mirror.env"
fi

echo "Install and enable hourly timer with:"
echo "  sudo cp $SYSTEMD_DIR/frontier-sol-2000-github-mirror.service /etc/systemd/system/"
echo "  sudo cp $SYSTEMD_DIR/frontier-sol-2000-github-mirror.timer /etc/systemd/system/"
echo "  sudo systemctl daemon-reload"
echo "  sudo systemctl enable --now frontier-sol-2000-github-mirror.timer"
echo ""
echo "Edit .mirror.env before first run if needed, then test with:"
echo "  set -a && source $REPO_DIR/.mirror.env && set +a && $REPO_DIR/scripts/github_mirror_hourly.sh"
