# ecomsbd backend image.
#
# Two stages so the runtime layer carries no build toolchain, and a non-root
# user because this process holds courier credentials and customer phone data.

FROM python:3.12-slim AS builder

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /build
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

COPY pyproject.toml ./
COPY app ./app
RUN pip install --upgrade pip && pip install .

# --------------------------------------------------------------------------- #

FROM python:3.12-slim AS runtime

ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    APP_ENV=production

# curl is used by the container healthcheck only.
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 10001 ecomsbd

COPY --from=builder /opt/venv /opt/venv

WORKDIR /srv/app
COPY --chown=ecomsbd:ecomsbd alembic.ini ./
COPY --chown=ecomsbd:ecomsbd migrations ./migrations
COPY --chown=ecomsbd:ecomsbd app ./app

USER ecomsbd
EXPOSE 8000

# Liveness only: readiness checks the database and belongs to the orchestrator,
# which can act on it, rather than to a container restart loop.
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD curl -fsS http://localhost:8000/health/live || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
