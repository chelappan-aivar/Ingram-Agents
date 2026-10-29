"""MCP Calculator Tool - Creates AWS Calculator estimates via Strands agent."""

import json
import logging
import os
import shlex
import shutil
import time
from typing import Optional

from mcp import StdioServerParameters, stdio_client
from strands import Agent
from strands.models import BedrockModel
from strands.tools.mcp import MCPClient

from cost_estimate.service_validator import (
    CoverageJudge,
    ServiceValidator,
    extract_checklist,
    post_validate,
)

logger = logging.getLogger(__name__)

# Defaults are the values the agent ran with before the move to deployment/; the runtime sets them from
# template parameters (MODEL_ID, AWS_REGION, MAX_OUTPUT_TOKENS).
DEFAULT_MODEL_ID = "global.anthropic.claude-sonnet-5-5"
# The build_estimate tool call carries the full estimate as JSON, which can
# exceed the default output limit and get truncated mid-call.
DEFAULT_MAX_OUTPUT_TOKENS = 32000
MAX_RETRIES = 3
# Pinned so a new npm release of the MCP server cannot change behavior without a redeploy.
# The Dockerfile installs exactly this version globally (MCP_SERVER_VERSION build arg must match).
MCP_SERVER_PACKAGE = "sample-aws-pricing-calculator-mcp"
MCP_SERVER_VERSION = "1.3.3"


def model_id() -> str:
    return os.environ.get("MODEL_ID") or DEFAULT_MODEL_ID


def region() -> Optional[str]:
    return os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION") or None


def max_output_tokens() -> int:
    return int(os.environ.get("MAX_OUTPUT_TOKENS") or DEFAULT_MAX_OUTPUT_TOKENS)


def mcp_command() -> list[str]:
    """The command that starts the MCP server over stdio.

    MCP_COMMAND (set in the container image) launches the pre-installed binary, so nothing is downloaded at run
    time. Without it: the installed binary if it is on PATH, else `npx -y <package>@<pinned version>` (local dev).
    """
    configured = os.environ.get("MCP_COMMAND", "").strip()
    if configured:
        return shlex.split(configured)
    if shutil.which(MCP_SERVER_PACKAGE):
        return [MCP_SERVER_PACKAGE]
    return ["npx", "-y", f"{MCP_SERVER_PACKAGE}@{MCP_SERVER_VERSION}"]

_PROMPT_TEMPLATE = """\
Create an AWS cost estimate for: {user_input}

CRITICAL INSTRUCTIONS:
1. Use the AWS pricing calculator MCP tools (build_estimate)
2. Return the calculator URL and service configurations
3. EXCLUDE AWS Free Tier
4. For each service, include BOTH 'name' AND 'service_code'
5. Put all pricing attributes in 'config' as flat key-value pairs
6. Return ONLY valid JSON (no explanations)

Return this exact JSON structure:
{{
  "estimate_name": "Cost Estimate",
  "estimate_id": "id-from-calculator",
  "calculator_url": "https://calculator.aws/#/estimate?id=...",
  "region": "ap-south-1",
  "currency": "USD",
  "environments": {{
    "Production": {{
      "services": [
        {{
          "name": "Amazon EC2",
          "service_code": "AmazonEC2",
          "description": "EC2 Instance",
          "config": {{
            "location": "ap-south-1",
            "instanceType": "t3.medium",
            "operatingSystem": "Linux",
            "tenancy": "Shared",
            "preInstalledSw": "NA",
            "capacitystatus": "Used",
            "quantity": 1,
            "rate_type": "hourly"
          }}
        }},
        {{
          "name": "Amazon S3",
          "service_code": "AmazonS3",
          "description": "S3 Storage",
          "config": {{
            "location": "ap-south-1",
            "volumeType": "Standard",
            "quantity": 10,
            "rate_type": "flat"
          }}
        }}
      ]
    }}
  }}
}}

Service Code Mapping Rules:
- EC2 → "AmazonEC2"
- S3 → "AmazonS3"
- RDS / MySQL / PostgreSQL / Database → "AmazonRDS"
- EBS → "AmazonEC2" (use volumeApiName filter)
- Load Balancer / ELB / ALB → "AWSELB"
- CloudFront → "AmazonCloudFront"
- Lambda → "AWSLambda"
- DynamoDB → "AmazonDynamoDB"

DO NOT EVER use: ec2Enhancement, amazonS3Standard, amazonRDSMySQLDB, applicationLoadBalancer

EC2 required config fields:
- operatingSystem: "Linux" or "Windows" (capitalize first letter)
- tenancy: "Shared"
- preInstalledSw: "NA"
- capacitystatus: "Used"

RDS required config fields:
- databaseEngine: "MySQL" or "PostgreSQL" etc.

Rules:
- service_code must be exact AWS Pricing API codes
- quantity must be a NUMBER (10, not "10 GB")
- operatingSystem: "Linux" not "linux"
- tenancy: "Shared" not "shared"
- config must have 'location' (region code) and 'rate_type' ('hourly' for compute, 'flat' for storage)
"""


