#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"

echo "==> Backend venv + deps"
cd "$ROOT/backend"
if [[ ! -d .venv ]]; then
  python3.12 -m venv .venv 2>/dev/null || ~/.pyenv/versions/3.12.8/bin/python -m venv .venv
fi
# shellcheck disable=SC1091
source .venv/bin/activate
pip install -q -r requirements.txt
if [[ ! -f .env ]]; then
  cp .env.example .env
  echo "Created backend/.env from .env.example — edit credentials before connecting."
fi

echo "==> Frontend deps"
cd "$ROOT/frontend"
npm install

echo "Done."
echo "Start backend:  cd backend && source .venv/bin/activate && uvicorn app.main:app --reload --port 8000"
echo "Start frontend: cd frontend && npm run dev"
