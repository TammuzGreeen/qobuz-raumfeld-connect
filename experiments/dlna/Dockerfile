# Build the stable baseline first; no registry latest dependency or Cast code.
FROM raumfeld-connect:dlna-base-c5b87d5
USER root
COPY experiments/dlna/requirements.lock /app/dlna-requirements.lock
RUN uv pip install --python /app/qobuz/.venv/bin/python --require-hashes --no-deps -r /app/dlna-requirements.lock
COPY experiments/dlna /app/experiments/dlna
COPY qobuz/upstream_backend.py /app/qobuz/upstream_backend.py
COPY raumkernel/adapter.js raumkernel/companion-main.js /app/raumkernel/
USER 10001:10001
ENV API_PORT=8877 CONFIG_PATH=/data/config.json
LABEL org.opencontainers.image.title="Experimental logical-room DLNA bridge" \
      org.opencontainers.image.version="dlna-experiment-1"
HEALTHCHECK --interval=15s --timeout=5s --start-period=20s --retries=3 \
  CMD ["/app/qobuz/.venv/bin/python", "-m", "experiments.dlna.healthcheck"]
CMD ["/app/qobuz/.venv/bin/python", "-m", "experiments.dlna.main"]
