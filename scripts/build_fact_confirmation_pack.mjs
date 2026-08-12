#!/usr/bin/env node
/** Build and visually render a user-editable fact confirmation workbook. */

import fs from "node:fs/promises";
import path from "node:path";
import { pathToFileURL } from "node:url";

function parseArgs(argv) {
  const result = {};
  for (let i = 2; i < argv.length; i += 1) {
    const key = argv[i];
    if (!key.startsWith("--")) continue;
    result[key.slice(2)] = argv[i + 1];
    i += 1;
  }
  for (const required of ["input", "output", "preview-dir"]) {
    if (!result[required]) throw new Error(`missing --${required}`);
  }
  return result;
}

async function loadArtifactTool() {
  const modulesRoot = process.env.CODEX_NODE_MODULES;
  if (modulesRoot) {
    const entry = path.join(modulesRoot, "@oai", "artifact-tool", "dist", "artifact_tool.mjs");
    return import(pathToFileURL(entry).href);
  }
  return import("@oai/artifact-tool");
}

function matrix(rows) {
  return rows.map((row) => row.map((value) => (value === undefined || value === null ? "" : value)));
}

function applyTitle(sheet, range, title) {
  range.merge();
  range.values = [[title]];
  range.format = {
    fill: "#17365D",
    font: { bold: true, color: "#FFFFFF", size: 16 },
    horizontalAlignment: "left",
    verticalAlignment: "center",
  };
  range.format.rowHeight = 30;
}

function applyHeader(range) {
  range.format = {
    fill: "#2F75B5",
    font: { bold: true, color: "#FFFFFF" },
    wrapText: true,
    verticalAlignment: "center",
    borders: { preset: "outside", style: "thin", color: "#9EADBE" },
  };
  range.format.rowHeight = 30;
}

function applyBody(range) {
  range.format = {
    font: { color: "#1F1F1F", size: 10 },
    wrapText: true,
    verticalAlignment: "top",
    borders: {
      insideHorizontal: { style: "thin", color: "#D9E2F3" },
      bottom: { style: "thin", color: "#B4C6E7" },
    },
  };
}

const args = parseArgs(process.argv);
const input = JSON.parse(await fs.readFile(args.input, "utf8"));
const { SpreadsheetFile, Workbook } = await loadArtifactTool();
const workbook = Workbook.create();
const guide = workbook.worksheets.add("使用说明");
const factSheet = workbook.worksheets.add("事实核验");
const inferenceSheet = workbook.worksheets.add("推断核验");
const questionSheet = workbook.worksheets.add("问题清单");
const historySheet = workbook.worksheets.add("确认记录");

for (const sheet of [guide, factSheet, inferenceSheet, questionSheet, historySheet]) {
  sheet.showGridLines = false;
}

applyTitle(guide, guide.getRange("A1:F1"), "项目事实核验包（P1）");
guide.getRange("A3:B13").values = matrix([
  ["项目名称", input.project.official_name],
  ["项目编号", input.project.project_code],
  ["基线版本", input.project.baseline_version],
  ["事实总数", ""],
  ["A/B级事实数", ""],
  ["本轮待作决定数", ""],
  ["本轮已填写决定数", ""],
  ["既有确认记录数", ""],
  ["开放问题数", ""],
  ["填写规则", "仅在黄色列选择“确认/否决/修改/暂缓”；修改时必须填写建议口径。"],
  ["门禁说明", "A/B级待确认、待补充或冲突事实不能写成确定性正文。"],
]);
guide.getRange("A3:A13").format = { fill: "#D9EAF7", font: { bold: true }, wrapText: true };
guide.getRange("B3:B13").format = { wrapText: true };
guide.getRange("B6").formulas = [["=COUNTA('事实核验'!$A$2:$A$501)"]];
guide.getRange("B7").formulas = [["=COUNTIF('事实核验'!$J$2:$J$501,\"A\")+COUNTIF('事实核验'!$J$2:$J$501,\"B\")"]];
guide.getRange("B8").formulas = [["=B6-B9"]];
guide.getRange("B9").formulas = [["=COUNTIF('事实核验'!$O$2:$O$501,\"确认\")"]];
guide.getRange("B10").formulas = [["=COUNTA('确认记录'!$A$2:$A$201)"]];
guide.getRange("B11").formulas = [["=COUNTIF('问题清单'!$H$2:$H$201,\"open\")"]];
guide.getRange("A3:B13").format.borders = { preset: "outside", style: "thin", color: "#A6A6A6" };
guide.getRange("A3:A13").format.columnWidth = 20;
guide.getRange("B3:B13").format.columnWidth = 75;

