(() => {
  "use strict";
  const $ = selector => document.querySelector(selector);
  const esc = value => String(value ?? "").replace(/[&<>"']/g, char => ({ "&":"&amp;", "<":"&lt;", ">":"&gt;", '"':"&quot;", "'":"&#39;" }[char]));
  const store = { tab:"discover", apps:[], installed:new Map(), poll:null };
  const FALLBACK_ICON = "/static/assets/app-fallback.svg";

  async function request(path, options = {}) {
    const headers = new Headers(options.headers || {});
    if (options.body) headers.set("Content-Type", "application/json");
    if (options.method && options.method !== "GET" && window.lunarxState?.csrf) headers.set("X-CSRF-Token", window.lunarxState.csrf);
    const response = await fetch(path, { ...options, headers, credentials:"same-origin" });
    const body = await response.json().catch(() => ({}));
    if (!response.ok || body.ok === false) throw Error(body.error || "Não foi possível concluir a ação.");
    return body;
  }

  const isAdmin = () => Boolean(window.lunarxState?.user?.is_admin);
  const installed = app => store.installed.get(app.id) || null;
  const provider = app => app.selected_provider || app.compatibility?.selected_provider || null;
  const providerKind = app => provider(app)?.provider || app.compatibility?.backend || app.backend || "provider";
  const providerType = app => provider(app)?.provider_type || (providerKind(app) === "web-pwa" ? "web/PWA" : "native");
  const providerLabel = app => ({ "flatpak-user":"Flatpak", "apt-system":"APT ARM64", "admin-apt-group":"APT global", "web-pwa":"Web/PWA", "ide-extension":"Extensão", "verified-source":"Fonte oficial", "vendor-apt":"APT oficial", "pinned-deb":"DEB verificado" }[providerKind(app)] || providerKind(app));
  const canLaunch = app => {
    const record = installed(app), selected = provider(app);
    if (record?.launch_url || selected?.launch_url) return true;
    const method = String(selected?.launch_method || app.launch_mode || "").toLowerCase();
    return Boolean(method && !["none", "not launchable", "admin-service", "external"].includes(method));
  };
  const canManage = app => {
    const record = installed(app), selected = provider(app);
    const system = record?.scope === "system" || selected?.scope === "system" || selected?.requires_admin || app.admin_only;
    return !system || isAdmin();
  };
  function actionState(app) {
    const record = installed(app), selected = provider(app);
    if (app.installable === false) return { label:"Ver fonte", disabled:false, operation:"source" };
    if (!app.compatibility?.compatible || !selected) return { label:"Indisponível", disabled:true, operation:"none" };
    if ((selected.requires_admin || app.admin_only) && !isAdmin() && !record) return { label:"Requer admin", disabled:true, operation:"none" };
    if (record) return canLaunch(app) ? { label:"Abrir", disabled:false, operation:"launch" } : { label:"Instalado", disabled:true, operation:"none" };
    return { label:selected.scope === "system" ? "Instalar no sistema" : selected.provider === "web-pwa" ? "Adicionar" : "Instalar", disabled:false, operation:"install" };
  }
  function visibleApps() {
    if (store.tab === "installed") return store.apps.filter(app => store.installed.has(app.id));
    if (store.tab === "updates") return store.apps.filter(app => store.installed.get(app.id)?.update_available);
    return store.apps;
  }
  function iconUrl(app) { return app.icon_url || app.icon || FALLBACK_ICON; }
  function safeIconMarkup(app, className = "") {
    const src = iconUrl(app);
    return `<img class="${esc(className)}" src="${esc(src)}" alt="" loading="lazy" referrerpolicy="no-referrer" onerror="this.onerror=null;this.src='${FALLBACK_ICON}'">`;
  }
  function providerChip(app) {
    const status = String(app.compatibility_status || app.compatibility?.status || "");
    const kind = providerKind(app);
    const cls = app.compatibility?.compatible ? (kind === "web-pwa" ? "web" : "available") : "unavailable";
    return `<span class="provider-chip ${cls}">${esc(status || providerLabel(app))}</span>`;
  }

  function render() {
    const grid = $("#app-store-grid"); if (!grid) return;
    const items = visibleApps();
    grid.innerHTML = items.map(app => {
      const unavailable = app.installable !== false && !app.compatibility?.compatible;
      const action = actionState(app), record = installed(app), selected = provider(app);
      const reason = unavailable ? (app.unavailable_reason || app.compatibility?.reason || "Nenhum provider compatível foi verificado para este dispositivo.") : "";
      const systemManaged = record?.scope === "system" || selected?.scope === "system";
      const update = Boolean(record?.update_available) && canManage(app);
      const removable = Boolean(record) && canManage(app);
      return `<article class="store-card ${unavailable ? "unavailable" : ""}" data-store-app="${esc(app.id)}">
        <div class="store-icon">${safeIconMarkup(app)}</div>
        <div class="store-copy"><span class="store-category">${esc(app.category || "Aplicativo")}</span><h4>${esc(app.name)}</h4><p>${esc(app.summary || "Descrição não informada pelo catálogo.")}</p><small>${esc(app.developer || "Desenvolvedor não informado")} · ${esc(app.license || "licença não informada")}</small><div class="store-provider-row">${providerChip(app)}${selected ? `<span class="scope-chip">${esc(providerLabel(app))}${systemManaged ? " · sistema" : ""}</span>` : ""}${record?.version ? `<span class="scope-chip">v${esc(record.version)}</span>` : ""}</div></div>
        ${reason ? `<div class="compatibility-note">${esc(reason)}</div>` : ""}
        <div class="store-actions"><button type="button" class="text-button" data-detail="${esc(app.id)}">Detalhes</button><button type="button" class="button ${action.disabled ? "secondary" : "primary"}" data-app="${esc(app.id)}" ${action.disabled ? "disabled" : ""}>${esc(action.label)}</button>${update ? `<button type="button" class="button secondary" data-update="${esc(app.id)}">Atualizar</button>` : ""}${removable ? `<button type="button" class="button danger" data-remove="${esc(app.id)}">Desinstalar</button>` : ""}</div>
      </article>`;
    }).join("") || `<div class="empty-state"><b>◇</b><strong>Nenhuma aplicação nesta visão</strong><span>Ajuste a busca ou selecione outra aba.</span></div>`;
    grid.querySelectorAll("[data-detail]").forEach(button => button.onclick = () => showDetails(button.dataset.detail));
    grid.querySelectorAll("[data-app]").forEach(button => button.onclick = () => primaryAction(button.dataset.app));
    grid.querySelectorAll("[data-update]").forEach(button => button.onclick = () => start(button.dataset.update, "update"));
    grid.querySelectorAll("[data-remove]").forEach(button => button.onclick = () => start(button.dataset.remove, "uninstall"));
  }

  function setText(id, value) { const node = $(id); if (node) node.textContent = value || "—"; }
  function showDetails(id) {
    const app = store.apps.find(item => item.id === id), dialog = $("#app-detail-dialog");
    if (!app || !dialog) return;
    const selected = provider(app), record = installed(app), action = actionState(app);
    const detailIcon = $("#app-detail-icon img"); if (detailIcon) { detailIcon.onerror = () => { detailIcon.onerror = null; detailIcon.src = FALLBACK_ICON; }; detailIcon.src = iconUrl(app); }
    setText("#app-detail-name", app.name); setText("#app-detail-summary", app.summary || "Descrição não informada pelo catálogo."); setText("#app-detail-category", app.category || "Aplicativo");
    setText("#app-detail-status", app.compatibility_status || app.compatibility?.status || (record ? "Instalado" : "—"));
    setText("#app-detail-developer", app.developer || "Não informado"); setText("#app-detail-license", app.license || "Não informada");
    setText("#app-detail-provider", selected ? providerLabel(app) : "Nenhum provider disponível");
    setText("#app-detail-platform", selected?.platforms?.join(", ") || window.lunarxState?.capabilities?.platform?.mode || "Não informada");
    setText("#app-detail-architecture", selected?.architectures?.join(", ") || window.lunarxState?.capabilities?.platform?.architecture || "Não informada");
    setText("#app-detail-type", selected?.provider_type || providerType(app)); setText("#app-detail-installed-version", record?.version || "Não instalado");
    setText("#app-detail-available-version", selected?.available_version || app.available_version || record?.available_version || "Não informada"); setText("#app-detail-storage", app.storage_requirement || app.download_size || "Não informado");
    setText("#app-detail-scope", selected?.scope === "system" || record?.scope === "system" ? "Sistema / todos os usuários" : selected ? "Usuário" : "—"); setText("#app-detail-id", app.id);
    const compat = $("#app-detail-compat"); const reason = app.compatibility?.reason || selected?.reason || app.availability_note || "Estado de compatibilidade não informado."; compat.textContent = reason; compat.classList.toggle("success", Boolean(app.compatibility?.compatible));
    const source = $("#app-detail-source"); source.href = selected?.source || app.source_url || "#"; source.textContent = "Fonte oficial ↗";
    const install = $("#app-detail-install"), update = $("#app-detail-update"), remove = $("#app-detail-uninstall");
    install.textContent = action.label; install.disabled = action.disabled; install.classList.toggle("secondary", action.disabled); install.classList.toggle("primary", !action.disabled); install.onclick = () => { if (action.operation === "source") { window.open(app.source_url, "_blank", "noopener"); return; } if (action.operation !== "none") { dialog.close(); primaryAction(app.id); } };
    const showUpdate = Boolean(record?.update_available) && canManage(app); update.classList.toggle("hidden", !showUpdate); update.onclick = () => { dialog.close(); start(app.id, "update"); };
    const showRemove = Boolean(record) && canManage(app); remove.classList.toggle("hidden", !showRemove); remove.onclick = () => { dialog.close(); start(app.id, "uninstall"); };
    dialog.showModal();
  }

  async function load() {
    const query = $("#app-search")?.value.trim() || "", category = $("#app-category")?.value || "all";
    try {
      const catalog = await request(`/api/apps/catalog?q=${encodeURIComponent(query)}&category=${encodeURIComponent(category)}`);
      let installedResult = { apps:[], available:false, error:"" };
      try { installedResult = await request("/api/apps/installed"); } catch (error) { installedResult.error = error.message; }
      store.apps = catalog.apps || []; store.installed = new Map((installedResult.apps || []).map(app => [app.id, app]));
      const select = $("#app-category"), current = select.value;
      if (select.options.length === 1) [...new Set(store.apps.map(app => app.category).filter(Boolean))].sort().forEach(value => select.add(new Option(value, value)));
      select.value = current || "all";
      const compatible = store.apps.filter(app => app.compatibility?.compatible).length;
      const platform = window.lunarxState?.capabilities?.platform || {};
      $("#app-catalog-status").textContent = installedResult.error ? `${catalog.count || 0} entradas · ${compatible} compatíveis em ${platform.mode || "este dispositivo"} · estado instalado indisponível: ${installedResult.error}` : `${catalog.count || 0} entradas · ${compatible} compatíveis · ${platform.mode || "runtime"} / ${platform.architecture || "arquitetura detectada"}`;
      render();
    } catch (error) {
      $("#app-catalog-status").textContent = error.message;
      $("#app-store-grid").innerHTML = `<div class="empty-state"><b>!</b><strong>Catálogo temporariamente indisponível</strong><span>${esc(error.message)}</span></div>`;
    }
  }

  async function primaryAction(id) {
    const app = store.apps.find(item => item.id === id); if (!app) return;
    const action = actionState(app), selected = provider(app), record = installed(app);
    if (action.operation === "source") { window.open(app.source_url, "_blank", "noopener"); return; }
    if (action.disabled || action.operation === "none") return;
    if (action.operation === "launch" && (record?.launch_url || selected?.launch_url)) { window.open(record?.launch_url || selected.launch_url, "_blank", "noopener"); return; }
    await start(id, action.operation);
  }
  async function start(appId, operation) {
    try {
      const result = await request("/api/apps/action", { method:"POST", body:JSON.stringify({ app_id:appId, operation }) });
      watch(result.job.id, appId, operation);
    } catch (error) { $("#app-catalog-status").textContent = error.message; }
  }
  async function watch(jobId, appId, operation) {
    clearInterval(store.poll); const box = $("#app-job"); box.classList.remove("hidden"); $("#app-job-title").textContent = store.apps.find(app => app.id === appId)?.name || appId;
    const tick = async () => {
      try {
        const data = await request(`/api/apps/jobs?id=${encodeURIComponent(jobId)}`), job = data.jobs?.[0]; if (!job) return;
        $("#app-job-message").textContent = job.message; $("#app-job-progress").style.width = `${job.progress}%`;
        if (["complete","failed"].includes(job.state)) {
          clearInterval(store.poll); store.poll = null;
          if (job.state === "complete" && operation === "launch" && job.result?.launch_url) window.open(job.result.launch_url, "_blank", "noopener");
          await load(); setTimeout(() => box.classList.add("hidden"), 2200);
        }
      } catch (error) { clearInterval(store.poll); store.poll = null; $("#app-job-message").textContent = error.message; }
    };
    await tick(); store.poll = setInterval(tick, 1200);
  }

  window.loadLunarXStore = load;
  window.addEventListener("DOMContentLoaded", () => {
    const closeDetail = () => $("#app-detail-dialog")?.close();
    $("#app-detail-close")?.addEventListener("click", closeDetail); $("#app-detail-x")?.addEventListener("click", closeDetail);
    document.querySelectorAll(".store-tab").forEach(button => button.onclick = () => { store.tab = button.dataset.storeTab; document.querySelectorAll(".store-tab").forEach(item => item.classList.toggle("active", item === button)); render(); });
    let timer; $("#app-search")?.addEventListener("input", () => { clearTimeout(timer); timer = setTimeout(load, 250); }); $("#app-category")?.addEventListener("change", load);
  });
})();
