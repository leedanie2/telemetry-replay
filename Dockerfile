FROM python:3.11-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Cache dependencies separately from application edits.
COPY requirements.txt ./
RUN python -m pip install --no-cache-dir -r requirements.txt \
    && groupadd --gid 10001 telemetry \
    && useradd --uid 10001 --gid telemetry --create-home telemetry

COPY telemetry/ ./telemetry/
COPY sql/ ./sql/
COPY fixtures/ ./fixtures/
COPY tests/ ./tests/
COPY scripts/ ./scripts/

# Code stays root-owned and readable; the application needs no root privileges.
USER telemetry

ENTRYPOINT ["python", "-m", "telemetry"]
CMD ["demo"]
