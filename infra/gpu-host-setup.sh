#!/usr/bin/env bash
#
# One-time setup of a DigitalOcean GPU droplet as the reconstruction worker host.
# Run ON the droplet as root (Ubuntu 22.04/24.04 GPU image with the NVIDIA driver
# preinstalled — DO's "GPU" images ship it; `nvidia-smi` must already work).
#
#   ssh root@<gpu-host> 'bash -s' < infra/gpu-host-setup.sh
#
# Then from your machine:  WORKER_HOST=<gpu-host> WORKER_DOCKERFILE=Dockerfile ./infra/deploy-worker.sh
set -euo pipefail

echo "▶ driver check"
nvidia-smi --query-gpu=name,driver_version,memory.total --format=csv,noheader

echo "▶ docker ce"
if ! command -v docker >/dev/null 2>&1; then
  apt-get update
  apt-get install -y --no-install-recommends ca-certificates curl gnupg
  install -m 0755 -d /etc/apt/keyrings
  curl -fsSL https://download.docker.com/linux/ubuntu/gpg | gpg --dearmor -o /etc/apt/keyrings/docker.gpg
  . /etc/os-release
  echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu ${VERSION_CODENAME} stable" \
    > /etc/apt/sources.list.d/docker.list
  apt-get update
  apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin
fi

echo "▶ nvidia container toolkit"
if ! dpkg -s nvidia-container-toolkit >/dev/null 2>&1; then
  curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey | gpg --dearmor -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg
  curl -fsSL https://nvidia.github.io/libnvidia-container/stable/deb/nvidia-container-toolkit.list \
    | sed 's#deb https://#deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://#g' \
    > /etc/apt/sources.list.d/nvidia-container-toolkit.list
  apt-get update
  apt-get install -y nvidia-container-toolkit
  nvidia-ctk runtime configure --runtime=docker
  systemctl restart docker
fi

echo "▶ GPU visible inside a container?"
docker run --rm --gpus all nvidia/cuda:12.4.1-base-ubuntu22.04 nvidia-smi --query-gpu=name --format=csv,noheader

echo "▶ housekeeping"
# the worker is outbound-only (polls Supabase); nothing listens
if command -v ufw >/dev/null 2>&1; then ufw allow OpenSSH >/dev/null; ufw --force enable >/dev/null; fi
# unattended-upgrades triggers systemd reloads that strip GPU access from running
# containers (cgroup v2 + nvidia-container-toolkit); off during the test campaign
systemctl disable --now unattended-upgrades >/dev/null 2>&1 || true
# don't let unattended upgrades reboot mid-test
sed -i 's/^Unattended-Upgrade::Automatic-Reboot .*/Unattended-Upgrade::Automatic-Reboot "false";/' /etc/apt/apt.conf.d/50unattended-upgrades 2>/dev/null || true
mkdir -p /opt/stoma
echo "✔ host ready — now run infra/deploy-worker.sh with WORKER_HOST=$(hostname -I | awk '{print $1}') WORKER_DOCKERFILE=Dockerfile"
