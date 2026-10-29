# AgentCore Runtime container for the Cost estimate agent (free text -> AWS Pricing Calculator estimate), on Strands.
# AgentCore runs ARM64 (Graviton) only. The build context is this deployment/ folder:
#   docker buildx build --platform linux/arm64 -f Dockerfile -t <ecr-uri>:<tag> --push .
FROM --platform=linux/arm64 python:3.12.4-slim

# Must match MCP_SERVER_VERSION in agent/cost_estimate/mcp_tool.py.
ARG MCP_SERVER_VERSION=1.3.3
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 DEBIAN_FRONTEND=noninteractive

# Node.js runs the AWS Pricing Calculator MCP server (stdio child of the agent). Debian bookworm ships Node 18,
# which meets the package's dependencies (@modelcontextprotocol/sdk 1.32.1 and express 5.2.1 need node >= 18).
# The server is installed globally at the pinned version during the build: nothing is downloaded at run time.
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates nodejs npm \
    && npm install -g --no-audit --no-fund "sample-aws-pricing-calculator-mcp@${MCP_SERVER_VERSION}" \
    && npm cache clean --force \
    && apt-get purge -y npm && apt-get autoremove -y \
    && rm -rf /var/lib/apt/lists/* /root/.npm \
    && node --version && command -v sample-aws-pricing-calculator-mcp

# The root filesystem is read-only at run time: HOME and any npm/node cache live under /tmp.
ENV HOME=/tmp npm_config_cache=/tmp/.npm NPM_CONFIG_UPDATE_NOTIFIER=false \
    MCP_COMMAND=sample-aws-pricing-calculator-mcp

WORKDIR /opt/app
COPY agent/requirements.txt /opt/app/requirements.txt
RUN pip install --no-cache-dir -r /opt/app/requirements.txt
COPY agent/main.py /opt/app/main.py
COPY agent/cost_estimate /opt/app/cost_estimate

RUN useradd -r -u 1001 appuser
USER 1001
# Writes only under /tmp. The code under /opt is read-only at run time.
EXPOSE 8080
CMD ["python", "main.py"]
