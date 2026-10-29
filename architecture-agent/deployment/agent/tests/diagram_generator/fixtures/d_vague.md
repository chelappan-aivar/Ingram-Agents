# Final Solution Design: Customer Engagement Portal

**Status:** FINAL - approved by the steering committee
**Customer:** Fabrikam Retail Group (fictional)

## Summary

This document is the final architecture for the Customer Engagement Portal. The design has been reviewed and signed off, so no further architecture changes are expected.

## Architecture

The portal is hosted in the cloud. Users reach the portal through their web browser, and the traffic passes through a load balancer before it gets to the application.

The backend runs in containers. The containers talk to a database where all the portal data is kept. The exact sizing and the high-availability setup will be decided during the build.

When a customer submits a request through the portal, the backend puts a message on some queue so that it can be processed later by a worker. The worker picks up the message and does the processing. How many workers there are has not been decided.

The portal also integrates with the customer's third-party CRM to keep contact records up to date. The integration is done through the vendor's interface. Details are to be confirmed with the vendor.

Files uploaded by users are kept in storage. Logs are collected and monitored with the usual tooling.

## Security

The solution follows security best practices. Access is protected and data is encrypted.

## Next steps

Start the build in the next quarter.
