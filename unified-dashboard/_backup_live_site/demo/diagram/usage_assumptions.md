# Usage assumptions

These figures are estimates from the requirements, not measurements. Replace them with real numbers as they become known.

- **Users:** about 150 billing agents, about 25 working at the same time at the peak (requirements, section 2.2).
- **Volume:** 60 disputes a day at month end, each with 3 to 5 documents, about 24,000 pages of text to read a month.
- **Draft generation:** one model call for each dispute and one for each revision; about 36,000 calls a month at roughly 2,500 input tokens and 500 output tokens each.
- **Fargate:** two always-on tasks of 1 vCPU and 2 GB for the whole month (720 hours each).
- **Database:** one PostgreSQL instance in a single Availability Zone, as the pilot accepts; Multi-AZ would double its instance cost.
- **Storage:** 800 GB in S3 Standard and 2.4 TB in Glacier Flexible Retrieval, from the seven-year retention rule.
- **Network:** 120 GB a month through the NAT Gateway for outbound calls to the ERP and the ticketing system; CloudFront serves about 40 GB.
