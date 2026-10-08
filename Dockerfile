FROM node:24.8.0-bookworm-slim@sha256:cadbfafeb6baf87eaaffa40b3640209c4b7fd38cebde65059d15bc39cd636b85 AS node-deps
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci --omit=dev --ignore-scripts

FROM ghcr.io/astral-sh/uv:0.8.22@sha256:9874eb7afe5ca16c363fe80b294fe700e460df29a55532bbfea234a0f12eddb1 AS uv
FROM python:3.13.7-slim-bookworm@sha256:adafcc17694d715c905b4c7bebd96907a1fd5cf183395f0ebc4d3428bd22d92d AS runtime
COPY --from=node-deps /usr/local/bin/node /usr/local/bin/node
COPY --from=uv /uv /usr/local/bin/uv
WORKDIR /app
COPY qobuz/pyproject.toml qobuz/uv.lock /app/qobuz/
RUN uv sync --project qobuz --frozen --no-dev --no-editable --no-cache \
    && /app/qobuz/.venv/bin/python -c "from qobuz_proxy.connect import DiscoveryService, WsManager; from qobuz_proxy.backends.base import AudioBackend"
COPY --from=node-deps /app/node_modules /app/node_modules
COPY package.json ./
COPY raumkernel ./raumkernel
COPY qobuz/*.py qobuz/*.html ./qobuz/
COPY shared ./shared
COPY docker ./docker
COPY LICENSE ARCHITECTURE.md README.md SPEAKER_AVAILABILITY.md VOLUME_REVIEW.md HANDOFF_REVIEW.md CHECKPOINT_STATUS.md ./
RUN /app/qobuz/.venv/bin/python -c "from qobuz.receiver import Receiver; from qobuz.service import Service" \
    && node -e "const {Raumkernel}=require('node-raumkernel'); new Raumkernel()" \
    && useradd --uid 10001 --create-home app \
    && mkdir -p /data && chown 10001:10001 /data
USER 10001:10001
ENV PYTHONUNBUFFERED=1 API_BIND=127.0.0.1 API_PORT=8787
LABEL org.opencontainers.image.source="https://github.com/TammuzGreeen/qobuz-raumfeld-connect" \
      org.opencontainers.image.version="0.2.0" \
      org.opencontainers.image.licenses="MIT"
HEALTHCHECK --interval=15s --timeout=5s --start-period=20s --retries=3 \
  CMD ["/app/qobuz/.venv/bin/python", "docker/healthcheck.py"]
STOPSIGNAL SIGTERM
CMD ["/app/qobuz/.venv/bin/python", "docker/supervise.py"]
