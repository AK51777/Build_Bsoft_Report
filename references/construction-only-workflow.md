# 清单对照与建设内容 Word 专项流程

适用：只要求建设清单、标准方案对照、建设内容拼接或专项 Word。全文可研必须由用户明确要求后再进入。项目文种记录为 `construction_only`，交付格式默认 `docx`；MD/JSON 是内部中间产物。

## 本链路使用什么

| 环节 | 使用模块 | 输出/用途 |
|---|---|---|
| 知识准备 | 既有知识连接、导入/同步与项目 SQLite 快照 | 固定标准方案包版本、哈希、完整性证明 |
| 清单读取 | `extract_xlsx_scope.py`、`ingest_scope_items_sqlite.py` | 原始显示表、逻辑建设项、来源定位 |
| 对照与目录 | `construction_alignment.py capture-scope/match` | 候选模块、缺口、待确认父路径 |
| 一轮确认 | `apply-decisions` + `hierarchy_review` | 候选决定与目录归属分别记录，复用已有确认 |
| 原文装配 | `construction_alignment.py assemble/validate` | 逐字标准正文、已确认缺口、哈希绑定清单 |
| 专项 Word | `build_construction_docx.py` | 建设清单与建设内容两章、内置样式、实际大纲父子校验 |
| MCP 适配 | `construction_prepare_review` → `construction_apply_and_assemble` | 后者校验通过后默认继续生成上述专项 Word |
| 排版复核 | Word 格式 lint + 当前环境渲染工具 | 字体、编号、表格、分页、目录实查 |

本模式不调用 `run_project_pipeline.py`、事实/政策核验包、投资估算、效益分析、完整可研章节生成及 `full_report_delivery`。保留这些模块供完整可研使用。正文只读已验证项目快照；不得把全库候选或原始参考稿当成已确认标准。

专项入口只要求标准包，使用 `load_config(required_kinds=("standard",))` 和原子 `sync_to_project(construction_only=True)`；政策包缺失或损坏不阻断专项，标准包完整性失败仍阻断。已有项目固定并验证标准快照，不静默升级。完整可研与双包管理默认行为保持原契约。

## 共享编排入口

Skill 与 MCP 优先使用 `construction_workflow.py`，而不是临时串联多个脚本。目录只展示 `01-请确认建设清单对照.md` 和 `02-交付/建设清单与建设内容.docx`，技术结果集中在 `运行数据/`。首轮只生成核对数据，不装配长正文预览，不创建完整可研空产物。

```text
python scripts/construction_workflow.py prepare-review <工作台> <清单.xlsx/csv/tsv/docx/md/json> --project-code <编号> [--project-name <名称>] [--database <已有项目.sqlite>] [--plan <结构建议.json>]
python scripts/construction_workflow.py confirm-and-generate <工作台> <确认.json>
python scripts/construction_workflow.py resume <工作台>
python scripts/construction_workflow.py status <工作台>
python scripts/construction_workflow.py render <工作台>
python scripts/construction_workflow.py record-render-review <工作台> <实际视觉复核.json>
```

粘贴表格可保存为 Markdown 输入。字段含义不明时在提取 JSON 的 `detected_columns` 中明确解释，不无条件填充分类。结构建议数组通过 `original_ordinal` 关联原始行，可设置 `role: heading_only`、`title`、`parent_path` 或 `modules: [{name, parent_path, choice}]`。`choice` 指定当前候选信息。展开、标题用途、目录、缺口及首选在主核对页一起展示，确认前仍为建议；外部显示原始序号与 `2.1` 一类子号。

确认 JSON 包含 `review_id`、`reviewed_by`、实际 `user_reply` 和 `accept_all: true`。标志只表示接受本页已展示建议，不能代替用户答复。局部更改通过 `overrides` 指定当前 `scope_row_id` 的明确 `decision/candidate_id/capability_id/root_heading_path/parent_path`；其余接受本页建议。低置信、无法唯一定位根的项必须明确指定；不自动变成缺口。结构拆分/删项改变时重新生成核对页。已有用户确认不得重复询问。

原始行ID、逐行哈希、标题用途、一对多 `mapping_edges` 和来源顺序写入manifest；Word原表从同一manifest读取，禁止读取旁路清单。装配保存唯一 `heading_tree`（父ID、层级、范围行、源块、树哈希），Word只映射样式；继续执行 DEV-077 的目录确认、自父级、深度和真实大纲检查。

首选子树重复在首轮提示，最终映射重复也保存到manifest；默认分别保留，不自动删正文。匹配缓存绑定清单、实际标准能力与正文、阈值和匹配版本。同一有效业务决定重复提交幂等；输入/标准/建议改变则旧确认失效。续跑先验证最新决定与manifest，再按文件哈希及代码/样式签名复用Word；不会仅凭“文件存在”跳过校验。

确认后自动生成DOCX并尝试整稿渲染。Windows使用本机Word，其他系统使用LibreOffice，PNG生成需要pypdfium2和Pillow；不自动安装依赖。环境失败时保留 `docx_created_render_pending` 及具体错误，可用 `render/resume` 续跑。机器渲染成功不等于视觉复核完成。

视觉复核记录必须包含当前 `docx_sha256`、`result: pass`、`checked_all_pages: true`、`reviewer_type: ai_visual/human`、实际 `reviewed_by`，以及与 `render-binding.json` 完全相同的 `page_count/pages`（页号、路径和哈希）。逐页检查后才能记录，不把AI复核写成真人签认。全部通过才返回 `ready/ready_with_gaps`。本版仅复用同一Word哈希的整套渲染，不跨Word版本复用页面视觉结论。

