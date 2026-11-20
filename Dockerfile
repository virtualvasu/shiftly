# --- Stage 1: build the web UI (skipped cleanly if there is no frontend yet)
FROM node:22-alpine AS web
WORKDIR /src
COPY frontend/ ./frontend/
RUN if [ -f frontend/package.json ]; then \
      cd frontend && npm ci && npm run build; \
    else \
      mkdir -p frontend/dist; \
    fi

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
