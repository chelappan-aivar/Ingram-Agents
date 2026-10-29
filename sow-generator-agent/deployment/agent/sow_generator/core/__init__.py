"""Small local replacements for the monolith's app.core / app.integrations.aws packages.

config.py   SowSettings + CompanyProfile, read from environment variables
errors.py   AppError and its HTTP-flavoured subclasses (status_code, code, message)
bedrock.py  botocore config for Bedrock, image size cap, temperature policy
session.py  boto3 session factory (region, optional profile)
s3.py       async S3 client wrapper used by repositories/sow_storage.py
"""
