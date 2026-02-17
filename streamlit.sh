#!/usr/bin/env bash
set -e

# Run from repository root (helps when startup context differs)
cd "$(dirname "$0")" || true

PORT="${PORT:-8000}"

echo "[startup] Streamlit starting on port ${PORT} — $(date)"

if [ -f app.py ]; then
    exec python -m streamlit run app.py --server.port "$PORT" --server.address 0.0.0.0 --server.headless true --browser.gatherUsageStats false --logger.level info
elif [ -f agentic_ai.py ]; then
    exec python -m streamlit run agentic_ai.py --server.port "$PORT" --server.address 0.0.0.0 --server.headless true --browser.gatherUsageStats false --logger.level info
else
    echo "Error: Neither app.py nor agentic_ai.py exists."
    ls -la
    exit 1
fi