#!/usr/bin/env bash
# Create the stoma-api droplet (Docker host for backend/). Idempotent-ish: bails
# if a droplet named stoma-api already exists. Costs money — run deliberately.
set -euo pipefail

: "${DIGITALOCEAN_ACCESS_TOKEN:?set DIGITALOCEAN_ACCESS_TOKEN (see .env.example)}"
: "${SSH_KEY_FINGERPRINT:?set SSH_KEY_FINGERPRINT (a key uploaded to DigitalOcean)}"

NAME="stoma-api"
REGION="${DO_REGION:-nyc3}"
SIZE="${DO_API_SIZE:-s-2vcpu-4gb}"
IMAGE="${DO_IMAGE:-ubuntu-24-04-x64}"
HERE="$(cd "$(dirname "$0")" && pwd)"

if doctl compute droplet list --format Name --no-header | grep -qx "$NAME"; then
  echo "Droplet '$NAME' already exists — nothing to do."
  doctl compute droplet get "$NAME" --format Name,PublicIPv4,Status
  exit 0
fi

echo "Creating droplet '$NAME' ($SIZE, $REGION)…"
doctl compute droplet create "$NAME" \
  --region "$REGION" \
  --size "$SIZE" \
  --image "$IMAGE" \
  --ssh-keys "$SSH_KEY_FINGERPRINT" \
  --tag-name "$NAME" \
  --user-data-file "$HERE/cloud-init.yaml" \
  --wait \
  --format Name,PublicIPv4,Status

echo "Done. Deploy backend/ with docker on the printed IP (see P1-1 Dockerfile)."
