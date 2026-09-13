# QA Review 1 — Functional/UI Regression — LunarX Home 3.1.2

**Decision: PASS**

The production frontend assets were executed in real Chromium through Playwright at every requested viewport:

- Phone: 360×800, 390×844, 412×915, 430×932
- Tablet: 768×1024, 820×1180, 1024×1366
- Desktop: 1280×720, 1366×768, 1440×900, 1920×1080

Final consolidated run: **356/356 checks passed**, with **0 console/page errors**.

The run performs actual click events for all personal/admin sidebar destinations, checks the visible destination, single active/`aria-current` state, title/kicker and destination loader calls, rejects an invalid route without corrupting state, exercises the mobile drawer open→navigate→close→reopen path, Escape/outside-click close, computed body scroll lock, focus return and focus-visible styling.

It also covers first-run setup, real async login, logout, normal-user admin-nav hiding, Discover/Installed/Updates tabs, application details metadata, dialog viewport containment, local logo decode, reduced-motion preference, 44px drawer-range touch targets and horizontal document overflow on every route/viewport. Representative application screenshots are captured at 390×844 and 1440×900.

The QA host administratively blocks browser navigation to local HTTP. The browser runner therefore executes the exact packaged HTML/CSS/JS in Chromium at an intercepted HTTPS origin and intercepts API responses deterministically. Backend HTTP/first-run/install/update behavior is independently exercised by the Python integration suite and QA Review 2.
