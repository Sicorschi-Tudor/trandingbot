#!/usr/bin/env bash
# Pornește serverul botului (FastAPI + motor + dashboard).
set -euo pipefail
cd "$(dirname "$0")"
if [ ! -d .venv ]; then
  echo "Lipsește .venv — creează-l: python3 -m venv .venv && .venv/bin/pip install -r requirements.txt" >&2
  exit 1
fi
# shellcheck disable=SC1091
source .venv/bin/activate
if [ ! -f .env ]; then
  echo "Lipsește .env — copiază .env.example în .env și completează valorile." >&2
  exit 1
fi
HOST="$(python -c 'from app.config import get_settings as g; print(g().HOST)')"
PORT="$(python -c 'from app.config import get_settings as g; print(g().PORT)')"
echo "Pornesc pe http://${HOST}:${PORT}"
exec uvicorn app.main:app --host "$HOST" --port "$PORT"
