<!-- GENERATED FILE — do not edit directly. Source: project-rules.md (Production & Security Governance) -->

## 50. Security-Critical Changes

Changes involving authentication, authorization, secrets, encryption, payments, personal
data, uploads, access control, or infrastructure permissions require additional review
and testing.

AI-generated security-sensitive code must not be accepted solely because it compiles.

## 51. Production Safety

Before production-impacting changes:

1. Understand the blast radius.
2. Confirm rollback/recovery strategy.
3. Verify configuration.
4. Test appropriately.
5. Monitor after deployment.
6. Document significant changes.

Prefer a feature flag or kill switch for risky changes so they can be disabled quickly
without a redeploy or rollback.

Avoid destructive operations without explicit confirmation and safeguards.

## 52. No Silent Security Downgrades

Never use shortcuts such as:

```text
Disable authentication
Disable authorization
Allow unrestricted CORS without justification
Disable TLS verification
Store credentials in source code
Disable input validation
Disable security middleware
Ignore security scanner failures
Use plaintext secrets
```

A genuine temporary exception must be explicitly documented, reviewed, and tracked.

