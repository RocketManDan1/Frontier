# Hourly GitHub Mirror (VM)

This project now includes an automated hourly mirror push.

## 1) One-time setup

From the project root:

```bash
chmod +x scripts/setup_github_mirror.sh scripts/github_mirror_hourly.sh
./scripts/setup_github_mirror.sh
```

Edit `.mirror.env` and set `GITHUB_REMOTE_URL` if your `origin` remote is not configured yet.

Example token URL:

```bash
GITHUB_REMOTE_URL=https://<token>@github.com/<owner>/<repo>.git
```

Use a GitHub PAT with minimum repo write permissions.

## 2) Install timer

```bash
sudo cp ops/systemd/earthmoon-github-mirror.service /etc/systemd/system/
sudo cp ops/systemd/earthmoon-github-mirror.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now earthmoon-github-mirror.timer
```

## 3) Test immediately

```bash
set -a && source ./.mirror.env && set +a
./scripts/github_mirror_hourly.sh
```

## 4) Verify

```bash
systemctl status earthmoon-github-mirror.timer
systemctl list-timers --all | grep earthmoon-github-mirror
journalctl -u earthmoon-github-mirror.service -n 100 --no-pager
```

## Notes

- This mirrors the full working tree except files in `.gitignore`.
- The timer catches up after reboot (`Persistent=true`).
- Consider using SSH deploy keys instead of PAT-in-URL for stronger security.
