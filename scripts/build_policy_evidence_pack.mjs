#!/usr/bin/env node
/** Build and render a policy evidence and citation-matrix workbook from match_project_policies JSON. */

import fs from "node:fs/promises";
import path from "node:path";
import { pathToFileURL } from "node:url";

function parseArgs(argv) {
  const result = {};
  for (let i = 2; i < argv.length; i += 1) {
    if (!argv[i].startsWith("--")) continue;
    result[argv[i].slice(2)] = argv[i + 1];
    i += 1;
  }
  for (const key of ["input", "output", "preview-dir"]) if (!result[key]) throw new Error(`missing --${key}`);
  return result;
}

async function loadArtifactTool() {
  const root = process.env.CODEX_NODE_MODULES;
  if (root) return import(pathToFileURL(path.join(root, "@oai", "artifact-tool", "dist", "artifact_tool.mjs")).href);
  return import("@oai/artifact-tool");
}

function val(value) { return value === null || value === undefined ? "" : value; }
function styleHeader(range) {
  range.format = { fill: "#1F4E78", font: { bold: true, color: "#FFFFFF" }, wrapText: true, verticalAlignment: "center", borders: { preset: "outside", style: "thin", color: "#9EADBE" } };
  range.format.rowHeight = 30;
}
function styleBody(range) {
  range.format = { wrapText: true, verticalAlignment: "top", font: { size: 10 }, borders: { insideHorizontal: { style: "thin", color: "#D9E2F3" }, bottom: { style: "thin", color: "#B4C6E7" } } };
}

const args = parseArgs(process.argv);
const data = JSON.parse(await fs.readFile(args.input, "utf8"));
const { SpreadsheetFile, Workbook } = await loadArtifactTool();
const wb = Workbook.create();
const summary = wb.worksheets.add("使用说明");
const policies = wb.worksheets.add("政策候选");
const clauses = wb.worksheets.add("政策条款");
const matrix = wb.worksheets.add("引用矩阵");
for (const sheet of [summary, policies, clauses, matrix]) sheet.showGridLines = false;

summary.getRange("A1:F1").merge();
summary.getRange("A1:F1").values = [["政策证据与引用矩阵（P1）"]];
summary.getRange("A1:F1").format = { fill: "#17365D", font: { bold: true, color: "#FFFFFF", size: 16 } };
summary.getRange("A3:B13").values = [
  ["项目名称", data.project.official_name],
  ["项目编号", data.project.project_code],
  ["匹配运行", data.match_run_id],
  ["行政区划", data.project.jurisdiction_name],
  ["匹配主题", data.topics.join("、")],
  ["候选政策数", ""],
  ["匹配条款数", ""],
  ["进入依据数", ""],
  ["用户已确认数", ""],
  ["排序规则", "性质分组→行政层级→发布机关层级→发布日期升序→文号→标题"],
  ["使用规则", "仅现行有效、已核验官方原文可自动进入依据候选；AI推荐仍需用户确认。"],
];
summary.getRange("A3:A13").format = { fill: "#D9EAF7", font: { bold: true }, wrapText: true };
summary.getRange("B3:B13").format = { wrapText: true };
summary.getRange("B8").formulas = [["=COUNTA('政策候选'!$A$2:$A$201)"]];
summary.getRange("B9").formulas = [["=COUNTA('政策条款'!$A$2:$A$501)"]];
summary.getRange("B10").formulas = [["=COUNTIF('政策候选'!$O$2:$O$201,\"是\")"]];
summary.getRange("B11").formulas = [["=COUNTIF('政策候选'!$R$2:$R$201,\"用户确认\")"]];
summary.getRange("A3:A13").format.columnWidth = 22;
summary.getRange("B3:B13").format.columnWidth = 78;

