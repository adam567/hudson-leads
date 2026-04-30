// Hudson Leads — frontend logic. Vanilla JS + supabase-js + Leaflet.

(() => {
  const cfg = window.HUDSON_LEADS_CONFIG || {};
  const NEEDS_SETUP = !cfg.SUPABASE_URL || cfg.SUPABASE_URL.includes("YOUR-PROJECT") || !cfg.SUPABASE_ANON_KEY || cfg.SUPABASE_ANON_KEY === "YOUR_ANON_KEY";

  // ── Tiny DOM helpers ──────────────────────────────────────────────────
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
        <p>This deployment is missing its Supabase credentials. Set the <code>SUPABASE_URL</code> and <code>SUPABASE_ANON_KEY</code> repository secrets and push, or edit <code>site/config.js</code> directly.</p>
        <p>See <code>SETUP.md</code> in the repo for the 10-minute provisioning steps.</p>
      </main>`;
    return;
  }

  const supabase = window.supabase.createClient(cfg.SUPABASE_URL, cfg.SUPABASE_ANON_KEY, {
    auth: { persistSession: true, autoRefreshToken: true, detectSessionInUrl: false }
  });

  const STATUS_ORDER = ["new", "reviewing", "contacted", "paused", "won", "dropped"];

  // ── Auth view ─────────────────────────────────────────────────────────
  const authView = $("#authView"), appView = $("#appView"), topbar = $("#topbar");
  const emailInput = $("#email"), otpInput = $("#otp"), authErr = $("#authErr");

  $("#sendCodeBtn").addEventListener("click", async () => {
    authErr.textContent = "";
    const email = emailInput.value.trim();
    if (!email) { authErr.textContent = "Enter your email."; return; }
    const { error } = await supabase.auth.signInWithOtp({
      email,
      options: { shouldCreateUser: true }
    });
    if (error) { authErr.textContent = error.message; return; }
    $("#authStep1").hidden = true;
    $("#authStep2").hidden = false;
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
    $("#authStep1").hidden = false;
    $("#authStep2").hidden = true;
    otpInput.value = "";
    authErr.textContent = "";
  });

  $("#signOutBtn").addEventListener("click", async () => {
    await supabase.auth.signOut();
    location.reload();
  });

  // ── App state ─────────────────────────────────────────────────────────
  let allRows = [];
  let view = [];
  let sortKey = "total_score";
  let sortDir = "desc";
  let currentDrawerId = null;

  const fTier = $("#filterTier"), fStatus = $("#filterStatus"),
        fConf = $("#filterConfirmed"), fSearch = $("#filterSearch");
  [fTier, fStatus, fConf].forEach(el => el.addEventListener("change", render));
  fSearch.addEventListener("input", () => { clearTimeout(render._d); render._d = setTimeout(render, 120); });

  $$("th[data-sort]").forEach(th => th.addEventListener("click", () => {
    const k = th.dataset.sort;
    if (sortKey === k) sortDir = sortDir === "asc" ? "desc" : "asc";
    else { sortKey = k; sortDir = (k === "display_name" || k === "situs_address" || k === "status") ? "asc" : "desc"; }
    render();
  }));

  // ── View switcher ─────────────────────────────────────────────────────
  $("#vTable").addEventListener("click", () => switchView("table"));
  $("#vMap").addEventListener("click", () => switchView("map"));
  function switchView(v) {
    $("#vTable").classList.toggle("active", v === "table");
    $("#vMap").classList.toggle("active", v === "map");
    $("#tableView").hidden = v !== "table";
    $("#mapView").hidden = v !== "map";
    if (v === "map") drawMap();
  }

  // ── CSV export ────────────────────────────────────────────────────────
  $("#exportCsvBtn").addEventListener("click", () => {
    if (!view.length) { toast("Nothing to export."); return; }
    const cols = [
      ["tier", "Tier"], ["total_score", "Score"], ["display_name", "Owner"],
      ["owner_names", "All owners"], ["situs_address", "Address"],
      ["situs_city", "City"], ["situs_zip", "Zip"], ["years_owned", "Yrs owned"],
      ["market_value", "Value"], ["sqft", "Sqft"], ["last_sale_date", "Last sale"],
      ["senior_confirmed", "Senior"], ["senior_school", "School"],
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

  // ── Boot ──────────────────────────────────────────────────────────────
  async function start() {
    const { data: { session } } = await supabase.auth.getSession();
    if (session) await onSignedIn();
  }

  async function onSignedIn() {
    authView.hidden = true;
    appView.hidden = false;
    topbar.hidden = false;
    const { data: { user } } = await supabase.auth.getUser();
    $("#userEmail").textContent = user?.email || "";
    await loadData();
  }

  async function loadData() {
    const { data, error } = await supabase.from("v_dashboard").select("*");
    if (error) { toast("Load failed: " + error.message); return; }
    allRows = data || [];
    setWindowPill(allRows[0]?.current_window || "off-window");
    render();
  }

  function setWindowPill(label) {
    const pill = $("#windowPill");
    pill.className = "window-pill";
    if (label.startsWith("silent")) pill.classList.add("win-primary");
    else if (label.startsWith("avoid")) pill.classList.add("win-avoid");
    else if (label === "planning") pill.classList.add("win-planning");
    else if (label === "reactivation") pill.classList.add("win-react");
    pill.textContent = "Window: " + label;
  }

  // ── Render ────────────────────────────────────────────────────────────
  function render() {
    const tier = fTier.value, status = fStatus.value, conf = fConf.value;
    const q = fSearch.value.trim().toLowerCase();
    view = allRows.filter(r => {
      if (tier && r.tier !== tier) return false;
      if (status && r.status !== status) return false;
      if (conf === "yes" && !r.senior_confirmed) return false;
      if (conf === "no" && r.senior_confirmed) return false;
      if (q) {
        const hay = [r.display_name, r.situs_address, ...(r.owner_names || [])].filter(Boolean).join(" ").toLowerCase();
        if (!hay.includes(q)) return false;
      }
      return true;
    });
    view.sort(cmp(sortKey, sortDir));
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
      if (k === "senior_confirmed") { x = x ? 1 : 0; y = y ? 1 : 0; }
      if (k === "last_touched_at") { x = x ? Date.parse(x) : 0; y = y ? Date.parse(y) : 0; }
      if (x == null && y == null) return 0;
      if (x == null) return 1;
      if (y == null) return -1;
      if (typeof x === "string") return x.localeCompare(y) * sign;
      return (x - y) * sign;
    };
  }

  function drawStats() {
    const tier = (t) => view.filter(r => r.tier === t).length;
    const conf = view.filter(r => r.senior_confirmed).length;
    const html = `
      <div class="stat"><div class="label">Showing</div><div class="val">${view.length}</div></div>
      <div class="stat"><div class="label">Tier A</div><div class="val">${tier("A")}</div></div>
      <div class="stat"><div class="label">Tier B</div><div class="val">${tier("B")}</div></div>
      <div class="stat"><div class="label">Tier C</div><div class="val">${tier("C")}</div></div>
      <div class="stat"><div class="label">Senior confirmed</div><div class="val">${conf}</div></div>
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
        <td>${r.years_owned ?? "—"}</td>
        <td>${fmt$(r.market_value)}</td>
        <td>${r.senior_confirmed ? `<span class="confirmed">${escape(r.senior_school || "yes")}</span>` : `<span class="unconfirmed">—</span>`}</td>
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
      const cls = r.senior_confirmed ? "marker-confirmed" : `marker-tier-${r.tier || "C"}`;
      const icon = L.divIcon({ className: "lead-marker " + cls, iconSize: [12, 12] });
      const m = L.marker([r.lat, r.lng], { icon })
        .bindPopup(`
          <strong>${escape(r.display_name || "—")}</strong><br>
          ${escape(r.situs_address || "")}<br>
          Tier ${r.tier || "—"} · Score ${Math.round(r.total_score || 0)}<br>
          ${r.senior_confirmed ? "<em>Senior confirmed</em><br>" : ""}
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
      ["Years owned", r.years_owned ?? "—"],
      ["Market value", fmt$(r.market_value)],
      ["Sq ft", r.sqft ? r.sqft.toLocaleString() : "—"],
      ["Year built", r.year_built ?? "—"],
      ["Last sale", fmtDate(r.last_sale_date)],
      ["Senior", r.senior_confirmed ? `confirmed${r.senior_school ? " — " + r.senior_school : ""}` : "not confirmed"],
      ["Last touched", fmtAgo(r.last_touched_at)],
      ["Window today", r.current_window]
    ];
    const max = 100;
    const pct = (n) => Math.min(100, Math.max(0, Math.round((n / max) * 100)));
    const scoreBars = [
      ["Tenure", r.tenure_points],
      ["Value", r.value_points],
      ["Senior confirmation", r.confirmation_points]
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
      <div class="muted small" style="margin-top:6px">× window multiplier ${(r.window_multiplier ?? 1).toFixed(2)}</div>

      <div class="section-h">Status</div>
      <div style="display:flex;gap:8px;align-items:center">
        <select id="dStatus">
          ${STATUS_ORDER.map(s =>
            `<option value="${s}" ${s===r.status?"selected":""}>${s}</option>`).join("")}
        </select>
      </div>

      <div class="section-h">Notes</div>
      <textarea id="dNotes" rows="6" placeholder="Private notes — what you know about this household, last touch, follow-up plan...">${escape(r.notes || "")}</textarea>
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
    const sel = $("#dStatus");
    if (sel) sel.value = newStatus;
    const { error } = await supabase
      .from("households").update({ status: newStatus }).eq("id", r.household_id);
    if (error) { toast("Save failed: " + error.message); return; }
    r.status = newStatus; r.last_touched_at = new Date().toISOString();
    toast("Status → " + newStatus);
    render();
  }

  // ── Keyboard shortcuts ────────────────────────────────────────────────
  document.addEventListener("keydown", (e) => {
    const inField = ["INPUT", "TEXTAREA", "SELECT"].includes(document.activeElement?.tagName);
    if (e.key === "Escape") {
      if (!$("#confirmModal").hidden) { $("#confirmModal").hidden = true; return; }
      if (!drawer.hidden) { closeDrawer(); return; }
    }
    if (drawer.hidden || inField) return;
    if (e.key === "j") { e.preventDefault(); nudgeStatus(+1); }
    else if (e.key === "k") { e.preventDefault(); nudgeStatus(-1); }
    else if (e.key === "n") { e.preventDefault(); $("#dNotes")?.focus(); }
  });

  // ── Senior confirmation flow ──────────────────────────────────────────
  $("#openConfirmBtn").addEventListener("click", () => $("#confirmModal").hidden = false);
  $("#cancelConfirm").addEventListener("click", () => $("#confirmModal").hidden = true);

  $("#submitConfirm").addEventListener("click", async () => {
    const errEl = $("#confirmErr"); errEl.textContent = "";
    const text = $("#seniorPaste").value;
    const school = $("#seniorSchool").value.trim() || null;
    const gradYear = parseInt($("#seniorGradYear").value, 10) || null;
    const sourceLabel = $("#seniorSource").value.trim() || null;
    const lines = text.split(/\r?\n/).map(s => s.trim()).filter(Boolean);
    if (!lines.length) { errEl.textContent = "Paste at least one name."; return; }

    const { data: orgRow, error: orgErr } = await supabase.from("org_members")
      .select("org_id").limit(1).maybeSingle();
    if (orgErr || !orgRow) { errEl.textContent = "No org membership found. Run the seed step in SETUP.md."; return; }

    const { data: { user } } = await supabase.auth.getUser();
    const { data: batch, error: bErr } = await supabase.from("senior_batches")
      .insert({ org_id: orgRow.org_id, uploaded_by: user.id, source_label: sourceLabel, grad_year: gradYear, raw_text: text })
      .select().single();
    if (bErr) { errEl.textContent = bErr.message; return; }

    const seniors = lines.map(line => {
      const norm = line.toUpperCase().replace(/\s+/g, " ").trim();
      const parts = norm.includes(",") ? norm.split(",", 2).map(s => s.trim()) : null;
      let last = null, first = null;
      if (parts) { last = parts[0]; first = (parts[1] || "").split(/\s+/)[0] || null; }
      else { const toks = norm.split(/\s+/); first = toks[0]; last = toks[toks.length - 1]; }
      return {
        org_id: orgRow.org_id,
        batch_id: batch.id,
        senior_name_raw: line,
        senior_name_norm: norm,
        first_name: first,
        last_name: last,
        school,
        grad_year: gradYear,
      };
    });
    const { error: sErr } = await supabase.from("confirmed_seniors").insert(seniors);
    if (sErr) { errEl.textContent = sErr.message; return; }

    const { data: matched, error: mErr } = await supabase.rpc("match_seniors", { target_org: orgRow.org_id });
    if (mErr) { errEl.textContent = "Match failed: " + mErr.message; return; }
    const { error: scErr } = await supabase.rpc("recompute_scores", { target_org: orgRow.org_id });
    if (scErr) { errEl.textContent = "Score failed: " + scErr.message; return; }

    $("#confirmModal").hidden = true;
    $("#seniorPaste").value = "";
    toast(`Processed ${seniors.length}, matched ${matched ?? 0}.`);
    await loadData();
  });

  start();
})();
