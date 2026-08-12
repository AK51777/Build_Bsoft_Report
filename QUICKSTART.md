# 快速开始

## 1. 检查环境

使用 Codex 内置文档运行环境或 Python 3.10 以上版本：

```powershell
python scripts/check_dependencies.py --output dependency-check.json
```

`core.ready=true` 表示本地 SQLite 主链可运行。`word.candidate_generation_ready=true` 只表示可以生成 DOCX；只有实际渲染并逐页检查后，Word 才达到视觉交付状态。

## 2. 建立项目

```powershell
python scripts/run_project_pipeline.py D:\projects\hospital-a `
  --project-code HOSPITAL-A-001 `
  --official-name "某医院信息化建设项目" `
  --owner-name "某医院" `
  --jurisdiction-code 100000 `
  --jurisdiction-name "某地区"
```

把项目材料复制到 `D:\projects\hospital-a\原始资料`，然后重复运行同一命令。原始材料不会被修改。

建议把参考可研、厂商方案、政策线索和 Word 模板使用明显文件名或子目录隔开。系统会生成 `source-role-register.json`；如分类不准确，在 `project-config.json` 的 `source_roles.overrides` 中按相对路径或通配符显式指定角色后重跑。

## 3. 查看本轮结果

优先查看：

1. `运行记录/pipeline-result.json`：总状态、阻断项和下一步；
2. `数据包/结构化数据/source-inventory.json`：资料清单与哈希；
3. `数据包/结构化数据/source-role-register.json`：哪些材料允许进入候选事实、哪些仅限参考；
4. `数据包/结构化数据/candidate-fact-workpack.json`：待提取和确认的事实输入；
4. `数据包/结构化数据/scope-baseline.json`：范围基线候选；
5. `数据包/结构化数据/traceability-matrix.csv`：贯通矩阵及缺失链；
6. `数据包/结构化数据/policy-selection.json`：政策候选；
7. `数据包/结构化数据/document-standard-selection.json`：文档标准候选；
8. `10-章节任务包`：逐章生成输入；
9. `运行记录/stage-gates.json`：阶段门禁；
11. `11-正文工作稿/report-working.md`：当前工作稿；
12. `15-项目复盘.md`：十阶段状态和只允许沉淀的通用候选。

## 4. 处理确认

- 事实使用现有事实核验包导出、填写、提取、回写流程；
- 政策候选必须由用户确认，AI 推荐不等于正式依据；
- 建设范围项全部确认后，运行 `build_scope_baseline.py`，再运行 `confirm_scope_baseline.py`；
- 贯通关系写入 `数据包/结构化数据/traceability-links.json`，不要让模型猜测问题、投资、指标和效益之间的对应关系；
- 重新运行统一入口，直到相关章节计划从 `blocked` 变为 `ready`。

## 5. 生成与交付

按章节任务包生成正文后，用 `save_section_draft.py` 保存版本，以 `manage_section_draft.py ... adopt` 采纳。所有章节采纳后执行：

```powershell
python scripts/assemble_report_markdown.py <knowledge.sqlite> <项目编号> --mode delivery --output report.md
python scripts/validate_full_report.py <knowledge.sqlite> <项目编号> --mode delivery --output validation.json
python scripts/build_report_docx.py report.md report.docx --project-name "项目名称" --owner-name "建设单位"
```

统一入口会在全部章节已采纳且交付校验通过后生成 `14-交付稿/report-candidate.docx`。最后必须渲染 DOCX、检查全部页面、执行格式 lint 和参考残留扫描，并登记复核：

```powershell
python scripts/record_word_render_review.py `
  14-交付稿/report-candidate.docx rendered-pages `
  --reviewed-by "复核人" --result pass --checked-all-pages `
  --output 运行记录/word-render-review.json
```

再次运行统一入口；只有复核记录与当前 DOCX、全部页面 PNG 的哈希一致时才会显示 `delivery_ready`。
