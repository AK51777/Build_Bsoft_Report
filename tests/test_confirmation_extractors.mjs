#!/usr/bin/env node
/** Forward-test decision extraction against the generated P1 workbooks. */

import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { pathToFileURL } from "node:url";

if (process.argv.length < 12) {
  throw new Error(
    "usage: node test_confirmation_extractors.mjs <extractor> <fact.xlsx> <policy.xlsx> " +
      "<apply-fact.py> <apply-policy.py> <export-fact.py> <export-policy.py> " +
      "<validate-gates.py> <database.sqlite> <project-code>",
  );
}
const [
  ,
  ,
  extractor,
  factInput,
  policyInput,
  applyFact,
  applyPolicy,
  exportFact,
  exportPolicy,
  validateGates,
  databaseInput,
  projectCode,
] = process.argv;
const modulesRoot = process.env.CODEX_NODE_MODULES;
if (!modulesRoot) throw new Error("CODEX_NODE_MODULES is required");
const python = process.env.P1_PYTHON;
if (!python) throw new Error("P1_PYTHON is required");
const { FileBlob, SpreadsheetFile } = await import(
  pathToFileURL(path.join(modulesRoot, "@oai", "artifact-tool", "dist", "artifact_tool.mjs")).href
);
const temporary = await fs.mkdtemp(path.join(os.tmpdir(), "p1-confirmation-test-"));

async function importWorkbook(input) {
  return SpreadsheetFile.importXlsx(await FileBlob.load(input));
}

async function saveWorkbook(workbook, output) {
  const blob = await SpreadsheetFile.exportXlsx(workbook);
  await blob.save(output);
}

try {
  const factWorkbook = await importWorkbook(factInput);
  const factSheet = factWorkbook.worksheets.getItem("事实核验");
  factSheet.getRange("O2").values = [["修改"]];
  factSheet.getRange("P2").values = [["电子病历应用水平分级评价五级"]];
  factSheet.getRange("Q2").values = [["5"]];
  factSheet.getRange("U2").values = [["回归测试用户"]];
  factSheet.getRange("V2").values = [["2026-08-04"]];
  const factCopy = path.join(temporary, "fact-filled.xlsx");
  const factJson = path.join(temporary, "fact-decisions.json");
  await saveWorkbook(factWorkbook, factCopy);
  execFileSync(process.execPath, [extractor, "--kind", "fact", "--input", factCopy, "--output", factJson], {
    env: process.env,
    stdio: "pipe",
  });
  const factResult = JSON.parse(await fs.readFile(factJson, "utf8"));
  assert.equal(factResult.pack_type, "fact_confirmation");
  assert.equal(factResult.decisions.length, 1);
  assert.equal(factResult.decisions[0].decision, "modify");
  assert.equal(factResult.decisions[0].proposed_value, "电子病历应用水平分级评价五级");
  assert.equal(factResult.decisions[0].proposed_normalized_value, "5");
  assert.equal(factResult.decisions[0].confirmed_by, "回归测试用户");

  const policyWorkbook = await importWorkbook(policyInput);
  const policySheet = policyWorkbook.worksheets.getItem("政策候选");
  policySheet.getRange("R2").values = [["用户确认"]];
  const policyCopy = path.join(temporary, "policy-filled.xlsx");
  const policyJson = path.join(temporary, "policy-decisions.json");
  await saveWorkbook(policyWorkbook, policyCopy);
  execFileSync(
    process.execPath,
    [
      extractor,
      "--kind",
      "policy",
      "--input",
      policyCopy,
      "--output",
      policyJson,
      "--default-confirmed-by",
      "回归测试用户",
      "--default-confirmed-at",
      "2026-08-04",
    ],
    { env: process.env, stdio: "pipe" },
  );
  const policyResult = JSON.parse(await fs.readFile(policyJson, "utf8"));
  assert.equal(policyResult.pack_type, "policy_confirmation");
  assert.equal(policyResult.decisions.length, 1);
  assert.equal(policyResult.decisions[0].decision, "confirm");
  assert.ok(policyResult.match_run_id.startsWith("PMRUN-"));

  // End-to-end: extracted workbook decisions -> copied SQLite -> re-export -> stage gates.
  const databaseCopy = path.join(temporary, "e2e.sqlite");
  const factApplyResult = path.join(temporary, "fact-apply-result.json");
  const policyApplyResult = path.join(temporary, "policy-apply-result.json");
  const factReexport = path.join(temporary, "fact-reexport.json");
  const policyReexport = path.join(temporary, "policy-reexport.json");
  const stageGates = path.join(temporary, "stage-gates.json");
  await fs.copyFile(databaseInput, databaseCopy);
  const pythonEnv = { ...process.env, PYTHONUTF8: "1" };
  execFileSync(python, [applyFact, databaseCopy, factJson, "--output", factApplyResult], {
    env: pythonEnv,
    stdio: "pipe",
  });
  execFileSync(python, [applyPolicy, databaseCopy, policyJson, "--output", policyApplyResult], {
    env: pythonEnv,
    stdio: "pipe",
  });
  execFileSync(python, [exportFact, databaseCopy, projectCode, "--output", factReexport], {
    env: pythonEnv,
    stdio: "pipe",
  });
  execFileSync(
    python,
    [exportPolicy, databaseCopy, projectCode, "--output-json", policyReexport],
    { env: pythonEnv, stdio: "pipe" },
  );
  execFileSync(python, [validateGates, databaseCopy, projectCode, "--output", stageGates], {
    env: pythonEnv,
    stdio: "pipe",
  });

  const factApplied = JSON.parse(await fs.readFile(factApplyResult, "utf8"));
  const policyApplied = JSON.parse(await fs.readFile(policyApplyResult, "utf8"));
  const factAfter = JSON.parse(await fs.readFile(factReexport, "utf8"));
  const policyAfter = JSON.parse(await fs.readFile(policyReexport, "utf8"));
  const gatesAfter = JSON.parse(await fs.readFile(stageGates, "utf8"));
  assert.equal(factApplied.decisions_applied, 1);
  assert.equal(policyApplied.decisions_applied, 1);
  assert.ok(factAfter.confirmations.some((item) => item.confirmed_by === "回归测试用户"));
  assert.ok(policyAfter.matches.some((item) => item.decision_status === "user_confirmed"));
  const gatesByCode = Object.fromEntries(gatesAfter.checks.map((item) => [item.gate_code, item.result]));
  assert.equal(gatesByCode["GATE-FACT-CONFLICT"], "pass");
  assert.equal(gatesByCode["GATE-POLICY-EVIDENCE"], "pass");
  console.log(
    JSON.stringify({
      factDecisions: 1,
      policyDecisions: 1,
      sqliteRoundtrip: true,
      reexport: true,
      stageGates: true,
      status: "ok",
    }),
  );
} finally {
  await fs.rm(temporary, { recursive: true, force: true });
}
