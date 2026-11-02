# Security notes

## Implemented in this prototype

- Passwords are hashed with scrypt and per-password random salts.
- Account tokens are signed and expire; account existence is checked on authenticated requests.
- API keys, when configured, are accepted alongside signed account tokens rather than blocking them.
- Request body and message lengths are bounded.
- Chat and account routes have a basic per-process in-memory rate limiter.
- Conversation records are user-scoped when storage is enabled; account deletion removes associated conversation records.
- Docker image runs as a non-root user and includes a health check.

## Known limitations

- No email verification, password reset, MFA, refresh-token rotation, or session management UI.
- No distributed rate limiting; multiple workers/instances can bypass the per-process aggregate limit.
- SQLite is not intended for multi-instance production scale.
- No independent penetration test, dependency audit, formal threat model, or external security certification has been completed.
- No content moderation or abuse-reporting workflow is provided.
- The PWA service worker caches the app shell, not private API responses.

## Operator requirements

Use HTTPS for all public deployments. Generate a unique random `AUTH_SECRET` of at least 32 characters. Keep provider keys and `.env` out of source control. Restrict database and volume access. Back up data securely and test restores. Apply dependency updates. Do not process sensitive or regulated information until the service has been independently assessed.

Report security issues privately to the project maintainers rather than publishing exploit details.
