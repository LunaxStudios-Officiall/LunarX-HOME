# Security changes

- Added one canonical path resolver and symlink-aware storage checks.
- Added local scrypt/PBKDF2 password hashes and one-time first-run setup token invalidation.
- Added structured broker error codes and removed raw helper response diagnostics from browser output.
- Added logical quota preflight for upload, copy, extract and compression paths.
- Added catalog URL-scheme validation and runtime compatibility annotations.
- Kept cookies, CSRF, origin, rate limiting, safe ZIP extraction, upload streaming, no-debug and secret exclusion controls from the baseline.
- Changed framing from a blanket deny to same-origin-only so the existing Desktop integration can be embedded safely.
