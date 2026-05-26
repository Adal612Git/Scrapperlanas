#!/bin/sh
set -eu

USER_FOLDER="${N8N_USER_FOLDER:-/home/node/.n8n}"
WORKFLOW_DIR="/files/workflows"
RENDER_DIR="/tmp/loto-signal-workflows"
BOOTSTRAP_MARKER="${USER_FOLDER}/.scrapperlanas-bootstrap-v1"
FORCE_BOOTSTRAP="${N8N_BOOTSTRAP_FORCE:-false}"
SCHEDULER_INTERVAL="${N8N_SCHEDULER_INTERVAL_MINUTES:-15}"
IMPORT_WEBHOOK_PATH="${N8N_IMPORT_WEBHOOK_PATH:-loto-signal/import}"
LOTO_SIGNAL_BASE_URL="${LOTO_SIGNAL_BASE_URL:-${SCRAPPERLANAS_BASE_URL:-http://web:8000}}"
CRON_SECRET_VALUE="${CRON_SECRET:-}"

mkdir -p "${USER_FOLDER}"
mkdir -p "${RENDER_DIR}"

escape_sed() {
  printf '%s' "$1" | sed -e 's/[\/&|]/\\&/g'
}

render_workflows() {
  base_url="$(printf '%s' "${LOTO_SIGNAL_BASE_URL}" | sed 's:/*$::')"
  escaped_base_url="$(escape_sed "${base_url}")"
  escaped_cron_secret="$(escape_sed "${CRON_SECRET_VALUE}")"
  escaped_webhook_path="$(escape_sed "${IMPORT_WEBHOOK_PATH}")"

  rm -f "${RENDER_DIR}"/*.json

  for template in "${WORKFLOW_DIR}"/*.json; do
    target="${RENDER_DIR}/$(basename "${template}")"
    sed \
      -e "s|__SCRAPPERLANAS_BASE_URL__|${escaped_base_url}|g" \
      -e "s|__CRON_SECRET__|${escaped_cron_secret}|g" \
      -e "s|__N8N_IMPORT_WEBHOOK_PATH__|${escaped_webhook_path}|g" \
      -e "s|__N8N_SCHEDULER_INTERVAL_MINUTES__|${SCHEDULER_INTERVAL}|g" \
      "${template}" > "${target}"
  done
}

if [ "${FORCE_BOOTSTRAP}" = "true" ] || [ ! -f "${BOOTSTRAP_MARKER}" ]; then
  echo "[loto-signal] Rendering bundled n8n workflows into ${RENDER_DIR}"
  render_workflows
  echo "[loto-signal] Importing bundled n8n workflows from ${RENDER_DIR}"
  n8n import:workflow --separate --input="${RENDER_DIR}"
  n8n update:workflow --id=scrapperlanas-scheduler --active=true
  n8n update:workflow --id=scrapperlanas-import-webhook --active=true
  date -u +"%Y-%m-%dT%H:%M:%SZ" > "${BOOTSTRAP_MARKER}"
else
  echo "[loto-signal] Reusing existing n8n bootstrap state"
fi

exec n8n start
