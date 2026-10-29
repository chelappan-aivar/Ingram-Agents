# Harbor Freight Telemetry - Ingest Layer (Part 1 of 2)

Harbor Freight Telemetry is a fictional platform that collects position and temperature readings
from refrigerated shipping containers. This document describes the ingest layer. The analytics layer
is described in the companion document "multi_b_analytics.md".

## Components

- Container trackers publish readings over MQTT to AWS IoT Core in us-east-1.
- An AWS IoT Core rule forwards every reading to an Amazon Kinesis Data Streams stream named `readings`.
- An AWS Lambda function `normalize-readings` consumes the `readings` stream, converts units and writes
  the cleaned records to the S3 bucket `harbor-raw-readings`.
- Malformed readings are sent by `normalize-readings` to an Amazon SQS dead-letter queue.

## Network

All AWS components run in a single region, us-east-1. The Lambda function runs outside any VPC.
