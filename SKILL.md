---
name: build-medical-it-feasibility-report
description: Create evidence-based Chinese government-investment medical informationization feasibility reports from mixed project materials such as DOCX, XLSX, PDF, Markdown, construction lists, vendor proposals, registration materials, policies, and external reference reports. Use when Codex must inventory sources, build a fact ledger, distinguish confirmed facts from unknowns and conflicts, normalize and map construction scope, evaluate reference reuse, create a traceability matrix and three-level outline, prepare chapter task packages, draft or continue chapters, validate facts/scope/investment/indicators/logic/language, or deliver a style-preserving Word report. Also use for any single stage of this workflow when prior artifacts already exist.
---

# 医疗信息化可研生成

## 目标

把可研编制作为一套可追溯的项目文档生产流程，不把任务简化为一次性长文生成。将每项关键事实、建设范围、投资分项、绩效指标和效益结论追溯到来源和当前确认状态。

面向政府投资或政府管理要求下的医疗信息化可行性研究报告。不得自动替代院方确认、投资决策、采购决策或专业评审。

## 开始前

1. 读取用户指定的全部材料和已有中间产物，不依赖当前对话记忆。
2. 识别当前处于哪个阶段，复用已经确认的产物，不重复已完成工作。
3. 在项目目录内建立或复用独立工作台；不得搬移、覆盖或修改原始材料。
4. 先锁定正式项目名称、文档类型、范围最高依据、核心验收目标、未知事项处理方式和目标交付格式。
5. 若上述信息缺失，先形成任务书和少量实质性问题；不得直接生成完整正文。

复制 `assets/project-workbench-template/` 作为新项目工作台起点。项目已有目录结构时，只复制需要的模板，不强制改名或搬迁。

## 按需读取规则

- 执行完整流程或判断阶段门禁时，读取 `references/workflow.md`。
- 建立文件、表格和编号时，读取 `references/artifact-schemas.md`。
- 提取、确认或引用事实时，读取 `references/source-and-fact-rules.md`。
- 生成事实核验包、处理隐性知识或写入用户确认时，同时读取 `references/fact-verification-rules.md`。
- 检索、入库、排序或引用政策时，读取 `references/policy-evidence-rules.md`。
- 识别地方编制标准、提取Word格式画像或处理缩进时，读取 `references/document-profile-rules.md`。
- 建库、迁移或解释数据对象时，读取 `references/knowledge-base-schema.md`。
- 处理客户清单、公司能力清单或投资对应关系时，读取 `references/scope-mapping-rules.md`。
- 使用外地可研、历史方案或厂商方案时，读取 `references/reference-reuse-rules.md`。
- 将高质量方案拆为语料块、映射产品能力或形成章节组合计划时，读取 `references/corpus-reuse-rules.md`。
- 形成目录、任务包或逐章编写时，读取 `references/chapter-task-rules.md` 和 `references/medical-it-feasibility-writing.md`。
- 复核正文或交付稿时，读取 `references/validation-rules.md`。
- 合并或排版 Word 时，读取 `references/word-delivery-rules.md`，并遵守当前环境的文档处理技能和渲染验证要求。

不要一次加载全部参考文件。只加载当前阶段必需的规则。

## 十阶段主流程

按顺序执行以下阶段。用户明确要求单阶段工作，或前序产物已经存在并通过门禁时，可以从对应阶段开始。

0. 任务定义：形成项目任务书，明确名称、文档类型、范围依据、验收目标、参考限制、未知事项处理和版本规则。
1. 资料与事实：登记来源，提取原子事实，登记单位、时点、位置、状态、冲突、缺失和确认问题。
1P. 政策证据：按项目地域、类型、范围和验收目标检索官方原文，核验有效性，提取条款，确定性排序并提交用户确认。
2. 清单与范围：标准化客户建设清单，区分建设方式和费用类型，映射公司能力但不改变客户边界。
3. 参考方案：按章节评估结构和写法复用，形成允许内容、禁止内容和残留扫描词表。
4. 贯通矩阵与目录：建立“问题—需求—建设—投资—指标—效益”链条，再形成三级目录。
5. 章节任务拆解：为每章分配目的、问题、事实ID、范围ID、素材、限制、表格、占位和完成条件。
6. 内容生成：先写证据骨架，再补充论证和衔接；逐章保存；不确定内容保留统一占位。
7. 多维校验：检查事实、范围、投资、指标、政策、逻辑、跨章一致性、语言、参考污染和占位。
8. 文档工程与交付：使用脱敏模板合并 Word，处理目录、编号、横向节、页码和元数据，并完成渲染复核。
9. 复盘沉淀：只沉淀通用规则、脚本和脱敏模板，不把客户事实或个人信息打包进 Skill。

每一阶段都必须有进入条件、标准产物、检查和门禁。详细规则见 `references/workflow.md`。

