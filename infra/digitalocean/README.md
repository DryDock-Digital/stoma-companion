# infra/digitalocean — droplet skeleton (P0-7)

Provisioning-as-code for the two DigitalOcean hosts. **This does not create
anything on its own** — running the scripts spends money on Aaron's DO account,
so it's a deliberate manual step, not part of CI.

Two roles (kept separate so the GPU box only exists when reconstruction runs):

| Host | Purpose | Sizing |
|---|---|---|
| `stoma-api` | FastAPI backend (Docker) | basic shared-CPU droplet |
| `stoma-worker` | COLMAP + OpenMVS reconstruction | GPU droplet (see P1-4/P1-5) |

## Prerequisites

- `doctl` installed and authed: `doctl auth init` (uses `DIGITALOCEAN_ACCESS_TOKEN`).
- An SSH key uploaded to DO; put its fingerprint in `SSH_KEY_FINGERPRINT`.

## Create the API droplet

```bash
export DIGITALOCEAN_ACCESS_TOKEN=...      # from .env
export SSH_KEY_FINGERPRINT=...
./create-api-droplet.sh
```

The script tags the droplet `stoma-api`, installs Docker via `cloud-init.yaml`,
and prints the public IP. Deploy is then `docker compose up` of `backend/`
(or a `git pull` + rebuild) — wired up in P1-1's Dockerfile.

## GPU worker — bring-up (P1-5)

DO GPU droplets (H100 / RTX-class, NYC2/TOR1) come with the NVIDIA driver on their
GPU image. From your machine, with the new box's IP:

```bash
# 1. host prep: Docker CE + NVIDIA container toolkit + a GPU smoke test (idempotent)
ssh root@<gpu-ip> 'bash -s' < infra/gpu-host-setup.sh

# 2. build + run the CUDA worker image (first build ~20–40 min: OpenMVS from source)
WORKER_HOST=<gpu-ip> WORKER_DOCKERFILE=Dockerfile ./infra/deploy-worker.sh

# 3. watch it claim a job; the admin run page shows diagnostics.gpu_name
ssh root@<gpu-ip> docker logs -f stoma-worker
```

Put `WORKER_HOST=<gpu-ip>` and `WORKER_ID=colmap-gpu-1` in `.env` so later deploys
default to it. The worker is **outbound-only** (polls Supabase); it needs no open
ports and no Caddy. Once it is claiming jobs, stop the CPU worker on the API box
(`docker stop stoma-worker` on 159.65.233.200) — both poll the same queue, so the
overlap is harmless. If the base image's CUDA is newer than the host driver
(`nvidia-smi` shows the max supported CUDA), pin an older `COLMAP_TAG` build arg.

Speed knobs live in `.env` (`COLMAP_MAX_IMAGE_SIZE`, `MVS_RESOLUTION_LEVEL`, …, see
`.env.example`); every run records the values it used in its diagnostics, and the
sweep that chose the defaults is in `docs/decisions.md` (D18).

## Deploying the backend — `infra/deploy.sh`

Once a droplet exists, push the backend to it with one command from the repo root:

```bash
./infra/deploy.sh
```

It reads the target from `.env` (`DROPLET_HOST`, `DROPLET_USER`, `DROPLET_SSH_KEY`),
then: ships `backend/` (tar-over-ssh), ships a **trimmed** env containing only the
Supabase/app keys the backend reads — never the DO token, droplet metadata, or
worker vars — builds the image on the droplet, swaps the container
(`--restart unless-stopped`, port 8000), and health-checks `/health`. It's
idempotent: re-run it to redeploy after any change.

`DROPLET_SSH_KEY` must be a **passphrase-less** key authorized on the box (SSH runs
non-interactively). Overrides: `REMOTE_DIR` (default `/opt/stoma`), `APP_PORT`
(default `8000`).

## Status

The API droplet is **live** (backend + Caddy TLS + a CPU fallback worker, D15). The
GPU worker droplet is provisioned by the client at P1-5; bring-up is the three
commands above.
