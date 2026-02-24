#!/usr/bin/env bash
# --------------------------------------------------------------------------
# unifi2netbox — LXC / bare-metal installer
#
# Installs unifi2netbox as a systemd service on Debian/Ubuntu.
# Run as root inside a fresh LXC container or on a VM.
#
# Usage:
#   curl -sSL <raw-url>/lxc/install.sh | bash
#   # or
#   bash lxc/install.sh
# --------------------------------------------------------------------------
set -euo pipefail

APP_DIR="/opt/unifi2netbox"
APP_USER="unifi2netbox"
REPO_URL="https://github.com/patricklind/unifi2netbox.git"
BRANCH="main"
VENV_DIR="${APP_DIR}/venv"

# --------------------------------------------------------------------------
# 1. System dependencies
# --------------------------------------------------------------------------
echo "==> Installing system packages ..."
apt-get update -qq
apt-get install -y --no-install-recommends \
    python3 python3-venv python3-pip \
    git iputils-ping ca-certificates

# --------------------------------------------------------------------------
# 2. Create service user
# --------------------------------------------------------------------------
if ! id -u "${APP_USER}" &>/dev/null; then
    echo "==> Creating user ${APP_USER} ..."
    useradd --system --home-dir "${APP_DIR}" --shell /usr/sbin/nologin "${APP_USER}"
fi

# --------------------------------------------------------------------------
# 3. Clone or update repo
# --------------------------------------------------------------------------
if [ -d "${APP_DIR}/.git" ]; then
    echo "==> Updating existing installation ..."
    git -C "${APP_DIR}" pull --ff-only origin "${BRANCH}"
else
    echo "==> Cloning repository ..."
    git clone --branch "${BRANCH}" --depth 1 "${REPO_URL}" "${APP_DIR}"
fi

# --------------------------------------------------------------------------
# 4. Python virtual environment
# --------------------------------------------------------------------------
echo "==> Setting up Python venv ..."
python3 -m venv "${VENV_DIR}"
"${VENV_DIR}/bin/pip" install --upgrade pip -q
"${VENV_DIR}/bin/pip" install --no-cache-dir -r "${APP_DIR}/requirements.txt" -q

# --------------------------------------------------------------------------
# 5. Directory structure and permissions
# --------------------------------------------------------------------------
mkdir -p "${APP_DIR}/logs"

# Create .env from example if it does not exist yet
if [ ! -f "${APP_DIR}/.env" ]; then
    cp "${APP_DIR}/.env.example" "${APP_DIR}/.env"
    echo "==> Created ${APP_DIR}/.env — edit it with your credentials before starting."
fi

chown -R "${APP_USER}:${APP_USER}" "${APP_DIR}"

# --------------------------------------------------------------------------
# 6. systemd service
# --------------------------------------------------------------------------
echo "==> Installing systemd service ..."
cp "${APP_DIR}/lxc/unifi2netbox.service" /etc/systemd/system/unifi2netbox.service
systemctl daemon-reload
systemctl enable unifi2netbox.service

echo ""
echo "============================================="
echo "  unifi2netbox installed to ${APP_DIR}"
echo "============================================="
echo ""
echo "Next steps:"
echo "  1. Edit ${APP_DIR}/.env with your credentials"
echo "  2. Start:  systemctl start unifi2netbox"
echo "  3. Logs:   journalctl -u unifi2netbox -f"
echo ""
