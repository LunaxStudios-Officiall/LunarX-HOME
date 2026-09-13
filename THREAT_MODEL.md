# Threat model

Assets: user files/photos, account metadata, session tokens, provider credentials and host service control.

Threats: path traversal, symlink escape, ZIP bombs, credential leakage, CSRF, brute force, arbitrary helper commands, unavailable provider misrepresentation and destructive disk enrollment.

Controls: canonical scoped paths, no symlink trees, bounded archive sizes, streaming uploads, HttpOnly/CSRF/origin/rate limits, hash-only local passwords, one-time setup token, allow-listed broker actions, structured errors, capability annotations and no automatic formatting.

Residual risk: host package supply chain, operator-chosen remote catalog URLs, reverse-proxy/TLS configuration, native PAM and desktop stack behavior remain deployment responsibilities.
