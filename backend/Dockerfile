# TCS OS backend — Dockerfile for Cloud Run
# Cloud Run expects the container to listen on $PORT (defaults to 8080)

FROM python:3.12-slim

# Prevents Python from buffering stdout/stderr (so logs show up in Cloud Run immediately)
ENV PYTHONUNBUFFERED=1

WORKDIR /app

# requirements.txt uses psycopg2-binary (a self-contained wheel), so no
# build-essential/libpq-dev needed here — nothing in requirements.txt
# requires compiling from source.

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# collectstatic only needs Django settings to import cleanly (it never touches
# the database), but settings.py reads SECRET_KEY/DATABASE_URL unconditionally
# at import time — so without *something* set, the build fails before it can
# collect a single file. These placeholders are passed as env vars scoped to
# this one RUN step only (not ARG/ENV'd into the image), so they never persist
# into the final image or get used at runtime — Cloud Run's real env vars
# always take over there. Runs as root, before the user switch below, since
# /app is root-owned at this point.
RUN SECRET_KEY=build-time-placeholder-unused-at-runtime \
    DATABASE_URL=postgres://build:build@localhost:5432/build \
    python manage.py collectstatic --noinput

# Run as a non-root user in production, not the container default (root).
# Home is set to /app (already owned by this user) rather than the default
# /home/django, which useradd -r registers in passwd but never creates on
# disk — gunicorn writes there and gets a permission error otherwise.
RUN groupadd -r django && useradd -r -g django -d /app -s /usr/sbin/nologin django \
    && chown -R django:django /app
USER django

# Cloud Run sets $PORT; gunicorn binds to it
CMD exec gunicorn tcs_os.wsgi:application --bind 0.0.0.0:${PORT:-8080} --workers 2 --threads 4
