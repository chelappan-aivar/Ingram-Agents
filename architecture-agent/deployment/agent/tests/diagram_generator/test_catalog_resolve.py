"""Catalog.resolve(): phrasing coverage, fallbacks, robustness."""
from __future__ import annotations

import json

import pytest

from diagram_generator.catalog.loader import load_catalog
from diagram_generator.config import Settings

CATS = json.loads(Settings().aws4_shapes_path.read_text(encoding="utf-8"))["categories"]
ALL_SHAPES = {s for v in CATS.values() for s in v["shapes"]}


@pytest.fixture(scope="module")
def catalog():
    return load_catalog()


# (service, name, kind) -> expected shape
CASES = [
    ("S3 bucket", None, "data_store", "s3"),
    ("AWS Lambda", None, "aws_service", "lambda"),
    ("API Gateway", None, "aws_service", "api_gateway"),
    ("Kinesis Data Streams", None, "aws_service", "kinesis_data_streams"),
    ("IoT Core", None, "aws_service", "iot_core"),
    ("DynamoDB", None, "data_store", "dynamodb"),
    ("Aurora PostgreSQL", None, "data_store", "aurora"),
    ("ALB", None, "aws_service", "application_load_balancer"),
    ("CloudFront", None, "aws_service", "cloudfront"),
    ("Cognito", None, "aws_service", "cognito"),
    ("Bedrock", None, "aws_service", "bedrock"),
    ("EKS", None, "aws_service", "eks"),
    ("SQS", None, "aws_service", "sqs"),
    ("SNS", None, "aws_service", "sns"),
    ("EventBridge", None, "aws_service", "eventbridge"),
    ("Step Functions", None, "aws_service", "step_functions"),
    ("Glue", None, "aws_service", "glue"),
    ("Athena", None, "aws_service", "athena"),
    ("Redshift", None, "data_store", "redshift"),
    ("QuickSight", None, "aws_service", "quicksight"),
    ("Timestream", None, "data_store", "timestream"),
    ("Secrets Manager", None, "aws_service", "secrets_manager"),
    ("KMS", None, "aws_service", "key_management_service"),
    ("WAF", None, "aws_service", "waf"),
    ("Route 53", None, "aws_service", "route_53"),
    ("CloudWatch", None, "aws_service", "cloudwatch_2"),
    ("Direct Connect", None, "aws_service", "direct_connect"),
    ("Site-to-Site VPN", None, "aws_service", "site_to_site_vpn"),
    ("Transit Gateway", None, "aws_service", "transit_gateway"),
    ("SageMaker", None, "aws_service", "sagemaker"),
    # spelling / prefix / case variants
    ("amazon s3", None, "data_store", "s3"),
    ("Amazon Simple Storage Service (S3)", None, "data_store", "s3"),
    ("AWS  Lambda   function", None, "aws_service", "lambda"),
    ("Amazon Elastic Kubernetes Service", None, "aws_service", "eks"),
    ("Amazon OpenSearch Service", None, "data_store", "elasticsearch_service"),
    ("Amazon Bedrock AgentCore", None, "aws_service", "bedrock_agentcore"),
    ("AgentCore Runtime", None, "aws_service", "bedrock_agentcore"),
    ("Managed Service for Apache Flink", None, "aws_service", "managed_service_for_apache_flink"),
    ("AWS IAM Identity Center", None, "aws_service", "single_sign_on"),
    ("Application Load Balancer", None, "aws_service", "application_load_balancer"),
    ("NAT Gateway", None, "aws_service", "nat_gateway"),
    ("AWS Application Migration Service", None, "aws_service", "cloudendure_migration"),
    # name only (service is null)
    (None, "Orders bucket in S3", "data_store", "s3"),
    (None, "Telemetry stream on Kinesis Data Streams", "aws_service", "kinesis_data_streams"),
    (None, "Orders database on Aurora", "data_store", "aurora"),
    (None, "RDS PostgreSQL", "data_store", "rds"),
    (None, "Users", "actor", "users"),
    (None, "Mobile app", "actor", "mobile_client"),
    (None, "Corporate data center", "external", "corporate_data_center"),
    (None, "Postgres database", "data_store", "generic_database"),
]


