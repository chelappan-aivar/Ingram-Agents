---
inclusion: always
---

<!-- GENERATED FILE — do not edit directly. Source: project-rules.md (Configuration, Secrets, Auth & RBAC) -->

## 8. Configuration and Environment Variables

Never hardcode:

- API URLs
- Credentials
- Tokens
- Database passwords
- Private keys
- Environment-specific configuration
- Secrets

Use environment variables, configuration systems, or secret managers.

Validate required configuration at startup where practical.

Never expose server-side secrets to client-side code.

Commit a `.env.example` (or equivalent) listing required variable names with no real
values. Never commit actual `.env` files; ensure they are excluded via `.gitignore`.

## 9. Secrets

Never commit passwords, API keys, access tokens, private keys, database credentials,
or session secrets.

If a secret is exposed:

1. Treat it as compromised.
2. Rotate/revoke it.
3. Remove it from source control/history where required.
4. Investigate usage.
5. Add preventive scanning.

## 10. Authentication

Use the authentication approach appropriate to the application's architecture and
threat model.

For web applications, where applicable:

- Prefer secure HttpOnly cookies for session/refresh credentials.
- Use short-lived access credentials when token-based authentication is required.
- Use refresh-token rotation where appropriate.
- Do not store sensitive authentication tokens in localStorage by default.
- Never put credentials in URLs.
- Never log tokens.
- Support session expiration and revocation.

### 10.1 Password Storage

- Hash passwords with a memory-hard algorithm — Argon2id (preferred), scrypt, or bcrypt
  with a cost factor tuned to current hardware (re-tune periodically as hardware gets
  faster). Never use MD5, SHA-1, SHA-256, or an un-salted/un-stretched hash for password
  storage.
- Use the language/framework's vetted password-hashing library (e.g. `argon2-cffi`,
  `bcrypt`, `passlib`) rather than a hand-rolled implementation — do not roll custom
  cryptography (see Section 12.1).
- Enforce a minimum password policy (length over complexity rules) and check new
  passwords against a breached-password list (e.g. Have I Been Pwned range API) where
  practical.
- Never store or log a plaintext password, even transiently or in error messages.

### 10.2 Multi-Factor Authentication

- Require MFA (TOTP, WebAuthn/passkeys, or push) for administrative accounts,
  privileged roles, and any access to production systems or sensitive data.
- Prefer WebAuthn/passkeys or TOTP over SMS-based OTP where feasible — SMS is
  vulnerable to SIM-swapping.
- Provide a secure account-recovery path for lost MFA devices that does not silently
  bypass the second factor.

### 10.3 Brute-Force and Account Lockout Protection

- Rate-limit authentication endpoints (login, password reset, MFA verification)
  separately from general API rate limits (see Section 22).
- Apply exponential backoff or temporary lockout after repeated failed attempts for a
  given account/IP, and alert on abnormal failure patterns.
- Use a CAPTCHA or equivalent challenge when automated abuse is detected, not as the
  first line of defense.
- Return the same generic error for "wrong password" and "unknown user" to avoid
  username enumeration.

## 11. Authorization and RBAC

Authentication answers who the user is; authorization determines what they can do.

Authorization must be enforced server-side.

Use roles, permissions, scopes, and resource ownership as appropriate.

Prefer centralized permission definitions over scattered magic strings.

Frontend checks are UX controls, never the security boundary.

### 11.1 Permission Model

- Define permissions as `resource:action` strings (e.g., `invoice:read`, `invoice:write`,
  `user:delete`) in one centralized registry/constant — never hardcode or repeat
  permission/role strings as inline literals across the codebase.
- Roles are named bundles of permissions, not a substitute for checking permissions
  directly — check permissions in business logic, not role names, so roles can be
  redefined without touching every check site.
- For resource-level access ("can this user edit *this* invoice"), combine a permission
  check with an explicit ownership/resource check — a role-only check isn't sufficient
  once ownership matters.

### 11.2 Backend Enforcement

- Enforce authorization in one place per request path (middleware/guard/decorator) — not
  scattered inline `if` checks duplicated across handlers.
- Default-deny: an endpoint with no explicit permission check must reject the request,
  not allow it.
- Multi-tenant systems must scope every query by tenant in addition to permission
  checks — a permission check alone doesn't prevent cross-tenant data leaks.

### 11.3 Frontend Consumption

- Read permissions from the authenticated session (e.g. a `usePermissions()` /
  `useCurrentUser()` hook — see Section 5), not re-derived from local state, hardcoded
  role checks (`if (user.role === "admin")` scattered across components), or guessed
  from UI context.
- Use permission checks to hide/disable UI the user can't act on — but the API call must
  still be protected server-side regardless of what the UI shows.

### 11.4 Testing

- Authorization tests must include negative cases: verify a user *without* the
  permission is actually denied, not just that an authorized user succeeds.

## 12. Defense in Depth

Apply security at appropriate layers:

```text
UI
 ↓
API
 ↓
Business Logic
 ↓
Database
 ↓
Infrastructure
```

A frontend permission check must never replace backend authorization.

### 12.1 Encryption and Transport Security

- Encrypt data in transit (TLS) for all network communication, internal and external.
- Encrypt sensitive data at rest (databases, object storage, backups, logs) using the
  platform's standard encryption or a managed KMS; do not roll custom cryptography.
- Configure CORS with an explicit origin allow-list; never combine a wildcard origin
  (`*`) with `credentials: true`.
- Rotate encryption keys and certificates on a defined schedule and before expiry.

### 12.2 CSRF Prevention

- For any endpoint that relies on cookies for authentication, protect state-changing
  requests (POST/PUT/PATCH/DELETE) against cross-site request forgery: set session
  cookies with `SameSite=Lax` or `Strict`, and add an anti-CSRF token (double-submit
  cookie or synchronizer token) for cross-site cookie use cases `SameSite` alone
  doesn't cover.
- Token-based APIs (Authorization header, not cookies) are inherently CSRF-resistant —
  don't add CSRF tokens where there's no ambient cookie-based auth to forge.
- Verify the `Origin`/`Referer` header as defense in depth for state-changing requests
  where feasible, in addition to (not instead of) the above.

### 12.3 Security Response Headers

Set security headers appropriate to the application on every HTTP response, where the
framework doesn't already default them:

```text
Content-Security-Policy     restrict script/style/frame sources; avoid unsafe-inline
Strict-Transport-Security   enforce HTTPS, include subdomains once verified safe
X-Content-Type-Options      nosniff
X-Frame-Options / frame-ancestors   prevent clickjacking unless embedding is required
Referrer-Policy              strict-origin-when-cross-origin or stricter
Permissions-Policy           disable unused browser features (camera, geolocation, etc.)
```

Use the framework's/proxy's built-in security-headers middleware rather than
hand-setting each header per response.

