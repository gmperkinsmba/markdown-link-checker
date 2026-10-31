"use strict";
function safeCell(value) {
  let cell = String(value ?? "");
  if (/^[\s\uFEFF]*[=+\-@]/u.test(cell)) cell = `'${cell}`;
  return `"${cell.replaceAll('"', '""')}"`;
}
function toCsv(rows) {
  return rows.map(row => row.map(safeCell).join(",")).join("\r\n");
}
if (typeof module !== "undefined") module.exports = {safeCell, toCsv};
if (typeof window !== "undefined") window.KnowledgeHealthCSV = {safeCell, toCsv};