阶段0同时产生文档类型和格式画像候选；阶段1同时产生推断台账、事实核验包和确认记录；阶段5开始必须用章节组合计划约束事实、政策、范围、语料和篇幅。AI推荐不等于用户确认。

## 强制事实规则

始终使用以下状态，不创造同义状态：

- `【已确认】`
- `【材料明确】`
- `【待确认】`
- `【待补充】`
- `【冲突】`
- `【分析建议】`
- `【仅作参考】`
- `【不适用】`

遵守以下红线：

1. 不得编造医院数据、建设范围、投资、价格、资金来源、负责人、工期、政策文号、基线值、目标值或验收结论。
2. 所有确定性项目事实必须有来源ID和证据位置。
3. 来源优先级不得用于静默解决冲突；冲突必须单独登记并提交确认。
4. 供应商方案只能支持技术线索和功能说明，不能自动代表院方决策。
5. 公司能力清单不能自动扩大客户范围。
6. 外地参考稿不得继承地域、单位、业务对象、投资、指标、结论和专属政策。
7. 互联网只用于核验公开政策、标准和通用信息，不得用于补写医院专有事实。
8. 不得覆盖原始材料；所有清洗、合并和格式处理均输出新文件。

## 工作层与交付层

工作层可以保留来源ID、证据位置、HTML注释、内部提示和待补充状态。交付层必须过滤内部注释、参考项目提醒、个人联系方式、无关供应商信息和模板旧内容。

用户要求保留占位时，可以在阶段性工作稿中保留 `【待补充】`、`【待确认】` 和 `【分析建议】`；正式报批前必须生成占位汇总并逐项处置。

## 确定性脚本

优先使用以下脚本完成机械工作，避免每次重写临时代码：

```text
python scripts/init_project_workbench.py <target-dir> --project-code <project-code> [--official-name <name>] [--owner-name <name>]
python scripts/check_dependencies.py --output dependency-check.json
python scripts/run_project_pipeline.py <target-dir> --project-code <project-code> [--official-name <name>] [--owner-name <name>] [--word-template <confirmed-template.docx>] --output pipeline-result.json
python scripts/inventory_sources.py <paths...> --output 01-资料清单.json
python scripts/classify_source_roles.py source-inventory.json [--overrides source-role-overrides.json] --output source-role-register.json
python scripts/extract_docx_structure.py <report.docx> --output docx-structure.json
python scripts/extract_clean_document_blocks.py <source.docx|source.md|source.txt> --project-code <project-code> --output clean-document-blocks.json
python scripts/ingest_clean_documents_sqlite.py <knowledge.sqlite> <clean-document-blocks.json> --output sqlite-ingest-result.json
python scripts/build_candidate_fact_workpack.py <knowledge.sqlite> <project-code> --output candidate-fact-workpack.json
python scripts/build_reference_reuse_workpack.py <knowledge.sqlite> <project-code> --output-json reference-reuse-workpack.json --output-md 07-参考方案复用地图.md
MEDICAL_FEASIBILITY_DB_PASSWORD=<password> python scripts/ingest_clean_documents_postgres.py clean-document-blocks.json --host 127.0.0.1 --port <tunnel-port> --database <database> --user <user> --output postgres-ingest-result.json
python scripts/extract_xlsx_scope.py <scope.xlsx> --output xlsx-scope.json
python scripts/ingest_scope_items_sqlite.py <knowledge.sqlite> <xlsx-scope.json> --project-code <project-code> --output scope-ingest-result.json
python scripts/build_scope_baseline.py <knowledge.sqlite> <project-code> --output scope-baseline.json
python scripts/confirm_scope_baseline.py <knowledge.sqlite> <project-code> <baseline-id> --confirmed-by <name> --confirmed-at <time> --output scope-baseline-confirmation.json
python scripts/build_traceability_matrix.py <knowledge.sqlite> <project-code> --links traceability-links.json --output-json traceability-matrix.json --output-csv traceability-matrix.csv
python scripts/ingest_product_capabilities.py <knowledge.sqlite> <product-capabilities.json> --output capability-ingest-result.json
python scripts/map_scope_capabilities.py <knowledge.sqlite> <project-code> --output scope-capability-map.json
python scripts/build_section_composition_plan.py <knowledge.sqlite> <project-code> --output section-composition-plan.json
python scripts/export_section_task_packages.py <knowledge.sqlite> <project-code> <10-章节任务包> --output task-package-export.json
python scripts/save_section_draft.py <knowledge.sqlite> <project-code> <chapter-code> <draft.md> <chapter-package.json> --model <model> --prompt-version <version> --output draft-save-result.json
python scripts/manage_section_draft.py <knowledge.sqlite> <project-code> <chapter-code> <version-no> <adopt|discard|restore> --output draft-action-result.json
python scripts/assemble_report_markdown.py <knowledge.sqlite> <project-code> --mode <working|delivery> --output report.md --summary assembly-summary.json
python scripts/validate_full_report.py <knowledge.sqlite> <project-code> --mode <working|delivery> --residual-terms <参考残留词表.txt> --output validation-result.json
python scripts/build_report_docx.py <report.md> <report.docx> --project-name <name> --owner-name <owner> [--template <confirmed-template.docx>] --summary docx-build-summary.json
python scripts/record_word_render_review.py <report.docx> <rendered-pages-dir> --reviewed-by <name> --result pass --checked-all-pages --output word-render-review.json
python scripts/scan_reference_residue.py <draft paths...> --terms-file residual-terms.txt --output residual-scan.json
python scripts/init_knowledge_db.py <knowledge.sqlite> --output db-init.json
python scripts/ingest_project_facts.py <knowledge.sqlite> <project-facts.json>
python scripts/export_fact_confirmation_data.py <knowledge.sqlite> <project-code> --output fact-confirmation-data.json
node scripts/build_fact_confirmation_pack.mjs --input fact-confirmation-data.json --output fact-confirmation.xlsx --preview-dir previews
node scripts/render_data_pack_previews.mjs --kind fact --input fact-confirmation-data.json --preview-dir previews
node scripts/extract_confirmation_decisions.mjs --kind fact --input fact-confirmation.xlsx --output fact-decisions.json --default-confirmed-by <name> --default-confirmed-at <date>
python scripts/apply_project_confirmations.py <knowledge.sqlite> fact-decisions.json [--freeze] --output fact-apply-result.json
python scripts/ingest_policies.py <knowledge.sqlite> <policies.json>
python scripts/match_project_policies.py <knowledge.sqlite> <project-code> --topics <tags> --output-json policy-selection.json --output-md policy-matrix.md
node scripts/build_policy_evidence_pack.mjs --input policy-selection.json --output policy-evidence.xlsx --preview-dir previews
node scripts/render_data_pack_previews.mjs --kind policy --input policy-selection.json --preview-dir previews
node scripts/extract_confirmation_decisions.mjs --kind policy --input policy-evidence.xlsx --output policy-decisions.json --default-confirmed-by <name> --default-confirmed-at <date>
python scripts/apply_policy_confirmations.py <knowledge.sqlite> policy-decisions.json --output policy-apply-result.json
python scripts/export_policy_selection_data.py <knowledge.sqlite> <project-code> --output-json policy-selection-confirmed.json --output-md policy-matrix-confirmed.md
python scripts/ingest_document_standards.py <knowledge.sqlite> <document-standards.json>
python scripts/match_document_standards.py <knowledge.sqlite> <project-code> --output-json document-standard-selection.json --output-md document-standard-match.md
python scripts/extract_document_profile.py <reference.docx> --profile-output document-profile.json --style-output style-contract.json --database <knowledge.sqlite> --project-code <project-code> --standard-id <standard-id>
python scripts/lint_docx_format.py <draft.docx> --style-contract style-contract.json --output format-lint.json
python scripts/summarize_format_lint.py format-lint.json --output format-lint-summary.md
python scripts/validate_project_gates.py <knowledge.sqlite> <project-code> --output stage-gates.json
```

