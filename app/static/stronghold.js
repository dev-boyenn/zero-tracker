let strongholdRefreshSeq = 0;
const expandedStrongholdPreviewIds = new Set();
let lastStrongholdLatestAttemptId = null;
let lastStrongholdLatestMappedAttemptId = null;

function setText(id, value) {
  const node = document.getElementById(id);
  if (node) node.textContent = value;
}

function escapeHtmlAttr(value) {
  return String(value)
    .replace(/&/g, "&amp;")
    .replace(/"/g, "&quot;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
}

function formatPct(value) {
  return `${Number(value || 0).toFixed(2)}%`;
}

function formatSec(value) {
  return `${Number(value || 0).toFixed(2)}s`;
}

function formatNumMaybe(value, digits = 2) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return "-";
  return Number(value).toFixed(digits);
}

function formatDateTime(value) {
  if (!value) return "-";
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return String(value);
  return d.toLocaleString();
}

function formatRelativeDateTime(value) {
  if (!value) return "-";
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return String(value);
  const now = Date.now();
  const diffMs = Math.max(0, now - d.getTime());
  const sec = Math.floor(diffMs / 1000);
  if (sec < 5) return "just now";
  if (sec < 60) return `${sec}s ago`;
  const min = Math.floor(sec / 60);
  if (min < 60) return `${min}m ago`;
  const hours = Math.floor(min / 60);
  const remMin = min % 60;
  if (hours < 24) {
    if (remMin === 0) return `${hours}h ago`;
    return `${hours}h ${remMin}m ago`;
  }
  const days = Math.floor(hours / 24);
  const remHours = hours % 24;
  if (remHours === 0) return `${days}d ago`;
  return `${days}d ${remHours}h ago`;
}

function sideFromType(zeroType) {
  const t = String(zeroType || "");
  if (t.startsWith("Front ")) return "Front";
  if (t.startsWith("Back ")) return "Back";
  return "Unknown";
}

function deriveNavSecondsFromVisits(visits) {
  const rows = Array.isArray(visits) ? visits : [];
  if (rows.length === 0) return 0;
  const enters = [];
  const exits = [];
  for (const row of rows) {
    const enterGt = Number(row?.enter_gt || 0);
    const exitGt = Number(row?.exit_gt || 0);
    if (Number.isFinite(enterGt) && enterGt > 0) enters.push(enterGt);
    if (Number.isFinite(exitGt) && exitGt > 0) exits.push(exitGt);
  }
  if (enters.length === 0 || exits.length === 0) return 0;
  const start = Math.min(...enters);
  const end = Math.max(...exits);
  if (!Number.isFinite(start) || !Number.isFinite(end) || end <= start) return 0;
  return (end - start) / 20;
}

function parseStrongholdAttemptIdFromPath() {
  const match = String(window.location.pathname).match(/^\/stronghold\/(\d+)$/);
  if (!match) return null;
  const id = Number(match[1]);
  if (!Number.isFinite(id) || id <= 0) return null;
  return id;
}

async function fetchJson(url) {
  const res = await fetch(url);
  const payload = await res.json();
  if (!res.ok) {
    const errorText = payload && payload.error ? String(payload.error) : `HTTP ${res.status}`;
    throw new Error(errorText);
  }
  return payload;
}

function renderStrongholdSummary(summary) {
  setText("shAttempts", Number(summary?.attempts || 0));
  setText("shSuccessRate", formatPct(summary?.portal_success_rate || 0));
  setText("shAvgStarter", formatSec(summary?.avg_starter_seconds || 0));
  setText("shAvgRoomTime", formatSec(summary?.avg_room_seconds || 0));
  setText("shAvgNav", formatSec(summary?.avg_nav_seconds || 0));
  setText("shAvgRooms", formatNumMaybe(summary?.avg_rooms_entered));
  setText("shAvgOptimalRooms", formatNumMaybe(summary?.avg_optimal_rooms));
  setText("shAvgRoomDelta", formatNumMaybe(summary?.avg_room_delta));
}

