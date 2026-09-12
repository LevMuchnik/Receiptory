# Stage 1: Build frontend
FROM node:24-slim AS frontend-build
WORKDIR /app/frontend
COPY frontend/package*.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# Stage 2: Python runtime
FROM python:3.12-slim

# Install system dependencies for weasyprint.
# libharfbuzz-subset0: weasyprint 70 warns that HarfBuzz-Subset "will be required
# by future versions" and renders fine without it; the next major will not. With
# no CI, that would surface as a broken deploy, so take the one-word fix now.
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpango-1.0-0 libpangocairo-1.0-0 libgdk-pixbuf-2.0-0 \
    libffi-dev libcairo2 libglib2.0-0 libharfbuzz-subset0 \
    curl unzip \
    && rm -rf /var/lib/apt/lists/*

# Install latest rclone (Debian package is too old for OneDrive token refresh)
RUN curl -fsSL https://rclone.org/install.sh | bash

# Install uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app

# Install Python dependencies
COPY pyproject.toml uv.lock ./
RUN uv sync --no-dev --frozen

# Install Playwright and Chromium for URL fetching (JS-rendered receipt pages)
RUN uv run playwright install chromium --with-deps

# Copy backend code
COPY backend/ ./backend/
COPY migrations/ ./migrations/

# Copy built frontend
COPY --from=frontend-build /app/frontend/dist ./frontend/dist

# Create data directory
RUN mkdir -p /app/data/storage/originals /app/data/storage/converted \
    /app/data/storage/filed /app/data/storage/page_cache /app/data/logs

EXPOSE ${RECEIPTORY_PORT:-8484}

CMD ["sh", "-c", "uv run uvicorn backend.main:create_app --host 0.0.0.0 --port ${RECEIPTORY_PORT:-8484} --factory"]
