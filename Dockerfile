# ── Stage 1: Build frontend ─────────────────────────────────────
FROM node:20-alpine AS frontend-builder

WORKDIR /frontend
COPY frontend/package*.json ./
RUN npm ci

COPY frontend/ .
RUN npm run build
# Output in /frontend/dist


# ── Stage 2: Python runtime ─────────────────────────────────────
FROM python:3.11-slim

WORKDIR /app

# Install Python deps
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy source code
COPY bitget_bot/ ./bitget_bot/
COPY backtest.py .

# Copy frontend build from Stage 1
COPY --from=frontend-builder /frontend/dist ./frontend/dist

# Create data directory
RUN mkdir -p data

# Expose port
EXPOSE 8080

# Start command
CMD ["uvicorn", "bitget_bot.api:app", "--host", "0.0.0.0", "--port", "8080"]
