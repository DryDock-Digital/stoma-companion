#!/usr/bin/env bash
#
# One-command deploy of the backend to the DigitalOcean droplet.
#
#   ./infra/deploy.sh
#
# What it does (idempotent — safe to re-run):
#   1. ships backend/ to the droplet (tar-over-ssh; excludes local build junk)
#   2. ships a TRIMMED env — only the keys the backend actually reads, never the
#      DO token / droplet metadata / worker vars (keeps cloud creds off the host)
#   3. builds the image on the droplet
#   4. swaps the running container (build first, then replace → brief downtime only)
#   5. health-checks /health with retries
#
# Connection details come from the repo-root .env:
#   DROPLET_HOST   (required)   public IP / hostname
#   DROPLET_USER   (default root)
#   DROPLET_SSH_KEY(default ~/.ssh/stoma_droplet)  path to a passphrase-less key
# Optional overrides via env: REMOTE_DIR (default /opt/stoma), APP_PORT (8000).
#
set -euo pipefail

# --- locate repo + config --------------------------------------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
ENV_FILE="$REPO_ROOT/.env"

[[ -f "$ENV_FILE" ]] || { echo "error: $ENV_FILE not found (copy .env.example → .env)"; exit 1; }

# Read a single KEY=value from .env without sourcing it (it isn't shell-safe).
getenv() { grep -E "^$1=" "$ENV_FILE" | head -1 | cut -d= -f2- || true; }

HOST="$(getenv DROPLET_HOST)"
USER="$(getenv DROPLET_USER)"; USER="${USER:-root}"
SSH_KEY="$(getenv DROPLET_SSH_KEY)"; SSH_KEY="${SSH_KEY:-$HOME/.ssh/stoma_droplet}"; SSH_KEY="${SSH_KEY/#\~/$HOME}"
REMOTE_DIR="${REMOTE_DIR:-/opt/stoma}"
APP_PORT="${APP_PORT:-8000}"
IMAGE="stoma-backend"
CONTAINER="stoma-backend"

[[ -n "$HOST" ]]        || { echo "error: DROPLET_HOST not set in .env"; exit 1; }
[[ -f "$SSH_KEY" ]]     || { echo "error: SSH key not found: $SSH_KEY"; exit 1; }

# Keys the backend reads (app/config.py). Everything else in .env stays local.
ENV_ALLOWLIST='^(SUPABASE_URL|SUPABASE_SERVICE_ROLE_KEY|SUPABASE_ANON_KEY|SUPABASE_STORAGE_BUCKET|MAX_UPLOAD_MB|STORAGE_OBJECT_MAX_MB|KEYFRAME_INTERVAL_SECONDS|KEYFRAME_MAX_FRAMES|KEYFRAME_TARGET_FRAMES|GRACE_RING_MM|TOLERANCE_MM|MARKER_SIDE_MM|ARUCO_DICT|GCODE_DIALECT|RUN_KEYFRAME_WORKER|ARCHIVE_KEYFRAMES|CLAIM_TIMEOUT_S|MAX_ATTEMPTS|RECONSTRUCT_TIMEOUT_S|MEASURE_TIMEOUT_S|CORS_ORIGINS)='

SSH_OPTS=(-o BatchMode=yes -o StrictHostKeyChecking=accept-new -o ConnectTimeout=30 -i "$SSH_KEY")
remote() { ssh "${SSH_OPTS[@]}" "$USER@$HOST" "$@"; }

echo "▶ deploying to $USER@$HOST:$REMOTE_DIR (port $APP_PORT)"

# --- 1. connectivity -------------------------------------------------------
remote 'echo "  ssh ok on $(hostname)"'

# --- 2. ship code ----------------------------------------------------------
echo "▶ syncing backend/ …"
COPYFILE_DISABLE=1 tar --no-xattrs -czf - \
  --exclude .venv --exclude __pycache__ --exclude '*.egg-info' \
  --exclude .pytest_cache --exclude .ruff_cache \
  -C "$REPO_ROOT" backend \
  | remote "mkdir -p $REMOTE_DIR && tar xzf - -C $REMOTE_DIR && echo '  code synced'"

# --- 3. ship trimmed env ---------------------------------------------------
echo "▶ syncing env (allowlisted keys only) …"
grep -E "$ENV_ALLOWLIST" "$ENV_FILE" \
  | remote "umask 077 && cat > $REMOTE_DIR/.env && echo \"  env synced (\$(grep -c . $REMOTE_DIR/.env) keys)\""

# --- 4. build --------------------------------------------------------------
echo "▶ building image (this can take a few minutes) …"
remote "cd $REMOTE_DIR/backend && docker build -t $IMAGE . >/tmp/stoma-build.log 2>&1 \
  && echo '  build ok' || { echo '  BUILD FAILED:'; tail -30 /tmp/stoma-build.log; exit 1; }"

# --- 5. swap container -----------------------------------------------------
echo "▶ restarting container …"
remote "docker rm -f $CONTAINER >/dev/null 2>&1 || true; \
  docker run -d --name $CONTAINER --restart unless-stopped \
    -p $APP_PORT:8000 --env-file $REMOTE_DIR/.env $IMAGE >/dev/null \
  && echo '  container started'"

# --- 6. health check -------------------------------------------------------
echo "▶ health check …"
ok=0
for i in $(seq 1 10); do
  if remote "curl -sf --max-time 5 http://localhost:$APP_PORT/health" 2>/dev/null | grep -q '"status":"ok"'; then
    ok=1; break
  fi
  sleep 2
done

if [[ "$ok" == "1" ]]; then
  body="$(remote "curl -s http://localhost:$APP_PORT/health")"
  echo "✔ deployed — /health: $body"
  echo "  reachable at http://$HOST:$APP_PORT  (/health, /docs)"
else
  echo "✗ health check failed; recent logs:"
  remote "docker logs --tail 30 $CONTAINER" || true
  exit 1
fi