## 先恢复目录含义，再确认匹配

原始显示值、逻辑匹配项、目录父路径是三种数据。禁止把“第一非空格”直接当大类；空白单元格也不能无条件全列向下填充。

1. XLSX 优先依据合并区域、真实列位和明确的大类起止范围。聊天粘贴的表格可能已经丢失合并语义，须结合原清单上下文恢复候选树。
2. 大类 → 业务分组/系统 → 模块 → 标准子功能分别占相应层级。大类应包含其下建设项；模块不能成为自身父标题，也不能仅因落在“大类”列便与大类同级。
3. 标准方案祖先和相邻行只提供归属建议，不能覆盖客户明确分类。不能把同名大类的重复出现自动解释为范围合并；保持来源顺序，非连续分组是否合并属于目录决定。
4. 只作为标题的行不参与模块检索，其下范围要写入子项的父路径；一对多展开保留原行关联。来源显示表不得被展开后的逻辑模块表无说明替换。纯标题、展开、重复项与缺口均在首次核对中明确呈现。
5. `match` 产出候选 `hierarchy_review`。核对后填写 `status=confirmed`、实际确认人和带时区时间，保留匹配运行/清单快照/知识包哈希，每个逻辑 `scope_row_id` 恰有一条 `parent_path`（只含父标题，不含模块自身）。传给 `assemble --hierarchy-review`。目录归属不得只存在临时排版脚本中。
6. `module_name_used_as_parent` 必须解决。即使模块精确匹配，错误分类仍需处理。缺口没有标准父路径时尤其不能猜。其他语义歧义由调用者在候选树中标明；自动警告并非业务正确性的充分证明。

## 第一轮只让用户找到一个入口

对话第一句说明“请先确认下面的目录归属和匹配例外；确认后会直接生成 Word”。紧接着给唯一核对入口 [待确认：清单与目录](占位路径)，实际回复必须替换为已生成文件的绝对路径，不能把它夹在内部产物列表中。

优先展示：目录树/有歧义的父路径 → 待选候选 → 展开、纯标题、缺口。每项带原序号、现状、首选及影响；精确且归属清楚的项只报告数量。推荐项必须用“建议”标注。文末给可直接回复的例子：

> 目录按展示的层级；相似项采用首选；列出的展开、纯标题和缺口按建议处理，其余不变。

仅当上一轮已经明确展示这些处置且用户作出相应确认，才能批量记为确认。“按首选”通常只覆盖已呈现的候选；不得扩展为对未展示的目录、删项、拆分或缺口的批准。已有明确答复不重复询问；追加问题只针对新增/仍未知的归属。分批确认时保存进度，不重新跑已固定的全部候选。

## 确认后的终点是 Word

CLI 路径：执行决定回写 → 带目录审查的 `assemble` → `validate` → 下面的专项 Word 构建。实际运行脚本前查看 `--help`。

```text
python scripts/build_construction_docx.py <knowledge.sqlite> <construction-assembly-manifest.json> <建设清单与建设内容.docx> --project-name <项目名称> [--format-config <已确认格式配置.json>]
```

MCP 新工作台使用 `construction_workflow`，action 为 `prepare_review/confirm_and_generate/resume/get_status/render/record_render_review`，共用上述编排器。兼容旧工作台仍可调用 `construction_apply_and_assemble`，`generate_word` 默认true。项目名未知使用项目代码，不编造医院名称。

共享编排器默认内置A4中文样式、Heading 1—7编号、缩进、页边距与页码。底层专项Word构建器继续支持已确认格式配置。封面为“建设清单与建设内容”，正文两章。`construction`模式必须绑定当前数据库、项目与manifest且输入正文完全一致；不注入完整可研工作稿标记，也不代表通过可研报批门禁，禁止生成后手工删除门禁标记。

`word_structure_pass_render_required` 只说明结构通过；继续渲染检查。`word_generation_failed` 要报告具体原因、修复并重试，不能改成交付 MD。最终第一链接给 DOCX，说明保留的缺口和实际检查结果；审计 JSON/MD 放内部运行目录，不向用户罗列整套工作台。没有渲染条件时仍提供已生成 Word，并明确排版未完成视觉复核，不宣称正式交付。

## 最小检查

- 原清单及逻辑建设项覆盖、顺序、展开/纯标题处置与确认一致；缺项、多项、错序阻断。
- 每个模块的实际 Word 大纲父路径与确认目录一致；不仅检查 Heading 名称或编号连续。不得用最大级别截断压平下级标题。
- 标准正文完整子树、块顺序、文本哈希保持一致；缺口正文仅 `【待补充】`。重复来源行不自动删重。
- 最终DOCX独立核对有序标题、正文段落、表格行/单元格与真实编号；缺块、多块或篡改阻断。分别报告文本、基础表格和图片保真范围，在首轮提示图示悬空引用。
- 两章之外的可研论证内容为零；Word 保留真实标题样式、编号及内置中文字体设置。
- 纯文本旧知识包明确保真限制；图片、复杂表格和富文本不能声称已保留。专项构建器遇到富文本/资产会阻断，须使用保留资产的适配路径，不能静默降级。
- 渲染后核对字体实际可用、目录大类包含子项、编号、跨页表格及末页；不把“阻断项 0”说成“所有格式提示为 0”。
