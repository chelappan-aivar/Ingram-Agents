<!-- GENERATED FILE — do not edit directly. Source: project-rules.md (Observability, Resilience & Privacy) -->

## 39. Observability

Production systems should have appropriate:

```text
Logs
Metrics
Traces
Health checks
Alerts
```

Important operations should be diagnosable without reproducing production locally.

## 40. Availability and Resilience

Critical systems should consider:

- Timeouts
- Retries
- Graceful degradation
- Health checks
- Failure isolation
- Backups
- Recovery procedures
- Dependency failures

## 41. Data Privacy

Collect and retain only data required for the application's purpose.

Avoid unnecessary storage of personal, authentication, or sensitive business data.

AI systems should receive only the minimum data necessary for the requested task.

Define and honor data retention limits and deletion/export requests where a regulation
(GDPR, CCPA, HIPAA, or equivalent) or contractual obligation applies to the data involved.

