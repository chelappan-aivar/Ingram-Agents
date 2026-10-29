<!-- GENERATED FILE — do not edit directly. Source: project-rules.md (Rule Hierarchy & Tooling) -->

## 53. Project-Specific Rules

Projects may extend this file with:

- Framework conventions
- Folder structure
- State-management patterns
- API conventions
- Database conventions
- Deployment rules
- Naming conventions
- Testing strategy
- Domain-specific rules
- Approved libraries

Hierarchy:

```text
Organization rules.md
        ↓
Project-specific rules
        ↓
Feature-specific conventions
```

Project-specific rules must not weaken mandatory organization-level security, quality,
maintainability, or compliance requirements.

## 54. Rule Conflict Resolution

When rules appear to conflict:

1. Security requirements take priority.
2. Organization-wide mandatory rules take priority over project conventions.
3. Project conventions take priority over individual preference.
4. Prefer the simpler and safer implementation.
5. Document exceptions when necessary.

If the AI cannot determine the correct interpretation, it should ask rather than silently
choosing a risky option.

## 55. Definition of Done

Before considering meaningful work complete, verify where applicable:

```text
[ ] Existing code searched and reused where appropriate
[ ] Architecture/conventions followed
[ ] No unnecessary duplication
[ ] No unnecessarily large files
[ ] Input validation considered
[ ] Authentication/authorization considered
[ ] Security risks reviewed
[ ] Error handling implemented
[ ] Tests added/updated
[ ] Lint passes
[ ] Type checking passes
[ ] Build passes
[ ] Secret scan passes
[ ] Documentation updated where needed
[ ] Observability considered
[ ] Breaking changes identified
[ ] Human review/approval obtained before merge
```

## 56. Final AI Instruction

For every code-generation or code-modification task, follow:

```text
Understand
   ↓
Search
   ↓
Reuse
   ↓
Design
   ↓
Implement
   ↓
Validate
   ↓
Test
   ↓
Security Review
   ↓
Lint / Type Check
   ↓
Document
```

The goal is not merely working code.

The goal is:

**Secure + Modular + Reusable + Testable + Maintainable + Observable + AI-efficient code.**

> **AI should accelerate good engineering practices, not accelerate technical debt.**

## 57. Tooling: How These Rules Reach Each AI Assistant

This document has no effect unless it is actually loaded into the AI tool's context.
Different tools read instructions from different, tool-specific locations, and none of
them natively pull in a file from another repository.

### 57.1 Distribution Mechanism

Each repository keeps exactly one copy of this file, named `project-rules.md` at the repo
root. Project-specific detail is added directly into that copy (see Section 53); it does
not need a separate file.

The repo's `README.md` has a mandatory first-step setup prompt (see the project template).
Whoever sets up the repo — or notices their tool's rules file is missing or stale — pastes
that prompt into their AI coding assistant. The assistant reads `project-rules.md` and
creates or updates its own tool's native rules/instructions file from it. That generated
file is then committed, so the rest of the team gets it for free on their next
clone/pull — no per-tool setup required a second time for that repo.

### 57.2 Where Each Tool Reads Instructions

```text
Claude Code      CLAUDE.md (project root; supports @path imports)
Cursor           .cursor/rules/*.mdc (alwaysApply: true for baseline rules)
GitHub Copilot   .github/copilot-instructions.md
Kiro             .kiro/steering/*.md (inclusion: always)
Cross-tool       AGENTS.md (emerging open convention; confirm current adoption)
```

Verify these locations against each tool's current documentation before relying on
them — per Section 28.2, these conventions change.

### 57.3 Keeping It From Going Stale

When `project-rules.md` changes, whoever changed it re-runs the same setup prompt so
their tool's generated file reflects the update, and commits it. There is no automated
enforcement of this — treat a stale generated rules file as a bug, the same as any other
outdated documentation.
