// Hudson Leads — frontend. Property-prospecting only.
// Vanilla JS + supabase-js + Leaflet. No build step.

(() => {
  const cfg = window.HUDSON_LEADS_CONFIG || {};
  const NEEDS_SETUP = !cfg.SUPABASE_URL || cfg.SUPABASE_URL.includes("YOUR-PROJECT") || !cfg.SUPABASE_ANON_KEY || cfg.SUPABASE_ANON_KEY === "YOUR_ANON_KEY";

  const $ = (sel) => document.querySelector(sel);
  const $$ = (sel) => Array.from(document.querySelectorAll(sel));
  const fmt$ = (n) => n == null ? "—" : "$" + Math.round(n).toLocaleString();
  const fmtDate = (s) => s ? new Date(s).toLocaleDateString("en-US", { year: "numeric", month: "short", day: "numeric" }) : "—";
  const fmtAgo = (s) => {
    if (!s) return "—";
    const ms = Date.now() - new Date(s).getTime();
    if (ms < 0) return "—";
    const d = Math.floor(ms / 864e5);
    if (d > 60) return fmtDate(s);
    if (d >= 1) return d + "d ago";
    const h = Math.floor(ms / 36e5);
    if (h >= 1) return h + "h ago";
    const m = Math.max(1, Math.floor(ms / 6e4));
    return m + "m ago";
  };
  const escape = (s) => (s == null ? "" : String(s)).replace(/[&<>"]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;","\"":"&quot;"}[c]));
  const toast = (msg, ms=2400) => {
    const t = $("#toast"); t.textContent = msg; t.hidden = false;
    clearTimeout(toast._t); toast._t = setTimeout(() => t.hidden = true, ms);
  };

  if (NEEDS_SETUP) {
    document.body.innerHTML = `
      <main style="max-width:600px;margin:60px auto;padding:24px;background:#fff;border:1px solid #e6e2d8;border-radius:8px;font:15px/1.5 -apple-system,system-ui,sans-serif">
        <h1 style="margin:0 0 12px">Setup needed</h1>
        <p>This deployment is missing its Supabase credentials. See <code>SETUP.md</code>.</p>
      </main>`;
    return;
  }

  const supabase = window.supabase.createClient(cfg.SUPABASE_URL, cfg.SUPABASE_ANON_KEY, {
    auth: { persistSession: true, autoRefreshToken: true, detectSessionInUrl: false }
  });

  const STATUS_ORDER = ["new", "reviewing", "contacted", "paused", "won", "dropped"];

  const authView = $("#authView"), appView = $("#appView"), topbar = $("#topbar");
  const emailInput = $("#email"), otpInput = $("#otp"), authErr = $("#authErr");

  $("#sendCodeBtn").addEventListener("click", async () => {
    authErr.textContent = "";
    const email = emailInput.value.trim();
    if (!email) { authErr.textContent = "Enter your email."; return; }
    const { error } = await supabase.auth.signInWithOtp({ email, options: { shouldCreateUser: true } });
    if (error) { authErr.textContent = error.message; return; }
    $("#authStep1").hidden = true; $("#authStep2").hidden = false;
    setTimeout(() => otpInput.focus(), 50);
    toast("Check your inbox for a 6-digit code.");
  });

  $("#verifyBtn").addEventListener("click", async () => {
    authErr.textContent = "";
    const email = emailInput.value.trim();
    const code = otpInput.value.trim();
    if (!code) { authErr.textContent = "Enter the 6-digit code."; return; }
    const { error } = await supabase.auth.verifyOtp({ email, token: code, type: "email" });
    if (error) { authErr.textContent = error.message; return; }
    await onSignedIn();
  });

  $("#restartBtn").addEventListener("click", () => {
    $("#authStep1").hidden = false; $("#authStep2").hidden = true;
    otpInput.value = ""; authErr.textContent = "";
  });

  $("#signOutBtn").addEventListener("click", async () => {
    await supabase.auth.signOut(); location.reload();
  });

  // ── State ─────────────────────────────────────────────────────────────
  let allRows = [];
  let view = [];
  let sortKey = "total_score";
  let sortDir = "desc";
  let topN = null;  // null = no cap
  let currentDrawerId = null;

  const fTier = $("#filterTier"), fStatus = $("#filterStatus"),
        fTenure = $("#filterTenure"), fSearch = $("#filterSearch");
  [fTier, fStatus, fTenure].forEach(el => el.addEventListener("change", render));
  fSearch.addEventListener("input", () => { clearTimeout(render._d); render._d = setTimeout(render, 120); });

  $$("th[data-sort]").forEach(th => th.addEventListener("click", () => {
    const k = th.dataset.sort;
    if (sortKey === k) sortDir = sortDir === "asc" ? "desc" : "asc";
    else { sortKey = k; sortDir = (k === "display_name" || k === "situs_address" || k === "status") ? "asc" : "desc"; }
    render();
  }));

  $("#vTable").addEventListener("click", () => switchView("table"));
  $("#vMap").addEventListener("click", () => switchView("map"));
  function switchView(v) {
    $("#vTable").classList.toggle("active", v === "table");
    $("#vMap").classList.toggle("active", v === "map");
    $("#tableView").hidden = v !== "table";
    $("#mapView").hidden = v !== "map";
    if (v === "map") drawMap();
  }

  $("#topNBtn").addEventListener("click", () => {
    topN = topN === 50 ? null : 50;
    $("#topNBtn").textContent = topN ? "Show all" : "Top 50";
    render();
  });

  $("#exportCsvBtn").addEventListener("click", () => {
    if (!view.length) { toast("Nothing to export."); return; }
    const cols = [
      ["tier", "Tier"], ["total_score", "Score"], ["display_name", "Owner"],
      ["owner_names", "All owners"], ["situs_address", "Address"],
      ["situs_city", "City"], ["situs_zip", "Zip"], ["years_owned", "Yrs owned"],
      ["last_sale_date", "Last sale"], ["last_sale_price", "Last sale $"],
      ["market_value", "Value"], ["sqft", "Sqft"], ["year_built", "Built"],
      ["mailing_same_as_situs", "Owner-occupied"],
      ["status", "Status"], ["notes", "Notes"], ["last_touched_at", "Last touched"]
    ];
    const escapeCsv = (v) => {
      if (v == null) return "";
      const s = Array.isArray(v) ? v.join("; ") : String(v);
      return /[",\n\r]/.test(s) ? '"' + s.replace(/"/g, '""') + '"' : s;
    };
    const head = cols.map(c => c[1]).join(",");
    const body = view.map(r => cols.map(c => escapeCsv(r[c[0]])).join(",")).join("\n");
    const today = new Date().toISOString().slice(0, 10);
    const blob = new Blob([head + "\n" + body], { type: "text/csv;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url; a.download = `hudson-leads-${today}.csv`;
    a.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
    toast(`Exported ${view.length} rows.`);
  });

  async function start() {
    const { data: { session } } = await supabase.auth.getSession();
    if (session) await onSignedIn();
  }

  async function onSignedIn() {
    authView.hidden = true; appView.hidden = false; topbar.hidden = false;
    const { data: { user } } = await supabase.auth.getUser();
    $("#userEmail").textContent = user?.email || "";
    await loadData();
  }

  async function loadData() {
    const { data, error } = await supabase.from("v_dashboard").select("*");
    if (error) { toast("Load failed: " + error.message); return; }
    allRows = data || [];
    render();
  }

  function render() {
    const tier = fTier.value, status = fStatus.value;
    const minTen = parseInt(fTenure.value, 10) || 0;
    const q = fSearch.value.trim().toLowerCase();
    view = allRows.filter(r => {
      if (tier && r.tier !== tier) return false;
      if (status && r.status !== status) return false;
      if (minTen && (r.years_owned || 0) < minTen) return false;
      if (q) {
        const hay = [r.display_name, r.situs_address, ...(r.owner_names || [])].filter(Boolean).join(" ").toLowerCase();
        if (!hay.includes(q)) return false;
      }
      return true;
    });
    view.sort(cmp(sortKey, sortDir));
    if (topN) view = view.slice(0, topN);
    drawTable();
    drawStats();
    if (!$("#mapView").hidden) drawMap();
    $$("th[data-sort]").forEach(th => {
      if (th.dataset.sort === sortKey) th.dataset.active = sortDir;
      else delete th.dataset.active;
    });
  }

  function cmp(k, dir) {
    const sign = dir === "asc" ? 1 : -1;
    return (a, b) => {
      let x = a[k], y = b[k];
      if (k === "tier") { x = "ABC".indexOf(x ?? "C"); y = "ABC".indexOf(y ?? "C"); }
      if (k === "last_touched_at" || k === "last_sale_date") { x = x ? Date.parse(x) : 0; y = y ? Date.parse(y) : 0; }
      if (x == null && y == null) return 0;
      if (x == null) return 1;
      if (y == null) return -1;
      if (typeof x === "string") return x.localeCompare(y) * sign;
      return (x - y) * sign;
    };
  }

  function drawStats() {
    const n = view.length;
    const tier = (t) => view.filter(r => r.tier === t).length;
    const medTen = (() => {
      const v = view.map(r => r.years_owned).filter(x => x != null).sort((a,b)=>a-b);
      return v.length ? v[Math.floor(v.length/2)] : "—";
    })();
    const medVal = (() => {
      const v = view.map(r => r.market_value).filter(x => x != null).sort((a,b)=>a-b);
      return v.length ? fmt$(v[Math.floor(v.length/2)]) : "—";
    })();
    const html = `
      <div class="stat"><div class="label">Showing</div><div class="val">${n}</div></div>
      <div class="stat"><div class="label">Tier A</div><div class="val">${tier("A")}</div></div>
      <div class="stat"><div class="label">Tier B</div><div class="val">${tier("B")}</div></div>
      <div class="stat"><div class="label">Median yrs owned</div><div class="val">${medTen}</div></div>
      <div class="stat"><div class="label">Median value</div><div class="val">${medVal}</div></div>
    `;
    $("#stats").innerHTML = html;
  }

  function drawTable() {
    const body = $("#leadBody");
    if (!view.length) { body.innerHTML = ""; $("#emptyMsg").hidden = false; return; }
    $("#emptyMsg").hidden = true;
    body.innerHTML = view.map(r => `
      <tr data-id="${r.household_id}">
        <td><span class="tier ${r.tier || "C"}">${r.tier || "—"}</span></td>
        <td class="score">${r.total_score == null ? "—" : Math.round(r.total_score)}</td>
        <td>${escape(r.display_name || "—")}</td>
        <td class="muted">${escape(r.situs_address || "—")}</td>
        <td>${r.years_owned ?? "?"}</td>
        <td>${fmt$(r.market_value)}</td>
        <td>${r.sqft ? r.sqft.toLocaleString() : "—"}</td>
        <td><span class="status-pill ${r.status}">${escape(r.status)}</span></td>
        <td class="muted small">${fmtAgo(r.last_touched_at)}</td>
      </tr>
    `).join("");
    $$("#leadBody tr").forEach(tr => tr.addEventListener("click", () => openDrawer(tr.dataset.id)));
  }

  // ── Map ───────────────────────────────────────────────────────────────
  let map, heatLayer, markerLayer;
  function drawMap() {
    const container = $("#map");
    if (!map) {
      map = L.map(container, { zoomControl: true, attributionControl: true });
      L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
        maxZoom: 19,
        attribution: '&copy; <a href="https://openstreetmap.org/copyright">OpenStreetMap</a>'
      }).addTo(map);
    }
    if (heatLayer) { map.removeLayer(heatLayer); heatLayer = null; }
    if (markerLayer) { map.removeLayer(markerLayer); markerLayer = null; }

    const points = view.filter(r => r.lat && r.lng);
    if (!points.length) {
      map.setView([41.2406, -81.4407], 12);
      return;
    }
    const heatData = points.map(r => {
      const w = r.tier === "A" ? 1.0 : r.tier === "B" ? 0.55 : 0.25;
      return [r.lat, r.lng, w];
    });
    heatLayer = L.heatLayer(heatData, { radius: 28, blur: 22, maxZoom: 17 }).addTo(map);

    markerLayer = L.layerGroup().addTo(map);
    points.forEach(r => {
      const cls = `marker-tier-${r.tier || "C"}`;
      const icon = L.divIcon({ className: "lead-marker " + cls, iconSize: [12, 12] });
      const m = L.marker([r.lat, r.lng], { icon })
        .bindPopup(`
          <strong>${escape(r.display_name || "—")}</strong><br>
          ${escape(r.situs_address || "")}<br>
          Tier ${r.tier || "—"} · Score ${Math.round(r.total_score || 0)}<br>
          ${r.years_owned ?? "?"} yrs owned · ${fmt$(r.market_value)}<br>
          <a href="#" data-id="${r.household_id}" class="popup-open">Open dossier →</a>
        `);
      m.on("popupopen", (e) => {
        const node = e.popup.getElement().querySelector(".popup-open");
        if (node) node.addEventListener("click", (ev) => { ev.preventDefault(); openDrawer(r.household_id); });
      });
      markerLayer.addLayer(m);
    });

    const bounds = L.latLngBounds(points.map(p => [p.lat, p.lng]));
    map.fitBounds(bounds.pad(0.15));
    setTimeout(() => map.invalidateSize(), 50);
  }

  // ── Dossier drawer ────────────────────────────────────────────────────
  const drawer = $("#drawer");
  $("#closeDrawer").addEventListener("click", closeDrawer);

  function closeDrawer() { drawer.hidden = true; currentDrawerId = null; }

  function openDrawer(id) {
    const r = allRows.find(x => x.household_id === id);
    if (!r) return;
    currentDrawerId = id;
    $("#dTier").innerHTML = `Tier <span class="tier ${r.tier || "C"}">${r.tier || "—"}</span> · Score ${Math.round(r.total_score || 0)}`;
    $("#dName").textContent = r.display_name || "Unknown owner";
    $("#dAddr").textContent = `${r.situs_address || "—"}, ${r.situs_city || ""} ${r.situs_zip || ""}`;
    const facts = [
      ["Years owned", r.years_owned ?? "unknown"],
      ["Last sale", fmtDate(r.last_sale_date) + (r.last_sale_price ? " · " + fmt$(r.last_sale_price) : "")],
      ["Market value (current)", fmt$(r.market_value)],
      ["Sq ft", r.sqft ? r.sqft.toLocaleString() : "—"],
      ["Year built", r.year_built ?? "—"],
      ["Owner-occupied?", r.mailing_same_as_situs ? "yes" : "no"],
      ["Last touched", fmtAgo(r.last_touched_at)]
    ];
    const max = 100;
    const pct = (n) => Math.min(100, Math.max(0, Math.round((n / max) * 100)));
    const scoreBars = [
      ["Tenure", r.tenure_points],
      ["Value", r.value_points]
    ];
    const ownerList = (r.owner_names || []).map(o => `<div class="muted small">${escape(o)}</div>`).join("");
    const html = `
      <div class="section-h">Facts</div>
      ${facts.map(([k,v]) => `<div class="fact"><span class="k">${k}</span><span class="v">${escape(String(v))}</span></div>`).join("")}

      <div class="section-h">Owners</div>
      ${ownerList || "<div class='muted small'>—</div>"}

      <div class="section-h">Score breakdown</div>
      ${scoreBars.map(([k,v]) => `
        <div class="score-bar">
          <div>
            <div class="muted small">${k}</div>
            <div class="bar"><div style="width:${pct(v||0)}%"></div></div>
          </div>
          <div class="v">${(v||0).toFixed(1)}</div>
        </div>`).join("")}

      <div class="section-h">Status</div>
      <select id="dStatus">
        ${STATUS_ORDER.map(s =>
          `<option value="${s}" ${s===r.status?"selected":""}>${s}</option>`).join("")}
      </select>

      <div class="section-h">Notes</div>
      <textarea id="dNotes" rows="6" placeholder="Private notes — what you know, last touch, follow-up plan...">${escape(r.notes || "")}</textarea>
      <div style="display:flex;gap:8px;justify-content:flex-end;margin-top:10px">
        <button class="btn primary" id="dSave">Save</button>
      </div>
    `;
    $("#drawerBody").innerHTML = html;
    drawer.hidden = false;
    $("#dStatus").addEventListener("change", () => saveDrawer());
    $("#dSave").addEventListener("click", () => saveDrawer(true));
  }

  async function saveDrawer(showToast = true) {
    if (!currentDrawerId) return;
    const r = allRows.find(x => x.household_id === currentDrawerId);
    if (!r) return;
    const status = $("#dStatus")?.value ?? r.status;
    const notes = $("#dNotes")?.value ?? r.notes ?? "";
    if (status === r.status && notes === (r.notes || "")) return;
    const { error } = await supabase
      .from("households")
      .update({ status, notes })
      .eq("id", r.household_id);
    if (error) { toast("Save failed: " + error.message); return; }
    r.status = status; r.notes = notes; r.last_touched_at = new Date().toISOString();
    if (showToast) toast("Saved");
    render();
  }

  async function nudgeStatus(direction) {
    if (!currentDrawerId) return;
    const r = allRows.find(x => x.household_id === currentDrawerId);
    if (!r) return;
    const cur = STATUS_ORDER.indexOf(r.status);
    if (cur < 0) return;
    const next = Math.max(0, Math.min(STATUS_ORDER.length - 1, cur + direction));
    if (next === cur) return;
    const newStatus = STATUS_ORDER[next];
    const sel = $("#dStatus"); if (sel) sel.value = newStatus;
    const { error } = await supabase.from("households").update({ status: newStatus }).eq("id", r.household_id);
    if (error) { toast("Save failed: " + error.message); return; }
    r.status = newStatus; r.last_touched_at = new Date().toISOString();
    toast("Status → " + newStatus);
    render();
  }

  document.addEventListener("keydown", (e) => {
    const inField = ["INPUT", "TEXTAREA", "SELECT"].includes(document.activeElement?.tagName);
    if (e.key === "Escape" && !drawer.hidden) { closeDrawer(); return; }
    if (drawer.hidden || inField) return;
    if (e.key === "j") { e.preventDefault(); nudgeStatus(+1); }
    else if (e.key === "k") { e.preventDefault(); nudgeStatus(-1); }
    else if (e.key === "n") { e.preventDefault(); $("#dNotes")?.focus(); }
  });

  start();
})();
