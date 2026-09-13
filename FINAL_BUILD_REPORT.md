# LunarX Home 3.1.2 final build report

Release intent: final hardening patch continued from the existing 3.1.2 working tree produced from the canonical 3.1.1 baseline. No 2.1/3.0 re-merge or architecture rewrite was performed.

## Delivered hardening

- Correct interpolated navigation destination guard plus real browser click regression coverage.
- Async login `currentTarget` lifecycle fix.
- Truthful install/access summary and configured bind/port use across server/controller.
- Automatic existing-install rollback after failed activation/health verification.
- Portable uninstall stop/cleanup without user-data deletion.
- Final ARM64/PRoot provider audit: 154 apps, 306 provider records, 56 actionable, 1 source-only, 97 unavailable.
- Broker allow-list enforcement for privileged vendor providers and pinned-integrity package path.
- Focus/touch-target/drawer polish without changing the sidebar/hamburger navigation model.

## QA evidence before freezing the release archive

- Python suite: 60 tests pass after the last implementation change.
- QA Review 1 — Functional/UI: PASS, 356/356 Chromium checks across all 11 requested viewports, 0 console/page errors.
- QA Review 2 — Platform/Installer/Updater/Recovery: PASS, 38/38 disposable checks including real 3.1.1→3.1.2 update/preservation and injected-failure rollback.
- Python compilation, JavaScript syntax, shell syntax and JSON/catalog checks are required immediately before packaging.

QA Review 3 is intentionally an **external frozen-artifact gate**: the exact ZIP is hashed first, extracted to a clean directory, tested without mutation, and released only if that audit is PASS. Its final PASS/hash cannot be embedded into the already-frozen archive without changing that archive; the delivered artifact is therefore accompanied by the QA3 decision/hash in the final delivery response.

## Validation boundary

No physical Android handset or dedicated Ubuntu 24.04/26.04 systemd host is attached to this environment. Android/PRoot filesystem/runtime semantics were exercised in disposable isolated installs; native Ubuntu was simulated/static-validated. These are documented limitations, not hidden PASS claims.
