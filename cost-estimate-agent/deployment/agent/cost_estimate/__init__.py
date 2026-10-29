"""Cost estimate agent: free-text AWS infrastructure -> saved AWS Pricing Calculator estimate.

    orchestrator.py        one estimate: MCP tool -> structure check -> summary
    mcp_tool.py            Strands agent + Bedrock + the AWS Pricing Calculator MCP server (stdio)
    service_validator.py   requested-services checklist, pre-validation hook on build_estimate, post-validation
    validators.py          structure check of the estimate JSON
"""

__version__ = "1.1.0"
