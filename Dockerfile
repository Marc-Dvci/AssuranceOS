FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PORT=8080

RUN groupadd --system assuranceos && useradd --system --gid assuranceos --home /app assuranceos
WORKDIR /app

COPY pyproject.toml README.md alembic.ini ./
COPY src ./src
RUN pip install --no-cache-dir '.[postgres,gcp-runtime]'

COPY migrations ./migrations
COPY scripts ./scripts
COPY agents ./agents
COPY audit-packs ./audit-packs
COPY demo ./demo
COPY examples ./examples
COPY tests-library ./tests-library
COPY security/release-keys ./security/release-keys

RUN mkdir -p /app/var/evidence /app/var/evidence-exports \
    && chown -R assuranceos:assuranceos /app

USER assuranceos
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/ready', timeout=3)"

CMD ["sh", "-c", "uvicorn assuranceos.api:app --host 0.0.0.0 --port ${PORT:-8080} --proxy-headers --forwarded-allow-ips='*'"]
