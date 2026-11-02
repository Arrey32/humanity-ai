# Accounts, privacy and mobile install

## Accounts

The API supports email/password registration, login, current-account lookup and account deletion. Passwords are stored as salted scrypt hashes; bearer tokens are HMAC-SHA256 signed and expire after `AUTH_TOKEN_HOURS`. Set `AUTH_REQUIRED=true` and provide a random `AUTH_SECRET` of at least 32 characters before exposing the service. Keep the secret private and rotate it if exposed.

Example:

```bash
curl -X POST http://localhost:8000/api/auth/register -H 'Content-Type: application/json' -d '{"email":"you@example.com","password":"use-a-long-unique-password"}'
```

Use the returned token as `Authorization: Bearer TOKEN` for `/api/auth/me`, `/api/auth/me` DELETE and chat when `AUTH_REQUIRED=true`. The browser UI includes sign-in, account creation, sign-out and account deletion. It keeps the bearer token in sessionStorage for the current browser tab/session and sends it with chat requests. Account deletion removes the account row. Conversation rows are not yet associated with accounts; do not enable global conversation storage for a multi-user deployment until that is implemented.

## Privacy status

- Chat history persistence defaults off.
- No analytics or ad trackers are bundled.
- Account email and password hash are stored when a user registers.
- Account deletion removes the user record.
- No self-service export, email verification, password reset, MFA, consent management, or account-linked conversation deletion yet.
- Requests are sent to the configured model provider; review that provider's retention policy before using sensitive data.

## Mobile

The web interface is now a Progressive Web App shell. On HTTPS, users can use their browser's Install/Add to Home Screen feature. It caches the app shell only; chat requires a network connection. This is not a native iOS/Android app distributed through app stores.
