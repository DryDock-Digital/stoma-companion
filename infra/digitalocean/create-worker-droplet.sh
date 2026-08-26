#!/usr/bin/env bash
# Stub: create the GPU reconstruction droplet (stoma-worker).
#
# Intentionally not fully wired: DO's GPU droplet sizes/regions and pricing move,
# and the sizing decision belongs to P1-4/P1-5 when the COLMAP harness is up and
# we can measure reconstruction time vs cost. Filling in $SIZE + a GPU-enabled
# image (or cloud-init that installs NVIDIA drivers) makes this live.
set -euo pipefail

: "${DIGITALOCEAN_ACCESS_TOKEN:?set DIGITALOCEAN_ACCESS_TOKEN (see .env.example)}"

cat <<'NOTE'
stoma-worker (GPU) provisioning is a P1-4/P1-5 step, not P0-7.

When ready:
  - pick a GPU droplet size:  doctl compute size list | grep gpu
  - use an image with NVIDIA drivers, or extend cloud-init.yaml to install them
  - deploy the worker-colmap/ image, pointed at the same Supabase queue

Deliberately left as a stub to avoid idle GPU spend before the engine is proven.
NOTE
exit 0
