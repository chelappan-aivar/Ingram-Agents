# Harbor Freight Telemetry - Analytics Layer (Part 2 of 2)

This document continues "multi_a_ingest.md". It describes how the cleaned readings are analysed and
shown to operations staff.

## Components

- The S3 bucket `harbor-raw-readings` (written by the ingest layer) triggers an AWS Glue crawler that
  catalogues the data.
- Amazon Athena queries the catalogued data for the daily cold-chain compliance report.
- Operations staff use an Amazon QuickSight dashboard that reads from Amazon Athena.
- An Amazon EventBridge schedule starts the daily report at 06:00 UTC and publishes the result to an
  Amazon SNS topic `cold-chain-alerts`, which emails the operations team.

## Notes

Operations staff sign in to QuickSight with their corporate identity provider.
