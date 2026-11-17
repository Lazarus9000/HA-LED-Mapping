#!/usr/bin/with-contenv bashio
echo "Starting LED 2D Mapping Web UI..."
exec uvicorn app:app --host 0.0.0.0 --port 8099
