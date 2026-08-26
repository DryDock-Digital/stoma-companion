#!/usr/bin/env bash
#
# One-command deploy of the reconstruction + measurement worker (CPU image) to
# the droplet. Mirrors deploy.sh; the worker needs the same Supabase keys plus the
# measurement/queue knobs (all allowlisted below), and runs from the repo root
# because the image copies both backend/ and worker-colmap/.
#
#   ./infra/deploy-worker.sh            # CPU image (Dockerfile.cpu) — current droplet (D15)
#   WORKER_DOCKERFILE=Dockerfile ./infra/deploy-worker.sh   # CUDA image on a GPU droplet
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
ENV_FILE="$REPO_ROOT/.env"
[[ -f "$ENV_FILE" ]] || { echo "error: $ENV_FILE not found (copy .env.example → .env)"; exit 1; }
getenv() { grep -E "^$1=" "$ENV_FILE" | head -1 | cut -d= -f2- || true; }

HOST="$(getenv DROPLET_HOST)"
USER="$(getenv DROPLET_USER)"; USER="${USER:-root}"
SSH_KEY="$(getenv DROPLET_SSH_KEY)"; SSH_KEY="${SSH_KEY:-$HOME/.ssh/stoma_droplet}"; SSH_KEY="${SSH_KEY/#\~/$HOME}"
REMOTE_DIR="${REMOTE_DIR:-/opt/stoma}"
DOCKERFILE="${WORKER_DOCKERFILE:-Dockerfile.cpu}"
IMAGE="stoma-worker"
CONTAINER="stoma-worker"
GPU_FLAG=""; [[ "$DOCKERFILE" == "Dockerfile" ]] && GPU_FLAG="--gpus all"

[[ -n "$HOST" ]]    || { echo "error: DROPLET_HOST not set in .env"; exit 1; }
[[ -f "$SSH_KEY" ]] || { echo "error: SSH key not found: $SSH_KEY"; exit 1; }

ENV_ALLOWLIST='^(SUPABASE_URL|SUPABASE_SERVICE_ROLE_KEY|SUPABASE_STORAGE_BUCKET|GRACE_RING_MM|TOLERANCE_MM|MARKER_SIDE_MM|ARUCO_DICT|GCODE_DIALECT|CLAIM_TIMEOUT_S|MAX_ATTEMPTS|RECONSTRUCT_TIMEOUT_S|MEASURE_TIMEOUT_S|WORKER_ID|WORKER_POLL_INTERVAL)='

SSH_OPTS=(-o BatchMode=yes -o StrictHostKeyChecking=accept-new -o ConnectTimeout=30 -i "$SSH_KEY")
remote() { ssh "${SSH_OPTS[@]}" "$USER@$HOST" "$@"; }

echo "▶ deploying worker ($DOCKERFILE) to $USER@$HOST:$REMOTE_DIR"
remote 'echo "  ssh ok on $(hostname)"'

echo "▶ syncing backend/ + worker-colmap/ …"
COPYFILE_DISABLE=1 tar --no-xattrs -czf - \
  --exclude .venv --exclude __pycache__ --exclude '*.egg-info' \
  --exclude .pytest_cache --exclude .ruff_cache \
  -C "$REPO_ROOT" backend worker-colmap \
  | remote "mkdir -p $REMOTE_DIR && tar xzf - -C $REMOTE_DIR && echo '  code synced'"

echo "▶ syncing env (allowlisted keys only) …"
grep -E "$ENV_ALLOWLIST" "$ENV_FILE" \
  | remote "umask 077 && cat > $REMOTE_DIR/.env.worker && echo \"  env synced (\$(grep -c . $REMOTE_DIR/.env.worker) keys)\""

echo "▶ building image (OpenMVS compiles from source — first build takes a while) …"
remote "cd $REMOTE_DIR && docker build -f worker-colmap/$DOCKERFILE -t $IMAGE . >/tmp/stoma-worker-build.log 2>&1 \
  && echo '  build ok' || { echo '  BUILD FAILED:'; tail -40 /tmp/stoma-worker-build.log; exit 1; }"

echo "▶ restarting container …"
remote "docker rm -f $CONTAINER >/dev/null 2>&1 || true; \
  docker run -d --name $CONTAINER --restart unless-stopped $GPU_FLAG \
    --env-file $REMOTE_DIR/.env.worker $IMAGE >/dev/null && echo '  container started'"

echo "▶ worker log (first lines) …"
sleep 3
remote "docker logs --tail 10 $CONTAINER" || true
echo "✔ worker deployed. It polls the queue; watch: ssh $USER@$HOST docker logs -f $CONTAINER"
