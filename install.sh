#!/usr/bin/env bash
#
# install.sh — one-liner installer for the TrueNAS MCP server.
#
# Usage:
#   curl -fsSL https://raw.githubusercontent.com/a-simple-test-organization/truenas-mcp/support/25.04.2.6/install.sh | sudo bash
#
# The script auto-detects the TrueNAS version on the host, maps it to the
# correct git branch/tag, installs the package into a virtualenv, and starts
# a systemd unit. Safe to re-run (idempotent).
#
# Environment (defaults shown; existing values are NOT overwritten):
#   TNS_REF    - git ref to install (auto-detected when unset)
#   TNS_REPO   - git repository URL (default: https://github.com/a-simple-test-organization/truenas-mcp.git)
#   TNS_VENV   - virtualenv path (default: /root/mcp; TrueNAS OS is read-only,
#                so it must live on a writable, persistent location)
#   MCP_PORT   - HTTP listen port (default: 38888)
#   MCP_TOKEN  - Bearer token. If unset, an existing token is reused from the
#                installed unit when present; otherwise a new one is generated
#                and printed at the end of the install.

set -euo pipefail

# ---------------------------------------------------------------------------
# Defaults (only set when the caller has not already exported them)
# ---------------------------------------------------------------------------
TNS_REPO="${TNS_REPO:-https://github.com/a-simple-test-organization/truenas-mcp.git}"
TNS_VENV="${TNS_VENV:-/root/mcp}"
MCP_PORT="${MCP_PORT:-38888}"
MCP_TOKEN="${MCP_TOKEN:-}"

UNIT_NAME="truenas-mcp.service"
UNIT_SRC_DIR="deploy"
UNIT_DST="/etc/systemd/system/${UNIT_NAME}"

say() { printf '\n\033[1;34m==>\033[0m %s\n' "$*"; }
fail() { printf '\n\033[1;31mERROR:\033[0m %s\n' "$*" >&2; exit 1; }

# ---------------------------------------------------------------------------
# Root check
# ---------------------------------------------------------------------------
if [[ "${EUID}" -ne 0 ]]; then
    fail "This installer must run as root (it writes to ${UNIT_DST} and manages systemd)." \
         "Re-run with:  sudo bash install.sh"
fi

say "TrueNAS MCP installer"

# ---------------------------------------------------------------------------
# 1. Detect TrueNAS version: midclt -> /etc/version -> /etc/os-release
# ---------------------------------------------------------------------------
detect_truenas_version() {
    local raw=""

    # 1) midclt call system.version  -> JSON like {"version": "TrueNAS-SCALE-25.04.2.6"}
    if command -v midclt >/dev/null 2>&1; then
        if raw="$(midclt call system.version 2>/dev/null)"; then
            local parsed
            parsed="$(printf '%s' "$raw" | sed -n 's/.*"version"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p')"
            if [[ -n "${parsed}" ]]; then
                printf '%s' "${parsed}"
                return 0
            fi
            # Some versions may return a bare string rather than JSON.
            if [[ "${raw}" =~ TrueNAS ]]; then
                printf '%s' "${raw}"
                return 0
            fi
        fi
        say "midclt present but returned no usable version; falling back."
    fi

    # 2) /etc/version  -> e.g. "TrueNAS-SCALE-25.04.2.6"
    if [[ -f /etc/version ]]; then
        raw="$(tr -d '\n' < /etc/version | tr -d '[:space:]')"
        if [[ -n "${raw}" ]]; then
            printf '%s' "${raw}"
            return 0
        fi
    fi

    # 3) /etc/os-release  -> VERSION/VERSION_ID/PRETTY_NAME
    if [[ -f /etc/os-release ]]; then
        # shellcheck disable=SC1091
        . /etc/os-release
        for candidate in "${VERSION:-}" "${VERSION_ID:-}" "${PRETTY_NAME:-}"; do
            if [[ -n "${candidate}" ]]; then
                printf '%s' "${candidate}"
                return 0
            fi
        done
    fi

    return 1
}

say "Detecting TrueNAS version"
VERSION_RAW="$(detect_truenas_version)" || {
    fail "Could not determine the TrueNAS version." \
         "Tried: midclt call system.version, /etc/version, /etc/os-release." \
         "Set TNS_REF manually to the correct branch/tag and re-run."
}

say "Detected version string: ${VERSION_RAW}"

# Extract the numeric version, e.g. "25.04.2.6" from "TrueNAS-SCALE-25.04.2.6".
extract_numeric_version() {
    printf '%s' "$1" \
        | grep -Eo '[0-9]+\.[0-9]+(\.[0-9]+)*' \
        | head -n 1
}

VERSION_NUM="$(extract_numeric_version "${VERSION_RAW}")"
if [[ -z "${VERSION_NUM}" ]]; then
    fail "Could not parse a numeric version from '${VERSION_RAW}'." \
         "Set TNS_REF manually to the correct branch/tag and re-run."
fi

# ---------------------------------------------------------------------------
# 2. Map version -> git ref
# ---------------------------------------------------------------------------
# Override always wins.
if [[ -n "${TNS_REF:-}" ]]; then
    say "Using TNS_REF override: ${TNS_REF}"
