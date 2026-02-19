let progressionChart;
let damageChart;
let efficiencyChart;
let timeSeriesChart;
let rollingConsistencyChart;
let speedBinsChart;
let attemptsByHourChart;
let outcomeRunsChart;
let standingHeightConsistencyChart;
let towerBackRadarChart;
let towerFrontRadarChart;
let selectedTower = "__GLOBAL__";
let selectedSide = "__GLOBAL__";
let selectedWindow = "all";
let selectedSeedMode = "all";
let includeOneEight = true;
let selectedRotation = "both";
const selectedSource = "mpk";
let selectedLeniencyTarget = null;
let rollingMode = "r50";
let lastPayload = null;
let lastHealth = null;
let leniencyRefreshTimer = null;
let refreshRequestSeq = 0;
const expandedTowerRows = new Set();
let currentPracticeCommand = "";
let practiceAudioCtx = null;
let lockedMpkTargets = new Set();
let lockRequestInFlight = false;
let fullRandomOverrideRequestInFlight = false;
let weakLockSkipRequestInFlight = false;
let thresholdTooltipEl = null;
let lastWeakLockState = {
  lockActive: false,
  targetKey: "",
  streak: 0,
  minStreak: 3,
};

function formatPct(value) {
  return `${Number(value || 0).toFixed(2)}%`;
}

function formatSec(value) {
  return `${Number(value || 0).toFixed(2)}s`;
}

function formatNum(value) {
  return Number(value || 0).toFixed(2);
}

function formatNumMaybe(value) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return "-";
  return Number(value).toFixed(2);
}