脚本输出只作为资料处理和校验依据。语义分类、事实可信度、范围归属和复用判断仍需结合项目材料判断。

当当前Windows运行时无法使用表格工具的原生渲染接口时，可用 `render_data_pack_previews.mjs` 生成逐工作表HTML/PNG预览；Excel创建与导出仍必须由表格工具完成，并在预览目录记录替代渲染原因。

核验包回写必须执行“提取决定JSON→检查项目编号、事实基线或政策匹配运行→追加确认记录→更新对象状态→从数据库重建输出→重跑门禁”。不得直接凭Excel视觉状态宣称数据库已确认；同一核验包重复导入必须幂等。

运行脚本前查看 `--help`。新增或修改脚本后必须实际运行测试。

## 阶段性输出

除非用户明确要求只回答问题，否则把阶段产物保存到项目目录，并在最终回复中给出可点击文件链接。至少维护：

- 项目任务书、资料登记表、事实台账和项目事实卡；
- 推断台账、事实核验包和追加式用户确认记录；
- 政策候选、官方条款证据、确定性排序和依据—背景引用矩阵；
- 文档标准识别、格式画像、样式契约和格式偏差报告；
- 缺失资料及确认问题；
- 标准化建设清单和映射差异表；
- 参考方案复用地图和残留词表；
- 项目贯通矩阵、确认版目录和章节任务包；
- 分章工作稿、校验问题清单和版本变更记录；
- Word候选交付稿及其渲染复核结果。

只有在文件确已生成并通过对应检查后，才能声称该阶段完成。

## 结束条件

完成用户要求的阶段后，说明：

1. 已形成哪些文件；
2. 哪些口径已经确认；
3. 哪些问题仍为待补充、待确认或冲突；
4. 哪些检查已经执行；
5. 下一阶段的进入条件是否满足。

不得以“正文已生成”代替事实、范围和交付质量的验收。
