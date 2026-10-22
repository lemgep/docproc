#!/usr/bin/env bash
# Stop and remove the DocProc systemd service.
#
# Usage (as root):
#   sudo bash /opt/docproc/scripts/uninstall_service.sh
#   sudo bash /opt/docproc/scripts/uninstall_service.sh --remove-user
#
# Runtime data (data/, logs/, clients/, errors/, incoming/) is kept.

set -euo pipefail

if [[ "${EUID}" -ne 0 ]]; then
    echo "Run as root: sudo $0" >&2
    exit 1
fi

SERVICE_NAME="docproc"
UNIT_DST="/etc/systemd/system/${SERVICE_NAME}.service"

systemctl stop "${SERVICE_NAME}" 2>/dev/null || true
systemctl disable "${SERVICE_NAME}" 2>/dev/null || true
rm -f "${UNIT_DST}"
systemctl daemon-reload
systemctl reset-failed "${SERVICE_NAME}" 2>/dev/null || true

echo "Service '${SERVICE_NAME}' removed. Project data was kept."

if [[ "${1:-}" == "--remove-user" ]]; then
    if id docproc >/dev/null 2>&1; then
        userdel docproc 2>/dev/null || true
        echo "User 'docproc' removed."
    fi
fi
