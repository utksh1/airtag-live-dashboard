#!/bin/sh
set -e

echo "=== Initializing AirTag Live Dashboard ==="

# Restore anisette machine provisioning if provided
mkdir -p /root/.config/anisette-v3
if [ -n "$ANISETTE_ADI_B64" ]; then
    echo "Restoring anisette adi.pb..."
    echo "$ANISETTE_ADI_B64" | base64 -d > /root/.config/anisette-v3/adi.pb
fi
if [ -n "$ANISETTE_DEVICE_B64" ]; then
    echo "Restoring anisette device.json..."
    echo "$ANISETTE_DEVICE_B64" | base64 -d > /root/.config/anisette-v3/device.json
fi

# Decode secrets if passed via environment variables
if [ -n "$ACCOUNT_JSON_B64" ]; then
    echo "Writing account.json from environment variable..."
    echo "$ACCOUNT_JSON_B64" | base64 -d > /app/account.json
fi

if [ -n "$TAG_JSON_B64" ]; then
    echo "Writing mytag.json from environment variable..."
    echo "$TAG_JSON_B64" | base64 -d > /app/mytag.json
fi

# Support additional tag files passed as <NAME>_TAG_B64
for var in $(env | grep -E '^[A-Za-z0-9_]+_TAG_B64=' | cut -d= -f1); do
    filename=$(echo "$var" | tr '[:upper:]' '[:lower:]' | sed 's/_b64//').json
    eval val=\$$var
    echo "Writing $filename from $var..."
    echo "$val" | base64 -d > "/app/$filename"
done

# Start anisette server in background
echo "Starting anisette-v3-server on 127.0.0.1:6969..."
mkdir -p /root/.config/anisette-v3
/opt/anisette-v3-server --host 127.0.0.1 --port 6969 --adi-path /root/.config/anisette-v3 &

echo "Waiting for anisette-v3-server to initialize..."
MAX_TRIES=30
COUNT=0
while ! curl -s http://127.0.0.1:6969 > /dev/null; do
    sleep 1
    COUNT=$((COUNT + 1))
    if [ "$COUNT" -ge "$MAX_TRIES" ]; then
        echo "Warning: Anisette server took too long to start, proceeding anyway..."
        break
    fi
done
echo "Anisette server is active."

# Ensure PORT is defined (Render provides $PORT, default to 10000)
export PORT="${PORT:-10000}"
export ANISETTE_URL="http://127.0.0.1:6969"

echo "Starting Live Map on port $PORT..."
exec python3 live_map.py
