# 快速开始

## 1. 检查环境

使用 Codex 内置文档运行环境或 Python 3.10 以上版本：

```powershell
python scripts/check_dependencies.py --knowledge-mode server_required --output dependency-check.json
```

`core.ready=true` 表示本地 SQLite 主链可运行。`word.candidate_generation_ready=true` 只表示可以生成 DOCX；只有实际渲染并逐页检查后，Word 才达到视觉交付状态。

## 2. 配置一次用户级共享知识

把 `assets/knowledge-base/medical-report-kb.example.json` 复制到：

```text
%USERPROFILE%\.codex\config\medical-report-kb.json
```

按实际只读数据库修改 host、port、database 和 user，不要把密码写入 JSON。启动 Codex/Trae 的同一进程环境应包含：

```powershell
$env:MEDICAL_FEASIBILITY_DB_PASSWORD = "<数据库密码>"
```

先诊断：

```powershell
python scripts/knowledge_doctor.py --profile default --json
```

如果数据库尚无专用只读账号，先运行 `provision_postgres_runtime_reader.py` 的默认 dry-run 并审查计划；只有数据库管理员明确批准后，才使用 `--apply --confirm-database <数据库名>`。完整命令、安全保护和手工 SQL 见 `references/knowledge-connection-rules.md` 的“运行时只读账户”。

## 3. 建立项目并自动同步

```powershell
python scripts/run_project_pipeline.py D:\projects\hospital-a `
  --project-code HOSPITAL-A-001 `
  --official-name "某医院信息化建设项目" `
  --owner-name "某医院" `
  --jurisdiction-code 100000 `
  --jurisdiction-name "某地区" `
  --knowledge-profile default `
  --word-template D:\templates\已确认可研格式模板.docx `
  --output D:\projects\hospital-a\运行记录\pipeline-result.json
```

指定 `--output` 时，完整 JSON 写入文件，控制台只显示项目状态、阻断数、知识包/目录 ID 和记录数等摘要；调试时增加 `--print-full-result` 才会在控制台打印完整结果。

把项目材料复制到 `D:\projects\hospital-a\原始资料`，然后重复运行同一命令。原始材料不会被修改。

建议把参考可研、厂商方案、政策线索和 Word 模板使用明显文件名或子目录隔开。系统会生成 `source-role-register.json`；如分类不准确，在 `project-config.json` 的 `source_roles.overrides` 中按相对路径或通配符显式指定角色后重跑。

流水线会自动诊断用户级 profile、从唯一适用的发布知识包和政策目录同步到项目 SQLite，再从本地快照生成章节。多个候选时会列出候选并阻断，不会默认取最新或全部。

没有服务器时，必须显式选择 `offline_pack`，再提供受审知识包：

```powershell
python scripts/run_project_pipeline.py D:\projects\hospital-a `
  --project-code HOSPITAL-A-001 `
  --official-name "某医院信息化建设项目" `
  --knowledge-mode offline_pack `
  --standard-knowledge-pack D:\private-kb\智慧医院标准知识包.json
```

同步只读取服务器 `runtime_*` 发布视图，并在项目库分别保存标准包、政策目录候选和正式政策条款的内容哈希快照；后续生成不依赖服务器持续在线。目录候选仍需官方核验，不会因同步自动成为正式依据。

没有服务器时，可把部门政策索引先转成受审目录并导入当前项目：

```powershell
python scripts/build_policy_catalog.py D:\private-kb\部门政策索引.xlsx `
  --output D:\private-kb\部门政策目录.json

python scripts/import_policy_catalog_sqlite.py `
  D:\projects\hospital-a\数据包\数据库\knowledge.sqlite `
  D:\private-kb\部门政策目录.json `
  --output D:\projects\hospital-a\运行记录\policy-catalog-import.json

python scripts/match_policy_catalog_candidates.py `
  D:\projects\hospital-a\数据包\数据库\knowledge.sqlite HOSPITAL-A-001 `
  --topic 医疗信息化 --limit-per-group 10 `
  --output-json D:\projects\hospital-a\运行记录\policy-catalog-candidates.json `
  --output-md D:\projects\hospital-a\运行记录\policy-catalog-candidates.md
```

重复的部门索引号会完整保留并标记冲突，不会覆盖或丢弃原始行。候选目录只用于找政策和搭结构，必须核验官方原文和条款后才能进入正式依据。

