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

## GPU worker

The reconstruction worker needs a GPU droplet; DO's GPU inventory and pricing
shift, so sizing is decided at P1-4/P1-5 when the COLMAP harness is up rather than
baked in here. `create-worker-droplet.sh` is a stub that documents the intended
shape (GPU droplet, NVIDIA drivers via cloud-init, `worker-colmap/` image).

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

The API droplet is **live** and running the backend (deployed via `infra/deploy.sh`).
The GPU worker droplet remains a skeleton — provisioned at P1-4/P1-5, not before, to
avoid idle spend.
