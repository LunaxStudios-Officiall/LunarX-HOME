# Security test report

PASS: Python syntax and JSON parsing; safe archive member rules; storage path traversal rejection; symlink-aware directory accounting; local hash verification; broker action allow-list; no secret-bearing runtime files in the release package; no arbitrary shell command input in the direct broker.

Not run: external penetration testing, live reverse-proxy TLS, PAM behavior on the supported Ubuntu releases, SELinux/AppArmor policy, real Tailscale ACLs and live Guacamole token consumption.