function renderStrongholdRecentTable(runs) {
  const tbody = document.getElementById("strongholdRecentTable");
  if (!tbody) return;
  tbody.innerHTML = "";
  const rows = Array.isArray(runs) ? runs : [];
  const latestWithMap = rows.find((run) => !!run?.has_map);
  const latestWithMapId = Number(latestWithMap?.id || 0);
  if (Number.isFinite(latestWithMapId) && latestWithMapId > 0) {
    // Always keep the newest available map expanded on the list page.
    expandedStrongholdPreviewIds.add(latestWithMapId);
  }
  for (const run of rows) {
    const id = Number(run.id || 0);
    const hasMap = !!run.has_map;
    const previewExpanded = expandedStrongholdPreviewIds.has(id);
    const isLatestWithMap = hasMap && id === latestWithMapId;
    const showPreview = isLatestWithMap || previewExpanded;
    const navTimeRaw = Number(run.stronghold_nav_seconds || 0);
    const navTime =
      navTimeRaw > 0 ? navTimeRaw : deriveNavSecondsFromVisits(run.visits || []);
    const portalText = run.portal_success ? "yes" : "no";
    const side = sideFromType(run.zero_type);
    const startedAtFull = escapeHtmlAttr(formatDateTime(run.started_at_utc));
    const startedAtRelative = formatRelativeDateTime(run.started_at_utc);
    const previewBtn = hasMap
      ? isLatestWithMap
        ? `<span class="muted">Latest</span>`
        : `<button type="button" class="stronghold-preview-btn" data-attempt-id="${id}">${
            previewExpanded ? "Hide" : "Show"
          }</button>`
      : "-";
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td><a class="inline-link" href="/stronghold/${id}">#${id}</a></td>
      <td title="${startedAtFull}">${startedAtRelative}</td>
      <td class="${run.portal_success ? "status-success" : "status-fail"}">${portalText}</td>
      <td>${run.tower_name || "Unknown"}</td>
      <td>${side}</td>
      <td>${run.o_level === null || run.o_level === undefined ? "-" : `O${Number(run.o_level)}`}</td>
      <td>${formatSec(run.stronghold_starter_seconds || 0)}</td>
      <td>${run.stronghold_rooms_entered ?? "-"}</td>
      <td>${run.stronghold_optimal_rooms ?? "-"}</td>
      <td>${run.stronghold_room_delta ?? "-"}</td>
      <td>${formatSec(navTime)}</td>
      <td>${previewBtn}</td>
    `;
    tbody.appendChild(tr);

    if (showPreview) {
      const previewRow = document.createElement("tr");
      previewRow.className = "stronghold-preview-row";
      previewRow.innerHTML = `
        <td colspan="12">
          <div class="stronghold-preview-wrap">
            ${
              hasMap
                ? `<img src="/api/stronghold/attempt/${id}/svg?t=${Date.now()}" alt="Stronghold map attempt ${id}" />`
                : `<p class="muted">SVG not available for this attempt.</p>`
            }
          </div>
        </td>
      `;
      tbody.appendChild(previewRow);
    }
  }
  const buttons = tbody.querySelectorAll(".stronghold-preview-btn");
  for (const button of buttons) {
    button.addEventListener("click", () => {
      const id = Number(button.getAttribute("data-attempt-id") || "0");
      if (!Number.isFinite(id) || id <= 0) return;
      if (expandedStrongholdPreviewIds.has(id)) {
        expandedStrongholdPreviewIds.delete(id);
      } else {
        expandedStrongholdPreviewIds.add(id);
      }
      renderStrongholdRecentTable(rows);
    });
  }
}

async function refreshStrongholdIndex(force = false) {
  const requestSeq = ++strongholdRefreshSeq;
  try {
    const [health, version] = await Promise.all([
      fetchJson("/api/health"),
      fetchJson("/api/stronghold/version"),
    ]);
    if (requestSeq !== strongholdRefreshSeq) {
      return;
    }
    const watch = String(health?.mpk_log_path || "").trim();
    setText(
      "strongholdUpdated",
      `Updated ${new Date().toLocaleTimeString()}${watch ? ` | Watching: ${watch}` : ""}`
    );
    const latestAttemptId = Number(version?.latest_attempt_id || 0);
    const latestMappedAttemptId = Number(version?.latest_mapped_attempt_id || 0);
    if (
      !force &&
      lastStrongholdLatestAttemptId !== null &&
      latestAttemptId === lastStrongholdLatestAttemptId &&
      latestMappedAttemptId === lastStrongholdLatestMappedAttemptId
    ) {
      return;
    }
    lastStrongholdLatestAttemptId = latestAttemptId;
    lastStrongholdLatestMappedAttemptId = latestMappedAttemptId;
    const payload = await fetchJson("/api/stronghold/dashboard?limit=80");
    if (requestSeq !== strongholdRefreshSeq) {
      return;
    }
    renderStrongholdSummary(payload.summary || {});
    renderStrongholdRecentTable(payload.runs || []);
  } catch (error) {
    if (requestSeq !== strongholdRefreshSeq) {
      return;
    }
    setText("strongholdUpdated", `Stronghold fetch error: ${error}`);
  }
}

function renderStrongholdDetail(payload) {
  const attempt = payload?.attempt || {};
  const analysis = payload?.analysis || {};
  const visits = Array.isArray(analysis?.visits) ? analysis.visits : [];
  const attemptId = Number(attempt.id || 0);
  setText(
    "strongholdDetailTitle",
    `Attempt #${attemptId} | ${attempt.world_name || "Unknown World"} | ${formatDateTime(
      attempt.started_at_utc
    )}`
  );
  setText("shdPortal", attempt.portal_success ? "Yes" : "No");
  setText("shdStarterTime", formatSec(attempt.stronghold_starter_seconds || 0));
  setText("shdRoomsEntered", attempt.stronghold_rooms_entered ?? "-");
  setText("shdOptimalRooms", attempt.stronghold_optimal_rooms ?? "-");
  setText("shdRoomDelta", attempt.stronghold_room_delta ?? "-");
  setText("shdAvgRoomTime", formatSec(attempt.stronghold_avg_room_seconds || 0));
  const navSecondsRaw = Number(attempt.stronghold_nav_seconds || 0);
  const navSeconds = navSecondsRaw > 0 ? navSecondsRaw : deriveNavSecondsFromVisits(visits);
  setText("shdNavTime", formatSec(navSeconds));

  const svgWrap = document.getElementById("strongholdSvgWrap");
  if (svgWrap) {
    if (attempt.svg_url) {
      svgWrap.innerHTML = `<img src="${escapeHtmlAttr(
        `/api/stronghold/attempt/${attemptId}/svg?t=${Date.now()}`
      )}" alt="Stronghold map attempt ${attemptId}" />`;
    } else {
      svgWrap.innerHTML = `<p class="muted">SVG is not available for this attempt yet.</p>`;
    }
  }

  const tbody = document.getElementById("strongholdVisitTable");
  if (tbody) {
    tbody.innerHTML = "";
    if (visits.length === 0) {
      const tr = document.createElement("tr");
      tr.innerHTML = `<td colspan="6">No room visits available.</td>`;
      tbody.appendChild(tr);
    } else {
      for (let idx = 0; idx < visits.length; idx += 1) {
        const visit = visits[idx] || {};
        const tr = document.createElement("tr");
        tr.innerHTML = `
          <td>${idx + 1}</td>
          <td>${visit.room_id ?? "-"}</td>
          <td>${visit.room_type || "Unknown"}</td>
          <td>${visit.enter_gt ?? "-"}</td>
          <td>${visit.exit_gt ?? "-"}</td>
          <td>${formatSec(visit.duration_seconds || 0)}</td>
        `;
        tbody.appendChild(tr);
      }
    }
  }
}

async function refreshStrongholdDetail() {
  const attemptId = parseStrongholdAttemptIdFromPath();
  if (!attemptId) {
    setText("strongholdDetailTitle", "Invalid attempt id.");
    return;
  }
  try {
    const payload = await fetchJson(`/api/stronghold/attempt/${attemptId}`);
    if (!payload?.ok) {
      const err = payload && payload.error ? String(payload.error) : "Attempt not found.";
      setText("strongholdDetailTitle", err);
      return;
    }
    renderStrongholdDetail(payload);
  } catch (error) {
    setText("strongholdDetailTitle", `Detail fetch error: ${error}`);
  }
}

function initStrongholdPage() {
  const page = String(document.body?.dataset?.page || "");
  if (page === "stronghold-detail") {
    refreshStrongholdDetail();
    return;
  }
  if (page === "stronghold-index") {
    refreshStrongholdIndex(true);
    setInterval(() => refreshStrongholdIndex(false), 3000);
  }
}

initStrongholdPage();
