<!-- GENERATED FILE — do not edit directly. Source: project-rules.md (Core Principles) -->

# Organization Rules — Standardized AI-Assisted Development

> Organization-wide baseline rules for AI-assisted software development.
> Technology-agnostic by design. React.js and Python examples are illustrative only.
> Project-specific rules may extend these rules but must not weaken mandatory security,
> quality, maintainability, or compliance requirements.

> **Document control:** Version 1.1 · Last reviewed 2026-08-11.
> Review at least every 6 months, or sooner if the security landscape shifts materially.
> Approved libraries/versions (Section 28) go stale faster than the rest of this document
> and should be re-validated more frequently.

## 1. Core Principles

Prioritize, in order:

1. Security
2. Correctness
3. Maintainability
4. Modularity
5. Reusability
6. Testability
7. Observability
8. Performance
9. Readability
10. Simplicity

Follow SOLID, DRY, KISS, Separation of Concerns, Least Privilege, Defense in Depth,
and Fail Securely.

Do not optimize for fewer lines at the expense of clarity or maintainability.

## 2. Understand Before Modifying

Before creating or changing code, AI must:

- Inspect the relevant project structure.
- Search for existing implementations.
- Search for reusable components, services, hooks, utilities, types, validators, and tests.
- Understand existing conventions.
- Reuse or extend existing functionality when appropriate.
- Check the impact on security, authorization, persistence, APIs, and consumers.
- Avoid duplicate implementations.

Do not immediately create a new abstraction when an appropriate existing one already exists.

