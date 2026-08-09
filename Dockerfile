FROM python:3.14-slim@sha256:a7fb1e634c4a578f9e0bd6327f11a3cde11b7a9395f48e24360c0988bcc5c2bc AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PATH="/app/.venv/bin:$PATH" \
    PORT=8080

RUN groupadd --system assuranceos && useradd --system --gid assuranceos --home /app assuranceos
WORKDIR /app

COPY pyproject.toml uv.lock README.md alembic.ini ./
COPY src ./src
RUN pip install --no-cache-dir uv==0.11.17 \
    && uv sync --frozen --no-dev --extra postgres --extra gcp-runtime --extra agent-cloud \
    && uv cache clean

COPY migrations ./migrations
COPY scripts ./scripts
COPY agents ./agents
COPY audit-packs ./audit-packs
COPY apps ./apps
COPY demo ./demo
# The release evaluation reads evaluation/fixtures/asteria_context.json. Without
# it the image fails its own qualification 57/76 -- every cross-industry case --
# while the source tree passes 76/76, so Judge Mode in a deployed container
# reported a fleet that could not qualify.
COPY evaluation ./evaluation
COPY examples ./examples
COPY tests-library ./tests-library
COPY security/release-keys ./security/release-keys

RUN mkdir -p /app/var/evidence /app/var/evidence-exports \
    && chown -R assuranceos:assuranceos /app

USER assuranceos
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/health', timeout=3)"

CMD ["sh", "-c", "uvicorn assuranceos.api:app --host 0.0.0.0 --port ${PORT:-8080} --proxy-headers --forwarded-allow-ips='*'"]