function escapeHtmlAttr(value) {
  return String(value)
    .replace(/&/g, "&amp;")
    .replace(/"/g, "&quot;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
}

function clamp(value, minValue, maxValue) {
  return Math.min(maxValue, Math.max(minValue, value));
}

function oLevelColor(successRate) {
  const pct = clamp(Number(successRate || 0), 0, 100);
  const hue = 6 + pct * 1.3;
  return `hsl(${hue}deg 82% 54%)`;
}

function renderOLevelHeatmap(matrix) {
  const container = document.getElementById("oLevelHeatmap");
  if (!container) return;
  container.innerHTML = "";
  const oLevels = Array.isArray(matrix?.o_levels) ? matrix.o_levels : [];
  const rows = Array.isArray(matrix?.rows) ? matrix.rows : [];
  if (oLevels.length === 0 || rows.length === 0) {
    const empty = document.createElement("div");
    empty.className = "o-heatmap-empty";
    empty.textContent = "No O-level data yet.";
    container.appendChild(empty);
    return;
  }
  const table = document.createElement("table");
  table.className = "o-heatmap-table";
  const mpkInteractive = selectedSource === "mpk";

  const thead = document.createElement("thead");
  const headRow = document.createElement("tr");
  const thTower = document.createElement("th");
  thTower.className = "o-sticky-col";
  thTower.textContent = "Tower";
  headRow.appendChild(thTower);
  for (const levelRaw of oLevels) {
    const level = Number(levelRaw);
    const th = document.createElement("th");
    th.className = "o-level-head";
    th.textContent = level === 48 ? "Open (O48)" : `O ${level}`;
    headRow.appendChild(th);
  }
  thead.appendChild(headRow);
  table.appendChild(thead);

  const tbody = document.createElement("tbody");
  for (const row of rows) {
    const tr = document.createElement("tr");
    const rowLabel = String(row.label || `${row.side || ""} ${row.tower_name || ""}`).trim();
    const labelCell = document.createElement("td");
    labelCell.className = "o-sticky-col o-row-name";
    labelCell.textContent = rowLabel;
    tr.appendChild(labelCell);

    const rowCells = Array.isArray(row.cells) ? row.cells : [];
    for (const cell of rowCells) {
      const level = Number(cell.o_level);
      const attempts = Number(cell.attempts || 0);
      const successes = Number(cell.successes || 0);
      const successRateRaw = cell.success_rate;
      const hasData = attempts > 0 && successRateRaw !== null && successRateRaw !== undefined;
      const successRate = hasData ? Number(successRateRaw) : 0;
      const leniencyRaw = cell.leniency;
      const hasLeniency = leniencyRaw !== null && leniencyRaw !== undefined;
      const leniency = hasLeniency ? Number(leniencyRaw) : null;
      const targetKey = cell.target_key ? String(cell.target_key) : "";
      const isLocked = !!targetKey && (lockedMpkTargets.has(targetKey) || !!cell.is_locked);
      const leniencyText = hasLeniency ? `L ${leniency.toFixed(2)}` : "L -";
      const leniencyBlocked =
        selectedSource === "mpk" &&
        (!hasLeniency || leniency <= Number(selectedLeniencyTarget ?? 0));
      const standingRows = Array.isArray(cell.standing_height_breakdown)
        ? cell.standing_height_breakdown
        : [];
      const standingLines =
        standingRows.length > 0
          ? standingRows
              .map((entry) => {
                const y = Number(entry.standing_height);
                const s = Number(entry.successes || 0);
                const a = Number(entry.attempts || 0);
                return `Y(${y}): ${s}/${a}`;
              })
              .join("\n")
          : "No standing-height samples";
      let bestStandingY = null;
      if (standingRows.length > 0) {
        let best = null;
        for (const entry of standingRows) {
          const curSuccesses = Number(entry.successes || 0);
          const curAttempts = Number(entry.attempts || 0);
          const curY = Number(entry.standing_height);
          if (best === null) {
            best = { successes: curSuccesses, attempts: curAttempts, y: curY };
            continue;
          }
          if (curSuccesses > best.successes) {
            best = { successes: curSuccesses, attempts: curAttempts, y: curY };
            continue;
          }
          if (curSuccesses === best.successes && curAttempts > best.attempts) {
            best = { successes: curSuccesses, attempts: curAttempts, y: curY };
            continue;
          }
          if (
            curSuccesses === best.successes &&
            curAttempts === best.attempts &&
            curY > best.y
          ) {
            best = { successes: curSuccesses, attempts: curAttempts, y: curY };
          }
        }
        bestStandingY = best ? best.y : null;
      }

      const td = document.createElement("td");
      td.className = `o-heat-cell${hasData ? "" : " is-empty"}${leniencyBlocked ? " is-leniency-blocked" : ""}${targetKey && mpkInteractive ? " is-lockable" : ""}${isLocked ? " is-locked" : ""}`;
      td.style.backgroundColor = leniencyBlocked
        ? "rgba(0, 0, 0, 0.92)"
        : hasData
          ? oLevelColor(successRate)
          : "rgba(120, 131, 143, 0.55)";
      const blockedText = leniencyBlocked
        ? `\nExcluded by leniency target > ${Number(selectedLeniencyTarget ?? 0).toFixed(2)}`
        : "";
      const lockedText = targetKey ? `\nLock: ${isLocked ? "ON" : "OFF"} (click to toggle)` : "";
      td.title = hasData
        ? `${rowLabel} | O ${level}\n${successes}/${attempts} (${successRate.toFixed(
            2
          )}%)\n${leniencyText}${blockedText}${lockedText}\n${standingLines}`
        : `${rowLabel} | O ${level}\nNo attempts\n${leniencyText}${blockedText}${lockedText}\nNo standing-height samples`;
      if (targetKey && mpkInteractive) {
        td.addEventListener("click", () => {
          if (lockRequestInFlight) return;
          const shouldLock = !lockedMpkTargets.has(targetKey);
          toggleMpkHeatmapLock(targetKey, shouldLock);
        });
      }
      if (hasData) {
        const yText = bestStandingY === null ? "Y-" : `Y${bestStandingY}`;
        td.innerHTML = `<div class="o-cell-wrap"><span class="o-cell-rate">${Math.round(
          successRate
        )}%</span><span class="o-cell-y">${yText}</span><span class="o-cell-attempts">${attempts} att</span><span class="o-cell-leniency">${leniencyText}</span></div>`;
      } else if (hasLeniency) {
        td.innerHTML = `<div class="o-cell-wrap"><span class="o-cell-leniency is-empty">${leniencyText}</span></div>`;
      } else {
        td.textContent = "";
      }
      tr.appendChild(td);
    }
    tbody.appendChild(tr);
  }
  table.appendChild(tbody);
  container.appendChild(table);
}

function sideFromType(zeroType) {
  const t = String(zeroType || "");
  if (t.startsWith("Front ")) return "Front";
  if (t.startsWith("Back ")) return "Back";
  return "Unknown";
}

function setText(id, value) {
  const node = document.getElementById(id);
  if (node) node.textContent = value;
}

function setHtml(id, value) {
  const node = document.getElementById(id);
  if (node) node.innerHTML = value;
}

function buildLeniencyInlineHtml(leniencyValue, thresholdValue, maxLeniencyValue) {
  if (leniencyValue === null || leniencyValue === undefined || Number.isNaN(Number(leniencyValue))) {
    return `<span class="leniency-inline"><span class="leniency-dot-glyph leniency-dot-unknown">●</span>L -</span>`;
  }
  const leniency = Number(leniencyValue);
  const threshold = Number(thresholdValue || 0);
  const maxLeniency = Math.max(Number(maxLeniencyValue || 0), threshold + 0.01);
  const ratio = clamp((leniency - threshold) / (maxLeniency - threshold), 0, 1);
  const hue = 6 + ratio * 138;
  const dotColor = `hsl(${hue}deg 82% 54%)`;
  return `<span class="leniency-inline"><span class="leniency-dot-glyph" style="color:${dotColor}">●</span>L ${leniency.toFixed(2)}</span>`;
}

function enforcePracticeSplitLayout() {
  const grid = document.querySelector(".practice-target-grid");
  if (!grid) return;
  grid.style.display = "grid";
  grid.style.gridTemplateColumns = "repeat(2, minmax(0, 1fr))";
  grid.style.gap = "0";
  grid.style.border = "1px solid rgba(129, 199, 255, 0.24)";
  grid.style.borderRadius = "12px";
  grid.style.overflow = "hidden";
  grid.style.background = "rgba(9, 23, 33, 0.42)";
  const panels = Array.from(grid.querySelectorAll(".practice-target-panel"));
  panels.forEach((panel, idx) => {
    panel.style.border = "0";
    panel.style.borderRadius = "0";
    panel.style.padding = "0.58rem 0.68rem";
    panel.style.background = "transparent";
    panel.style.borderLeft = idx > 0 ? "1px solid rgba(129, 199, 255, 0.22)" : "0";
  });
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
  if (sec < 60) return `${sec} second${sec === 1 ? "" : "s"} ago`;
  const min = Math.floor(sec / 60);
  if (min < 60) return `${min} minute${min === 1 ? "" : "s"} ago`;
  const hours = Math.floor(min / 60);
  const remMin = min % 60;
  if (hours < 24) {
    if (remMin === 0) return `${hours} hour${hours === 1 ? "" : "s"} ago`;
    return `${hours} hour${hours === 1 ? "" : "s"} ${remMin} minute${remMin === 1 ? "" : "s"} ago`;
  }
  const days = Math.floor(hours / 24);
  const remHours = hours % 24;
  if (days < 7) {
    if (remHours === 0) return `${days} day${days === 1 ? "" : "s"} ago`;
    return `${days} day${days === 1 ? "" : "s"} ${remHours} hour${remHours === 1 ? "" : "s"} ago`;
  }
  const weeks = Math.floor(days / 7);
  const remDays = days % 7;
  if (remDays === 0) return `${weeks} week${weeks === 1 ? "" : "s"} ago`;
  return `${weeks} week${weeks === 1 ? "" : "s"} ${remDays} day${remDays === 1 ? "" : "s"} ago`;
}

function renderInjectedComponents(health) {
  const container = document.getElementById("mpkInjectedComponents");
  if (!container) return;
  container.innerHTML = "";
  const components = Array.isArray(health?.mpk_injected_components)
    ? health.mpk_injected_components
    : [];
  if (components.length === 0) {
    const empty = document.createElement("p");
    empty.className = "injected-component-desc";
    empty.textContent = "No injected instance state yet.";
    container.appendChild(empty);
    return;
  }
  for (const comp of components) {
    const row = document.createElement("div");
    row.className = "injected-component-row";

    const present = !!comp?.present;
    const active = !!comp?.active;
    const enabled = comp?.enabled !== false;
    let stateText = "missing";
    let stateClass = "is-missing";
    if (!enabled) {
      stateText = "off";
      stateClass = present ? "is-present" : "is-missing";
    } else if (active) {
      stateText = "active";
      stateClass = "is-active";
    } else if (present) {
      stateText = "present";
      stateClass = "is-present";
    }

    const badge = document.createElement("span");
    badge.className = `injected-component-state ${stateClass}`;
    badge.textContent = stateText;
    row.appendChild(badge);

    const body = document.createElement("div");
    const name = document.createElement("div");
    name.className = "injected-component-name";
    name.textContent = String(comp?.name || "Unknown component");
    body.appendChild(name);

    const desc = document.createElement("div");
    desc.className = "injected-component-desc";
    const seed = String(comp?.seed || "").trim();
    const baseDesc = String(comp?.description || "").trim();
    desc.textContent = seed ? `${baseDesc} Current seed: ${seed}.` : baseDesc;
    body.appendChild(desc);

    const expectedPath = String(comp?.expected_path || "").trim();
    if (expectedPath) {
      row.title = expectedPath;
    }
    row.appendChild(body);
    container.appendChild(row);
  }
}

function syncMpkLegalToggleState() {
  const legalToggle = document.getElementById("mpkLegalRankedToggle");
  const recipeToggle = document.getElementById("mpkInjectRecipeBookToggle");
  const dragonPatchToggle = document.getElementById("mpkInjectDragonPatchToggle");
  const legalEnabled = !!(legalToggle && legalToggle.checked);
  if (recipeToggle) {
    if (legalEnabled) {
      recipeToggle.checked = false;
    }
    recipeToggle.disabled = legalEnabled;
  }
  if (dragonPatchToggle) {
    if (legalEnabled) {
      dragonPatchToggle.checked = false;
    }
    dragonPatchToggle.disabled = legalEnabled;
  }
}

function renderMpkSetupCard(health) {
  const card = document.getElementById("mpkSetupCard");
  const message = document.getElementById("mpkSetupMessage");
  const status = document.getElementById("mpkSetupStatus");
  const input = document.getElementById("mpkInstancePathInput");
  const clearBtn = document.getElementById("mpkClearBtn");
  const legalToggle = document.getElementById("mpkLegalRankedToggle");
  const recipeToggle = document.getElementById("mpkInjectRecipeBookToggle");
  const dragonPatchToggle = document.getElementById("mpkInjectDragonPatchToggle");
  if (!card || !message || !status || !input) return;
  const mpkEnabled = !!(health && health.mpk_enabled);
  const required = !!(health && health.mpk_setup_required && mpkEnabled);
  card.classList.toggle("hidden", !mpkEnabled);
  renderInjectedComponents(health);
  if (!mpkEnabled) return;
  if (clearBtn) {
    const hasConfiguredPath = String((health && health.mpk_instance_path) || "").trim().length > 0;
    clearBtn.disabled = !hasConfiguredPath;
  }
  const setupError = String((health && health.mpk_setup_error) || "").trim();
  if (required) {
    message.textContent =
      setupError ||
      "Configured MPK instance path is missing/invalid. Set your MultiMC instance path so injector can configure Atum and datapack.";
  } else {
    message.textContent = "MPK injector is configured. You can update the instance path or uninject and clear it.";
  }
  if (document.activeElement !== input) {
    input.value = String((health && health.mpk_instance_path) || "");
  }
  if (legalToggle) {
    legalToggle.checked = !!(health && health.mpk_legal_ranked_instance);
  }
  if (recipeToggle) {
    recipeToggle.checked = !!(health && health.mpk_inject_recipe_book !== false);
  }
  if (dragonPatchToggle) {
    dragonPatchToggle.checked = !!(health && health.mpk_inject_dragon_patch !== false);
  }
  syncMpkLegalToggleState();
}

function formatMpkTargetMain(side, towerName, oLevel) {
  const sideText = String(side || "").trim();
  const towerText = String(towerName || "").trim();
  const oText =
    oLevel === null || oLevel === undefined || Number.isNaN(Number(oLevel))
      ? ""
      : ` (O${Number(oLevel)})`;
  const main = `${sideText} ${towerText}`.trim();
  return main ? `${main}${oText}` : "-";
}

function normalizeLockedTargetKeys(rawKeys) {
  if (!Array.isArray(rawKeys)) return new Set();
  const out = new Set();
  for (const raw of rawKeys) {
    const key = String(raw || "").trim();
    if (key.startsWith("mpk|")) out.add(key);
  }
  return out;
}

function formatMpkTargetFromKey(targetKey) {
  const parts = String(targetKey || "").split("|");
  if (parts.length !== 4 || parts[0] !== "mpk") return String(targetKey || "");
  const towerName = parts[1];
  const side = parts[2];
  const oLevel = Number(parts[3]);
  return formatMpkTargetMain(side, towerName, Number.isNaN(oLevel) ? null : oLevel);
}

function updateMpkLockControls(widget) {
  const lockRow = document.querySelector(".practice-lock-row");
  const clearBtn = document.getElementById("clearMpkLocksBtn");
  const skipWeakBtn = document.getElementById("skipWeakLockBtn");
  const overrideBtn = document.getElementById("fullRandomOverrideBtn");
  const legalRankedEnabled = !!(lastHealth && lastHealth.mpk_legal_ranked_instance);
  const isMpk = widget && widget.source === "mpk";
  const overrideEnabled = !!(widget && widget.full_random_override_enabled);
  const currentPractice = widget && widget.current_practice ? widget.current_practice : null;
  const currentMode = String((currentPractice && currentPractice.selection_mode) || "").toLowerCase();
  const weakLockActive =
    !!(currentPractice && currentMode === "weak" && currentPractice.weak_lock_active) ||
    !!(widget && widget.weak_streak_status && widget.weak_streak_status.lock_target);
  if (lockRow) {
    lockRow.style.display = isMpk ? "flex" : "none";
  }
  if (clearBtn) {
    clearBtn.style.display = isMpk ? "" : "none";
    const lockCount = lockedMpkTargets.size;
    clearBtn.disabled = lockRequestInFlight || lockCount === 0;
    clearBtn.textContent = lockRequestInFlight ? "Updating..." : "Clear Lock List";
  }
  if (skipWeakBtn) {
    skipWeakBtn.style.display = isMpk ? "" : "none";
    skipWeakBtn.disabled = weakLockSkipRequestInFlight || !weakLockActive;
    skipWeakBtn.textContent = weakLockSkipRequestInFlight ? "Skipping..." : "Skip Weak Lock";
  }
  if (overrideBtn) {
    overrideBtn.style.display = isMpk ? "" : "none";
    overrideBtn.disabled = fullRandomOverrideRequestInFlight || legalRankedEnabled;
    overrideBtn.textContent = fullRandomOverrideRequestInFlight
      ? "Updating..."
      : legalRankedEnabled
        ? "Forced (Legal Mode)"
      : overrideEnabled
        ? "Disable Full Random"
        : "Enable Full Random";
  }
  if (!isMpk) return;
  const labels = Array.from(lockedMpkTargets).map((k) => formatMpkTargetFromKey(k));
  const lockSummary =
    labels.length > 0
      ? `Lock list (${labels.length}): ${labels.join(", ")}`
      : "Lock list: none (click heatmap cells to add multiple targets).";
  setText("practiceMissingTowers", lockSummary);
  setText("practiceMissing18", "");
}

async function postMpkLockTarget(targetKey, locked) {
  const params = new URLSearchParams();
  params.set("target_key", String(targetKey || ""));
  if (locked !== null && locked !== undefined) {
    params.set("locked", locked ? "true" : "false");
  }
  const res = await fetch(`/api/mpk/lock-target?${params.toString()}`, { method: "POST" });
  const json = await res.json();
  if (!res.ok || !json.ok) {
    throw new Error(json.error || `Lock update failed (${res.status})`);
  }
  return normalizeLockedTargetKeys(json.locked_target_keys || []);
}

async function postClearMpkLocks() {
  const res = await fetch("/api/mpk/lock-targets/clear", { method: "POST" });
  const json = await res.json();
  if (!res.ok || !json.ok) {
    throw new Error(json.error || `Clear lock list failed (${res.status})`);
  }
  return normalizeLockedTargetKeys(json.locked_target_keys || []);
}

async function postSetSingleMpkLock(targetKey) {
  const params = new URLSearchParams();
  params.set("target_key", String(targetKey || ""));
  const res = await fetch(`/api/mpk/lock-targets/set-single?${params.toString()}`, { method: "POST" });
  const json = await res.json();
  if (!res.ok || !json.ok) {
    throw new Error(json.error || `Set single lock target failed (${res.status})`);
  }
  return normalizeLockedTargetKeys(json.locked_target_keys || []);
}

async function postMpkFullRandomOverride(enabled) {
  const params = new URLSearchParams();
  params.set("enabled", enabled ? "true" : "false");
  const res = await fetch(`/api/mpk/full-random-override?${params.toString()}`, { method: "POST" });
  const json = await res.json();
  if (!res.ok || !json.ok) {
    throw new Error(json.error || `Full random override update failed (${res.status})`);
  }
  return json;
}

async function postSkipMpkWeakLock() {
  const res = await fetch("/api/mpk/weak-lock/skip", { method: "POST" });
  const json = await res.json();
  if (!res.ok || !json.ok) {
    throw new Error(json.error || `Skip weak lock failed (${res.status})`);
  }
  return json;
}

async function toggleMpkHeatmapLock(targetKey, nextLocked) {
  if (!targetKey || lockRequestInFlight) return;
  lockRequestInFlight = true;
  updateMpkLockControls(lastPayload?.practice_next || {});
  try {
    lockedMpkTargets = await postMpkLockTarget(targetKey, nextLocked);
    await refresh("full");
  } catch (error) {
    console.error("Failed to update MPK lock list:", error);
  } finally {
    lockRequestInFlight = false;
    updateMpkLockControls(lastPayload?.practice_next || {});
  }
}

const MPK_SCHEDULER_THRESHOLD_NOOKS = [
  { threshold: 25, range: "25% to <50%", behavior: "Fill 90%, Weak 10%, Maintain 0%" },
  { threshold: 50, range: "50% to <80%", behavior: "Fill 50%, Weak 30%, Maintain 20%" },
  { threshold: 80, range: "80% to <95%", behavior: "Fill 30%, Weak 50%, Maintain 20%" },
  { threshold: 95, range: "95% to 100%", behavior: "Fill 10%, Weak 50%, Maintain 40%" },
];

function clearPracticeThresholdNooks() {
  const nooks = document.getElementById("practiceDataThresholds");
  if (!nooks) return;
  nooks.innerHTML = "";
}

function ensureThresholdTooltip() {
  if (thresholdTooltipEl && document.body.contains(thresholdTooltipEl)) {
    return thresholdTooltipEl;
  }
  const node = document.createElement("div");
  node.id = "practiceThresholdTooltip";
  node.className = "practice-threshold-tooltip";
  document.body.appendChild(node);
  thresholdTooltipEl = node;
  return node;
}

function hideThresholdTooltip() {
  const tip = ensureThresholdTooltip();
  tip.classList.remove("is-visible");
}

function showThresholdTooltip(text, clientX, clientY) {
  const tip = ensureThresholdTooltip();
  tip.textContent = text;
  tip.classList.add("is-visible");
  const margin = 14;
  const maxLeft = Math.max(8, window.innerWidth - tip.offsetWidth - 8);
  const maxTop = Math.max(8, window.innerHeight - tip.offsetHeight - 8);
  const left = Math.min(maxLeft, Math.max(8, clientX + margin));
  const top = Math.min(maxTop, Math.max(8, clientY + margin));
  tip.style.left = `${left}px`;
  tip.style.top = `${top}px`;
}

function bindThresholdTooltip(node, text) {
  if (!node) return;
  node.addEventListener("mouseenter", (event) => {
    showThresholdTooltip(text, event.clientX, event.clientY);
  });
  node.addEventListener("mousemove", (event) => {
    showThresholdTooltip(text, event.clientX, event.clientY);
  });
  node.addEventListener("mouseleave", () => {
    hideThresholdTooltip();
  });
}

function renderPracticeThresholdNooks(progressPct, isMpkSource) {
  const nooks = document.getElementById("practiceDataThresholds");
  if (!nooks) return;
  nooks.innerHTML = "";
  if (!isMpkSource) return;
  for (const row of MPK_SCHEDULER_THRESHOLD_NOOKS) {
    const node = document.createElement("span");
    const threshold = Number(row.threshold || 0);
    node.className = `practice-threshold-nook${progressPct >= threshold ? " is-active" : ""}`;
    node.style.left = `${threshold}%`;
    const tooltipText =
      `Sample coverage >= ${threshold}%\n` +
      `Range: ${row.range}\n` +
      `Scheduler: ${row.behavior}`;
    bindThresholdTooltip(node, tooltipText);
    nooks.appendChild(node);
  }
}

function renderPracticeNext(payload) {
  enforcePracticeSplitLayout();
  const widget = payload.practice_next || {};
  if (Array.isArray(widget.locked_target_keys)) {
    lockedMpkTargets = normalizeLockedTargetKeys(widget.locked_target_keys);
  }
  const rec = widget.recommended;
  const copyBtn = document.getElementById("copyPracticeCommandBtn");
  const soundBtn = document.getElementById("testPracticeSoundBtn");
  const commandRow = document.querySelector(".practice-command-row");
  setText("practiceCurrentHeading", "Current");
  setText("practiceNextHeading", "Next");
  setHtml("practiceCurrentLeniency", "");
  setHtml("practiceNextLeniency", "");
  if (widget.disabled) {
    if (commandRow) commandRow.style.display = widget.source === "mpk" ? "none" : "";
    if (soundBtn) soundBtn.style.display = widget.source === "mpk" ? "none" : "";
    setText("practiceCurrentMain", "");
    setText("practiceCurrentStats", "");
    setText("practiceCurrentReason", "");
    setText("practiceNextMain", "What To Practice Next is disabled for MPK view.");
    setText("practiceNextStats", "");
    setText("practiceNextReason", "");
    setText("practiceNextMeta", widget.disabled_reason || "");
    setText("practiceNextCommand", "");
    setText("practiceMissingTowers", "");
    setText("practiceMissing18", "");
    updateMpkLockControls(widget);
    if (copyBtn) {
      copyBtn.disabled = true;
      copyBtn.textContent = "Copy";
    }
    const progressBar = document.getElementById("practiceDataProgressBar");
    if (progressBar) progressBar.style.width = "0%";
    clearPracticeThresholdNooks();
    const progressTextNode = document.getElementById("practiceDataProgressText");
    if (progressTextNode) progressTextNode.textContent = "";
    currentPracticeCommand = "";
    lastWeakLockState = { lockActive: false, targetKey: "", streak: 0, minStreak: 3 };
    return;
  }
  const windowSize = widget.window_size || 250;
  const progressBar = document.getElementById("practiceDataProgressBar");
  const progressTextNode = document.getElementById("practiceDataProgressText");
  const distribution = widget.distribution || {};
  const coveragePct = Number(distribution.coverage_percent || 0);
  const thresholdPct = Number(distribution.threshold_percent || 80);
  const qualifiedTargets = Number(distribution.qualified_targets || 0);
  const totalTargets = Number(distribution.total_targets || 0);
  const minPointsPerTarget = Number(distribution.min_points_per_target || 3);
  const modeCoverage = Number(distribution.mode_coverage_percent || 0);
  const modeQualified = Number(distribution.mode_qualified_targets || 0);
  const modeTotal = Number(distribution.mode_total_targets || totalTargets);
  const modeMinSamples = Number(distribution.min_points_per_target || 2);
  const progressCoveragePct = widget.source === "mpk" ? modeCoverage : coveragePct;
  if (progressBar) {
    const widthPct = Math.max(0, Math.min(100, progressCoveragePct));
    progressBar.style.width = `${widthPct}%`;
  }
  renderPracticeThresholdNooks(progressCoveragePct, widget.source === "mpk");
  if (progressTextNode) {
    if (widget.source === "mpk") {
      const eligibleTargets = Number(distribution.eligible_targets || totalTargets);
      const seedTargets = Number(distribution.seed_targets || totalTargets);
      progressTextNode.textContent =
        `Sample coverage (>=${modeMinSamples} attempts/target): ${modeCoverage.toFixed(2)}% (${modeQualified}/${modeTotal}); MPK attempted (any samples): ${coveragePct.toFixed(2)}% (${qualifiedTargets}/${totalTargets}); eligible by leniency: ${eligibleTargets}/${seedTargets}.`;
    } else {
      progressTextNode.textContent =
        `Data coverage (last ${windowSize}): ${coveragePct.toFixed(2)}% (${qualifiedTargets}/${totalTargets} targets with >=${minPointsPerTarget} attempts). Need ${thresholdPct.toFixed(0)}%.`;
    }
  }
  if (widget.source === "mpk" && !!widget.full_random_override_enabled) {
    if (commandRow) commandRow.style.display = "none";
    if (soundBtn) soundBtn.style.display = "none";
    setText("practiceCurrentHeading", "Current (full random)");
    setText("practiceNextHeading", "Next (full random)");
    setHtml("practiceCurrentLeniency", "");
    setHtml("practiceNextLeniency", "");
    setText("practiceCurrentMain", "Random MPK seed");
    setText("practiceCurrentStats", "Scheduler paused. Forced seed injection disabled.");
    setText("practiceCurrentReason", "");
    setText("practiceNextMain", "Random MPK seed");
    setText("practiceNextStats", "Atum seed is cleared and left empty while override is enabled.");
    setText("practiceNextReason", "");
    setText("practiceNextMeta", "Disable Full Random to resume normal scheduling and seed injection.");
    setText("practiceNextCommand", "");
    currentPracticeCommand = "";
    if (copyBtn) {
      copyBtn.disabled = true;
      copyBtn.textContent = "Copy";
    }
    lastWeakLockState = { lockActive: false, targetKey: "", streak: 0, minStreak: 3 };
    updateMpkLockControls(widget);
    return;
  }
  if (!rec) {
    if (commandRow) commandRow.style.display = widget.source === "mpk" ? "none" : "";
    if (soundBtn) soundBtn.style.display = widget.source === "mpk" ? "none" : "";
    setText("practiceCurrentMain", "");
    setText("practiceCurrentStats", "");
    setText("practiceCurrentReason", "");
    setText("practiceNextMain", "No recommendation yet.");
    setText("practiceNextStats", "");
    setText("practiceNextReason", "");
    setText("practiceNextMeta", "");
    setText("practiceNextCommand", "");
    currentPracticeCommand = "";
    lastWeakLockState = { lockActive: false, targetKey: "", streak: 0, minStreak: 3 };
    if (copyBtn) {
      copyBtn.disabled = true;
      copyBtn.textContent = "Copy";
    }
    setText("practiceMissingTowers", "");
    setText("practiceMissing18", "");
    updateMpkLockControls(widget);
    return;
  }
  if (rec.target_kind === "full_random") {
    if (commandRow) commandRow.style.display = "";
    if (soundBtn) soundBtn.style.display = "";
    setText("practiceCurrentMain", "");
    setText("practiceCurrentStats", "");
    setText("practiceCurrentReason", "");
    setText("practiceNextMain", "Full Random");
    setText("practiceNextStats", "");
    setText("practiceNextReason", "");
    setText(
      "practiceNextMeta",
      `Not enough balanced data yet. Play full random until coverage reaches ${thresholdPct.toFixed(0)}%.`
    );
  } else if (rec.target_kind === "mpk_zero") {
    if (commandRow) commandRow.style.display = "none";
    if (soundBtn) soundBtn.style.display = "none";
    const current = widget.current_practice || null;
    const currentModeLabel = String((current && current.selection_mode) || "").trim() || "n/a";
    const nextModeLabel = String(widget.selection_mode || "").trim() || "n/a";
    setText("practiceCurrentHeading", `Current (${currentModeLabel})`);
    setText("practiceNextHeading", `Next (${nextModeLabel})`);
    const currentLabel =
      current && current.o_level !== null && current.o_level !== undefined
        ? formatMpkTargetMain(current.side, current.tower_name, current.o_level)
        : current && current.seed_value
          ? `Unknown target (Seed ${current.seed_value})`
          : "Waiting for run start";
    setText("practiceCurrentMain", currentLabel);
    setText("practiceNextMain", formatMpkTargetMain(rec.side, rec.tower_name, rec.o_level));
    const seedValue = rec.selected_seed ? String(rec.selected_seed) : "-";
    const leniencyThreshold = Number(widget.leniency_target || 0);
    const maxLeniency = Number(distribution.max_leniency || 0);
    const currentLeniencyPill = buildLeniencyInlineHtml(
      current && current.leniency,
      leniencyThreshold,
      maxLeniency
    );
    const nextLeniencyPill = buildLeniencyInlineHtml(rec.leniency, leniencyThreshold, maxLeniency);
    setHtml("practiceCurrentLeniency", currentLeniencyPill);
    setHtml("practiceNextLeniency", nextLeniencyPill);
    setHtml(
      "practiceCurrentStats",
      current
        ? `Success ${formatPct(current.success_rate)} (${Number(current.successes || 0)}/${Number(current.attempts || 0)})`
        : "No run loaded yet."
    );
    const weakMin = Number(widget.min_streak_to_swap || 3);
    if (current && String(current.selection_mode || "").toLowerCase() === "weak") {
      const currentWeakStreak = Number(current.weak_streak ?? 0);
      const currentWeakLock = !!current.weak_lock_active;
      setText(
        "practiceCurrentReason",
        `Weak streak: ${currentWeakStreak}/${weakMin}${currentWeakLock ? " (locked)" : ""}.`
      );
    } else {
      setText("practiceCurrentReason", "");
    }
    setHtml(
      "practiceNextStats",
      `Success ${formatPct(rec.success_rate)} (${rec.successes}/${rec.attempts}) | Seed ${seedValue}`
    );
    if (String(widget.selection_mode || "").toLowerCase() === "weak") {
      const nextWeakStreak = Number(
        rec.weak_streak ?? widget.current_streak_on_recommended ?? 0
      );
      const nextWeakLock = !!(rec.weak_lock_active || widget.lock_applied);
      setText(
        "practiceNextReason",
        `Weak streak: ${nextWeakStreak}/${weakMin}${nextWeakLock ? " (locked)" : ""}.`
      );
    } else {
      setText("practiceNextReason", "");
    }
    setText("practiceNextMeta", "");
  } else {
    if (commandRow) commandRow.style.display = "";
    if (soundBtn) soundBtn.style.display = "";
    setText("practiceCurrentMain", "");
    setText("practiceCurrentStats", "");
    setText("practiceCurrentReason", "");
    setText("practiceNextMain", `${rec.tower_name} | ${rec.side} | ${rec.rotation}`);
    setText("practiceNextStats", `Success ${formatPct(rec.success_rate)} (${rec.successes}/${rec.attempts})`);
    const streak = Number(widget.current_streak_on_recommended || 0);
    const minStreak = Number(widget.min_streak_to_swap || 3);
    const lockText = widget.lock_applied
      ? `Locked: keep this target until target streak reaches ${minStreak} (current target streak: ${streak}).`
      : `Not locked: target streak ${streak}/${minStreak}.`;
    setText("practiceNextReason", "");
    setText(
      "practiceNextMeta",
      `Last ${windowSize} attempts. ${lockText}`
    );
  }
  currentPracticeCommand = rec.chat_command ? String(rec.chat_command) : "";
  if (rec.target_kind === "mpk_zero") {
    setText("practiceNextCommand", "");
  } else {
    setText("practiceNextCommand", currentPracticeCommand ? `Paste in chat: ${currentPracticeCommand}` : "");
  }
  if (copyBtn) {
    copyBtn.disabled = rec.target_kind === "mpk_zero" || !currentPracticeCommand;
    copyBtn.textContent = "Copy";
  }
  const missingTowers = widget.missing_towers || [];
  const missing18 = widget.missing_1_8_groups || [];
  if (rec.target_kind !== "mpk_zero" && widget.source !== "mpk") {
    setText(
      "practiceMissingTowers",
      missingTowers.length > 0
        ? `Unattempted targets: ${missingTowers.join(", ")}`
        : `All non-1/8 towers were attempted in last ${windowSize}.`
    );
    setText(
      "practiceMissing18",
      missing18.length > 0
        ? `1/8 groups not yet attempted in last ${windowSize}: ${missing18.join(", ")}`
        : `All observed 1/8 groups were attempted in last ${windowSize}.`
    );
  }
  const currentPractice = widget.current_practice || null;
  const currentMode = String((currentPractice && currentPractice.selection_mode) || "").toLowerCase();
  const weakLockActiveNow = currentMode === "weak" && !!(currentPractice && currentPractice.weak_lock_active);
  const weakTargetKeyNow = weakLockActiveNow ? String(currentPractice.target_key || "") : "";
  const weakStreakNow = weakLockActiveNow ? Number(currentPractice.weak_streak || 0) : 0;
  const weakMinNow = weakLockActiveNow
    ? Number(currentPractice.weak_min_streak || widget.min_streak_to_swap || 3)
    : Number(widget.min_streak_to_swap || 3);
  const weakJustCompleted =
    lastWeakLockState.lockActive &&
    (!weakLockActiveNow || weakTargetKeyNow !== lastWeakLockState.targetKey) &&
    Number(lastWeakLockState.streak || 0) >= Number(lastWeakLockState.minStreak || 3);

  if (weakJustCompleted) {
    playPracticeSwitchSound();
  }
  if (weakLockActiveNow) {
    lastWeakLockState = {
      lockActive: true,
      targetKey: weakTargetKeyNow,
      streak: weakStreakNow,
      minStreak: weakMinNow,
    };
  } else {
    lastWeakLockState = { lockActive: false, targetKey: "", streak: 0, minStreak: weakMinNow };
  }

  updateMpkLockControls(widget);
}

function applyRollingMode() {
  if (!rollingConsistencyChart) return;
  const show10 = rollingMode === "all" || rollingMode === "r10";
  const show25 = rollingMode === "all" || rollingMode === "r25";
  const show50 = rollingMode === "all" || rollingMode === "r50";
  if (rollingConsistencyChart.data.datasets[0]) {
    rollingConsistencyChart.data.datasets[0].hidden = !show10;
  }
  if (rollingConsistencyChart.data.datasets[1]) {
    rollingConsistencyChart.data.datasets[1].hidden = !show25;
  }
  if (rollingConsistencyChart.data.datasets[2]) {
    rollingConsistencyChart.data.datasets[2].hidden = !show50;
  }
}

function playPracticeSwitchSound() {
  const ctx = ensurePracticeAudioContext();
  if (!ctx) return;
  if (ctx.state === "suspended") {
    ctx.resume().catch(() => {});
  }
  const now = ctx.currentTime;

  const notes = [
    { freq: 660, start: 0.0, dur: 0.08, gain: 0.06 },
    { freq: 880, start: 0.085, dur: 0.1, gain: 0.07 },
    { freq: 1046, start: 0.19, dur: 0.14, gain: 0.08 },
  ];

  for (const note of notes) {
    const osc = ctx.createOscillator();
    const gain = ctx.createGain();
    osc.type = "sine";
    osc.frequency.setValueAtTime(note.freq, now + note.start);
    gain.gain.setValueAtTime(0, now + note.start);
    gain.gain.linearRampToValueAtTime(note.gain, now + note.start + 0.01);
    gain.gain.exponentialRampToValueAtTime(0.0001, now + note.start + note.dur);
    osc.connect(gain);
    gain.connect(ctx.destination);
    osc.start(now + note.start);
    osc.stop(now + note.start + note.dur + 0.02);
  }
}

function ensurePracticeAudioContext() {
  const AudioCtx = window.AudioContext || window.webkitAudioContext;
  if (!AudioCtx) return null;
  if (!practiceAudioCtx || practiceAudioCtx.state === "closed") {
    practiceAudioCtx = new AudioCtx();
  }
  return practiceAudioCtx;
}

function unlockPracticeAudio() {
  const ctx = ensurePracticeAudioContext();
  if (!ctx) return;
  if (ctx.state === "suspended") {
    ctx.resume().catch(() => {});
  }
}

async function copyPracticeCommand() {
  const copyBtn = document.getElementById("copyPracticeCommandBtn");
  if (!copyBtn || !currentPracticeCommand) return;

  let copied = false;
  try {
    if (navigator.clipboard && navigator.clipboard.writeText) {
      await navigator.clipboard.writeText(currentPracticeCommand);
      copied = true;
    }
  } catch {
    copied = false;
  }

  if (!copied) {
    try {
      const ta = document.createElement("textarea");
      ta.value = currentPracticeCommand;
      ta.setAttribute("readonly", "");
      ta.style.position = "fixed";
      ta.style.left = "-9999px";
      document.body.appendChild(ta);
      ta.select();
      copied = document.execCommand("copy");
      document.body.removeChild(ta);
    } catch {
      copied = false;
    }
  }

  copyBtn.textContent = copied ? "Copied" : "Copy failed";
  setTimeout(() => {
    copyBtn.textContent = "Copy";
  }, 1200);
}

function getScope(payload) {
  if (!payload) return null;
  if (payload.scope) return payload.scope;
  if (payload.global) return payload.global;
  return null;
}

function updateFilters(payload) {
  const scopedPayload = payload;
  const towerSelect = document.getElementById("towerFilter");
  const sideSelect = document.getElementById("sideFilter");
  if (!towerSelect || !sideSelect || !scopedPayload) return;

  const fallbackTowers = (scopedPayload.tower_front_back_overview || [])
    .map((r) => r.tower_name)
    .filter((v) => v && v !== "Unknown");
  const fallbackSides = (scopedPayload.tower_front_back_overview || [])
    .map((r) => r.front_back)
    .filter((v) => v === "Front" || v === "Back");

  const unique = (arr) => [...new Set(arr)];
  const towerOptions = ["__GLOBAL__", ...unique(scopedPayload.available_towers || fallbackTowers)];
  const sideOptions = ["__GLOBAL__", ...unique(scopedPayload.available_front_backs || fallbackSides)];
  const currentTower = selectedTower;
  const currentSide = selectedSide;

  towerSelect.innerHTML = "";
  for (const value of towerOptions) {
    const opt = document.createElement("option");
    opt.value = value;
    opt.textContent = value === "__GLOBAL__" ? "All Towers" : value;
    towerSelect.appendChild(opt);
  }

  sideSelect.innerHTML = "";
  for (const value of sideOptions) {
    const opt = document.createElement("option");
    opt.value = value;
    opt.textContent = value === "__GLOBAL__" ? "Both Sides" : value;
    sideSelect.appendChild(opt);
  }

  if (!towerOptions.includes(currentTower)) {
    selectedTower = "__GLOBAL__";
  }
  if (!sideOptions.includes(currentSide)) {
    selectedSide = "__GLOBAL__";
  }

  towerSelect.value = selectedTower;
  sideSelect.value = selectedSide;
}

function ensureFilterDefaults() {
  const leniencyInput = document.getElementById("leniencyTarget");
  const windowSelect = document.getElementById("windowFilter");
  const rotationSelect = document.getElementById("rotationFilter");
  const towerSelect = document.getElementById("towerFilter");
  const sideSelect = document.getElementById("sideFilter");
  if (rotationSelect && rotationSelect.options.length === 0) {
    const options = [
      ["both", "Both"],
      ["cw", "CW"],
      ["ccw", "CCW"],
    ];
    for (const [value, label] of options) {
      const opt = document.createElement("option");
      opt.value = value;
      opt.textContent = label;
      rotationSelect.appendChild(opt);
    }
  }
  if (windowSelect && windowSelect.options.length === 0) {
    const options = [
      ["all", "All"],
      ["current_session", "Current Session"],
      ["last_10", "Last 10"],
      ["last_25", "Last 25"],
      ["last_50", "Last 50"],
      ["last_100", "Last 100"],
    ];
    for (const [value, label] of options) {
      const opt = document.createElement("option");
      opt.value = value;
      opt.textContent = label;
      windowSelect.appendChild(opt);
    }
  }
  if (towerSelect && towerSelect.options.length === 0) {
    const opt = document.createElement("option");
    opt.value = "__GLOBAL__";
    opt.textContent = "All Towers";
    towerSelect.appendChild(opt);
  }
  if (sideSelect && sideSelect.options.length === 0) {
    const opt = document.createElement("option");
    opt.value = "__GLOBAL__";
    opt.textContent = "Both Sides";
    sideSelect.appendChild(opt);
  }
  if (leniencyInput && leniencyInput.value === "") {
    leniencyInput.value = selectedLeniencyTarget === null ? "" : String(selectedLeniencyTarget);
  }
}

function ensureCharts() {
  if (!progressionChart) {
    progressionChart = new Chart(document.getElementById("progressionChart"), {
      type: "line",
      data: {
        labels: [],
        datasets: [
          {
            label: "Success Rate %",
            data: [],
            borderColor: "#4ad7a7",
            backgroundColor: "rgba(74, 215, 167, 0.2)",
            fill: true,
            tension: 0.25,
            pointRadius: 4,
            pointHoverRadius: 5,
            pointBackgroundColor: "#4ad7a7",
            pointBorderColor: "#e8f5ff",
            pointBorderWidth: 1,
          },
        ],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        plugins: { legend: { labels: { color: "#e8f5ff" } } },
        scales: {
          x: { ticks: { color: "#c8deed" } },
          y: { min: 0, max: 100, ticks: { color: "#c8deed" } },
        },
      },
    });
  }

  if (!damageChart) {
    damageChart = new Chart(document.getElementById("damageChart"), {
      type: "bar",
      data: {
        labels: [],
        datasets: [
          {
            label: "Avg Major Damage",
            data: [],
            backgroundColor: "rgba(110, 168, 255, 0.75)",
            borderColor: "#6ea8ff",
            borderWidth: 1,
          },
        ],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        plugins: { legend: { labels: { color: "#e8f5ff" } } },
        scales: {
          x: { ticks: { color: "#c8deed" } },
          y: { ticks: { color: "#c8deed" } },
        },
      },
    });
  }

  if (!efficiencyChart) {
    efficiencyChart = new Chart(document.getElementById("efficiencyChart"), {
      type: "line",
      data: {
        labels: [],
        datasets: [
          {
            label: "Avg Rotations",
            data: [],
            borderColor: "#f7c96c",
            backgroundColor: "rgba(247, 201, 108, 0.2)",
            borderWidth: 1,
            tension: 0.25,
          },
          {
            label: "Avg Total Explosives",
            data: [],
            borderColor: "#ff7564",
            backgroundColor: "rgba(255, 117, 100, 0.2)",
            borderWidth: 1,
            tension: 0.25,
          },
        ],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        plugins: { legend: { labels: { color: "#e8f5ff" } } },
        scales: {
          x: { ticks: { color: "#c8deed" } },
          y: { ticks: { color: "#c8deed" } },
        },
      },
    });
  }

  if (!timeSeriesChart) {
    timeSeriesChart = new Chart(document.getElementById("timeSeriesChart"), {
      type: "line",
      data: {
        labels: [],
        datasets: [
          {
            label: "Success Time (s)",
            data: [],
            borderColor: "#4ad7a7",
            backgroundColor: "rgba(74, 215, 167, 0.15)",
            tension: 0.25,
          },
        ],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        plugins: { legend: { labels: { color: "#e8f5ff" } } },
        scales: {
          x: { ticks: { color: "#c8deed" } },
          y: { ticks: { color: "#c8deed" } },
        },
      },
    });
  }

  if (!rollingConsistencyChart) {
    rollingConsistencyChart = new Chart(document.getElementById("rollingConsistencyChart"), {
      type: "line",
      data: {
        labels: [],
        datasets: [
          {
            label: "R10",
            data: [],
            borderColor: "#f7c96c",
            backgroundColor: "rgba(247, 201, 108, 0.14)",
            tension: 0.35,
            pointRadius: 0,
            pointHoverRadius: 0,
            borderWidth: 2,
          },
          {
            label: "R25",
            data: [],
            borderColor: "#6ea8ff",
            backgroundColor: "rgba(110, 168, 255, 0.14)",
            tension: 0.35,
            pointRadius: 0,
            pointHoverRadius: 0,
            borderWidth: 2,
          },
          {
            label: "R50",
            data: [],
            borderColor: "#4ad7a7",
            backgroundColor: "rgba(74, 215, 167, 0.14)",
            tension: 0.35,
            pointRadius: 0,
            pointHoverRadius: 0,
            borderWidth: 2,
          },
        ],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        plugins: {
          legend: {
            position: "bottom",
            labels: {
              color: "#e8f5ff",
              boxWidth: 14,
              boxHeight: 6,
              padding: 10,
            },
          },
        },
        scales: {
          x: {
            ticks: {
              color: "#a9c7db",
              maxTicksLimit: 8,
            },
            grid: { color: "rgba(157, 194, 217, 0.08)" },
          },
          y: {
            min: 0,
            max: 100,
            ticks: { color: "#c8deed" },
            grid: { color: "rgba(157, 194, 217, 0.12)" },
          },
        },
      },
    });
  }

  if (!speedBinsChart) {
    speedBinsChart = new Chart(document.getElementById("speedBinsChart"), {
      type: "bar",
      data: {
        labels: [],
        datasets: [
          {
            label: "Successes",
            data: [],
            backgroundColor: "rgba(74, 215, 167, 0.7)",
            borderColor: "#4ad7a7",
            borderWidth: 1,
          },
        ],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        plugins: { legend: { labels: { color: "#e8f5ff" } } },
        scales: {
          x: { ticks: { color: "#c8deed" } },
          y: { ticks: { color: "#c8deed" } },
        },
      },
    });
  }

  if (!attemptsByHourChart) {
    attemptsByHourChart = new Chart(document.getElementById("attemptsByHourChart"), {
      type: "bar",
      data: {
        labels: [],
        datasets: [
          {
            label: "Attempts",
            data: [],
            backgroundColor: "rgba(247, 201, 108, 0.6)",
            borderColor: "#f7c96c",
            borderWidth: 1,
          },
          {
            label: "Success Rate %",
            data: [],
            type: "line",
            borderColor: "#4ad7a7",
            backgroundColor: "rgba(74, 215, 167, 0.2)",
            yAxisID: "y1",
            tension: 0.25,
          },
        ],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        plugins: { legend: { labels: { color: "#e8f5ff" } } },
        scales: {
          x: { ticks: { color: "#c8deed" } },
          y: { ticks: { color: "#c8deed" } },
          y1: {
            position: "right",
            min: 0,
            max: 100,
            ticks: { color: "#c8deed" },
            grid: { drawOnChartArea: false },
          },
        },
      },
    });
  }

  if (!outcomeRunsChart) {
    outcomeRunsChart = new Chart(document.getElementById("outcomeRunsChart"), {
      type: "bar",
      data: {
        labels: [],
        datasets: [
          {
            label: "Run Length",
            data: [],
            backgroundColor: [],
          },
        ],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        plugins: { legend: { labels: { color: "#e8f5ff" } } },
        scales: {
          x: { ticks: { color: "#c8deed" } },
          y: { ticks: { color: "#c8deed" } },
        },
      },
    });
  }

  if (!standingHeightConsistencyChart) {
    standingHeightConsistencyChart = new Chart(
      document.getElementById("standingHeightConsistencyChart"),
      {
        type: "bar",
        data: {
          labels: [],
          datasets: [
            {
              label: "Attempts",
              data: [],
              backgroundColor: "rgba(247, 201, 108, 0.6)",
              borderColor: "#f7c96c",
              borderWidth: 1,
            },
            {
              label: "Success Rate %",
              data: [],
              type: "line",
              borderColor: "#4ad7a7",
              backgroundColor: "rgba(74, 215, 167, 0.2)",
              yAxisID: "y1",
              tension: 0.25,
            },
          ],
        },
        options: {
          responsive: true,
          maintainAspectRatio: false,
          plugins: { legend: { labels: { color: "#e8f5ff" } } },
          scales: {
            x: { ticks: { color: "#c8deed" } },
            y: { ticks: { color: "#c8deed" } },
            y1: {
              position: "right",
              min: 0,
              max: 100,
              ticks: { color: "#c8deed" },
              grid: { drawOnChartArea: false },
            },
          },
        },
      }
    );
  }

  if (!towerBackRadarChart) {
    towerBackRadarChart = new Chart(document.getElementById("towerBackRadarChart"), {
      type: "radar",
      data: {
        labels: [],
        datasets: [
          {
            label: "Success %",
            data: [],
            borderColor: "#4ad7a7",
            backgroundColor: "rgba(74, 215, 167, 0.18)",
            pointRadius: 2,
          },
          {
            label: "Median Explosives",
            data: [],
            borderColor: "#f7c96c",
            backgroundColor: "rgba(247, 201, 108, 0.16)",
            pointRadius: 2,
          },
          {
            label: "Attempt Count",
            data: [],
            borderColor: "#6ea8ff",
            backgroundColor: "rgba(110, 168, 255, 0.16)",
            pointRadius: 2,
          },
        ],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        plugins: { legend: { labels: { color: "#e8f5ff" } } },
        scales: {
          r: {
            beginAtZero: true,
            max: 100,
            angleLines: { color: "rgba(157, 194, 217, 0.2)" },
            grid: { color: "rgba(157, 194, 217, 0.2)" },
            pointLabels: { color: "#c8deed" },
            ticks: { color: "#c8deed", backdropColor: "rgba(0,0,0,0)" },
          },
        },
      },
    });
  }

  if (!towerFrontRadarChart) {
    towerFrontRadarChart = new Chart(document.getElementById("towerFrontRadarChart"), {
      type: "radar",
      data: {
        labels: [],
        datasets: [
          {
            label: "Success %",
            data: [],
            borderColor: "#4ad7a7",
            backgroundColor: "rgba(74, 215, 167, 0.18)",
            pointRadius: 2,
          },
          {
            label: "Median Explosives",
            data: [],
            borderColor: "#f7c96c",
            backgroundColor: "rgba(247, 201, 108, 0.16)",
            pointRadius: 2,
          },
          {
            label: "Attempt Count",
            data: [],
            borderColor: "#6ea8ff",
            backgroundColor: "rgba(110, 168, 255, 0.16)",
            pointRadius: 2,
          },
        ],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        plugins: { legend: { labels: { color: "#e8f5ff" } } },
        scales: {
          r: {
            beginAtZero: true,
            max: 100,
            angleLines: { color: "rgba(157, 194, 217, 0.2)" },
            grid: { color: "rgba(157, 194, 217, 0.2)" },
            pointLabels: { color: "#c8deed" },
            ticks: { color: "#c8deed", backdropColor: "rgba(0,0,0,0)" },
          },
        },
      },
    });
  }
}

function renderTowerTable(rows, typeBreakdownRows) {
  const tbody = document.getElementById("towerTable");
  if (!tbody) return;
  tbody.innerHTML = "";

  const byTowerSide = new Map();
  for (const row of typeBreakdownRows || []) {
    const key = `${row.tower_name}|${row.front_back}`;
    if (!byTowerSide.has(key)) {
      byTowerSide.set(key, []);
    }
    byTowerSide.get(key).push(row);
  }

  for (const row of rows) {
    const key = `${row.tower_name}|${row.front_back || "Unknown"}`;
    const typeRows = byTowerSide.get(key) || [];
    const expandable = typeRows.length > 1;
    const expanded = expandable && expandedTowerRows.has(key);
    const tr = document.createElement("tr");
    tr.className = "tower-parent-row";
    tr.innerHTML = `
      <td>
        <button class="tower-expand-btn" data-key="${key}" ${expandable ? "" : "disabled"}>
          ${expanded ? "-" : "+"}
        </button>
        <span>${row.tower_name}</span>
      </td>
      <td>${row.front_back || "Unknown"}</td>
      <td>${row.attempts}</td>
      <td>${formatPct(row.success_rate)}</td>
      <td>${formatSec(row.avg_success_time_seconds)}</td>
      <td>${formatNumMaybe(row.avg_rotations_success)}</td>
      <td>${formatNumMaybe(row.avg_total_explosives_success)}</td>
      <td>${Number(row.avg_damage_per_bed || 0).toFixed(2)}</td>
    `;
    tbody.appendChild(tr);

    if (expanded) {
      for (const detail of typeRows) {
        const child = document.createElement("tr");
        child.className = "tower-child-row";
        child.innerHTML = `
          <td class="tower-child-label">Type: ${detail.zero_type}</td>
          <td>${detail.front_back || "Unknown"}</td>
          <td>${detail.attempts}</td>
          <td>${formatPct(detail.success_rate)}</td>
          <td>${formatSec(detail.avg_success_time_seconds)}</td>
          <td>${formatNumMaybe(detail.avg_rotations_success)}</td>
          <td>${formatNumMaybe(detail.avg_total_explosives_success)}</td>
          <td>${Number(detail.avg_damage_per_bed || 0).toFixed(2)}</td>
        `;
        tbody.appendChild(child);
      }
    }
  }

  const buttons = tbody.querySelectorAll(".tower-expand-btn");
  for (const btn of buttons) {
    btn.addEventListener("click", () => {
      const key = btn.getAttribute("data-key");
      if (!key || btn.hasAttribute("disabled")) return;
      if (expandedTowerRows.has(key)) {
        expandedTowerRows.delete(key);
      } else {
        expandedTowerRows.add(key);
      }
      renderTowerTable(rows, typeBreakdownRows);
    });
  }
}

function renderAttemptTable(rows) {
  const tbody = document.getElementById("attemptTable");
  if (!tbody) return;
  tbody.innerHTML = "";
  for (const row of rows.slice(0, 60)) {
    let statusLabel = String(row.status || "");
    if (statusLabel === "fail" && String(row.fail_reason || "") === "broke_crystal") {
      statusLabel = "fail ( broke crystal )";
    }
    let rotExplMain = "-";
    if (row.rotations !== null && row.rotations !== undefined) {
      if (row.explosives_left === null || row.explosives_left === undefined) {
        rotExplMain = `${row.rotations}`;
      } else {
        rotExplMain = `${row.rotations}+${row.explosives_left}`;
      }
    }
    const bedsExploded = Number(row.beds_exploded || 0);
    const anchorsExploded = Number(row.anchors_exploded || 0);
    const hasExplodeBreakdown =
      String(row.attempt_source || "").toLowerCase() === "mpk" &&
      Number.isFinite(bedsExploded) &&
      Number.isFinite(anchorsExploded) &&
      bedsExploded + anchorsExploded > 0;
    const rotExpl = hasExplodeBreakdown
      ? `<div class="attempt-explosive-main">${rotExplMain}</div><div class="attempt-explosive-sub">(${bedsExploded}b${anchorsExploded}a)</div>`
      : rotExplMain;
    const bowShots = Number(row.bow_shots || 0);
    const crossbowShots = Number(row.crossbow_shots || 0);
    const totalBowShots =
      row.bow_shots_total === null || row.bow_shots_total === undefined
        ? bowShots + crossbowShots
        : Number(row.bow_shots_total || 0);
    const bowShotsText =
      totalBowShots > 0 ? `${totalBowShots} (${bowShots}+${crossbowShots})` : "0";
    const oLevelText =
      row.o_level === null || row.o_level === undefined || Number.isNaN(Number(row.o_level))
        ? "-"
        : `O${Number(row.o_level)}`;
    const isOneEightText =
      row.is_1_8 === null || row.is_1_8 === undefined ? "-" : row.is_1_8 ? "Yes" : "No";
    const standingYText =
      row.standing_height === null ||
      row.standing_height === undefined ||
      Number.isNaN(Number(row.standing_height))
        ? "-"
        : `Y${Number(row.standing_height)}`;
    const relativeStartedAt = formatRelativeDateTime(row.started_at_utc);
    const fullStartedAt = escapeHtmlAttr(formatDateTime(row.started_at_utc));
    const retryTargetKey = String(row.retry_target_key || "").trim();
    const canRetry =
      String(row.attempt_source || "").toLowerCase() === "mpk" && retryTargetKey.startsWith("mpk|");
    const retryCell = canRetry
      ? `<button type="button" class="attempt-retry-btn" data-target-key="${escapeHtmlAttr(retryTargetKey)}"${
          lockRequestInFlight ? " disabled" : ""
        }>Retry</button>`
      : "-";
    const tr = document.createElement("tr");
    const tooltipParts = [];
    if (crossbowShots > 0 || bowShots > 0) {
      tooltipParts.push(`Bow shots: ${bowShots}, Crossbow shots: ${crossbowShots}`);
    }
    if (String(row.status || "") === "flyaway") {
      const flyY =
        row.flyaway_dragon_y === null || row.flyaway_dragon_y === undefined
          ? "?"
          : String(row.flyaway_dragon_y);
      const flyNode = row.flyaway_node ? String(row.flyaway_node) : "?";
      const flyGt = Number(row.flyaway_gt || 0);
      const flyCrystals =
        row.flyaway_crystals_alive === null || row.flyaway_crystals_alive === undefined
          ? "?"
          : String(row.flyaway_crystals_alive);
      tooltipParts.push(`Flyaway: node=${flyNode}, y=${flyY}, gt=${flyGt}, crystals=${flyCrystals}`);
    }
    tr.title = tooltipParts.join(" | ");
    tr.innerHTML = `
      <td>${row.id}</td>
      <td title="${fullStartedAt}">${relativeStartedAt}</td>
      <td>${String(row.attempt_source || "practice").toUpperCase()}</td>
      <td class="status-${row.status}">${statusLabel}</td>
      <td>${row.tower_name || "Unknown"}</td>
      <td>${sideFromType(row.zero_type)}</td>
      <td>${isOneEightText}</td>
      <td>${oLevelText}</td>
      <td>${standingYText}</td>
      <td>${bowShotsText}</td>
      <td>${row.total_damage}</td>
      <td>${rotExpl}</td>
      <td>${formatSec(row.success_time_seconds)}</td>
      <td>${retryCell}</td>
    `;
    tbody.appendChild(tr);
    if (canRetry) {
      const retryBtn = tr.querySelector(".attempt-retry-btn");
      if (retryBtn) {
        retryBtn.addEventListener("click", async () => {
          if (lockRequestInFlight) return;
          const targetKey = String(retryBtn.getAttribute("data-target-key") || "").trim();
          if (!targetKey) return;
          lockRequestInFlight = true;
          retryBtn.disabled = true;
          retryBtn.textContent = "Locking...";
          updateMpkLockControls(lastPayload?.practice_next || {});
          try {
            lockedMpkTargets = await postSetSingleMpkLock(targetKey);
            await refresh("full");
          } catch (error) {
            console.error("Failed to set retry lock target:", error);
          } finally {
            lockRequestInFlight = false;
            updateMpkLockControls(lastPayload?.practice_next || {});
          }
        });
      }
    }
  }
}

function renderPayload(payload) {
  const isFirstRender = lastPayload === null;
  lastPayload = payload;
  ensureCharts();
  const leniencyInput = document.getElementById("leniencyTarget");
  if (leniencyInput) {
    const incoming = Number(payload.selected_leniency_target ?? selectedLeniencyTarget ?? 0);
    if (!Number.isNaN(incoming)) {
      selectedLeniencyTarget = incoming;
    }
    if (document.activeElement !== leniencyInput) {
      leniencyInput.value = String(selectedLeniencyTarget);
    }
  }
  const includeToggle = document.getElementById("includeOneEight");
  if (includeToggle) {
    includeToggle.checked = includeOneEight;
  }
  const seedModeSelect = document.getElementById("seedModeFilter");
  if (seedModeSelect) {
    const valid = ["all", "full_random", "set_seed"];
    const incoming = String(payload.selected_attempt_seed_mode || selectedSeedMode || "all").toLowerCase();
    if (valid.includes(incoming)) {
      selectedSeedMode = incoming;
    }
    if (!valid.includes(selectedSeedMode)) {
      selectedSeedMode = "all";
    }
    seedModeSelect.value = selectedSeedMode;
  }
  const rotationSelect = document.getElementById("rotationFilter");
  if (rotationSelect) {
    const valid = ["both", "cw", "ccw"];
    if (!valid.includes(selectedRotation)) {
      selectedRotation = "both";
    }
    rotationSelect.value = selectedRotation;
  }
  const windowSelect = document.getElementById("windowFilter");
  if (windowSelect) {
    const defaultWindow = "all";
    const windows = payload.window_options || [];
    const knownKeys = windows.map((w) => w.key);
    if (windows.length > 0 && windowSelect.options.length !== windows.length) {
      windowSelect.innerHTML = "";
      for (const row of windows) {
        const opt = document.createElement("option");
        opt.value = row.key;
        opt.textContent = row.label;
        windowSelect.appendChild(opt);
      }
    }
    if (!knownKeys.includes(selectedWindow)) {
      selectedWindow = defaultWindow;
    }
    windowSelect.value = selectedWindow;
  }
  updateFilters(payload);
  renderPracticeNext(payload);

  const scope = getScope(payload);
  if (!scope) return;

  const summary = scope.summary;
  const streaks = scope.streaks;
  const windows = scope.consistency_windows || [];
  const byWindow = Object.fromEntries(windows.map((w) => [w.window, w]));

  setText("kpiAttempts", summary.total_attempts);
  setText("kpiSuccessRate", formatPct(summary.success_rate));
  setText("kpiRecentSuccessRate", formatPct(summary.recent_success_rate));
  setText("kpiCurrentStreak", streaks.current_success_streak);
  setText("kpiBestStreak", streaks.best_success_streak);
  setText("kpiDamagePerBed", Number(summary.avg_damage_per_bed || 0).toFixed(2));
  setText("kpiSuccessTime", formatSec(summary.avg_success_time_seconds));
  setText("kpiBestTime", formatSec(scope.bests.best_success_time_seconds));
  setText("kpiMedianSuccessTime", formatSec(summary.median_success_time_seconds));
  setText("kpiAvgRotations", formatNumMaybe(summary.avg_rotations_success));
  setText("kpiAvgExplosives", formatNumMaybe(summary.avg_total_explosives_success));
  setText(
    "kpiPerfect22",
    `${summary.perfect_2_2_count} (${formatPct(summary.perfect_2_2_rate_among_successes)})`
  );
  setText("kpiPerfectRate", formatPct(summary.perfect_2_2_rate_among_successes));
  setText("consistency10", formatPct((byWindow[10] || {}).success_rate));
  setText("consistency25", formatPct((byWindow[25] || {}).success_rate));
  setText("consistency50", formatPct((byWindow[50] || {}).success_rate));

  const progression = scope.session_progression || [];
  progressionChart.data.labels = progression.map((row) => row.session_label);
  progressionChart.data.datasets[0].data = progression.map((row) => row.success_rate);
  progressionChart.update("none");

  const damagePerBed = scope.damage_per_bed || [];
  damageChart.data.labels = damagePerBed.map((row) => `Bed ${row.bed_number}`);
  damageChart.data.datasets[0].data = damagePerBed.map((row) => row.avg_damage);
  damageChart.update("none");

  efficiencyChart.data.labels = progression.map((row) => row.session_label);
  efficiencyChart.data.datasets[0].data = progression.map((row) => row.avg_rotations_success);
  efficiencyChart.data.datasets[1].data = progression.map(
    (row) => row.avg_total_explosives_success
  );
  efficiencyChart.update("none");

  const timeSeries = scope.time_series || [];
  timeSeriesChart.data.labels = timeSeries.map((row) => `#${row.id}`);
  timeSeriesChart.data.datasets[0].data = timeSeries.map((row) =>
    row.status === "success" ? row.success_time_seconds : null
  );
  timeSeriesChart.update("none");

  const rolling = scope.rolling_consistency_10 || [];
  const rolling25 = scope.rolling_consistency_25 || [];
  const rolling50 = scope.rolling_consistency_50 || [];
  const rolling25ById = new Map(rolling25.map((row) => [row.id, row.rolling_success_rate]));
  const rolling50ById = new Map(rolling50.map((row) => [row.id, row.rolling_success_rate]));
  rollingConsistencyChart.data.labels = rolling.map((row) => `#${row.id}`);
  rollingConsistencyChart.data.datasets[0].data = rolling.map((row) => row.rolling_success_rate);
  rollingConsistencyChart.data.datasets[1].data = rolling.map((row) => rolling25ById.get(row.id) ?? null);
  rollingConsistencyChart.data.datasets[2].data = rolling.map((row) => rolling50ById.get(row.id) ?? null);
  applyRollingMode();
  rollingConsistencyChart.update("none");

  const speedBins = scope.speed_bins || [];
  speedBinsChart.data.labels = speedBins.map((row) => row.label);
  speedBinsChart.data.datasets[0].data = speedBins.map((row) => row.count);
  speedBinsChart.update("none");

  const attemptsBySession = scope.attempts_by_session || [];
  attemptsByHourChart.data.labels = attemptsBySession.map((row) => row.session_label);
  attemptsByHourChart.data.datasets[0].data = attemptsBySession.map((row) => row.attempts);
  attemptsByHourChart.data.datasets[1].data = attemptsBySession.map((row) => row.success_rate);
  attemptsByHourChart.update("none");

  const runs = (scope.outcome_runs || {}).runs || [];
  outcomeRunsChart.data.labels = runs.map((_, idx) => `#${idx + 1}`);
  outcomeRunsChart.data.datasets[0].data = runs.map((row) => row.length);
  outcomeRunsChart.data.datasets[0].backgroundColor = runs.map((row) =>
    row.status === "success" ? "rgba(74, 215, 167, 0.7)" : "rgba(255, 117, 100, 0.7)"
  );
  outcomeRunsChart.update("none");

  const oLevelMatrix = scope.o_level_heatmap || { o_levels: [], rows: [] };
  renderOLevelHeatmap(oLevelMatrix);

  const standingHeights = scope.standing_height_consistency || [];
  standingHeightConsistencyChart.data.labels = standingHeights.map((row) => `Y ${row.standing_height}`);
  standingHeightConsistencyChart.data.datasets[0].data = standingHeights.map((row) => row.attempts);
  standingHeightConsistencyChart.data.datasets[1].data = standingHeights.map((row) => row.success_rate);
  standingHeightConsistencyChart.update("none");

  const radar = payload.tower_radar || {};
  const backRows = radar.back || [];
  const frontRows = radar.front || [];
  const backAttemptCap = Math.max(Number(radar.back_attempt_cap || 1), 1);
  const frontAttemptCap = Math.max(Number(radar.front_attempt_cap || 1), 1);
  const backSuccessMax = Math.max(...backRows.map((row) => Number(row.success_rate || 0)), 0);
  const backExplMax = Math.max(...backRows.map((row) => Number(row.median_explosives || 0)), 0);
  const backAttemptMax = Math.max(...backRows.map((row) => Number(row.attempt_count_capped || 0)), 0);
  const frontSuccessMax = Math.max(...frontRows.map((row) => Number(row.success_rate || 0)), 0);
  const frontExplMax = Math.max(...frontRows.map((row) => Number(row.median_explosives || 0)), 0);
  const frontAttemptMax = Math.max(...frontRows.map((row) => Number(row.attempt_count_capped || 0)), 0);

  towerBackRadarChart.data.labels = backRows.map((row) => row.tower_name);
  towerBackRadarChart.data.datasets[0].label = `Success (max ${backSuccessMax.toFixed(1)})`;
  towerBackRadarChart.data.datasets[1].label = `Expl (max ${backExplMax.toFixed(1)})`;
  towerBackRadarChart.data.datasets[2].label = `Attempts (max ${Math.round(backAttemptMax)})`;
  towerBackRadarChart.data.datasets[0].data = backRows.map((row) =>
    Math.min(100, Number(row.success_rate || 0))
  );
  towerBackRadarChart.data.datasets[1].data = backRows.map((row) =>
    (Math.min(8, Number(row.median_explosives || 0)) / 8) * 100
  );
  towerBackRadarChart.data.datasets[2].data = backRows.map(
    (row) => (Math.min(backAttemptCap, Number(row.attempt_count_capped || 0)) / backAttemptCap) * 100
  );
  towerBackRadarChart.update("none");

  towerFrontRadarChart.data.labels = frontRows.map((row) => row.tower_name);
  towerFrontRadarChart.data.datasets[0].label = `Success (max ${frontSuccessMax.toFixed(1)})`;
  towerFrontRadarChart.data.datasets[1].label = `Expl (max ${frontExplMax.toFixed(1)})`;
  towerFrontRadarChart.data.datasets[2].label = `Attempts (max ${Math.round(frontAttemptMax)})`;
  towerFrontRadarChart.data.datasets[0].data = frontRows.map((row) =>
    Math.min(100, Number(row.success_rate || 0))
  );
  towerFrontRadarChart.data.datasets[1].data = frontRows.map((row) =>
    (Math.min(8, Number(row.median_explosives || 0)) / 8) * 100
  );
  towerFrontRadarChart.data.datasets[2].data = frontRows.map(
    (row) => (Math.min(frontAttemptCap, Number(row.attempt_count_capped || 0)) / frontAttemptCap) * 100
  );
  towerFrontRadarChart.update("none");

  const towerTitle = document.getElementById("towerTableTitle");
  const attemptTitle = document.getElementById("attemptTableTitle");
  const scopeLabel =
    selectedTower === "__GLOBAL__" && selectedSide === "__GLOBAL__"
      ? "All Towers, Both Sides"
      : `${selectedTower === "__GLOBAL__" ? "All Towers" : selectedTower} | ${
          selectedSide === "__GLOBAL__" ? "Both Sides" : selectedSide
        }`;
  if (towerTitle) {
    towerTitle.textContent = `Tower Performance (${scopeLabel})`;
  }
  if (attemptTitle) {
    attemptTitle.textContent = `Recent Attempts (${scopeLabel})`;
  }

  renderTowerTable(scope.tower_performance || [], scope.tower_type_breakdown || []);
  renderAttemptTable(scope.recent_attempts || []);
}

function buildDashboardUrl(detail = "full") {
  const params = new URLSearchParams();
  params.set("detail", detail);
  params.set("include_1_8", includeOneEight ? "true" : "false");
  params.set("rotation", selectedRotation);
  params.set("window", selectedWindow);
  params.set("seed_mode", selectedSeedMode);
  if (selectedLeniencyTarget !== null && !Number.isNaN(Number(selectedLeniencyTarget))) {
    params.set("leniency_target", String(selectedLeniencyTarget));
  }
  if (selectedTower !== "__GLOBAL__") {
    params.set("tower", selectedTower);
  }
  if (selectedSide !== "__GLOBAL__") {
    params.set("side", selectedSide);
  }
  return `/api/dashboard?${params.toString()}`;
}

async function persistLeniencyTarget(value) {
  const numeric = Number(value);
  const normalized = Number.isNaN(numeric) ? 0 : numeric;
  const params = new URLSearchParams();
  params.set("value", String(normalized));
  const res = await fetch(`/api/mpk/leniency-target?${params.toString()}`, { method: "POST" });
  if (!res.ok) {
    throw new Error(`Failed to save leniency target (${res.status})`);
  }
  return res.json();
}

async function refresh(detail = "full") {
  const requestSeq = ++refreshRequestSeq;
  try {
    const [healthRes, dashboardRes] = await Promise.all([fetch("/api/health"), fetch(buildDashboardUrl(detail))]);
    if (requestSeq !== refreshRequestSeq) {
      return;
    }
    const health = await healthRes.json();
    lastHealth = health;
    renderMpkSetupCard(health);
    const payload = await dashboardRes.json();
    if (requestSeq !== refreshRequestSeq) {
      return;
    }

    renderPayload(payload);

    const healthDot = document.getElementById("healthDot");
    const healthText = document.getElementById("healthText");
    const mpkAlive = !!(health.ok && health.mpk_enabled && health.mpk_log_exists);
    const alive = mpkAlive;
    healthDot.className = `dot ${alive ? "dot-on" : "dot-off"}`;
    healthText.textContent = alive ? "Reader active" : "Log path not found";
    const watchLabel = health.mpk_log_path;
    setText(
      "lastUpdated",
      `Updated ${new Date(payload.server_time_utc).toLocaleTimeString()} | Watching: ${watchLabel}`
    );
  } catch (error) {
    if (requestSeq !== refreshRequestSeq) {
      return;
    }
    setText("lastUpdated", `Dashboard fetch error: ${error}`);
    const healthDot = document.getElementById("healthDot");
    const healthText = document.getElementById("healthText");
    if (healthDot) healthDot.className = "dot dot-off";
    if (healthText) healthText.textContent = "API unreachable";
  }
}

async function refreshHealth() {
  try {
    const healthRes = await fetch("/api/health");
    const health = await healthRes.json();
    lastHealth = health;
    renderMpkSetupCard(health);
    const healthDot = document.getElementById("healthDot");
    const healthText = document.getElementById("healthText");
    const mpkAlive = !!(health.ok && health.mpk_enabled && health.mpk_log_exists);
    const alive = mpkAlive;
    healthDot.className = `dot ${alive ? "dot-on" : "dot-off"}`;
    healthText.textContent = alive ? "Reader active" : "Log path not found";
  } catch {
    const healthDot = document.getElementById("healthDot");
    const healthText = document.getElementById("healthText");
    if (healthDot) healthDot.className = "dot dot-off";
    if (healthText) healthText.textContent = "API unreachable";
  }
}

refresh("light");
refresh("full");
setInterval(() => refresh("full"), 2000);
refreshHealth();
setInterval(refreshHealth, 5000);
ensureFilterDefaults();

const towerFilter = document.getElementById("towerFilter");
if (towerFilter) {
  towerFilter.addEventListener("change", (event) => {
    selectedTower = event.target.value;
    refresh("full");
  });
}

const sideFilter = document.getElementById("sideFilter");
if (sideFilter) {
  sideFilter.addEventListener("change", (event) => {
    selectedSide = event.target.value;
    refresh("full");
  });
}

const windowFilter = document.getElementById("windowFilter");
if (windowFilter) {
  windowFilter.addEventListener("change", (event) => {
    selectedWindow = event.target.value;
    selectedTower = "__GLOBAL__";
    selectedSide = "__GLOBAL__";
    refresh("full");
  });
}

const seedModeFilter = document.getElementById("seedModeFilter");
if (seedModeFilter) {
  seedModeFilter.addEventListener("change", (event) => {
    selectedSeedMode = String(event.target.value || "all");
    selectedTower = "__GLOBAL__";
    selectedSide = "__GLOBAL__";
    refresh("full");
  });
}

const includeOneEightToggle = document.getElementById("includeOneEight");
if (includeOneEightToggle) {
  includeOneEightToggle.addEventListener("change", (event) => {
    includeOneEight = !!event.target.checked;
    selectedTower = "__GLOBAL__";
    selectedSide = "__GLOBAL__";
    refresh("full");
  });
}

const rotationFilter = document.getElementById("rotationFilter");
if (rotationFilter) {
  rotationFilter.addEventListener("change", (event) => {
    selectedRotation = event.target.value;
    selectedTower = "__GLOBAL__";
    selectedSide = "__GLOBAL__";
    refresh("full");
  });
}

const mpkSetupForm = document.getElementById("mpkSetupForm");
if (mpkSetupForm) {
  mpkSetupForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    const input = document.getElementById("mpkInstancePathInput");
    const status = document.getElementById("mpkSetupStatus");
    const saveBtn = document.getElementById("mpkSetupSaveBtn");
    const legalToggle = document.getElementById("mpkLegalRankedToggle");
    const recipeToggle = document.getElementById("mpkInjectRecipeBookToggle");
    const dragonPatchToggle = document.getElementById("mpkInjectDragonPatchToggle");
    if (!input || !status) return;
    const path = String(input.value || "").trim();
    if (!path) {
      status.textContent = "Error: Please enter an instance path.";
      return;
    }
    if (saveBtn) saveBtn.disabled = true;
    status.textContent = "Applying MPK injector...";
    try {
      const res = await fetch("/api/setup/mpk-instance", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          path,
          legal_ranked_instance: !!(legalToggle ? legalToggle.checked : false),
          open_recipe_book_on_run_start: !!(recipeToggle ? recipeToggle.checked : true),
          enable_dragon_node_patch: !!(dragonPatchToggle ? dragonPatchToggle.checked : true),
        }),
      });
      const json = await res.json();
      if (!res.ok || !json.ok) {
        const err = json && json.error ? String(json.error) : `Setup failed (${res.status})`;
        status.textContent = `Error: ${err}`;
      } else {
        status.textContent = "MPK setup complete.";
      }
      await refresh("full");
    } catch (error) {
      status.textContent = `Error: ${error}`;
    } finally {
      if (saveBtn) saveBtn.disabled = false;
    }
  });
}

