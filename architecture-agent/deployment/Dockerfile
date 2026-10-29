# AgentCore Runtime container for the Architecture Agent (SRS -> ADR -> usage -> diagram), on Strands.
# AgentCore runs ARM64 (Graviton) only. The build context is this deployment/ folder:
#   docker buildx build --platform linux/arm64 -f Dockerfile -t <ecr-uri>:<tag> --push .
FROM --platform=linux/arm64 python:3.12.4-slim

# draw.io desktop 26.0.9 arm64 .deb: release asset checked on 2026-10-15 (HTTP 200).
ARG DRAWIO_VERSION=26.0.9
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 DEBIAN_FRONTEND=noninteractive

# draw.io desktop is the only export tool the diagram pipeline uses (-x -f png|svg). It is an Electron app,
# so it needs the shared libraries below and a virtual X display (xvfb) when run headless.
# nodejs (Debian's v18) runs the ELK layout (render/layout_elk.mjs, vendor/elkjs) and the libavoid edge router
# (render/route_edges.mjs, vendor/libavoid).
RUN apt-get update && apt-get install -y --no-install-recommends \
      ca-certificates curl xvfb xauth fonts-dejavu-core nodejs \
      libgtk-3-0 libnss3 libasound2 libxss1 libgbm1 libdrm2 libxkbcommon0 \
      libxcomposite1 libxdamage1 libxrandr2 libsecret-1-0 libatk-bridge2.0-0 \
    && curl -fsSL -o /tmp/drawio.deb \
      "https://github.com/jgraph/drawio-desktop/releases/download/v${DRAWIO_VERSION}/drawio-arm64-${DRAWIO_VERSION}.deb" \
    && apt-get install -y /tmp/drawio.deb \
    && rm -rf /tmp/drawio.deb /var/lib/apt/lists/*

# The pipeline calls one command, DRAWIO_CLI. This wrapper gives it a display and disables the Electron
# sandbox, which cannot start in a container without extra privileges.
RUN printf '#!/bin/sh\nexec xvfb-run -a /usr/bin/drawio --no-sandbox "$@"\n' > /usr/local/bin/drawio \
    && chmod 0755 /usr/local/bin/drawio
ENV DRAWIO_CLI=/usr/local/bin/drawio
# Headless (software-rendered) draw.io fails to export PNGs above ~6 Mpx (scale 1.4 of a 2270x1610 page failed,
# 1.25 worked); the pipeline lowers the PNG scale to stay under this cap.
ENV PNG_MAX_PIXELS=5000000

# The agent source (agent/ in this folder). An editable install keeps it under /opt/app, where the pipelines
# find vendor/ next to the packages.
COPY agent/pyproject.toml /opt/app/pyproject.toml
COPY agent/architecture_agent /opt/app/architecture_agent
COPY agent/adr_generator /opt/app/adr_generator
COPY agent/diagram_generator /opt/app/diagram_generator
COPY agent/usage_estimator /opt/app/usage_estimator
COPY agent/vendor /opt/app/vendor
RUN python -m venv /opt/venv && /opt/venv/bin/pip install --no-cache-dir -e /opt/app

RUN useradd -r -u 1001 appuser
USER appuser
# Writes only under /tmp (per-request scratch). The code under /opt is read-only at run time.
EXPOSE 8080
CMD ["/opt/venv/bin/python", "-m", "architecture_agent.agent"]
