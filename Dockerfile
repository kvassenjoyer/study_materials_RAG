FROM node:26-slim AS frontend-build

WORKDIR /build/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_PROJECT_ENVIRONMENT=/app/.venv

WORKDIR /app
RUN pip install --no-cache-dir uv==0.12.23
COPY pyproject.toml uv.lock ./
COPY src ./src
COPY --from=frontend-build /build/src/study_rag/web ./src/study_rag/web
RUN uv sync --frozen --no-dev
COPY alembic.ini ./
COPY migrations ./migrations

EXPOSE 8000
CMD ["uv", "run", "--no-sync", "uvicorn", "study_rag.main:create_application", "--factory", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
