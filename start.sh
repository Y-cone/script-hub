#!/bin/bash
set -e
cd "$(dirname "$0")"

UV="/home/yy/.hermes/bin/uv"

echo "Starting ScriptHub..."
echo "Backend:  http://localhost:8001"
echo "Frontend: http://localhost:5173"
echo "API Docs: http://localhost:8001/docs"
echo ""

# Backend
cd backend
$UV run uvicorn app.main:app --reload --port 8001 --host 0.0.0.0 &
BACKEND_PID=$!
cd ..

# Wait for backend to be ready
for i in $(seq 1 20); do
  curl -sf http://localhost:8001/api/health >/dev/null 2>&1 && break
  sleep 0.5
done

# Frontend
cd frontend
npm run dev -- --host 0.0.0.0 &
FRONTEND_PID=$!
cd ..

echo ""
echo "Both services started. Press Ctrl+C to stop."
wait $BACKEND_PID $FRONTEND_PID
