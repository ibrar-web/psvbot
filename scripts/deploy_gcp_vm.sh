#!/usr/bin/env bash
set -euo pipefail

# ==========================
# Config (can be overridden by env)
# ==========================
APP_DIR="${APP_DIR:?APP_DIR is required}"
SERVICE_NAME="${SERVICE_NAME:?SERVICE_NAME is required}"
APP_MODULE="${APP_MODULE:-main:app}"
APP_HOST="${APP_HOST:-127.0.0.1}"
APP_PORT="${APP_PORT:-8000}"
VENV_DIR="${VENV_DIR:-$APP_DIR/venv}"
SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}.service"
HEALTHCHECK_HOST="${HEALTHCHECK_HOST:-127.0.0.1}"
HEALTHCHECK_URL="${HEALTHCHECK_URL:-http://${HEALTHCHECK_HOST}:${APP_PORT}/health}"
EXPECTED_BUILD_SHA="${EXPECTED_BUILD_SHA:-}"
LOG_LINES="${LOG_LINES:-100}"

run_as_root() {
    if [ "$(id -u)" -eq 0 ]; then
        "$@"
    else
        sudo -n "$@"
    fi
}

print_failure_logs() {
    echo "----- systemctl status ${SERVICE_NAME} -----"
    run_as_root systemctl --no-pager --full status "${SERVICE_NAME}" || true
    echo "----- journalctl ${SERVICE_NAME} -----"
    run_as_root journalctl -u "${SERVICE_NAME}" -n "${LOG_LINES}" --no-pager || true
}

wait_for_healthcheck() {
    local attempts=15
    local sleep_seconds=2
    local response=""
    for _ in $(seq 1 "${attempts}"); do
        response="$(curl -fsS "${HEALTHCHECK_URL}" 2>/dev/null || true)"
        if [ -n "${response}" ]; then
            if [ -z "${EXPECTED_BUILD_SHA}" ] || printf '%s' "${response}" | grep -F "\"build_sha\":\"${EXPECTED_BUILD_SHA}\"" >/dev/null 2>&1; then
                echo "Health check passed: ${HEALTHCHECK_URL}"
                return 0
            fi
        fi
        sleep "${sleep_seconds}"
    done
    echo "Health check failed: ${HEALTHCHECK_URL}"
    if [ -n "${response}" ]; then
        echo "Last health response: ${response}"
    fi
    return 1
}

stop_port_processes() {
    local pids=""

    if command -v lsof >/dev/null 2>&1; then
        pids="$(lsof -tiTCP:${APP_PORT} -sTCP:LISTEN 2>/dev/null || true)"
    elif command -v fuser >/dev/null 2>&1; then
        pids="$(fuser "${APP_PORT}/tcp" 2>/dev/null || true)"
    fi

    if [ -n "${pids}" ]; then
        echo "Stopping existing process(es) on port ${APP_PORT}: ${pids}"
        for pid in ${pids}; do
            run_as_root kill -9 "${pid}" >/dev/null 2>&1 || true
        done
    fi
}

setup_venv() {
    if [ ! -d "${VENV_DIR}" ]; then
        python3 -m venv "${VENV_DIR}"
    fi
    . "${VENV_DIR}/bin/activate"
    python -m pip install --upgrade pip
    python -m pip install -r "${APP_DIR}/requirements.txt"

    if [ ! -x "${VENV_DIR}/bin/uvicorn" ]; then
        echo "uvicorn is not installed in ${VENV_DIR}"
        exit 1
    fi
}

deploy_systemd() {
    TMP_SERVICE_FILE="$(mktemp)"
    cat > "${TMP_SERVICE_FILE}" <<EOF
[Unit]
Description=${SERVICE_NAME} FastAPI service
After=network.target

[Service]
Type=simple
User=$(id -un)
WorkingDirectory=${APP_DIR}
Environment=PATH=${VENV_DIR}/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
EnvironmentFile=-${APP_DIR}/.env
ExecStart=${VENV_DIR}/bin/uvicorn ${APP_MODULE} --host ${APP_HOST} --port ${APP_PORT}
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

    run_as_root install -m 0644 "${TMP_SERVICE_FILE}" "${SERVICE_FILE}"
    rm -f "${TMP_SERVICE_FILE}"

    echo "Stopping old service if running..."
    run_as_root systemctl stop "${SERVICE_NAME}" || true
    stop_port_processes
    run_as_root systemctl daemon-reload
    run_as_root systemctl enable "${SERVICE_NAME}"
    run_as_root systemctl start "${SERVICE_NAME}"

    if ! run_as_root systemctl is-active --quiet "${SERVICE_NAME}"; then
        echo "Service failed to start!"
        print_failure_logs
        exit 1
    fi

    if ! wait_for_healthcheck; then
        print_failure_logs
        exit 1
    fi

    echo "Deployment completed successfully."
}

main() {
    if [ ! -d "${APP_DIR}" ]; then
        echo "App directory not found: ${APP_DIR}"
        exit 1
    fi

    if [ ! -f "${APP_DIR}/main.py" ]; then
        echo "main.py not found in ${APP_DIR}"
        exit 1
    fi

    if [ ! -f "${APP_DIR}/requirements.txt" ]; then
        echo "requirements.txt not found in ${APP_DIR}"
        exit 1
    fi

    cd "${APP_DIR}"

    if [ -n "${APP_ENV_FILE:-}" ]; then
        printf '%s' "${APP_ENV_FILE}" > "${APP_DIR}/.env"
        chmod 600 "${APP_DIR}/.env"
    fi

    if [ -n "${EXPECTED_BUILD_SHA}" ]; then
        if [ -f "${APP_DIR}/.env" ]; then
            grep -v '^APP_BUILD_SHA=' "${APP_DIR}/.env" > "${APP_DIR}/.env.tmp" || true
            mv "${APP_DIR}/.env.tmp" "${APP_DIR}/.env"
        fi
        printf '\nAPP_BUILD_SHA=%s\n' "${EXPECTED_BUILD_SHA}" >> "${APP_DIR}/.env"
        chmod 600 "${APP_DIR}/.env"
    fi

    find "${APP_DIR}" -type d -name __pycache__ -prune -exec rm -rf {} +
    find "${APP_DIR}" -type f \( -name '*.pyc' -o -name '*.pyo' \) -delete

    setup_venv
    deploy_systemd
}

trap 'print_failure_logs' ERR
main "$@"