else
    # shellcheck disable=SC2034
    IFS='.' read -r MAJOR MINOR PATCH REST <<< "${VERSION_NUM}"
    case "${MAJOR}.${MINOR}" in
        25.04)
            # 25.04.2.x (patch >= 2) targets the support branch; earlier
            # 25.04.0 / 25.04.1 releases use the plain docker-variant branch.
            if [[ -n "${PATCH:-}" && "${PATCH}" -ge 2 ]]; then
                TNS_REF="support/25.04.2.6"
            else
                TNS_REF="docker-variant"
            fi
            ;;
        23.*|24.*)
            TNS_REF="master"
            ;;
        *)
            fail "Unsupported TrueNAS version '${VERSION_NUM}'." \
                 "Supported branches: support/25.04.2.6 (25.04.2.x), docker-variant (25.04.0/25.04.1), master (23.x/24.x)." \
                 "Override with: TNS_REF=<branch|tag> sudo bash install.sh"
            ;;
    esac
    say "Mapped ${VERSION_NUM} -> ref '${TNS_REF}'"
fi

# ---------------------------------------------------------------------------
# 3. Create virtualenv if missing
# ---------------------------------------------------------------------------
if [[ ! -x "${TNS_VENV}/bin/python" ]]; then
    # TrueNAS SCALE mounts the OS (/, /usr, /opt) read-only, so the venv must
    # live on a writable, persistent location. Fail early with a clear message
    # instead of a cryptic venv error.
    VENV_PARENT="$(dirname "${TNS_VENV}")"
    if [[ ! -d "${VENV_PARENT}" ]]; then
        fail "Parent directory '${VENV_PARENT}' does not exist." \
             "Set TNS_VENV to a writable, persistent path (e.g. /root/mcp or a dataset under /mnt)."
    fi
    if [[ ! -w "${VENV_PARENT}" ]]; then
        fail "Parent directory '${VENV_PARENT}' is read-only (TrueNAS OS filesystem is read-only)." \
             "Set TNS_VENV to a writable, persistent path (e.g. /root/mcp or a dataset under /mnt)."
    fi
    say "Creating virtualenv at ${TNS_VENV}"
    python3 -m venv "${TNS_VENV}" \
        || fail "Failed to create virtualenv at ${TNS_VENV}. Is python3-venv installed?"
else
    say "Virtualenv already exists at ${TNS_VENV}"
fi

PYTHON_BIN="${TNS_VENV}/bin/python"

# ---------------------------------------------------------------------------
# 4. Install / upgrade the package from git
# ---------------------------------------------------------------------------
say "Installing truenas-mcp from ${TNS_REPO}@${TNS_REF}"
"${PYTHON_BIN}" -m pip install --upgrade "git+${TNS_REPO}@${TNS_REF}" \
    || fail "pip install failed for ${TNS_REPO}@${TNS_REF}"

# ---------------------------------------------------------------------------
# 4.5 Auth token: reuse an existing one, else auto-generate.
#     Only generate a fresh token when none is provided AND there is no
#     existing install, so a re-run never rotates the token out from under
#     already-connected clients.
# ---------------------------------------------------------------------------
if [[ -z "${MCP_TOKEN}" ]]; then
    if [[ -f "${UNIT_DST}" ]]; then
        MCP_TOKEN="$(sed -n 's/^Environment=MCP_TOKEN=//p' "${UNIT_DST}" | head -n 1)"
        if [[ -n "${MCP_TOKEN}" ]]; then
            say "Reusing existing auth token from ${UNIT_DST}"
        fi
    fi
    if [[ -z "${MCP_TOKEN}" ]]; then
        if command -v openssl >/dev/null 2>&1; then
            MCP_TOKEN="$(openssl rand -hex 32)"
        else
            MCP_TOKEN="$(od -An -N32 -tx1 /dev/urandom | tr -d ' \n')"
        fi
        say "No token provided and no existing install — generated a new auth token."
    fi
fi

# ---------------------------------------------------------------------------
# 5. Install the systemd unit
# ---------------------------------------------------------------------------
if [[ -f "${UNIT_DST}" ]]; then
    BACKUP="${UNIT_DST}.bak.$(date +%Y%m%d%H%M%S)"
    say "Existing unit found; backing up to ${BACKUP}"
    cp -p "${UNIT_DST}" "${BACKUP}"
fi

# Prefer the real template when available (running from a git checkout or a
# copy alongside the script); otherwise fall back to the embedded copy below so
# the `curl | bash` one-liner works without a checkout.
UNIT_SRC=""
for candidate in \
    "./${UNIT_SRC_DIR}/${UNIT_NAME}" \
    "$(dirname "$0")/${UNIT_SRC_DIR}/${UNIT_NAME}" \
    "${TNS_VENV}/deploy/${UNIT_NAME}" \
    "${TNS_VENV}/share/deploy/${UNIT_NAME}"; do
    if [[ -f "${candidate}" ]]; then
        UNIT_SRC="${candidate}"
        break
    fi