class MCPCalculatorTool:
    """Creates AWS Pricing Calculator estimates via the Strands MCP agent."""

    def create_estimate(self, user_input: str, report: Optional[dict] = None) -> dict:
        """Return the estimate JSON the agent produced.

        ``report`` (optional) is filled with what happened along the way for the caller to show; it does not
        change the flow: ``checklist`` (the requested services), ``attempts``, ``preflight_rejections`` and
        ``coverage`` (the post-validation summary, or None).
        """
        prompt = _PROMPT_TEMPLATE.format(user_input=user_input)
        last_exc = None
        report = report if report is not None else {}

        model = BedrockModel(model_id=model_id(), max_tokens=max_output_tokens(), region_name=region())
        # Validation setup: the services the user asked for, and a judge that
        # decides which of them an estimate is missing. Extracted once per request.
        checklist = extract_checklist(user_input, model)
        judge = CoverageJudge(model)
        report["checklist"] = [{"name": r.name, "environment": r.environment} for r in checklist]
        command = mcp_command()

        for attempt in range(1, MAX_RETRIES + 1):
            try:
                logger.info("MCP attempt %d/%d: starting agent", attempt, MAX_RETRIES)
                report["attempts"] = attempt

                mcp_client = MCPClient(
                    lambda: stdio_client(
                        StdioServerParameters(
                            command=command[0],
                            args=command[1:],
                        )
                    )
                )
                # PRE-validation: the hook blocks build_estimate until every
                # requested service is included in the call.
                validator = ServiceValidator(checklist, judge)
                agent = Agent(tools=[mcp_client], model=model, hooks=[validator])
                response_str = str(agent(prompt))

                start = response_str.find('{')
                end = response_str.rfind('}') + 1
                if start == -1 or end == 0:
                    raise ValueError(f"No JSON in agent response: {response_str[:200]}")

                data = json.loads(response_str[start:end])
                logger.info(
                    "MCP success on attempt %d — calculator_url=%s",
                    attempt,
                    data.get('calculator_url', 'N/A'),
                )

                report["preflight_rejections"] = validator.rejections
                # POST-validation: read the saved estimate back with
                # import_estimate and log it to CloudWatch (log only, never raises).
                report["coverage"] = post_validate(
                    mcp_client,
                    data.get('calculator_url') or data.get('estimate_id'),
                    checklist,
                    judge,
                )
                return data

            except Exception as exc:
                last_exc = exc
                logger.warning("MCP attempt %d failed: %s", attempt, exc)
                if attempt < MAX_RETRIES:
                    wait = 2 ** (attempt - 1) * 2  # 2s, 4s
                    logger.info("Retrying in %ds...", wait)
                    time.sleep(wait)

        raise RuntimeError(
            f"MCP failed after {MAX_RETRIES} attempts: {last_exc}"
        ) from last_exc
