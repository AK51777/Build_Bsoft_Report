#!/usr/bin/env node
/** Render deterministic PNG previews for fact/policy workbooks when native artifact rendering is unavailable. */

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
  for (const key of ["kind", "input", "preview-dir"]) if (!result[key]) throw new Error(`missing --${key}`);
  if (!["fact", "policy"].includes(result.kind)) throw new Error("--kind must be fact or policy");
  return result;
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

function tableHtml(title, headers, rows, editableColumns = []) {
  const header = headers.map((item) => `<th>${escapeHtml(item)}</th>`).join("");
  const body = rows.length
    ? rows.map((row) => `<tr>${row.map((item, index) => `<td class="${editableColumns.includes(index) ? "editable" : ""}">${escapeHtml(item)}</td>`).join("")}</tr>`).join("")
    : `<tr><td colspan="${headers.length}" class="empty">无记录</td></tr>`;
  return `<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><style>
    *{box-sizing:border-box}body{margin:20px;font-family:"Microsoft YaHei","Noto Sans CJK SC",sans-serif;color:#1f1f1f;background:#fff}
    h1{font-size:24px;margin:0 0 16px;color:#17365d}table{border-collapse:collapse;table-layout:auto;min-width:100%;font-size:12px}
    th{background:#2f75b5;color:#fff;font-weight:700;text-align:left;vertical-align:middle;padding:8px;border:1px solid #9eadbe;white-space:nowrap}
    td{vertical-align:top;padding:7px;border:1px solid #d9e2f3;max-width:360px;min-width:90px;white-space:pre-wrap;word-break:break-word}
    tr:nth-child(even) td{background:#f7faff}td.editable{background:#fff2cc!important}.empty{text-align:center;color:#777}
  </style></head><body><h1>${escapeHtml(title)}</h1><table><thead><tr>${header}</tr></thead><tbody>${body}</tbody></table></body></html>`;
}

function factSheets(data) {
  const facts = data.facts.map((f) => [f.fact_id, f.fact_key, f.fact_category, f.fact_content, f.normalized_value, f.data_unit, f.statistical_date, f.fact_status_zh, f.conflict_group_id, f.materiality, f.source_files, f.source_locations, f.evidence_texts, f.confirmation_required ? "是" : "否", "", "", "", "", "", "", "", ""]);
  const inferences = data.inferences.map((i) => [i.inference_id, i.proposition, i.inference_type, i.premise_ids_json, i.reasoning_note, i.materiality, i.confirmation_required ? "是" : "否", i.allowed_expression, i.status, "", "", ""]);
  const questions = data.questions.map((q) => [q.question_id, q.target_type, q.target_id, q.question_text, q.materiality, q.impact_type, q.batch_no, q.status, "", ""]);
  const confirmations = data.confirmations.map((c) => [c.confirmation_id, c.target_type, c.target_id, c.decision, c.before_value_json, c.after_value_json, c.decision_note, c.confirmed_by, c.confirmed_at, c.baseline_version]);
  const abCount = data.facts.filter((f) => ["A", "B"].includes(f.materiality)).length;
  return [
    {name:"使用说明", headers:["项目", "内容"], rows:[["项目名称",data.project.official_name],["项目编号",data.project.project_code],["基线版本",data.project.baseline_version],["事实总数",data.facts.length],["A/B级事实数",abCount],["本轮待作决定数",data.facts.length],["本轮已填写决定数",0],["既有确认记录数",data.confirmations.length],["开放问题数",data.questions.filter((q)=>q.status==="open").length],["填写规则","仅在黄色列选择“确认/否决/修改/暂缓”；修改时必须填写建议口径。"],["门禁说明","A/B级待确认、待补充或冲突事实不能写成确定性正文。"]], editable:[]},
    {name:"事实核验", headers:["事实ID","事实键","类别","事实内容","标准值","单位","统计时点","当前状态","冲突组","重要性","来源文件","证据位置","证据原文","必须确认","用户决定","建议修改口径","建议标准值","建议单位","建议统计时点","确认说明","确认人","确认日期"], rows:facts, editable:[14,15,16,17,18,19,20,21]},
    {name:"推断核验", headers:["推断ID","推断内容","类型","前提ID","推断路径","重要性","必须确认","允许表述","当前状态","用户决定","建议修改口径","确认说明"], rows:inferences, editable:[9,10,11]},
    {name:"问题清单", headers:["问题ID","目标类型","目标ID","实质性问题","重要性","影响类型","批次","状态","用户答复","处理说明"], rows:questions, editable:[8,9]},
    {name:"确认记录", headers:["确认记录ID","目标类型","目标ID","决定","修改前","修改后","说明","确认人","确认时间","基线版本"], rows:confirmations, editable:[]},
  ];
}