## 4. 查看本轮结果

优先查看：

1. `运行记录/pipeline-result.json`：总状态、阻断项和下一步；
2. `数据包/结构化数据/source-inventory.json`：资料清单与哈希；
3. `数据包/结构化数据/source-role-register.json`：哪些材料允许进入候选事实、哪些仅限参考；
4. `数据包/结构化数据/candidate-fact-workpack.json`：待提取和确认的事实输入；
5. `数据包/结构化数据/scope-baseline.json`：范围基线候选；
6. `数据包/结构化数据/traceability-matrix.csv`：贯通矩阵及缺失链；
7. `数据包/结构化数据/policy-selection.json`：政策候选；
8. `数据包/结构化数据/document-standard-selection.json`：文档标准候选；
9. `10-章节任务包`：逐章生成输入；
10. `运行记录/stage-gates.json`：阶段门禁；
11. `11-正文工作稿/report-working.md`：当前工作稿；
12. `15-项目复盘.md`：十阶段状态和只允许沉淀的通用候选。

## 5. 处理确认

- 事实使用现有事实核验包导出、填写、提取、回写流程；
- 政策候选必须由用户确认，AI 推荐不等于正式依据；
- 建设范围项全部确认后，运行 `build_scope_baseline.py`，再运行 `confirm_scope_baseline.py`；
- 公司标准知识包先生成能力映射候选；候选语料只允许以带 `【待确认】` 标记的 `working_only/structure_only` 进入评审工作稿，不代表范围或配置已经确认；填写并回写 `mapping-decisions.json` 后，才允许以 `parameterized` 进入正式交付；
- 部门政策目录只是候选线索；只有官方核验后的 `policy_document` / `policy_clause` 才允许作为正式依据；
- 贯通关系写入 `数据包/结构化数据/traceability-links.json`，不要让模型猜测问题、投资、指标和效益之间的对应关系；
- 重新运行统一入口，直到相关章节计划从 `blocked` 变为 `ready`。

## 6. 生成与交付

可先从任务包批量形成证据约束工作初稿：

```powershell
python scripts/build_policy_section_material.py `
  <knowledge.sqlite> <项目编号> --mode working `
  --output-json policy-section-material.json --output-md policy-section-material.md

python scripts/build_evidence_bound_initial_drafts.py `
  <knowledge.sqlite> <项目编号> <10-章节任务包> <11-正文工作稿> `
  --output evidence-bound-drafts.json
```

也可逐章生成后，用 `save_section_draft.py` 保存版本，先执行 `validate_section_draft.py`，通过后再以 `manage_section_draft.py ... adopt` 采纳。所有适用章节采纳后执行：

```powershell
python scripts/assemble_report_markdown.py <knowledge.sqlite> <项目编号> --mode delivery --output report.md
python scripts/validate_full_report.py <knowledge.sqlite> <项目编号> --mode delivery --output validation.json
python scripts/build_report_docx.py report.md report.docx `
  --project-name "项目名称" --owner-name "建设单位" --mode delivery `
  --database <knowledge.sqlite> --project-code <项目编号> `
  --template <已确认格式模板.docx> --summary docx-build-summary.json
python scripts/audit_delivery_artifact.py report.docx `
  --build-summary docx-build-summary.json --database <knowledge.sqlite> `
  --project-code <项目编号> --output delivery-artifact-audit.json
```

统一入口会在全部章节已采纳且交付校验通过后生成 `14-交付稿/report-candidate.docx`。最后必须渲染 DOCX、检查全部页面、执行格式 lint 和参考残留扫描，并登记复核：

```powershell
python scripts/record_word_render_review.py `
  14-交付稿/report-candidate.docx rendered-pages `
  --reviewed-by "复核人" --result pass --checked-all-pages `
  --output 运行记录/word-render-review.json
```

再次运行统一入口；只有复核记录与当前 DOCX、全部页面 PNG 的哈希一致时才会显示 `delivery_ready`。

交付前更新 Word 目录，并确认所有 Markdown 标记已经转换为真实 Heading 样式。再运行真人基准检查：

```powershell
python scripts/benchmark_report_quality.py report-candidate.docx `
  --reference <真人可研.docx> --database <knowledge.sqlite> `
  --project-code <项目编号> --output benchmark-report.json
```
