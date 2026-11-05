FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends tesseract-ocr tesseract-ocr-rus curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY pyproject.toml README.md ./
COPY app ./app
COPY webhook_sink ./webhook_sink
COPY alembic.ini ./
COPY alembic ./alembic

RUN pip install . \
    && mkdir -p /app/storage /app/state \
    && useradd -m -u 10001 app \
    && chown -R app:app /app

USER app

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
