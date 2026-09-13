# Security model

- Sessions use random HttpOnly SameSite cookies, a separate CSRF token, origin checks and rate-limited login attempts.
- Local accounts use scrypt hashes with PBKDF2 fallback; plaintext passwords are retained only in encrypted process memory for the short-lived native Desktop handoff.
- The first-run token is high entropy, read from a mode-0600 file, shown by the installer, consumed once and deleted after administrator creation.
- Storage paths reject traversal, hidden/unapproved names, symlinked components and unsafe ZIP members. Uploads stream through a temporary file and use projected quota checks.
- The broker accepts an explicit action allow-list and emits stable error codes. Shell strings, arbitrary commands, raw stderr and user-supplied paths never cross the UI boundary.
- The browser response has nosniff, same-origin referrer, restrictive permissions and CSP headers. Guacamole embedding uses same-origin framing only.

See `THREAT_MODEL.md`, `SECURITY_CHANGES.md` and `SECURITY_TEST_REPORT.md` for the release evidence.
