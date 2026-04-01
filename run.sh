#!/bin/bash
set -euo pipefail

if [[ "${1:-}" == "deploy" ]]; then
  gcloud run deploy psvbot \
    --source . \
    --region us-central1 \
    --allow-unauthenticated \
    --memory 2Gi \
    --cpu 1 \
    --timeout 900
  exit 0
fi

uvicorn main:app --host 0.0.0.0 --port "${PORT:-8001}" --reload
