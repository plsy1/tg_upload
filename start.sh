#!/bin/bash
if [ ! -d "venv" ]; then
    echo "Creating virtual environment..."
    python3 -m venv venv
    ./venv/bin/pip install -r requirements.txt
fi

echo "Cleaning up port 8000..."
lsof -ti :8000 | xargs kill -9 2>/dev/null || true

echo "Starting TG Upload Tool..."
echo "Please visit: http://localhost:8000"
./venv/bin/python3 main.py
