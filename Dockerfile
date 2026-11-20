# --- Stage 1: build the web UI
FROM node:22-alpine AS web
WORKDIR /src/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --fetch-retries=5 --fetch-retry-mintimeout=2000 --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

# --- Stage 2: Python runtime serving the API and the built UI
FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY backend/ .
COPY --from=web /src/frontend/dist /app/static
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
