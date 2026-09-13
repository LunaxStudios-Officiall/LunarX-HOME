# Update manifest specification

The release manifest records product, semantic version, exact relative files, byte sizes, SHA-256 values, catalog count and exclusions. Evidence files are generated from the same clean source but are excluded from the manifest hash scope to avoid circular checksums. `SHA256SUMS.txt` covers the packaged tree evidence and final archive sidecar.
