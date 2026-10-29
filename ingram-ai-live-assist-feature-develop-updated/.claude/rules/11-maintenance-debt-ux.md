<!-- GENERATED FILE — do not edit directly. Source: project-rules.md (Maintenance, Debt & UX Concerns) -->

## 44. Refactoring

Refactor when:

- A module has multiple responsibilities.
- Logic is duplicated.
- Code is difficult to test.
- Dependencies are tightly coupled.
- Files become unreasonably large.
- A small change repeatedly requires unrelated modifications.

Avoid large unrelated refactors during feature work unless necessary.

Prefer incremental refactoring for legacy systems.

## 45. Technical Debt

Technical debt should be explicitly tracked.

Use appropriate:

```text
TODO
FIXME
Issue
Ticket
ADR
```

Do not silently add debt to already problematic areas.

If a feature requires a temporary exception to an important rule, document it and track
follow-up work.

## 46. Architecture Decisions

For significant architectural decisions, document:

```text
Context
Problem
Options
Decision
Trade-offs
Consequences
```

Use an ADR or equivalent mechanism.

## 47. Accessibility

User-facing applications should consider:

- Keyboard navigation
- Semantic HTML
- Focus management
- Accessible labels
- Contrast
- Screen readers
- Accessible errors
- Reduced motion where relevant

Do not rely only on visual appearance to communicate important information.

## 48. Internationalization

Applications intended for multiple locales should avoid hardcoded user-facing strings.

Consider:

- Date formats
- Number formats
- Currency
- Pluralization
- Text expansion
- Right-to-left languages where applicable

## 49. Time and Date Handling

Do not assume server timezone equals user timezone.

Use explicit timezone-aware timestamps for business-critical operations.

Be careful with:

- Daylight saving changes
- Date boundaries
- Recurring schedules
- Expiration times

### 49.1 Date/Time Format Standard

- Store and transmit timestamps in UTC using ISO 8601 (`2026-08-12T10:30:00Z`) in
  databases, APIs, logs, and inter-service messages. Never store or transmit a
  locale-formatted date string (e.g., `08/12/2026`) as the source of truth.
- Convert to the user's local timezone and locale-specific format only at the final
  display/rendering layer — never bake a display format into stored or transmitted data.
- Use the project's chosen date library consistently (see Section 28.1 —
  `date-fns`/`Temporal` for JS/TS) rather than mixing native `Date` parsing, a date
  library, and manual string formatting in the same codebase.
- Avoid ambiguous formats (`MM/DD/YYYY` vs `DD/MM/YYYY`) in any user-facing or logged
  output — use an unambiguous format or the library's locale-aware formatter.