const mpkLegalRankedToggle = document.getElementById("mpkLegalRankedToggle");
if (mpkLegalRankedToggle) {
  mpkLegalRankedToggle.addEventListener("change", () => {
    syncMpkLegalToggleState();
  });
}

const mpkClearBtn = document.getElementById("mpkClearBtn");
if (mpkClearBtn) {
  mpkClearBtn.addEventListener("click", async () => {
    const status = document.getElementById("mpkSetupStatus");
    const saveBtn = document.getElementById("mpkSetupSaveBtn");
    if (status) status.textContent = "Uninjecting and clearing configured instance path...";
    mpkClearBtn.disabled = true;
    if (saveBtn) saveBtn.disabled = true;
    try {
      const res = await fetch("/api/setup/mpk-clear", { method: "POST" });
      const json = await res.json();
      if (!res.ok || !json.ok) {
        const err = json && json.error ? String(json.error) : `Clear failed (${res.status})`;
        if (status) status.textContent = `Error: ${err}`;
      } else if (status) {
        status.textContent = "MPK un-injected and path cleared.";
      }
      await refresh("full");
    } catch (error) {
      if (status) status.textContent = `Error: ${error}`;
    } finally {
      if (saveBtn) saveBtn.disabled = false;
      await refreshHealth();
    }
  });
}