done

if [[ -n "${UNIT_SRC}" ]]; then
    say "Installing systemd unit from ${UNIT_SRC} -> ${UNIT_DST}"
    cp "${UNIT_SRC}" "${UNIT_DST}"
else
    say "No ${UNIT_NAME} template found; using embedded unit."
fi

# ---------------------------------------------------------------------------
# 6. Substitute Environment= values (MCP_PORT, MCP_TOKEN, python path)
# ---------------------------------------------------------------------------
# If we copied a template, rewrite the dynamic lines in place; if not, write a
# full unit from the embedded content. Keep this content in sync with
# deploy/truenas-mcp.service.
apply_env() {
    if [[ -n "${UNIT_SRC}" ]]; then
        sed -i \
            -e "s|^ExecStart=.*|ExecStart=${PYTHON_BIN} -m truenas_mcp.server|" \
            -e "s|^Environment=MCP_PORT=.*|Environment=MCP_PORT=${MCP_PORT}|" \
            -e "s|^Environment=MCP_TOKEN=.*|Environment=MCP_TOKEN=${MCP_TOKEN}|" \
            "${UNIT_DST}"
        # Ensure MCP_PORT / MCP_TOKEN lines exist even if the template lacked them.
        if ! grep -q '^Environment=MCP_PORT=' "${UNIT_DST}"; then
            sed -i "/^\[Service\]/a Environment=MCP_PORT=${MCP_PORT}" "${UNIT_DST}"
        fi
        if ! grep -q '^Environment=MCP_TOKEN=' "${UNIT_DST}"; then
            sed -i "/^\[Service\]/a Environment=MCP_TOKEN=${MCP_TOKEN}" "${UNIT_DST}"
        fi
    else
        cat > "${UNIT_DST}" <<EOF
[Unit]
Description=TrueNAS MCP Server — read-only Docker & TrueNAS API debug tools (TrueNAS SCALE 25.04.2.6 / Debian 13)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
ExecStart=${PYTHON_BIN} -m truenas_mcp.server
Environment=PYTHONUNBUFFERED=1
Environment=MCP_HOST=0.0.0.0
Environment=MCP_PORT=${MCP_PORT}
Environment=MCP_PATH=/mcp
Environment=MCP_TOKEN=${MCP_TOKEN}
Restart=always
RestartSec=5
StandardOutput=journal
StandardError=journal
SyslogIdentifier=truenas-mcp

# Security: restrict but allow docker/midclt
NoNewPrivileges=yes
ProtectKernelTunables=yes
ProtectKernelModules=yes
ProtectControlGroups=yes
PrivateTmp=yes

[Install]
WantedBy=multi-user.target
EOF
    fi
}
apply_env

if [[ -z "${MCP_TOKEN}" ]]; then
    say "WARNING: MCP_TOKEN is empty — authentication will be DISABLED."
    say "         Set MCP_TOKEN to enable Bearer token auth."
fi

# ---------------------------------------------------------------------------
# 7. Reload, enable, start
# ---------------------------------------------------------------------------
say "Reloading systemd and enabling ${UNIT_NAME}"
systemctl daemon-reload
systemctl enable --now "${UNIT_NAME}"

ACTIVE_STATE="$(systemctl is-active "${UNIT_NAME}" || true)"
say "Service state: ${ACTIVE_STATE}"

# ---------------------------------------------------------------------------
# 8. Summary
# ---------------------------------------------------------------------------
HOST_IP="$(hostname -I 2>/dev/null | awk '{print $1}')"
[[ -z "${HOST_IP}" ]] && HOST_IP="<host>"

cat <<EOF

==========================================================
 TrueNAS MCP server installed.

   Version detected : ${VERSION_RAW} (${VERSION_NUM})
   Git ref          : ${TNS_REF}
   Virtualenv       : ${TNS_VENV}
   Service          : ${UNIT_NAME} (state: ${ACTIVE_STATE})
   Health URL       : http://${HOST_IP}:${MCP_PORT}/health
   MCP endpoint     : http://${HOST_IP}:${MCP_PORT}/mcp
   Auth             : $([ -n "${MCP_TOKEN}" ] && echo "enabled (MCP_TOKEN set)" || echo "DISABLED (MCP_TOKEN empty)")
 Useful commands:
   journalctl -u ${UNIT_NAME} -f
   systemctl status ${UNIT_NAME}
   curl http://localhost:${MCP_PORT}/health
==========================================================
EOF

if [[ "${ACTIVE_STATE}" != "active" ]]; then
    say "Service did not reach 'active' state; check logs with: journalctl -u ${UNIT_NAME} -n 50"
    exit 1
fi

# ---------------------------------------------------------------------------
# 9. Final token disclosure
# ---------------------------------------------------------------------------
if [[ -n "${MCP_TOKEN}" ]]; then
    echo ""
    echo "Your MCP access token (Bearer) — save it now:"
    echo "  ${MCP_TOKEN}"
    echo ""
    echo "It is not stored anywhere by this installer except inside the systemd"
    echo "unit (${UNIT_DST})."
fi

say "Done."