function policySheets(data) {
  const unique = [];
  const seen = new Set();
  for (const row of data.matches) if (!seen.has(row.policy_id)) { seen.add(row.policy_id); unique.push(row); }
  const policies = unique.map((r) => [r.policy_id,r.title,r.document_no,r.issuer,r.authority_group,r.jurisdiction_level,r.jurisdiction_name,r.publish_date,r.validity_status,r.verification_status,r.official_url,r.official_domain,r.relevance_level,data.basis_policy_order.indexOf(r.policy_id)+1||"",r.basis_use?"是":"否",r.background_use?"是":"否",r.decision_status,"","",""]);
  const clauses = data.matches.map((r) => [r.clause_id,r.policy_id,r.title,r.article_path,r.original_text,r.normalized_summary,r.topics.join("、"),r.requirement_type,r.applicability_notes,r.project_relation,r.background_use?"是":"否",r.decision_status]);
  const matrix = data.matches.map((r) => [data.basis_policy_order.indexOf(r.policy_id)+1||"",r.policy_id,r.title,r.clause_id,r.article_path,r.basis_use?"是":"否",r.background_use?"是":"否",r.normalized_summary,r.project_relation,r.background_use?"第一章政策背景":"对应专业章节",r.decision_status,""]);
  return [
    {name:"使用说明",headers:["项目","内容"],rows:[["项目名称",data.project.official_name],["项目编号",data.project.project_code],["匹配运行",data.match_run_id],["行政区划",data.project.jurisdiction_name],["匹配主题",data.topics.join("、")],["候选政策数",unique.length],["匹配条款数",data.matches.length],["进入依据数",data.basis_policy_order.length],["用户已确认数",unique.filter((r)=>r.decision_status==="user_confirmed").length],["排序规则","性质分组→行政层级→发布机关层级→发布日期→文号→标题"],["使用规则","仅现行有效、已核验官方原文进入依据候选；AI推荐仍需用户确认。"]],editable:[]},
    {name:"政策候选",headers:["政策ID","正式名称","文号","发布单位","性质组","地域层级","行政区划","发布日期","有效状态","核验状态","官方原文","正式域名","相关性","依据顺序","进入依据","进入背景","AI状态","用户决定","用户顺序","排除/调整理由"],rows:policies,editable:[17,18,19]},
    {name:"政策条款",headers:["条款ID","政策ID","政策名称","条款位置","必要原文","审慎概括","主题标签","要求类型","适用说明","项目关系","进入背景","当前状态"],rows:clauses,editable:[]},
    {name:"引用矩阵",headers:["依据顺序","政策ID","政策名称","条款ID","条款位置","编制依据","政策背景","条款证据","项目关系","建议使用章节","确认状态","用户说明"],rows:matrix,editable:[11]},
  ];
}

const args = parseArgs(process.argv);
const data = JSON.parse(await fs.readFile(args.input, "utf8"));
const sheets = args.kind === "fact" ? factSheets(data) : policySheets(data);
const modulesRoot = process.env.CODEX_NODE_MODULES;
if (!modulesRoot) throw new Error("CODEX_NODE_MODULES is required");
const { chromium } = await import(pathToFileURL(path.join(modulesRoot, "playwright", "index.mjs")).href);
const executablePath = process.env.CODEX_BROWSER_EXECUTABLE || "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe";
await fs.mkdir(args["preview-dir"], { recursive: true });
const browser = await chromium.launch({ executablePath, headless: true });
try {
  for (const sheet of sheets) {
    const html = tableHtml(sheet.name, sheet.headers, sheet.rows, sheet.editable);
    const htmlPath = path.join(args["preview-dir"], `${sheet.name}.html`);
    const pngPath = path.join(args["preview-dir"], `${sheet.name}.png`);
    await fs.writeFile(htmlPath, html, "utf8");
    const page = await browser.newPage({ viewport: { width: Math.min(3200, Math.max(1400, sheet.headers.length * 170)), height: 900 }, deviceScaleFactor: 1 });
    await page.goto(pathToFileURL(path.resolve(htmlPath)).href);
    await page.screenshot({ path: pngPath, fullPage: true });
    await page.close();
  }
} finally {
  await browser.close();
}
await fs.writeFile(path.join(args["preview-dir"], "preview-method.json"), JSON.stringify({method:"playwright-html-fallback",reason:"当前Windows运行时的artifact-tool原生render在最小工作簿上退出，创建和导出仍由artifact-tool完成。",sheets:sheets.map((s)=>s.name)},null,2)+"\n","utf8");
console.log(JSON.stringify({kind:args.kind,previewDir:args["preview-dir"],sheets:sheets.length,method:"playwright-html-fallback"}));
