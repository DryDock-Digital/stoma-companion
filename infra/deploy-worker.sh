#!/usr/bin/env bash
#
# One-command deploy of the reconstruction + measurement worker (CPU image) to
# the droplet. Mirrors deploy.sh; the worker needs the same Supabase keys plus the
# measurement/queue knobs (all allowlisted below), and runs from the repo root
# because the image copies both backend/ and worker-colmap/.
#
#   ./infra/deploy-worker.sh                       # CPU image (Dockerfile.cpu) on DROPLET_HOST (D15)
#   WORKER_HOST=<gpu-ip> WORKER_DOCKERFILE=Dockerfile ./infra/deploy-worker.sh   # CUDA image on the GPU droplet
#
# WORKER_HOST (env or .env) overrides DROPLET_HOST so the worker can live on its own
# box; first run `infra/gpu-host-setup.sh` on a GPU host.
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
ENV_FILE="$REPO_ROOT/.env"
[[ -f "$ENV_FILE" ]] || { echo "error: $ENV_FILE not found (copy .env.example → .env)"; exit 1; }
getenv() { grep -E "^$1=" "$ENV_FILE" | head -1 | cut -d= -f2- || true; }

HOST="${WORKER_HOST:-$(getenv WORKER_HOST)}"; HOST="${HOST:-$(getenv DROPLET_HOST)}"
USER="$(getenv DROPLET_USER)"; USER="${USER:-root}"
SSH_KEY="$(getenv DROPLET_SSH_KEY)"; SSH_KEY="${SSH_KEY:-$HOME/.ssh/stoma_droplet}"; SSH_KEY="${SSH_KEY/#\~/$HOME}"
REMOTE_DIR="${REMOTE_DIR:-/opt/stoma}"
DOCKERFILE="${WORKER_DOCKERFILE:-Dockerfile.cpu}"
IMAGE="stoma-worker"
CONTAINER="stoma-worker"
GPU_FLAG=""; [[ "$DOCKERFILE" == "Dockerfile" ]] && GPU_FLAG="--gpus all"

[[ -n "$HOST" ]]    || { echo "error: DROPLET_HOST not set in .env"; exit 1; }
[[ -f "$SSH_KEY" ]] || { echo "error: SSH key not found: $SSH_KEY"; exit 1; }

ENV_ALLOWLIST='^(SUPABASE_URL|SUPABASE_SERVICE_ROLE_KEY|SUPABASE_STORAGE_BUCKET|GRACE_RING_MM|TOLERANCE_MM|MARKER_SIDE_MM|ARUCO_DICT|GCODE_DIALECT|CLAIM_TIMEOUT_S|MAX_ATTEMPTS|RECONSTRUCT_TIMEOUT_S|MEASURE_TIMEOUT_S|ARCHIVE_KEYFRAMES|WORKER_ID|WORKER_POLL_INTERVAL|COLMAP_MAX_IMAGE_SIZE|COLMAP_MAX_FEATURES|COLMAP_SEQ_OVERLAP|MVS_RESOLUTION_LEVEL|MVS_NUMBER_VIEWS|MVS_MAX_RESOLUTION|MESH_DECIMATE|DENSE_ENGINE|PMS_ITERATIONS|PMS_WINDOW_RADIUS|POISSON_DEPTH|POISSON_TRIM)='

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

# The build + container swap run as one script on the host. With DETACHED_BUILD=1 it
# is launched under nohup and this command returns immediately (a first CUDA build
# compiles OpenMVS for 20–40 min — longer than an SSH session should be relied on);
# follow it with:  ssh $USER@$HOST tail -f $REMOTE_DIR/worker-build.log
remote "cat > $REMOTE_DIR/build-worker.sh <<'EOS'
#!/bin/bash
set -e
cd $REMOTE_DIR
docker build -f worker-colmap/$DOCKERFILE -t $IMAGE . && echo BUILD_OK || { echo BUILD_FAILED; exit 1; }
docker rm -f $CONTAINER >/dev/null 2>&1 || true
docker run -d --name $CONTAINER --restart unless-stopped $GPU_FLAG --env-file $REMOTE_DIR/.env.worker $IMAGE
echo DEPLOY_OK
EOS
chmod +x $REMOTE_DIR/build-worker.sh"

if [[ "${DETACHED_BUILD:-0}" == "1" ]]; then
  echo "▶ building + deploying detached on the host …"
  remote "nohup $REMOTE_DIR/build-worker.sh > $REMOTE_DIR/worker-build.log 2>&1 < /dev/null & disown; echo '  launched'"
  echo "  follow: ssh $USER@$HOST tail -f $REMOTE_DIR/worker-build.log   (ends with DEPLOY_OK)"
  exit 0
fi

echo "▶ building image (OpenMVS compiles from source — first build takes a while) …"
remote "$REMOTE_DIR/build-worker.sh > $REMOTE_DIR/worker-build.log 2>&1 \
  && echo '  build + swap ok' || { echo '  BUILD FAILED:'; tail -40 $REMOTE_DIR/worker-build.log; exit 1; }"

echo "▶ worker log (first lines) …"
sleep 3
remote "docker logs --tail 10 $CONTAINER" || true
echo "✔ worker deployed. It polls the queue; watch: ssh $USER@$HOST docker logs -f $CONTAINER"