const leniencyTargetInput = document.getElementById("leniencyTarget");
if (leniencyTargetInput) {
  const applyLeniencyAndRefresh = (value, immediate = false) => {
    const parsed = Number(value);
    selectedLeniencyTarget = Number.isNaN(parsed) ? 0 : parsed;
    if (leniencyRefreshTimer) {
      clearTimeout(leniencyRefreshTimer);
      leniencyRefreshTimer = null;
    }
    const run = async () => {
      try {
        await persistLeniencyTarget(selectedLeniencyTarget);
      } catch (error) {
        console.error("Failed to persist leniency target:", error);
      }
      refresh("full");
    };
    if (immediate) {
      run();
      return;
    }
    leniencyRefreshTimer = setTimeout(() => {
      leniencyRefreshTimer = null;
      run();
    }, 220);
  };
  leniencyTargetInput.addEventListener("input", (event) => {
    applyLeniencyAndRefresh(event.target.value, false);
  });
  leniencyTargetInput.addEventListener("change", (event) => {
    applyLeniencyAndRefresh(event.target.value, true);
  });
  leniencyTargetInput.addEventListener("blur", (event) => {
    applyLeniencyAndRefresh(event.target.value, true);
  });
}

const rollingControls = document.getElementById("rollingControls");
if (rollingControls) {
  const buttons = rollingControls.querySelectorAll("button[data-rolling-mode]");
  const refreshButtons = () => {
    for (const btn of buttons) {
      const mode = btn.getAttribute("data-rolling-mode");
      btn.classList.toggle("active", mode === rollingMode);
    }
  };
  refreshButtons();
  for (const btn of buttons) {
    btn.addEventListener("click", () => {
      const mode = btn.getAttribute("data-rolling-mode");
      if (!mode) return;
      rollingMode = mode;
      refreshButtons();
      if (lastPayload) {
        renderPayload(lastPayload);
      } else if (rollingConsistencyChart) {
        applyRollingMode();
        rollingConsistencyChart.update("none");
      }
    });
  }
}

