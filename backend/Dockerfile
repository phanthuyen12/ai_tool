FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    fonts-noto-core \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

RUN pip install --upgrade pip && pip install uv

COPY pyproject.toml ./
RUN uv pip install --system --no-cache -r pyproject.toml || pip install --no-cache-dir \
    "fastapi>=0.115.0" \
    "uvicorn[standard]>=0.32.0" \
    "sqlalchemy>=2.0.36" \
    "psycopg[binary]>=3.2.3" \
    "alembic>=1.14.0" \
    "dramatiq[redis]>=1.17.0" \
    "redis>=5.2.0" \
    "httpx>=0.28.0" \
    "pydantic>=2.10.0" \
    "pydantic-settings>=2.6.0" \
    "email-validator>=2.2.0" \
    "pyjwt>=2.10.0" \
    "python-multipart>=0.0.18" \
    "pillow>=11.0.0" \
    "boto3>=1.35.0"

COPY alembic.ini ./
COPY migrations ./migrations
COPY app ./app

RUN useradd --create-home --shell /bin/sh appuser \
    && mkdir -p /app/storage_local /tmp/ai-video-studio \
    && chown -R appuser:appuser /app /tmp/ai-video-studio

USER appuser

EXPOSE 8000

