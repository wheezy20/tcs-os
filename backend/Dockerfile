# TCS OS backend — Dockerfile for Cloud Run
# Cloud Run expects the container to listen on $PORT (defaults to 8080)

FROM python:3.12-slim

# Prevents Python from buffering stdout/stderr (so logs show up in Cloud Run immediately)
ENV PYTHONUNBUFFERED=1

WORKDIR /app

# System deps needed to build psycopg2 (Postgres driver) — skip if using psycopg2-binary instead
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential libpq-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Collect static files at build time (served via whitenoise)
RUN python manage.py collectstatic --noinput

# Cloud Run sets $PORT; gunicorn binds to it
CMD exec gunicorn tcs_os.wsgi:application --bind 0.0.0.0:${PORT:-8080} --workers 2 --threads 4
