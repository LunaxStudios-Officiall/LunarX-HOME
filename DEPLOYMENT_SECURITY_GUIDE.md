# Deployment security guide

Place the portal behind TLS, set `LUNARX_SECURE_COOKIE=1`, restrict the bind address/reverse proxy to the intended LAN or tailnet, review provider packages and catalog source URLs, keep the config root mode-restricted, and rotate host credentials using the provider-native workflow. Do not expose `/api/setup` beyond the initial trusted network and verify it is disabled after onboarding.