@pytest.mark.parametrize("service, name, kind, shape", CASES)
def test_resolve_hits(catalog, service, name, kind, shape):
    icon = catalog.resolve(service, name or service, kind)
    assert icon.matched, (service, name)
    assert icon.shape == shape
    assert icon.shape in ALL_SHAPES


def test_service_field_wins_over_name(catalog):
    assert catalog.resolve("Amazon SQS", "Orders database", "aws_service").shape == "sqs"
    assert catalog.resolve("Not A Service", "Orders bucket in S3", "data_store").shape == "s3"


def test_longest_phrase_and_aws_preferred(catalog):
    assert catalog.resolve(None, "Gateway: API Gateway fronting Lambda", "aws_service").shape == "api_gateway"
    assert catalog.resolve(None, "Database on Aurora", "data_store").shape == "aurora"      # AWS beats generic "database"
    assert catalog.resolve(None, "CloudWatch Events rule", "aws_service").shape == "eventbridge"


def test_exact_only_names_do_not_match_inside_phrases(catalog):
    assert not catalog.resolve(None, "Batch processing job", "aws_service").matched
    assert catalog.resolve("AWS Batch", "Batch processing job", "aws_service").shape == "batch"
    assert catalog.resolve("Amazon Q", "Amazon Q", "aws_service").shape == "q"
    assert not catalog.resolve(None, "Q4 report generator", "aws_service").matched
    assert catalog.resolve(None, "AWS Config", "aws_service").shape == "config"


def test_actor_and_external_names_only_match_whole_text(catalog):
    assert catalog.resolve(None, "Mobile app users", "actor").shape == "users"                      # fallback, not Mobile Client
    assert not catalog.resolve(None, "Mobile app users", "actor").matched
    assert not catalog.resolve(None, "Billing process behind S3 gateway", "external").matched


# ----------------------------------------------------------------------------- fallbacks
@pytest.mark.parametrize("kind, shape", [
    ("actor", "users"),
    ("external", "internet"),
    ("data_store", "generic_database"),
    ("generic", "generic_application"),
    ("aws_service", "general"),
])
def test_fallback_per_kind(catalog, kind, shape):
    icon = catalog.resolve(None, "Zorblax Frobnicator 9000", kind)
    assert icon.matched is False
    assert icon.shape == shape
    assert icon.shape in ALL_SHAPES
    assert icon.fill_color.startswith("#") and icon.tint_color.startswith("#") and icon.stroke_color.startswith("#")
    assert icon.category and icon.category_label


def test_unknown_service_with_unknown_name_falls_back(catalog):
    icon = catalog.resolve("Amazon Imaginary Service", "The thing", "aws_service")
    assert icon.matched is False and icon.shape in ALL_SHAPES


def test_resolve_never_raises(catalog):
    weird = [None, "", "   ", "???", "\x00\x01", "a" * 5000, "S3 " * 500, 123, ["x"], {"a": 1}, "ünïcödé 日本語"]
    for service in weird:
        for name in weird:
            for kind in ("aws_service", "external", "actor", "data_store", "generic", "bogus", None):
                icon = catalog.resolve(service, name, kind)
                assert icon.shape in ALL_SHAPES


def test_matched_icons_have_category_colours(catalog):
    icon = catalog.resolve("Amazon S3", "S3", "data_store")
    assert (icon.category, icon.category_label, icon.style) == ("storage", "Storage", "resource_icon")
    assert icon.fill_color == "#3F8624" and icon.tint_color == "#E8F5E9" and icon.stroke_color == "#3F8624"
    alb = catalog.resolve("ALB", "ALB", "aws_service")
    assert alb.style == "sub_resource"
    users = catalog.resolve(None, "Users", "actor")
    assert users.style == "general"


def test_kind_of(catalog):
    assert catalog.kind_of("Amazon S3") == "data_store"
    assert catalog.kind_of("Users") == "actor"
    assert catalog.kind_of("Nope") is None