const uniquePolicies = [];
const seen = new Set();
for (const row of data.matches) {
  if (seen.has(row.policy_id)) continue;
  seen.add(row.policy_id);
  uniquePolicies.push(row);
}
const ph = ["政策ID", "正式名称", "文号", "发布单位", "性质组", "地域层级", "行政区划", "发布日期", "有效状态", "核验状态", "官方原文", "正式域名", "相关性", "依据顺序", "进入依据", "进入背景", "AI状态", "用户决定", "用户顺序", "排除/调整理由"];
policies.getRange("A1:T1").values = [ph]; styleHeader(policies.getRange("A1:T1"));
const pr = uniquePolicies.map((row) => [row.policy_id, row.title, row.document_no, row.issuer, row.authority_group, row.jurisdiction_level, row.jurisdiction_name, row.publish_date, row.validity_status, row.verification_status, row.official_url, row.official_domain, row.relevance_level, data.basis_policy_order.indexOf(row.policy_id) + 1 || "", row.basis_use ? "是" : "否", row.background_use ? "是" : "否", row.decision_status, "", "", ""]);
if (pr.length) { policies.getRange(`A2:T${pr.length + 1}`).values = pr.map((r) => r.map(val)); styleBody(policies.getRange(`A2:T${pr.length + 1}`)); policies.getRange(`R2:T${pr.length + 1}`).format.fill = "#FFF2CC"; }
policies.getRange("R2:R201").dataValidation = { rule: { type: "list", values: ["用户确认", "用户排除", "调整顺序", "暂缓"] } };
policies.getRange("S2:S201").dataValidation = { rule: { type: "whole", operator: "between", formula1: 1, formula2: 200 } };
policies.getRange("I2:I201").conditionalFormats.add("containsText", { text: "expired", format: { fill: "#F4CCCC", font: { color: "#9C0006" } } });
policies.getRange("J2:J201").conditionalFormats.add("containsText", { text: "unverified", format: { fill: "#FFE699" } });
policies.freezePanes.freezeRows(1);

const ch = ["条款ID", "政策ID", "政策名称", "条款位置", "必要原文", "审慎概括", "主题标签", "要求类型", "适用说明", "项目关系", "进入背景", "当前状态"];
clauses.getRange("A1:L1").values = [ch]; styleHeader(clauses.getRange("A1:L1"));
const cr = data.matches.map((row) => [row.clause_id, row.policy_id, row.title, row.article_path, row.original_text, row.normalized_summary, row.topics.join("、"), row.requirement_type, row.applicability_notes, row.project_relation, row.background_use ? "是" : "否", row.decision_status]);
if (cr.length) { clauses.getRange(`A2:L${cr.length + 1}`).values = cr.map((r) => r.map(val)); styleBody(clauses.getRange(`A2:L${cr.length + 1}`)); }
clauses.freezePanes.freezeRows(1);

const mh = ["依据顺序", "政策ID", "政策名称", "条款ID", "条款位置", "编制依据", "政策背景", "条款证据", "项目关系", "建议使用章节", "确认状态", "用户说明"];
matrix.getRange("A1:L1").values = [mh]; styleHeader(matrix.getRange("A1:L1"));
const mr = data.matches.map((row) => [data.basis_policy_order.indexOf(row.policy_id) + 1 || "", row.policy_id, row.title, row.clause_id, row.article_path, row.basis_use ? "是" : "否", row.background_use ? "是" : "否", row.normalized_summary, row.project_relation, row.background_use ? "第一章政策背景" : "对应专业章节", row.decision_status, ""]);
if (mr.length) { matrix.getRange(`A2:L${mr.length + 1}`).values = mr.map((r) => r.map(val)); styleBody(matrix.getRange(`A2:L${mr.length + 1}`)); matrix.getRange(`L2:L${mr.length + 1}`).format.fill = "#FFF2CC"; }
matrix.freezePanes.freezeRows(1);

const widths = {
  "政策候选": [17, 40, 18, 26, 10, 14, 16, 13, 14, 14, 48, 22, 12, 10, 10, 10, 16, 14, 10, 30],
  "政策条款": [17, 17, 38, 24, 58, 50, 24, 14, 30, 38, 10, 16],
  "引用矩阵": [10, 17, 38, 17, 24, 10, 10, 50, 38, 24, 16, 30],
};
for (const sheet of [policies, clauses, matrix]) {
  widths[sheet.name].forEach((w, i) => sheet.getRangeByIndexes(0, i, Math.max(2, sheet.getUsedRange()?.rowCount || 2), 1).format.columnWidth = w);
}

await fs.mkdir(path.dirname(args.output), { recursive: true });
await fs.mkdir(args["preview-dir"], { recursive: true });
const inspect = await wb.inspect({ kind: "sheet,formula", maxChars: 5000, options: { maxResults: 100 } });
await fs.writeFile(path.join(args["preview-dir"], "policy-workbook-inspect.txt"), inspect.ndjson || String(inspect), "utf8");
const output = await SpreadsheetFile.exportXlsx(wb); await output.save(args.output);
console.log(JSON.stringify({ output: args.output, previewDir: args["preview-dir"], policies: pr.length, clauses: cr.length, sheets: 4, previewStatus: "run render_data_pack_previews.mjs" }));
