# Base pinned to an exact tag plus the digest observed at build time
# (2026-09-29); refresh periodically.
FROM python:3.13.15-slim-bookworm@sha256:2325bb286ec344af3e5898cc224b5844e2707ac6e26b1632516fd3edc84a5e26

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONPATH=/app/src:/app \
    HOME=/home/app

RUN useradd --uid 10001 --create-home --home-dir /home/app --shell /usr/sbin/nologin app

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY main.py alembic.ini ./
COPY src ./src
COPY alembic ./alembic
COPY scripts/healthcheck.py ./scripts/healthcheck.py

USER 10001

EXPOSE 8000
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