const factHeaders = [
  "事实ID", "事实键", "类别", "事实内容", "标准值", "单位", "统计时点",
  "当前状态", "冲突组", "重要性", "来源文件", "证据位置", "证据原文",
  "是否必须确认", "用户决定", "建议修改口径", "建议标准值", "建议单位",
  "建议统计时点", "确认说明", "确认人", "确认日期"
];
factSheet.getRange(`A1:V1`).values = [factHeaders];
applyHeader(factSheet.getRange("A1:V1"));
const factRows = input.facts.map((fact) => [
  fact.fact_id, fact.fact_key, fact.fact_category, fact.fact_content,
  fact.normalized_value, fact.data_unit, fact.statistical_date,
  fact.fact_status_zh, fact.conflict_group_id, fact.materiality,
  fact.source_files, fact.source_locations, fact.evidence_texts,
  fact.confirmation_required ? "是" : "否", "", "", "", "", "", "", "", ""
]);
if (factRows.length) {
  factSheet.getRange(`A2:V${factRows.length + 1}`).values = matrix(factRows);
  applyBody(factSheet.getRange(`A2:V${factRows.length + 1}`));
  factSheet.getRange(`O2:V${factRows.length + 1}`).format.fill = "#FFF2CC";
}
factSheet.getRange("O2:O501").dataValidation = { rule: { type: "list", values: input.decision_options } };
factSheet.getRange("H2:H501").conditionalFormats.add("containsText", { text: "冲突", format: { fill: "#F8CBAD", font: { color: "#9C0006", bold: true } } });
factSheet.getRange("H2:H501").conditionalFormats.add("containsText", { text: "待确认", format: { fill: "#FFE699" } });
factSheet.getRange("J2:J501").conditionalFormats.add("containsText", { text: "A", format: { fill: "#F4CCCC", font: { bold: true } } });
factSheet.freezePanes.freezeRows(1);

const inferenceHeaders = ["推断ID", "推断内容", "类型", "前提ID", "推断路径", "重要性", "必须确认", "允许表述", "当前状态", "用户决定", "建议修改口径", "确认说明"];
inferenceSheet.getRange("A1:L1").values = [inferenceHeaders];
applyHeader(inferenceSheet.getRange("A1:L1"));
const inferenceRows = input.inferences.map((item) => [
  item.inference_id, item.proposition, item.inference_type, item.premise_ids_json,
  item.reasoning_note, item.materiality, item.confirmation_required ? "是" : "否",
  item.allowed_expression, item.status, "", "", ""
]);
if (inferenceRows.length) {
  inferenceSheet.getRange(`A2:L${inferenceRows.length + 1}`).values = matrix(inferenceRows);
  applyBody(inferenceSheet.getRange(`A2:L${inferenceRows.length + 1}`));
  inferenceSheet.getRange(`J2:L${inferenceRows.length + 1}`).format.fill = "#FFF2CC";
}
inferenceSheet.getRange("J2:J201").dataValidation = { rule: { type: "list", values: input.decision_options } };
inferenceSheet.freezePanes.freezeRows(1);

const questionHeaders = ["问题ID", "目标类型", "目标ID", "实质性问题", "重要性", "影响类型", "批次", "状态", "用户答复", "处理说明"];
questionSheet.getRange("A1:J1").values = [questionHeaders];
applyHeader(questionSheet.getRange("A1:J1"));
const questionRows = input.questions.map((item) => [
  item.question_id, item.target_type, item.target_id, item.question_text,
  item.materiality, item.impact_type, item.batch_no, item.status, "", ""
]);
if (questionRows.length) {
  questionSheet.getRange(`A2:J${questionRows.length + 1}`).values = matrix(questionRows);
  applyBody(questionSheet.getRange(`A2:J${questionRows.length + 1}`));
  questionSheet.getRange(`I2:J${questionRows.length + 1}`).format.fill = "#FFF2CC";
}
questionSheet.freezePanes.freezeRows(1);

const historyHeaders = ["确认记录ID", "目标类型", "目标ID", "决定", "修改前", "修改后", "说明", "确认人", "确认时间", "基线版本"];
historySheet.getRange("A1:J1").values = [historyHeaders];
applyHeader(historySheet.getRange("A1:J1"));
const historyRows = input.confirmations.map((item) => [
  item.confirmation_id, item.target_type, item.target_id, item.decision,
  item.before_value_json, item.after_value_json, item.decision_note,
  item.confirmed_by, item.confirmed_at, item.baseline_version
]);
if (historyRows.length) {
  historySheet.getRange(`A2:J${historyRows.length + 1}`).values = matrix(historyRows);
  applyBody(historySheet.getRange(`A2:J${historyRows.length + 1}`));
}
historySheet.freezePanes.freezeRows(1);

const widths = {
  "事实核验": [16, 18, 14, 46, 20, 10, 14, 16, 14, 10, 28, 28, 46, 12, 14, 30, 20, 12, 16, 30, 14, 16],
  "推断核验": [16, 46, 18, 24, 42, 10, 12, 36, 18, 14, 30, 30],
  "问题清单": [16, 14, 16, 52, 10, 14, 8, 12, 36, 30],
  "确认记录": [18, 14, 16, 12, 36, 36, 30, 14, 18, 14],
};
for (const sheet of [factSheet, inferenceSheet, questionSheet, historySheet]) {
  const sheetWidths = widths[sheet.name];
  sheetWidths.forEach((width, index) => {
    sheet.getRangeByIndexes(0, index, Math.max(2, (sheet.getUsedRange()?.rowCount || 2)), 1).format.columnWidth = width;
  });
}

await fs.mkdir(path.dirname(args.output), { recursive: true });
await fs.mkdir(args["preview-dir"], { recursive: true });
const inspection = await workbook.inspect({ kind: "sheet,formula", maxChars: 5000, options: { maxResults: 100 } });
await fs.writeFile(path.join(args["preview-dir"], "workbook-inspect.txt"), inspection.ndjson || String(inspection), "utf8");
const output = await SpreadsheetFile.exportXlsx(workbook);
await output.save(args.output);
console.log(JSON.stringify({ output: args.output, previewDir: args["preview-dir"], sheets: 5, facts: factRows.length, inferences: inferenceRows.length, questions: questionRows.length, previewStatus: "run render_data_pack_previews.mjs" }));
