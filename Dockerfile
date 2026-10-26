# Imagen del servidor TwinSight: compila el frontend y lo sirve desde el backend.
# Etapa 1 — build del frontend (SPA React/Vite → estáticos).
FROM node:22-slim AS frontend
WORKDIR /fe
COPY frontend/package.json frontend/package-lock.json* ./
RUN npm install
COPY frontend/ ./
RUN npm run build

# Etapa 2 — backend Python con los estáticos embebidos.
FROM python:3.12-slim AS backend
RUN pip install --no-cache-dir uv
WORKDIR /app
COPY backend/ ./backend/
COPY --from=frontend /fe/dist ./static
WORKDIR /app/backend
RUN uv sync
ENV TWINSIGHT_STATIC_DIR=/app/static \
    TWINSIGHT_DATA_DIR=/data \
    TWINSIGHT_HOST=0.0.0.0 \
    TWINSIGHT_PORT=8000
EXPOSE 8000
CMD ["uv", "run", "uvicorn", "twinsight.app.main:app", "--host", "0.0.0.0", "--port", "8000"]
