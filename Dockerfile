# QuantumLoop ERP — production image. Gunicorn serves the app; WhiteNoise serves static files.
# The same image runs the web process, the RQ worker and the daily scheduler (see docker-compose.prod.yml).
FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends libpq5 curl && rm -rf /var/lib/apt/lists/* \
    && groupadd -r app && useradd -r -g app -d /app app

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .
# Static files are collected at build time with DEBUG off so the manifest storage is used.
RUN DJANGO_DEBUG=0 DJANGO_SECRET_KEY=build-only DATABASE_URL=sqlite:///build.sqlite3 python manage.py collectstatic --noinput \
    && mkdir -p /app/media && chown -R app:app /app

USER app
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=40s CMD curl -fsS http://localhost:8000/healthz || exit 1
ENTRYPOINT ["/app/docker/entrypoint.sh"]
CMD ["web"]
