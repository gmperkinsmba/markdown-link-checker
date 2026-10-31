"use strict";
const $ = id => document.getElementById(id);
let report = null;
function el(name, className, text) {
  const node = document.createElement(name);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}
function metric(value, label) {
  const box = el("div", "metric");
  box.append(el("b", "", String(value)), el("span", "", label));
  return box;
}
function renderFindings() {
  const list = $("findingList");
  list.replaceChildren();
  if (!report) return;
  const query = $("filter").value.trim().toLowerCase();
  const matches = report.findings.filter(item => `${item.rule} ${item.severity} ${item.source} ${item.evidence}`.toLowerCase().includes(query));
  if (!matches.length) {
    const empty = el("div", "empty-state");
    empty.append(el("p", "", query ? "No findings match this filter." : "No findings were reported."));
    list.append(empty);
    return;
  }
  for (const item of matches) {
    const row = el("article", "finding");
    row.append(el("span", `severity ${item.severity}`, item.severity));
    const main = el("div", "finding-main");
    main.append(el("strong", "", item.rule), el("p", "", item.evidence));
    const location = item.source ? `${item.source}${item.line ? `:${item.line}` : ""}` : "Root scope";
    if (item.source && item.source !== "." && item.line) {
      const open = el("button", "source-button", location);
      open.type = "button";
      open.setAttribute("aria-label", `Open source context for ${location}`);
      open.addEventListener("click", () => openSource(item));
      row.append(main, open);
    } else {
      row.append(main, el("span", "source", location));
    }
    list.append(row);
  }
}
async function scan() {
  const button = $("scan");
  const area = document.querySelector(".results");
  button.disabled = true;
  button.innerHTML = '<span class="button-icon" aria-hidden="true">↻</span> Scanning';
  area.setAttribute("aria-busy", "true");
  $("resultTitle").textContent = "Reading selected files";
  try {
    const response = await fetch("/api/scan", {headers: {Accept: "application/json"}, cache: "no-store"});
    if (!response.ok) throw new Error(`Scan failed with HTTP ${response.status}.`);
    report = await response.json();
    $("rootName").textContent = report.root_name;
    $("entrypoint").textContent = report.entrypoint;
    $("resultTitle").textContent = `${report.files_checked} Markdown files checked`;
    const summary = $("summary");
    summary.replaceChildren(
      metric(report.counts.error, "ERRORS"), metric(report.counts.warning, "WARNINGS"),
      metric(report.counts.unknown, "UNKNOWN RULES"), metric(report.links_checked, "LINKS CHECKED")
    );
    summary.hidden = false;
    $("limitNote").textContent = `Read-only scan · ${report.tracked_files === null ? "Git tracking state unknown" : `${report.tracked_files} tracked paths checked`} · ${report.privacy_hits} privacy path match(es) · unsupported conventions stay unknown.`;
    $("filter").disabled = false;
    $("export").disabled = false;
    renderFindings();
  } catch (error) {
    report = null;
    $("resultTitle").textContent = "Scan could not finish";
    $("findingList").replaceChildren(el("div", "empty-state", error.message));
    $("summary").hidden = true;
    $("filter").disabled = true;
    $("export").disabled = true;
  } finally {
    button.disabled = false;
    button.innerHTML = '<span class="button-icon" aria-hidden="true">↻</span> Scan folder';
    area.setAttribute("aria-busy", "false");
  }
}

function openSource(item) {
  $("sourceTitle").textContent = `${item.source}:${item.line}`;
  const context = $("sourceContext");
  context.replaceChildren();
  if (!item.context) {
    $("sourceNote").textContent = "This finding uses tracked path metadata only. File content was not opened for this finding.";
    context.hidden = true;
  } else {
    $("sourceNote").textContent = "Read-only context from this scan. The highlighted row contains the finding.";
    context.hidden = false;
    for (const itemLine of item.context) {
      const line = el("span", `context-line${itemLine.line === item.line ? " current" : ""}`);
      line.append(el("b", "", String(itemLine.line).padStart(4, "0")), document.createTextNode(itemLine.text));
      context.append(line);
    }
  }
  $("sourceDialog").showModal();
}
async function openSettings() {
  const list = $("settingsList");
  list.replaceChildren(el("div", "", "Loading settings"));
  $("settingsDialog").showModal();
  try {
    const response = await fetch("/api/settings", {headers: {Accept: "application/json"}, cache: "no-store"});
    if (!response.ok) throw new Error(`Settings could not load: HTTP ${response.status}.`);
    const config = await response.json();
    list.replaceChildren();
    const addRow = (label, value, code = false) => {
      const item = el("div"), term = el("dt", "", label), detail = el("dd");
      detail.append(code ? el("code", "", String(value)) : document.createTextNode(String(value)));
      item.append(term, detail); list.append(item);
    };
    addRow("Selected root", config.root_path, true);
    addRow("Entrypoint", config.entrypoint, true);
    addRow("Checks", config.checks.join(" · "));
    addRow("Privacy path rules", config.privacy_rules.join(" · "), true);
    addRow("Limits", `${config.limits.markdown_files} Markdown files · ${Math.round(config.limits.per_file_bytes / 1000000)} MB per file · ${Math.round(config.limits.total_bytes / 1000000)} MB total`);
    addRow("Source access", "Read-only. Symlink targets and paths outside the selected root are not opened.");
  } catch (error) {
    list.replaceChildren(el("div", "", error.message));
  }
}

function exportReview() {
  if (!report) return;
  const rows = [["rule", "severity", "source", "line", "evidence"], ...report.findings.map(f => [f.rule, f.severity, f.source, f.line, f.evidence])];
  const csv = KnowledgeHealthCSV.toCsv(rows);
  const url = URL.createObjectURL(new Blob([csv], {type: "text/csv;charset=utf-8"}));
  const link = document.createElement("a");
  link.href = url;
  link.download = "markdown-link-checker-review.csv";
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
$("scan").addEventListener("click", scan);
$("settingsOpen").addEventListener("click", openSettings);
$("settingsClose").addEventListener("click", () => $("settingsDialog").close());
$("sourceClose").addEventListener("click", () => $("sourceDialog").close());
$("filter").addEventListener("input", renderFindings);
$("export").addEventListener("click", exportReview);
