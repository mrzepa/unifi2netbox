#!/usr/bin/env bash
# --------------------------------------------------------------------------
# unifi2netbox — Proxmox LXC helper
#
# Creates a Debian 12 LXC container on Proxmox and runs the installer.
# Run on the Proxmox host (not inside a container).
#
# Usage:
#   bash lxc/create-lxc.sh [CTID]
#
# Defaults:
#   CTID        = next available (or pass as argument)
#   hostname    = unifi2netbox
#   storage     = local-lvm
#   template    = debian-12-standard (downloaded if missing)
# --------------------------------------------------------------------------
set -euo pipefail

CTID="${1:-$(pvesh get /cluster/nextid)}"
HOSTNAME="unifi2netbox"
STORAGE="local-lvm"
TEMPLATE="debian-12-standard_12.7-1_amd64.tar.zst"
TEMPLATE_STORAGE="local"
MEMORY=512
SWAP=256
CORES=2
DISK_SIZE="4"
BRIDGE="vmbr0"

echo "==> Creating LXC container ${CTID} (${HOSTNAME}) ..."

# Download template if not present
if ! pveam list "${TEMPLATE_STORAGE}" | grep -q "${TEMPLATE}"; then
    echo "==> Downloading Debian 12 template ..."
    pveam download "${TEMPLATE_STORAGE}" "${TEMPLATE}"
fi

# Create container
pct create "${CTID}" "${TEMPLATE_STORAGE}:vztmpl/${TEMPLATE}" \
    --hostname "${HOSTNAME}" \
    --storage "${STORAGE}" \
    --rootfs "${STORAGE}:${DISK_SIZE}" \
    --memory "${MEMORY}" \
    --swap "${SWAP}" \
    --cores "${CORES}" \
    --net0 "name=eth0,bridge=${BRIDGE},ip=dhcp" \
    --unprivileged 1 \
    --features nesting=1 \
    --start 1

echo "==> Waiting for container to boot ..."
sleep 5

# Push installer into the container and run it
pct push "${CTID}" "lxc/install.sh" "/tmp/install.sh"
pct exec "${CTID}" -- bash -c "
    bash /tmp/install.sh
"

echo ""
echo "============================================="
echo "  LXC container ${CTID} (${HOSTNAME}) ready"
echo "============================================="
echo ""
echo "  Enter container:  pct enter ${CTID}"
echo "  Edit config:      nano /opt/unifi2netbox/.env"
echo "  Start service:    systemctl start unifi2netbox"
echo "  View logs:        journalctl -u unifi2netbox -f"
echo ""
