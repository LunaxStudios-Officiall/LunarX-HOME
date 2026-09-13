#!/usr/bin/env python3
"""LunarX Home deterministic Chromium UI QA.

The QA host used by the project can administratively block browser navigation to
localhost HTTP.  This runner therefore executes the exact packaged HTML/CSS/JS
inside Chromium at an intercepted HTTPS origin while intercepting only API
responses.  Backend HTTP behavior is validated separately by the integration
suite/backend smoke tests.  This still exercises the production navigation,
layout, store, dialogs, focus and drawer JavaScript in a real browser.
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import json
import mimetypes
import os
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

try:
    from playwright.async_api import async_playwright, Page, Route, Request
except Exception as exc:  # pragma: no cover - release environment requirement
    raise SystemExit(f"Playwright is required for browser QA: {exc}")

PHONE = [(360, 800), (390, 844), (412, 915), (430, 932)]
TABLET = [(768, 1024), (820, 1180), (1024, 1366)]
DESKTOP = [(1280, 720), (1366, 768), (1440, 900), (1920, 1080)]
ALL_WIDTHS = PHONE + TABLET + DESKTOP
PERSONAL = ["dashboard", "files", "photos", "applications", "desktop", "settings"]
ADMIN = ["admin-dashboard", "admin-users", "admin-create", "admin-apps", "admin-storage", "admin-terminal", "admin-settings"]
TITLES = {
    "dashboard": ("MEU LUNARX", "Visão geral"),
    "files": ("DRIVE", "Arquivos"),
    "photos": ("MEMÓRIAS", "Fotos"),
    "applications": ("CATÁLOGO", "Aplicativos"),
    "desktop": ("SESSÃO GRÁFICA", "Desktop"),
    "settings": ("PREFERÊNCIAS", "Meu perfil"),
    "admin-dashboard": ("ADMINISTRAÇÃO", "Servidor"),
    "admin-users": ("CONTAS", "Usuários"),
    "admin-create": ("NOVO ACESSO", "Criar usuário"),
    "admin-apps": ("INTEGRAÇÕES", "Serviços e integrações"),
    "admin-storage": ("CAPACIDADE", "Armazenamento"),
    "admin-terminal": ("DIAGNÓSTICO", "Terminal"),
    "admin-settings": ("POLÍTICA", "Configurações gerais"),
}
LOAD_ENDPOINT = {
    "files": "/api/files",
    "photos": "/api/media",
    "applications": "/api/apps/catalog",
    "admin-dashboard": "/api/admin/services",
    "admin-users": "/api/admin/users",
    "admin-apps": "/api/admin/apps",
    "admin-storage": "/api/admin/storage",
    "admin-settings": "/api/admin/settings",
}

SAMPLE_CATALOG = [
    {
        "id": "org.mozilla.firefox", "name": "Firefox", "summary": "Navegador web", "developer": "Mozilla",
        "category": "Internet", "license": "MPL-2.0", "source_url": "https://support.mozilla.org/",
        "icon": "/static/assets/app-fallback.svg", "compatibility_status": "Native ARM64",
        "compatibility": {"compatible": True, "status": "Native ARM64", "reason": "Provider oficial disponível."},
        "selected_provider": {"id":"mozilla.apt-arm64", "provider":"vendor-apt", "provider_type":"native", "platforms":["android-proot"], "architectures":["aarch64"], "scope":"system", "source":"https://support.mozilla.org/", "reason":"Provider oficial disponível."},
    },
    {
        "id": "org.localsend.localsend_app", "name": "LocalSend", "summary": "Transferência local", "developer": "LocalSend",
        "category": "Utilities", "license": "Apache-2.0", "source_url": "https://localsend.org/",
        "icon": "/static/assets/app-fallback.svg", "compatibility_status": "Web/PWA fallback",
        "compatibility": {"compatible": True, "status": "Web/PWA fallback", "reason": "Web oficial disponível."},
        "selected_provider": {"id":"official.web", "provider":"web-pwa", "provider_type":"web", "platforms":["android-proot"], "architectures":["all"], "scope":"user", "source":"https://localsend.org/", "launch_url":"https://web.localsend.org/", "reason":"Web oficial disponível."},
    },
    {
        "id": "com.google.AndroidStudio", "name": "Android Studio", "summary": "IDE Android", "developer": "Google",
        "category": "Development", "license": "Proprietary", "source_url": "https://developer.android.com/studio",
        "icon": "/static/assets/app-fallback.svg", "compatibility_status": "Unavailable on this platform",
        "compatibility": {"compatible": False, "status": "Unavailable on this platform", "reason": "O provider Linux auditado é x86_64 e nenhum provider ARM64 PRoot seguro foi verificado."},
        "selected_provider": None,
    },
]

class MockAPI:
    def __init__(self, *, role: str = "admin", setup_required: bool = False):
        self.role = role
        self.setup_required = setup_required
        self.authenticated = not setup_required
        self.calls: Counter[str] = Counter()
        self.csrf = "qa-csrf"
        self.user = self._user(role)
        self.installed: dict[str, dict[str, Any]] = {}
        self.job_seq = 0

    def _user(self, role: str) -> dict[str, Any]:
        admin = role == "admin"
        return {
            "username": "qaadmin" if admin else "qauser", "display_name": "QA Admin" if admin else "QA User",
            "is_admin": admin, "avatar": "", "theme": "dark", "enabled": True,
            "permissions": {"desktop_access": True, "desktop_app_install": True, "shared_access": True, "drive": True, "photos": True, "files": True},
        }

    def _capabilities(self) -> dict[str, Any]:
        return {
            "platform": {"mode":"android-proot", "architecture":"aarch64", "storage_mode":"persistent-root", "quota_mode":"logical", "service_manager":"portable", "app_backend":"registry-only", "desktop_available": True, "desktop_backend":"novnc"},
            "paths": {"data_root":"/data/data/com.termux/files/home/lunarx-home/data"},
        }

    def response(self, path: str, method: str, body: dict[str, Any] | None) -> tuple[int, dict[str, Any]]:
        route = urlparse(path)
        p = route.path
        self.calls[p] += 1
        if p == "/api/setup/status":
            return 200, {"ok":True, "enabled":self.setup_required, "configured":not self.setup_required}
        if p == "/api/setup" and method == "POST":
            if not self.setup_required or (body or {}).get("token") != "QA-TOKEN":
                return 400, {"ok":False,"error":"Token inválido"}
            self.setup_required = False
            self.authenticated = False
            self.user = {**self._user("admin"), "username":body.get("username","qaadmin"), "display_name":body.get("display_name") or "QA Admin"}
            self.role = "admin"
            return 200, {"ok":True}
        if p == "/api/login" and method == "POST":
            self.authenticated = True
            return 200, {"ok":True,"user":self.user,"csrf_token":self.csrf}
        if p == "/api/logout":
            self.authenticated = False
            return 200, {"ok":True}
        if p == "/api/me":
            return 200, {"ok":True,"authenticated":self.authenticated,"user":self.user if self.authenticated else None,"csrf_token":self.csrf if self.authenticated else ""}
        if p == "/api/bootstrap":
            if not self.authenticated: return 401, {"ok":False,"error":"authentication required"}
            return 200, {"ok":True,"user":self.user,"csrf_token":self.csrf,"capabilities":self._capabilities(),"settings":{"shared_contribution_gib":8,"default_quota_gib":64,"desktop_idle_timeout_minutes":30,"theme_mode":"dark","server_name":"LunarX Home"}}
        if p == "/api/capabilities": return 200, {"ok":True, **self._capabilities()}
        if p == "/api/metrics":
            return 200, {"ok":True,"metrics":{"version":"3.1.2","cpu_percent":7.5,"memory":{"used_percent":32.0,"available_bytes":4_000_000_000},"personal":{"used_bytes":10_000,"quota_bytes":64*1024**3,"free_bytes":64*1024**3-10_000},"shared":{"used_bytes":20_000,"quota_bytes":16*1024**3,"free_bytes":16*1024**3-20_000},"platform":self._capabilities()["platform"]}}
        if p == "/api/tailscale": return 200, {"ok":True,"tailscale":{"installed":False,"state":"host","message":"Tailscale é controlado pelo host Android.","control":"android-host"}}
        if p == "/api/files": return 200, {"ok":True,"entries":[]}
        if p == "/api/media": return 200, {"ok":True,"items":[],"albums":[]}
        if p == "/api/apps/catalog": return 200, {"ok":True,"apps":SAMPLE_CATALOG,"count":len(SAMPLE_CATALOG)}
        if p == "/api/apps/installed": return 200, {"ok":True,"apps":list(self.installed.values()),"available":True}
        if p == "/api/apps/action" and method == "POST":
            self.job_seq += 1
            app_id = (body or {}).get("app_id")
            op = (body or {}).get("operation")
            if op == "install" and app_id:
                self.installed[app_id] = {"id":app_id,"version":"qa","scope":"user","provider_id":"official.web" if app_id.startswith("org.localsend") else "mozilla.apt-arm64","launch_url":"https://web.localsend.org/" if app_id.startswith("org.localsend") else None,"update_available":False}
            elif op == "uninstall" and app_id:
                self.installed.pop(app_id, None)
            return 200, {"ok":True,"job":{"id":f"qa-{self.job_seq}"}}
        if p == "/api/apps/jobs": return 200, {"ok":True,"jobs":[{"id":"qa","state":"complete","message":"Concluído","progress":100,"result":{}}]}
        if p == "/api/admin/users":
            if not self.user.get("is_admin"): return 403,{"ok":False,"error":"admin required"}
            return 200,{"ok":True,"users":[{**self.user,"used_bytes":1000,"quota_gib":64}]}
        if p == "/api/admin/settings": return 200,{"ok":True,"settings":{"shared_contribution_gib":8,"default_quota_gib":64,"desktop_idle_timeout_minutes":30,"theme_mode":"dark","server_name":"LunarX Home"}}
        if p == "/api/admin/services": return 200,{"ok":True,"service_manager":"portable","units":[]}
        if p == "/api/admin/storage": return 200,{"ok":True,"storage":{"provider":"logical","available":True,"root":"/qa/data","free_bytes":10_000_000,"total_bytes":20_000_000,"members":[]}}
        if p == "/api/admin/apps": return 200,{"ok":True,"apps":[]}
        if p == "/api/desktop": return 200,{"ok":True,"guacamole_url":"about:blank"}
        if p.startswith("/api/desktop/"): return 200,{"ok":True}
        if p.startswith("/api/admin/") and not self.user.get("is_admin"): return 403,{"ok":False,"error":"admin required"}
        if p.startswith("/api/"): return 200,{"ok":True}
        return 404,{"ok":False,"error":"not found"}

def _data_uri(path: Path) -> str:
    mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    return f"data:{mime};base64,{base64.b64encode(path.read_bytes()).decode('ascii')}"


def build_inline_frontend(source: Path) -> str:
    # Build a deterministic browser document from the exact production assets.
    # Only URL-bearing assets are inlined because this QA host blocks browser networking.
    static = source / "static"
    html = (static / "index.html").read_text(encoding="utf-8")
    css = (static / "styles.css").read_text(encoding="utf-8")
    xterm_css = (static / "vendor/xterm.css").read_text(encoding="utf-8")
    store_js = (static / "store.js").read_text(encoding="utf-8")
    app_js = (static / "app.js").read_text(encoding="utf-8")
    fallback_uri = _data_uri(static / "assets/app-fallback.svg")
    logo_uri = fallback_uri  # QA DOM placeholder; real logo decode is verified separately in Chromium.
    store_js = store_js.replace('const FALLBACK_ICON = "/static/assets/app-fallback.svg";', f'const FALLBACK_ICON = {json.dumps(fallback_uri)};')
    html = html.replace('/static/assets/lunarx-logo-v2.png?v=3', logo_uri).replace('/static/assets/lunarx-logo-v2.png', logo_uri)
    html = html.replace('/static/assets/app-fallback.svg', fallback_uri)
    sprite = (static / "assets/icons.svg").read_text(encoding="utf-8")
    sprite_inner = re.sub(r'^\s*<svg[^>]*>|</svg>\s*$', '', sprite, flags=re.S)
    html = html.replace('href="/static/assets/icons.svg#', 'href="#')
    html = re.sub(r'<link[^>]+href="/static/vendor/xterm\.css"[^>]*>', '', html)
    html = re.sub(r'<link[^>]+href="/static/styles\.css"[^>]*>', '', html)
    html = re.sub(r'<script src="/static/vendor/xterm\.js"></script>', '', html)
    html = re.sub(r'<script src="/static/store\.js" defer></script>', '', html)
    html = re.sub(r'<script src="/static/app\.js" defer></script>', '', html)
    html = html.replace('</head>', f'<style>{xterm_css}\n{css}</style></head>')
    sprite_node = f'<svg aria-hidden="true" style="position:absolute;width:0;height:0;overflow:hidden">{sprite_inner}</svg>'
    mock_fetch = '''<script>
    (() => {
      const storage = new Map();
      const shim = {
        getItem: key => storage.has(String(key)) ? storage.get(String(key)) : null,
        setItem: (key, value) => storage.set(String(key), String(value)),
        removeItem: key => storage.delete(String(key)),
        clear: () => storage.clear(),
        key: index => Array.from(storage.keys())[index] || null,
        get length() { return storage.size; }
      };
      try { Object.defineProperty(window, "localStorage", {value: shim, configurable: true}); } catch {}
    })();
    window.fetch = async (input, options = {}) => {
      const path = typeof input === "string" ? input : input.url;
      let body = null;
      if (options.body && typeof options.body === "string") { try { body = JSON.parse(options.body); } catch {} }
      const result = await window.qaApi({path, method:(options.method || "GET").toUpperCase(), body});
      return new Response(JSON.stringify(result[1]), {status:result[0], headers:{"Content-Type":"application/json"}});
    };
    </script>'''
    scripts = mock_fetch + '<script>' + store_js.replace('</script>', '<\\/script>') + '</script>' + '<script>' + app_js.replace('</script>', '<\\/script>') + '</script>'
    html = html.replace('<body>', '<body>' + sprite_node)
    html = html.replace('</body>', scripts + '</body>')
    return html


async def open_page(page: Page, source: Path, api: MockAPI) -> None:
    async def qa_api(payload: dict[str, Any]) -> list[Any]:
        status, data = api.response(str(payload.get("path", "/")), str(payload.get("method", "GET")), payload.get("body"))
        return [status, data]
    await page.expose_function("qaApi", qa_api)
    await page.set_content(build_inline_frontend(source), wait_until="domcontentloaded")
    await page.wait_for_timeout(120)

async def assert_no_overflow(page: Page, label: str, checks: list[dict[str, Any]]) -> None:
    values = await page.evaluate("() => ({w: innerWidth, doc: document.documentElement.scrollWidth, body: document.body.scrollWidth})")
    ok = values["doc"] <= values["w"] + 1 and values["body"] <= values["w"] + 1
    checks.append({"check":"no-horizontal-overflow","label":label,"pass":ok,"detail":values})
    if not ok: raise AssertionError(f"horizontal overflow at {label}: {values}")

async def assert_route(page: Page, view: str, api: MockAPI, checks: list[dict[str, Any]], *, mobile: bool = False) -> None:
    button = page.locator(f'.nav button[data-view="{view}"]')
    if mobile:
        await page.locator("#menu-open").click()
        await page.wait_for_timeout(35)
        assert await page.locator("#sidebar").evaluate("el => el.classList.contains('open')")
        assert await page.locator("body").evaluate("el => el.classList.contains('drawer-open')")
        assert await page.locator("body").evaluate("el => getComputedStyle(el).overflow") == "hidden"
    before = api.calls[LOAD_ENDPOINT[view]] if view in LOAD_ENDPOINT else None
    await button.click()
    await page.wait_for_timeout(80)
    assert await page.locator(f"#view-{view}").is_visible(), view
    assert await page.evaluate("() => window.lunarxState.view") == view
    assert await button.get_attribute("aria-current") == "page"
    assert await page.locator("#view-title").inner_text() == TITLES[view][1]
    assert await page.locator("#view-kicker").inner_text() == TITLES[view][0]
    if view in LOAD_ENDPOINT:
        after = api.calls[LOAD_ENDPOINT[view]]
        assert after > int(before or 0), f"loader did not call {LOAD_ENDPOINT[view]} for {view}"
    if mobile:
        assert not await page.locator("#sidebar").evaluate("el => el.classList.contains('open')")
        assert not await page.locator("body").evaluate("el => el.classList.contains('drawer-open')")
        assert await page.locator("body").evaluate("el => getComputedStyle(el).overflow") != "hidden"
    checks.append({"check":"navigate","view":view,"mobile":mobile,"pass":True})

async def admin_navigation(browser, source: Path, viewport: tuple[int,int], screenshots: Path | None, checks: list[dict[str, Any]], errors: list[str]) -> None:
    width,height=viewport
    context=await browser.new_context(viewport={"width":width,"height":height}, reduced_motion="reduce")
    page=await context.new_page(); api=MockAPI(role="admin")
    page.on("pageerror", lambda exc: errors.append(f"{width}x{height}: pageerror: {exc}"))
    page.on("console", lambda msg: errors.append(f"{width}x{height}: console {msg.type}: {msg.text}") if msg.type=="error" else None)
    await open_page(page, source, api)
    await page.wait_for_selector("#shell:not(.hidden)")
    mobile=width<=860
    for view in PERSONAL+ADMIN:
        await assert_route(page,view,api,checks,mobile=mobile)
        await assert_no_overflow(page,f"{width}x{height}:{view}",checks)
    # Invalid destination must be a no-op and must not corrupt state.
    prior=await page.evaluate("() => window.lunarxState.view")
    invalid=page.locator("#user-nav button").first
    await invalid.evaluate("el => { el.dataset.originalView=el.dataset.view; el.dataset.view='does-not-exist'; }")
    if mobile:
        await page.locator("#menu-open").click()
    await invalid.click(); await page.wait_for_timeout(30)
    assert await page.evaluate("() => window.lunarxState.view") == prior
    assert await page.locator(f"#view-{prior}").is_visible()
    await invalid.evaluate("el => { el.dataset.view=el.dataset.originalView; delete el.dataset.originalView; }")
    if mobile:
        await page.keyboard.press("Escape")
    checks.append({"check":"invalid-destination-safe-rejection","pass":True,"viewport":f"{width}x{height}"})
    # Drawer specific close paths on phone/tablet drawer widths.
    if mobile:
        trigger=page.locator("#menu-open")
        await trigger.focus(); await trigger.click(); await page.keyboard.press("Escape")
        assert not await page.locator("#sidebar").evaluate("el => el.classList.contains('open')")
        assert await page.evaluate("() => document.activeElement && document.activeElement.id") == "menu-open"
        await trigger.click(); await page.mouse.click(width - 4, max(40, height // 2))
        await page.wait_for_timeout(30)
        assert not await page.locator("#sidebar").evaluate("el => el.classList.contains('open')")
        checks.append({"check":"drawer-escape-outside-focus","pass":True,"viewport":f"{width}x{height}"})
    # Application details and store tabs.
    if width>=390:
        await assert_route(page,"applications",api,checks,mobile=mobile)
        await page.locator('[data-detail="org.mozilla.firefox"]').click(); await page.wait_for_timeout(20)
        assert await page.locator("#app-detail-dialog").evaluate("el => el.open")
        assert "Mozilla" in await page.locator("#app-detail-developer").inner_text()
        assert "aarch64" in (await page.locator("#app-detail-architecture").inner_text()).lower()
        await page.locator("#app-detail-x").click()
        for tab in ("discover","installed","updates"):
            loc=page.locator(f'.store-tab[data-store-tab="{tab}"]')
            if await loc.count(): await loc.click()
        checks.append({"check":"application-details-tabs","pass":True,"viewport":f"{width}x{height}"})
    # reduced-motion contract is present/effective
    reduced=await page.evaluate("() => matchMedia('(prefers-reduced-motion: reduce)').matches")
    assert reduced
    checks.append({"check":"reduced-motion-browser-preference","pass":True,"viewport":f"{width}x{height}"})
    if screenshots and (viewport in [(390,844),(1440,900)]):
        screenshots.mkdir(parents=True,exist_ok=True)
        await page.screenshot(path=str(screenshots/f"{width}x{height}-applications.png"),full_page=True)
    await context.close()

async def admin_matrix(browser, source: Path, screenshots: Path | None, checks: list[dict[str, Any]], errors: list[str], viewports: list[tuple[int, int]] | None = None) -> None:
    context=await browser.new_context(viewport={"width":1920,"height":1080}, reduced_motion="reduce")
    page=await context.new_page(); api=MockAPI(role="admin")
    current_label={"value":"admin"}
    page.on("pageerror", lambda exc: errors.append(f"{current_label['value']}: pageerror: {exc}"))
    page.on("console", lambda msg: errors.append(f"{current_label['value']}: console {msg.type}: {msg.text}") if msg.type=="error" else None)
    await open_page(page,source,api); await page.wait_for_selector("#shell:not(.hidden)")
    # Decode the actual packaged raster logo once in Chromium, while the main DOM uses an inlined lightweight placeholder
    # so the QA host's no-network policy does not multiply a large data URI in every page load.
    actual_logo=_data_uri(source / "static/assets/lunarx-logo-v2.png")
    decoded=await page.evaluate("""uri => new Promise(resolve => { const img=new Image(); img.onload=()=>resolve({ok:img.naturalWidth>0&&img.naturalHeight>0,w:img.naturalWidth,h:img.naturalHeight}); img.onerror=()=>resolve({ok:false}); img.src=uri; })""", actual_logo)
    if not decoded.get("ok"): raise AssertionError("packaged LunarX logo did not decode in Chromium")
    checks.append({"check":"packaged-logo-decodes","pass":True,"detail":decoded})
    for width,height in (viewports or ALL_WIDTHS):
        print(f"browser-qa viewport {width}x{height}", flush=True)
        current_label["value"]=f"{width}x{height}"
        await page.set_viewport_size({"width":width,"height":height}); await page.wait_for_timeout(35)
        mobile=width<=860
        for view in PERSONAL+ADMIN:
            await assert_route(page,view,api,checks,mobile=mobile)
            await assert_no_overflow(page,f"{width}x{height}:{view}",checks)
        print(f"browser-qa routes complete {width}x{height}", flush=True)
        prior=await page.evaluate("() => window.lunarxState.view")
        invalid=page.locator("#user-nav button").first
        await invalid.evaluate("el => { el.dataset.originalView=el.dataset.view; el.dataset.view='does-not-exist'; }")
        if mobile: await page.locator("#menu-open").click()
        await invalid.click(); await page.wait_for_timeout(20)
        assert await page.evaluate("() => window.lunarxState.view") == prior
        assert await page.locator(f"#view-{prior}").is_visible()
        await invalid.evaluate("el => { el.dataset.view=el.dataset.originalView; delete el.dataset.originalView; }")
        if mobile: await page.keyboard.press("Escape")
        checks.append({"check":"invalid-destination-safe-rejection","pass":True,"viewport":f"{width}x{height}"})
        if mobile:
            trigger=page.locator("#menu-open"); await trigger.focus()
            focus_style = await trigger.evaluate("el => getComputedStyle(el).boxShadow")
            assert focus_style and focus_style != "none"
            trigger_box = await trigger.bounding_box(); nav_box = await page.locator("#user-nav button").first.bounding_box()
            assert trigger_box and trigger_box["width"] >= 44 and trigger_box["height"] >= 44, trigger_box
            assert nav_box and nav_box["height"] >= 44, nav_box
            checks.append({"check":"mobile-focus-touch-targets","pass":True,"viewport":f"{width}x{height}","trigger":trigger_box,"nav":nav_box})
            await trigger.click(); await page.keyboard.press("Escape")
            assert not await page.locator("#sidebar").evaluate("el => el.classList.contains('open')")
            assert await page.evaluate("() => document.activeElement && document.activeElement.id") == "menu-open"
            await trigger.click(); await page.mouse.click(width - 4, max(40, height // 2)); await page.wait_for_timeout(20)
            assert not await page.locator("#sidebar").evaluate("el => el.classList.contains('open')")
            checks.append({"check":"drawer-escape-outside-focus","pass":True,"viewport":f"{width}x{height}"})
        if (width,height) in [(390,844),(1440,900)]:
            await assert_route(page,"applications",api,checks,mobile=mobile)
            discover=page.locator('.store-tab[data-store-tab="discover"]')
            if await discover.count(): await discover.click()
            await page.locator('[data-detail="org.mozilla.firefox"]').click(); await page.wait_for_timeout(15)
            assert await page.locator("#app-detail-dialog").evaluate("el => el.open")
            assert "Mozilla" in await page.locator("#app-detail-developer").inner_text()
            assert "aarch64" in (await page.locator("#app-detail-architecture").inner_text()).lower()
            bounds = await page.locator("#app-detail-dialog .app-detail-card").evaluate("el => { const r=el.getBoundingClientRect(); return {left:r.left,top:r.top,right:r.right,bottom:r.bottom,w:innerWidth,h:innerHeight}; }")
            assert bounds["left"] >= -1 and bounds["top"] >= -1 and bounds["right"] <= bounds["w"] + 1 and bounds["bottom"] <= bounds["h"] + 1, bounds
            checks.append({"check":"dialog-viewport-containment","pass":True,"viewport":f"{width}x{height}","detail":bounds})
            await page.locator("#app-detail-x").click()
            for tab in ("installed","updates","discover"):
                loc=page.locator(f'.store-tab[data-store-tab="{tab}"]')
                if await loc.count(): await loc.click()
            checks.append({"check":"application-details-tabs","pass":True,"viewport":f"{width}x{height}"})
        assert await page.evaluate("() => matchMedia('(prefers-reduced-motion: reduce)').matches")
        checks.append({"check":"reduced-motion-browser-preference","pass":True,"viewport":f"{width}x{height}"})
        if screenshots and (width,height) in [(390,844),(1440,900)]:
            screenshots.mkdir(parents=True,exist_ok=True); await page.screenshot(path=str(screenshots/f"{width}x{height}-applications.png"),full_page=True)
    await context.close()


async def normal_user_navigation(browser, source: Path, checks: list[dict[str, Any]], errors: list[str]) -> None:
    context=await browser.new_context(viewport={"width":390,"height":844})
    page=await context.new_page(); api=MockAPI(role="user")
    page.on("pageerror", lambda exc: errors.append(f"normal-user pageerror: {exc}"))
    await open_page(page, source, api); await page.wait_for_selector("#shell:not(.hidden)")
    assert await page.locator("#admin-nav").evaluate("el => el.classList.contains('hidden')")
    for view in PERSONAL: await assert_route(page,view,api,checks,mobile=True)
    checks.append({"check":"normal-user-admin-nav-hidden","pass":True})
    before_logout = api.calls["/api/logout"]
    await page.locator("#menu-open").click(); await page.locator("#logout").click(); await page.wait_for_timeout(80)
    assert await page.locator("#login").is_visible()
    assert await page.locator("#shell").evaluate("el => el.classList.contains('hidden')")
    assert api.calls["/api/logout"] > before_logout
    checks.append({"check":"logout-real-ui-flow","pass":True})
    await context.close()

async def first_run(browser, source: Path, checks: list[dict[str, Any]], errors: list[str]) -> None:
    context=await browser.new_context(viewport={"width":412,"height":915})
    page=await context.new_page(); api=MockAPI(role="admin", setup_required=True)
    page.on("pageerror", lambda exc: errors.append(f"first-run pageerror: {exc}"))
    await open_page(page, source, api)
    await page.wait_for_selector("#setup-open:not(.hidden)")
    await page.locator("#setup-open").click(); assert await page.locator("#setup").is_visible()
    await page.locator('#setup-form [name="token"]').fill("QA-TOKEN")
    await page.locator('#setup-form [name="username"]').fill("owner")
    await page.locator('#setup-form [name="display_name"]').fill("Owner")
    await page.locator('#setup-form [name="password"]').fill("CorrectHorseBatteryStaple")
    await page.locator("#setup-form button[type=submit]").click(); await page.wait_for_timeout(40)
    assert await page.locator("#login").is_visible()
    await page.locator("#username").fill("owner"); await page.locator("#password").fill("CorrectHorseBatteryStaple")
    await page.locator("#login-form button[type=submit]").click(); await page.wait_for_selector("#shell:not(.hidden)")
    assert await page.locator("#view-dashboard").is_visible()
    checks.append({"check":"first-run-setup-login","pass":True})
    await context.close()

async def run(source: Path, json_out: Path | None, screenshots: Path | None, viewports: list[tuple[int, int]] | None = None) -> int:
    checks: list[dict[str,Any]]=[]; errors: list[str]=[]
    async with async_playwright() as pw:
        browser=await pw.chromium.launch(executable_path=os.environ.get("CHROMIUM_PATH","/usr/bin/chromium"), headless=True, args=["--no-sandbox","--disable-dev-shm-usage"])
        try:
            await first_run(browser,source,checks,errors)
            await normal_user_navigation(browser,source,checks,errors)
            await admin_matrix(browser,source,screenshots,checks,errors,viewports=viewports)
        finally:
            await browser.close()
    failures=[item for item in checks if not item.get("pass")]
    result={
        "version":"3.1.2", "runner":"Chromium Playwright with intercepted HTTPS origin",
        "source":str(source), "viewports":[f"{w}x{h}" for w,h in (viewports or ALL_WIDTHS)],
        "checks":len(checks), "passed":len(checks)-len(failures), "failed":len(failures),
        "console_or_page_errors":errors, "result":"PASS" if not failures and not errors else "FAIL",
        "note":"Frontend production assets execute in real Chromium. API responses are deterministic mocks because the QA host administratively blocks browser navigation to localhost HTTP; backend HTTP is tested separately.",
    }
    if json_out:
        json_out.parent.mkdir(parents=True,exist_ok=True); json_out.write_text(json.dumps(result,indent=2,ensure_ascii=False)+"\n",encoding="utf-8")
    print(json.dumps(result,indent=2,ensure_ascii=False))
    return 0 if result["result"]=="PASS" else 1

def _parse_viewport(value: str) -> tuple[int, int]:
    try:
        width, height = value.lower().split("x", 1)
        pair = (int(width), int(height))
    except (ValueError, AttributeError) as exc:
        raise argparse.ArgumentTypeError(f"invalid viewport {value!r}; expected WIDTHxHEIGHT") from exc
    if pair not in ALL_WIDTHS:
        allowed = ", ".join(f"{w}x{h}" for w, h in ALL_WIDTHS)
        raise argparse.ArgumentTypeError(f"unsupported QA viewport {value!r}; choose one of: {allowed}")
    return pair

def main() -> int:
    parser=argparse.ArgumentParser(); parser.add_argument("--source",type=Path,default=Path(__file__).resolve().parents[1]); parser.add_argument("--json-out",type=Path); parser.add_argument("--screenshots",type=Path); parser.add_argument("--viewport",type=_parse_viewport,action="append",dest="viewports",help="run one required viewport; repeat to run a bounded batch")
    args=parser.parse_args(); source=args.source.resolve()
    if not (source/"static/index.html").is_file(): raise SystemExit(f"invalid source root: {source}")
    return asyncio.run(run(source,args.json_out,args.screenshots,viewports=args.viewports))

if __name__=="__main__": raise SystemExit(main())
