#!/usr/bin/env node
/** Extract user decisions from P1 confirmation workbooks with lightweight OOXML parsing. */

import fs from "node:fs/promises";
import { createRequire } from "node:module";
import path from "node:path";

function parseArgs(argv) {
  const result = {};
  for (let i = 2; i < argv.length; i += 1) {
    if (!argv[i].startsWith("--")) continue;
    result[argv[i].slice(2)] = argv[i + 1];
    i += 1;
  }
  for (const key of ["kind", "input", "output"]) {
    if (!result[key]) throw new Error(`missing --${key}`);
  }
  if (!["fact", "policy"].includes(result.kind)) throw new Error("--kind must be fact or policy");
  return result;
}

function loadJsZip() {
  const root = process.env.CODEX_NODE_MODULES;
  if (!root) throw new Error("CODEX_NODE_MODULES is required");
  return createRequire(import.meta.url)(path.join(root, "jszip"));
}

function decodeXml(value) {
  return value
    .replace(/&#x([0-9a-f]+);/gi, (_, code) => String.fromCodePoint(Number.parseInt(code, 16)))
    .replace(/&#([0-9]+);/g, (_, code) => String.fromCodePoint(Number.parseInt(code, 10)))
    .replace(/&lt;/g, "<")
    .replace(/&gt;/g, ">")
    .replace(/&quot;/g, '"')
    .replace(/&apos;/g, "'")
    .replace(/&amp;/g, "&");
}

function attribute(fragment, name) {
  const match = fragment.match(new RegExp(`(?:^|\\s)${name}="([^"]*)"`));
  return match ? decodeXml(match[1]) : "";
}

function textNodes(xml) {
  return [...xml.matchAll(/<(?:[\w-]+:)?t(?:\s[^>]*)?>([\s\S]*?)<\/(?:[\w-]+:)?t>/g)]
    .map((match) => decodeXml(match[1]))
    .join("");
}

function columnIndex(reference) {
  const letters = reference.match(/^([A-Z]+)/i)?.[1] || "A";
  let result = 0;
  for (const letter of letters.toUpperCase()) result = result * 26 + letter.charCodeAt(0) - 64;
  return result - 1;
}

function parseSheet(xml, sharedStrings) {
  const matrix = [];
  for (const rowMatch of xml.matchAll(/<(?:[\w-]+:)?row\b[^>]*>([\s\S]*?)<\/(?:[\w-]+:)?row>/g)) {
    const row = [];
    for (const cellMatch of rowMatch[1].matchAll(/<(?:[\w-]+:)?c\b([^>]*?)(?:\/>|>([\s\S]*?)<\/(?:[\w-]+:)?c>)/g)) {
      const attributes = cellMatch[1];
      const body = cellMatch[2] || "";
      const reference = attribute(attributes, "r");
      const type = attribute(attributes, "t");
      const raw = body.match(/<(?:[\w-]+:)?v>([\s\S]*?)<\/(?:[\w-]+:)?v>/)?.[1] ?? "";
      let value = "";
      if (type === "s") value = sharedStrings[Number(raw)] ?? "";
      else if (type === "inlineStr") value = textNodes(body);
      else if (type === "b") value = raw === "1";
      else if (type === "str" || type === "e") value = decodeXml(raw);
      else value = raw === "" ? "" : Number.isFinite(Number(raw)) ? Number(raw) : decodeXml(raw);
      row[columnIndex(reference)] = value;
    }
    matrix.push(row);
  }
  return matrix;
}

async function readWorkbook(input) {
  const JSZip = loadJsZip();
  const archive = await JSZip.loadAsync(await fs.readFile(input));
  const workbookXml = await archive.file("xl/workbook.xml").async("string");
  const relationshipsXml = await archive.file("xl/_rels/workbook.xml.rels").async("string");
  const sharedFile = archive.file("xl/sharedStrings.xml");
  const sharedStrings = sharedFile
    ? [...(await sharedFile.async("string")).matchAll(/<(?:[\w-]+:)?si>([\s\S]*?)<\/(?:[\w-]+:)?si>/g)].map((match) => textNodes(match[1]))
    : [];
  const relationships = new Map(
    [...relationshipsXml.matchAll(/<Relationship\b([^>]*)\/?\s*>/g)].map((match) => [
      attribute(match[1], "Id"),
      attribute(match[1], "Target"),
    ]),
  );
  const sheets = new Map();
  for (const match of workbookXml.matchAll(/<(?:[\w-]+:)?sheet\b([^>]*)\/?\s*>/g)) {
    const name = attribute(match[1], "name");
    const target = relationships.get(attribute(match[1], "r:id"));
    if (!name || !target) continue;
    const normalizedTarget = target.startsWith("/")
      ? target.slice(1)
      : path.posix.normalize(path.posix.join("xl", target));
    const file = archive.file(normalizedTarget);
    if (!file) throw new Error(`worksheet part not found for ${name}: ${normalizedTarget}`);
    sheets.set(name, parseSheet(await file.async("string"), sharedStrings));
  }
  return sheets;
}

function text(value) {
  if (value === null || value === undefined) return "";
  return String(value).trim();
}

function normalizeDate(value) {
  if (value instanceof Date && !Number.isNaN(value.valueOf())) return value.toISOString();
  if (typeof value === "number" && Number.isFinite(value)) {
    const epoch = Date.UTC(1899, 11, 30);
    return new Date(epoch + value * 86400000).toISOString();
  }
  const raw = text(value);
  if (!raw) return "";
  const normalized = /^\d{4}-\d{2}-\d{2}$/.test(raw) ? `${raw}T00:00:00+08:00` : raw;
  if (Number.isNaN(Date.parse(normalized))) throw new Error(`invalid confirmation date: ${raw}`);
  return normalized;
}

function rows(workbook, sheetName) {
  const matrix = workbook.get(sheetName);
  if (!matrix) throw new Error(`worksheet not found: ${sheetName}`);
  return matrix;
}

function verifyHeaders(matrix, expected, sheetName) {
  const headers = matrix[0] || [];
  for (const [index, label] of Object.entries(expected)) {
    if (text(headers[Number(index)]) !== label) {
      throw new Error(`${sheetName} header mismatch at column ${Number(index) + 1}: expected ${label}`);
    }
  }
}

function summaryMap(workbook) {
  const matrix = rows(workbook, "使用说明");
  const result = {};
  for (const row of matrix.slice(1)) {
    const key = text(row[0]);
    if (key) result[key] = text(row[1]);
  }
  return result;
}

function metadata(rowUser, rowDate, args, rowLabel) {
  const confirmedBy = text(rowUser) || text(args["default-confirmed-by"]);
  const confirmedAt = normalizeDate(rowDate || args["default-confirmed-at"]);
  if (!confirmedBy) throw new Error(`${rowLabel}: confirmation user is required`);
  if (!confirmedAt) throw new Error(`${rowLabel}: confirmation date is required`);
  return { confirmed_by: confirmedBy, confirmed_at: confirmedAt };
}

function extractFact(workbook, args) {
  const summary = summaryMap(workbook);
  if (!summary["项目编号"] || !summary["基线版本"]) {
    throw new Error("fact workbook is missing project code or baseline version");
  }
  const factRows = rows(workbook, "事实核验");
  verifyHeaders(factRows, { 0: "事实ID", 14: "用户决定", 15: "建议修改口径", 16: "建议标准值", 17: "建议单位", 18: "建议统计时点", 19: "确认说明", 20: "确认人", 21: "确认日期" }, "事实核验");
  const inferenceRows = rows(workbook, "推断核验");
  verifyHeaders(inferenceRows, { 0: "推断ID", 9: "用户决定", 10: "建议修改口径", 11: "确认说明" }, "推断核验");
  const questionRows = rows(workbook, "问题清单");
  verifyHeaders(questionRows, { 0: "问题ID", 8: "用户答复", 9: "处理说明" }, "问题清单");

  const decisionMap = { "确认": "confirm", "否决": "reject", "修改": "modify", "暂缓": "defer" };
  const decisions = [];
  for (let index = 1; index < factRows.length; index += 1) {
    const row = factRows[index];
    const factId = text(row[0]);
    const rawDecision = text(row[14]);
    if (!factId || !rawDecision) continue;
    const decision = decisionMap[rawDecision];
    if (!decision) throw new Error(`事实核验 row ${index + 1}: unsupported decision ${rawDecision}`);
    const proposedValue = text(row[15]);
    if (decision === "modify" && !proposedValue) throw new Error(`事实核验 row ${index + 1}: modified value is required`);
    if (decision === "reject" && !text(row[19])) throw new Error(`事实核验 row ${index + 1}: rejection reason is required`);
    decisions.push({
      target_type: "fact",
      target_id: factId,
      decision,
      proposed_value: proposedValue,
      proposed_normalized_value: text(row[16]),
      proposed_data_unit: text(row[17]),
      proposed_statistical_date: text(row[18]),
      decision_note: text(row[19]),
      ...metadata(row[20], row[21], args, `事实核验 row ${index + 1}`),
    });
  }
  for (let index = 1; index < inferenceRows.length; index += 1) {
    const row = inferenceRows[index];
    const inferenceId = text(row[0]);
    const rawDecision = text(row[9]);
    if (!inferenceId || !rawDecision) continue;
    const decision = decisionMap[rawDecision];
    if (!decision) throw new Error(`推断核验 row ${index + 1}: unsupported decision ${rawDecision}`);
    const proposedValue = text(row[10]);
    if (decision === "modify" && !proposedValue) throw new Error(`推断核验 row ${index + 1}: modified value is required`);
    if (decision === "reject" && !text(row[11])) throw new Error(`推断核验 row ${index + 1}: rejection reason is required`);
    decisions.push({
      target_type: "inference",
      target_id: inferenceId,
      decision,
      proposed_value: proposedValue,
      decision_note: text(row[11]),
      ...metadata("", "", args, `推断核验 row ${index + 1}`),
    });
  }
  const questionAnswers = [];
  for (let index = 1; index < questionRows.length; index += 1) {
    const row = questionRows[index];
    const questionId = text(row[0]);
    const answer = text(row[8]);
    if (!questionId || !answer) continue;
    questionAnswers.push({
      question_id: questionId,
      answer,
      decision_note: text(row[9]),
      ...metadata("", "", args, `问题清单 row ${index + 1}`),
    });
  }
  return {
    pack_type: "fact_confirmation",
    project_code: summary["项目编号"],
    baseline_version: summary["基线版本"],
    source_workbook: path.basename(args.input),
    extracted_at: new Date().toISOString(),
    decisions,
    question_answers: questionAnswers,
  };
}

function extractPolicy(workbook, args) {
  const summary = summaryMap(workbook);
  if (!summary["项目编号"] || !summary["匹配运行"]) {
    throw new Error("policy workbook is missing project code or match run id; rebuild it with the current P1 script");
  }
  const policyRows = rows(workbook, "政策候选");
  verifyHeaders(policyRows, { 0: "政策ID", 17: "用户决定", 18: "用户顺序", 19: "排除/调整理由" }, "政策候选");
  const decisionMap = { "用户确认": "confirm", "用户排除": "exclude", "调整顺序": "reorder", "暂缓": "defer" };
  const decisions = [];
  for (let index = 1; index < policyRows.length; index += 1) {
    const row = policyRows[index];
    const policyId = text(row[0]);
    const rawDecision = text(row[17]);
    if (!policyId || !rawDecision) continue;
    const decision = decisionMap[rawDecision];
    if (!decision) throw new Error(`政策候选 row ${index + 1}: unsupported decision ${rawDecision}`);
    const orderText = text(row[18]);
    const userOrder = orderText ? Number(orderText) : null;
    if (decision === "reorder" && (!Number.isInteger(userOrder) || userOrder < 1)) {
      throw new Error(`政策候选 row ${index + 1}: a positive integer user order is required`);
    }
    if (decision === "exclude" && !text(row[19])) {
      throw new Error(`政策候选 row ${index + 1}: exclusion reason is required`);
    }
    decisions.push({
      policy_id: policyId,
      decision,
      user_order: userOrder,
      decision_note: text(row[19]),
      ...metadata("", "", args, `政策候选 row ${index + 1}`),
    });
  }
  return {
    pack_type: "policy_confirmation",
    project_code: summary["项目编号"],
    match_run_id: summary["匹配运行"],
    source_workbook: path.basename(args.input),
    extracted_at: new Date().toISOString(),
    decisions,
  };
}

const args = parseArgs(process.argv);
const workbook = await readWorkbook(args.input);
const result = args.kind === "fact" ? extractFact(workbook, args) : extractPolicy(workbook, args);
await fs.mkdir(path.dirname(args.output), { recursive: true });
await fs.writeFile(args.output, `${JSON.stringify(result, null, 2)}\n`, "utf8");
console.log(JSON.stringify({ kind: args.kind, decisions: result.decisions.length, questionAnswers: result.question_answers?.length || 0, output: args.output }));
