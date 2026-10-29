# check=skip=FromPlatformFlagConstDisallowed
# Container image for Amazon Bedrock AgentCore Runtime.
# The runtime requires linux/arm64 and an HTTP server on port 8080 serving
# /invocations and /ping, which BedrockAgentCoreApp (main.py) provides.
FROM --platform=linux/arm64 python:3.12-slim-bookworm

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    # Bind 0.0.0.0 inside the container (BedrockAgentCoreApp checks this).
    DOCKER_CONTAINER=1 \
    # Disable the per-request prompt/message dumps: they contain call content.
    PROMPT_LOG_DIR=""

WORKDIR /app

# Runtime dependencies only; test and UI dependencies are in requirements-dev.txt.
COPY requirements.txt ./
RUN pip install -r requirements.txt

COPY main.py ./
COPY app ./app
COPY config ./config

RUN useradd --create-home --uid 1000 agent && chown -R agent:agent /app
USER agent

EXPOSE 8080

CMD ["python", "main.py"]
