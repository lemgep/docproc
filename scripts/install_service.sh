#!/usr/bin/env bash
# Install and enable the DocProc systemd service.
#
# Usage (as root):
#   sudo bash /opt/docproc/scripts/install_service.sh
#
# The unit is generated from scripts/docproc.service with the actual project
# directory substituted, so the project does not have to live in /opt/docproc.

set -euo pipefail

if [[ "${EUID}" -ne 0 ]]; then
    echo "Run as root: sudo $0" >&2
    exit 1
fi

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname -- "${SCRIPT_DIR}")"
SERVICE_NAME="docproc"
RUN_USER="docproc"
RUN_GROUP="docproc"
UNIT_SRC="${SCRIPT_DIR}/docproc.service"
UNIT_DST="/etc/systemd/system/${SERVICE_NAME}.service"

command -v python3 >/dev/null 2>&1 || { echo "python3 not found" >&2; exit 1; }
python3 -c "import venv" >/dev/null 2>&1 || {
    echo "python3-venv is required: sudo apt install -y python3-venv" >&2
    exit 1
}

if [[ ! -f "${PROJECT_DIR}/config.ini" ]]; then
    echo "config.ini not found in ${PROJECT_DIR}" >&2
    exit 1
fi

if [[ "${PROJECT_DIR}" != "/opt/docproc" ]]; then
    echo "WARNING: project is not in /opt/docproc." >&2
    echo "         Ensure [app] base_dir in ${PROJECT_DIR}/config.ini points to ${PROJECT_DIR}." >&2
fi

# System user (no login shell).
if ! id "${RUN_USER}" >/dev/null 2>&1; then
    useradd --system --home "${PROJECT_DIR}" --shell /usr/sbin/nologin "${RUN_USER}"
    echo "Created system user ${RUN_USER}"
fi

# Virtualenv + dependencies.
if [[ ! -x "${PROJECT_DIR}/.venv/bin/python" ]]; then
    python3 -m venv "${PROJECT_DIR}/.venv"
    echo "Created virtualenv ${PROJECT_DIR}/.venv"
fi
"${PROJECT_DIR}/.venv/bin/pip" install --upgrade pip
"${PROJECT_DIR}/.venv/bin/pip" install -r "${PROJECT_DIR}/requirements.txt"

# Runtime directories.
mkdir -p "${PROJECT_DIR}"/incoming \
         "${PROJECT_DIR}"/clients \
         "${PROJECT_DIR}"/errors \
         "${PROJECT_DIR}"/logs \
         "${PROJECT_DIR}"/data

# Ownership: chown the project, but never recurse into incoming/ when it is a
# separate mount (e.g. an SMB share), otherwise chown would crawl the whole share.
chown "${RUN_USER}:${RUN_GROUP}" "${PROJECT_DIR}"
for entry in app data logs clients errors examples scripts tests \
             config.ini requirements.txt README.md .venv; do
    if [[ -e "${PROJECT_DIR}/${entry}" ]]; then
        chown -R "${RUN_USER}:${RUN_GROUP}" "${PROJECT_DIR}/${entry}"
    fi
done
chown "${RUN_USER}:${RUN_GROUP}" "${PROJECT_DIR}/incoming"
if ! mountpoint -q "${PROJECT_DIR}/incoming"; then
    chown -R "${RUN_USER}:${RUN_GROUP}" "${PROJECT_DIR}/incoming"
fi

# Generate the unit with real paths.
sed -e "s|/opt/docproc|${PROJECT_DIR}|g" \
    -e "s|^User=.*|User=${RUN_USER}|" \
    -e "s|^Group=.*|Group=${RUN_GROUP}|" \
    "${UNIT_SRC}" > "${UNIT_DST}"
chmod 0644 "${UNIT_DST}"

systemctl daemon-reload
systemctl enable --now "${SERVICE_NAME}"

echo
echo "Service '${SERVICE_NAME}' installed and started."
echo "  status: systemctl status ${SERVICE_NAME}"
echo "  logs  : journalctl -u ${SERVICE_NAME} -f"
echo "  file  : ${PROJECT_DIR}/logs/docproc.log"
