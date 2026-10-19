ARG STABLE_IMAGE=raumfeld-connect:cast-stable-base
FROM rust:1.90.0-bookworm@sha256:3914072ca0c3b8aad871db9169a651ccfce30cf58303e5d6f2db16d1d8a7e58f AS wire
WORKDIR /build
ADD --checksum=sha256:b6d49372f88dbe866f4ad319278bbdd9e9a867072f9c924ab5e3cf5998337470 https://codeload.github.com/yukarikaname/openchromecast/tar.gz/0cc510b2e0fd8c484029ccf8ab08f527d6ad752c /source.tar.gz
RUN mkdir vendor && tar -xzf /source.tar.gz --strip-components=1 -C vendor
COPY experiments/cast/wire/Cargo.toml experiments/cast/wire/Cargo.lock ./
COPY experiments/cast/wire/src ./src
RUN cargo test --locked && cargo build --locked --release

FROM ${STABLE_IMAGE}
USER root
COPY --from=wire /build/target/release/raumfeld-cast-diagnostic /usr/local/bin/cast-diagnostic
COPY --from=wire /build/vendor/LICENSE /app/experiments/cast/OPENCHROMECAST-LICENSE
COPY experiments/cast /app/experiments/cast
RUN mkdir -p /cast-data && chown 10001:10001 /cast-data
USER 10001:10001
ENV CAST_EXPERIMENT=0 CAST_DATA_DIR=/cast-data CAST_BIND_ADDRESS=127.0.0.1 CAST_PORT=8809 CAST_ADVERTISE=0 CAST_OBSERVE_ROOMS=0
LABEL io.raumfeld.experimental="cast-diagnostic-only" io.raumfeld.cast-media-enabled="false"
HEALTHCHECK --interval=15s --timeout=3s CMD ["/app/qobuz/.venv/bin/python", "-c", "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8799/healthz',timeout=2)"]
CMD ["/app/qobuz/.venv/bin/python", "/app/experiments/cast/service.py"]
