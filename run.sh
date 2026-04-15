#!/bin/bash
set -euo pipefail

# GCP Cloud Run deployment — commented out
# Triggered manually by running: bash run.sh deploy
# if [[ "${1:-}" == "deploy" ]]; then
#   gcloud run deploy psvbot \
#     --source . \
#     --region us-central1 \
#     --allow-unauthenticated \
#     --memory 2Gi \
#     --cpu 1 \
#     --timeout 900
#   exit 0
# fi

if [[ ! -x ".venv/bin/python" ]]; then
  echo "Missing .venv. Create it first with: python3 -m venv .venv && . .venv/bin/activate && pip install -r requirements.txt" >&2
  exit 1
fi


source .venv/bin/activate
exec .venv/bin/python -m uvicorn main:app --host 0.0.0.0 --port "${PORT:-8001}" --reload


# sudo systemctl daemon-reload
# sudo systemctl restart psvbot
