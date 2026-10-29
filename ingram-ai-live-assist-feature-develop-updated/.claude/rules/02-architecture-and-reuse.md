<!-- GENERATED FILE — do not edit directly. Source: project-rules.md (Architecture, Reuse & API Client) -->

## 3. Modular Architecture

Separate responsibilities such as:

- Presentation
- State/application logic
- Business logic
- API communication
- Validation
- Persistence
- Authorization
- Error handling

Avoid modules that contain unrelated responsibilities.

React example:

```text
Dashboard/
├── Dashboard.tsx
├── DashboardHeader.tsx
├── DashboardTable.tsx
├── hooks/
├── services/
├── types.ts
└── utils.ts
```

The equivalent Python module split follows the same pattern: `routes.py` /
`service.py` / `repository.py` / `schemas.py` / `models.py` / `validators.py`. The exact
structure is stack-specific; separation of responsibility is not.

### 3.1 Default Project Scaffold: Feature-Based / Domain-Driven

If a project has no established top-level structure yet, default to organizing code by
feature/domain rather than by technical type. Avoid a global `components/`, `services/`,
`utils/` dumping ground that mixes unrelated features together.

```text
src/
├── features/
│   ├── orders/
│   │   ├── components/
│   │   ├── hooks/
│   │   ├── services/
│   │   ├── types.ts
│   │   └── index.ts
│   └── billing/
│       ├── components/
│       ├── hooks/
│       ├── services/
│       ├── types.ts
│       └── index.ts
├── shared/          # cross-feature primitives only (design system, generic utils)
└── app/             # routing, providers, composition root
```

Only promote code into `shared/` once it is genuinely reused by more than one
feature/domain — do not pre-emptively generalize code that currently belongs to one
feature.

For backend services, apply the equivalent domain-driven grouping (bounded
contexts/modules per domain) rather than a purely technical layering (a single
`controllers/`, `services/`, `models/` tree containing every domain mixed together).

If the project already has an established structure — feature-based, domain-driven,
layered, or otherwise — follow it consistently rather than introducing a competing
pattern.

## 4. File and Module Size

Large files increase AI context usage, token cost, review complexity, regression risk,
merge conflicts, and maintenance cost.

As general warning thresholds:

```text
UI component/module      Prefer < 300–400 lines
Service/business module  Prefer < 300–400 lines
Utility module           Prefer < 200 lines
Controller/route module  Prefer < 300 lines
```

These are guidelines, not absolute laws. A file that grows unusually large should be
reviewed and split by responsibility where appropriate.

AI should flag files that are already very large before adding substantial new logic.

## 5. Reusability

Before implementing functionality, search for existing:

- API clients
- Authentication
- Authorization
- Validation
- Pagination
- Filtering
- Sorting
- Logging
- Error handling
- Notifications
- Date/time handling
- File handling
- Formatting
- Caching
- Retry logic

React examples:

```text
usePagination()
useDebounce()
useAuth()
usePermissions()
useCurrentUser()
useFileUpload()
useSearch()
```

Python examples:

```text
AuthService
NotificationService
StorageService
ValidationService
AuditService
```

Do not copy the same business logic into multiple locations.

## 6. Custom Hooks and Reusable UI Logic

For React, repeated stateful behavior should normally be extracted into reusable hooks.

A hook should represent reusable behavior, not simply hide an oversized component.

Do not create abstractions for one-off logic when the abstraction reduces clarity.

## 7. API Boundaries

Where appropriate, map external API responses into internal models/view models.

```text
External Response
      ↓
Validation / Transformation
      ↓
Internal Model
      ↓
Business Logic / UI
```

Do not allow external wire formats to spread unnecessarily throughout the application.

### 7.1 Centralized API Client and Interceptors

Use one centralized, shared API client instance per application/service rather than
constructing ad hoc requests scattered across components/services. Use its
request/response interceptor (or equivalent) mechanism for cross-cutting concerns instead
of repeating them at every call site: attaching auth tokens, normalizing errors into the
app's internal error shape (see Section 18), and handling 401/token-refresh centrally
(refresh-and-retry once, then redirect to login) rather than duplicating refresh logic per
call site.

React/TypeScript example (axios):

```ts
const api = axios.create({ baseURL: API_BASE_URL });

api.interceptors.request.use((config) => {
  config.headers.Authorization = `Bearer ${getAccessToken()}`;
  return config;
});

api.interceptors.response.use(
  (response) => response,
  (error) => handleApiError(error), // normalize errors + centralize 401 refresh
);
```

Python example (httpx):

```python
class ApiClient(httpx.Client):
    def __init__(self):
        super().__init__(
            base_url=API_BASE_URL,
            event_hooks={"request": [attach_auth_header], "response": [handle_api_error]},
        )
```

Do not attach auth headers or error handling manually in each component/service that
calls the API — that duplication is exactly what Section 5 (Reusability) exists to
prevent.