const copyPracticeCommandBtn = document.getElementById("copyPracticeCommandBtn");
if (copyPracticeCommandBtn) {
  copyPracticeCommandBtn.addEventListener("click", () => {
    copyPracticeCommand();
  });
}

const testPracticeSoundBtn = document.getElementById("testPracticeSoundBtn");
if (testPracticeSoundBtn) {
  testPracticeSoundBtn.addEventListener("click", () => {
    unlockPracticeAudio();
    playPracticeSwitchSound();
  });
}

const clearMpkLocksBtn = document.getElementById("clearMpkLocksBtn");
if (clearMpkLocksBtn) {
  clearMpkLocksBtn.addEventListener("click", async () => {
    if (lockRequestInFlight || lockedMpkTargets.size === 0) return;
    lockRequestInFlight = true;
    updateMpkLockControls(lastPayload?.practice_next || {});
    try {
      lockedMpkTargets = await postClearMpkLocks();
      await refresh("full");
    } catch (error) {
      console.error("Failed to clear MPK lock list:", error);
    } finally {
      lockRequestInFlight = false;
      updateMpkLockControls(lastPayload?.practice_next || {});
    }
  });
}

const fullRandomOverrideBtn = document.getElementById("fullRandomOverrideBtn");
if (fullRandomOverrideBtn) {
  fullRandomOverrideBtn.addEventListener("click", async () => {
    if (fullRandomOverrideRequestInFlight) return;
    const widget = lastPayload?.practice_next || {};
    const currentlyEnabled = !!widget.full_random_override_enabled;
    fullRandomOverrideRequestInFlight = true;
    updateMpkLockControls(widget);
    try {
      await postMpkFullRandomOverride(!currentlyEnabled);
      await refresh("full");
    } catch (error) {
      console.error("Failed to update full random override:", error);
    } finally {
      fullRandomOverrideRequestInFlight = false;
      updateMpkLockControls(lastPayload?.practice_next || {});
    }
  });
}

const skipWeakLockBtn = document.getElementById("skipWeakLockBtn");
if (skipWeakLockBtn) {
  skipWeakLockBtn.addEventListener("click", async () => {
    if (weakLockSkipRequestInFlight) return;
    weakLockSkipRequestInFlight = true;
    updateMpkLockControls(lastPayload?.practice_next || {});
    try {
      await postSkipMpkWeakLock();
      await refresh("full");
    } catch (error) {
      console.error("Failed to skip weak lock:", error);
    } finally {
      weakLockSkipRequestInFlight = false;
      updateMpkLockControls(lastPayload?.practice_next || {});
    }
  });
}

window.addEventListener(
  "pointerdown",
  () => {
    unlockPracticeAudio();
  },
  { once: true }
);
window.addEventListener(
  "keydown",
  () => {
    unlockPracticeAudio();
  },
  { once: true }
);
