# 知识库数据契约

## 1. 适用范围

本规则定义项目 SQLite、共享 PostgreSQL 和仓库外私有知识包的边界。项目库正式表结构以 `assets/knowledge-base/migrations/` 为准；共享库正式表结构以 `assets/knowledge-base/postgres/` 为准；本文只说明对象职责和调用约束。

## 2. 架构原则

1. `project` 表示一次具体项目；标准资产、政策、格式和语料不得再用“场景”表冒充项目。
2. `source_document` 统一登记项目资料和共享资料，必须保存来源范围、路径或官方URL、哈希和核验状态。
3. 原件保存在文件系统，数据库不保存 Word、PDF、Excel 或网页 BLOB。
4. 事实、证据、推断、确认分别建模；模型置信度不能替代证据状态。
5. 用户确认和审计日志为追加式记录，不允许覆盖历史决定。
6. 政策、格式、语料和项目事实都保存项目选择快照，保证知识库更新后仍可复现。
7. PostgreSQL 是多人共享知识的发布库，SQLite 是每个项目的执行库。项目正文只能使用已发布知识的本地快照，不能因服务器内容随后变化而静默改变。
8. 服务器不可用时，只有 `snapshot_required` 或明确允许旧快照的运行才可使用通过完整性和权限校验的 SQLite 快照；`server_required` 必须在 S0 阻断。从零离线运行可以在 `offline_pack` 模式导入仓库外受审 JSON 知识包。
9. 部门政策目录与正式政策证据分层存储：目录只提供发现线索，经过官方原文核验和条款切分后才能发布为正式政策证据。
10. RAG 或向量检索只能作用于候选召回层，不能绕过发布状态、审核状态、项目范围、政策核验和章节来源门禁。

## 3. 三层存储位置

| 层 | 建议位置 | 职责 |
| --- | --- | --- |
| 私有原始知识包 | Skill 仓库外的受控目录 | 保存公司标准方案/清单生成的 JSON 包和部门政策目录，不进入公开仓库。 |
| 共享发布库 | PostgreSQL 的 `medical_report_kb` schema | 保存已审核、可发布、可追踪版本的标准语料、能力、政策目录和正式政策条款。 |
| 项目执行库 | `<项目目录>/数据包/数据库/knowledge.sqlite` | 保存项目事实、范围、确认、章节、草稿、校验，以及从共享库同步的不可变快照。 |

同步链固定为：`私有原始文件 → 受审知识包 → PostgreSQL 发布视图 → 项目 SQLite 快照 → 章节任务包 → 正文`。禁止让生成器绕过快照直接从未审核表或实时网络取正文素材。

## 4. 项目 SQLite 核心对象

### 项目与来源

- `project`：正式名称、文档类型、行政区划、范围最高依据、验收目标、事实基线版本。
- `source_document`：项目材料、官方政策原文、模板、标准方案和语料来源。
- `evidence_record`：来源位置、必要原文、证据哈希、提取方式和可信等级。

### 事实与确认

- `project_fact`：原子事实、单位、时点、状态、重要性、冲突组和允许章节。
- `fact_evidence`：事实与证据的多对多关系，区分支持、反证和背景。
- `inference_record`：计算、政策推导、技术默认、上下文推断和项目假设。
- `confirmation_question`：第一轮至多15项的实质问题。
- `confirmation_record`：确认、否决、修改、暂缓和排除的追加式记录。

### 政策

- `policy_document`：正式名称、文号、发布单位、层级、行政区划、日期、有效性、官方URL和核验状态。
- `policy_clause`：条款位置、必要原文、审慎概括、主题、对象和适用边界。
- `policy_relation`：替代、废止、修订、配套和上位依据关系。
- `policy_match_run`：一次政策匹配的版本、输入主题、算法版本和结果摘要。
- `project_policy_match`：归属于一次匹配运行的项目适用性、依据/背景用途、项目关系、排序键和用户决定。
- `policy_citation`：条款在报告中的实际使用位置。

### 文档标准与格式

- `document_standard`：地区、文档类型、必备章节、表格要求和正式来源。
- `format_profile`：页面、封面、目录、编号、页眉页脚和分节画像。
- `style_rule`：语义角色到 Word/WPS 样式、层级、编号、字体、段落属性的映射。
- `project_document_profile`：本项目候选和确认的格式画像。
- `document_standard_match_run` / `project_document_standard_match`：地方或国家编制标准的候选匹配、理由、得分和用户决定。
- `format_lint_run` / `format_lint_issue`：直接格式、缩进、Tab、未映射样式和标题层级问题。

### 语料、产品与章节生成

- `corpus_document` / `corpus_block`：原始方案和原子语料块。
- `corpus_tag`：受控标签，不允许自由造同义标签。
- `product_capability`：产品能力、前提、接口依赖和不包含内容。
- `project_scope_item` / `scope_product_map`：客户清单边界与公司能力映射。
- `section_blueprint`：章节目的、必答问题、必需事实和篇幅边界。
- `section_composition_plan` / `section_plan_source`：本项目每节的事实、范围、政策、语料和禁止项组合计划。
- `draft_section_version`：AI、人工和恢复版本；只有通过检查且被采用的版本进入交付候选。

### 校验与审计

- `stage_gate_result`：每阶段的通过、告警、失败和跳过结果。
- `validation_run` / `validation_issue`：事实、范围、投资、政策、逻辑、语料和格式问题。
- `llm_call_log`：模型、提示版本、输入输出哈希、时长和状态；不默认保存敏感原文。
- `audit_log`：对象变更前后值和操作者，追加式保存。

### 共享知识快照

- `shared_knowledge_snapshot`：项目从哪个服务器 schema、知识包或政策集合同步，记录内容哈希、抓取时间和当前/已替代状态。
- `shared_knowledge_snapshot_item`：快照内每个语料块、能力、关系、政策文件和条款的完整负载及单项哈希。
- 快照同步后仍导入本地 `corpus_*`、`product_capability` 和 `policy_*` 运行表，现有章节生成和校验脚本不需要依赖在线数据库。

## 5. 共享 PostgreSQL 核心对象

### 受审标准知识

- `knowledge_package`：知识包版本、权限、内容哈希、审核摘要和发布状态。发布同一 `standard_solution` 来源的新知识包时，导入事务会把旧发布版本标记为 `retired`；已建立的项目快照不受影响。
- `source_document` / `package_source`：知识包来源及角色，不保存原始文件 BLOB。
- `corpus_document` / `corpus_block`：受审标准方案文档与可复用原子段落。
- `product_capability` / `capability_block`：标准清单能力与允许召回的正文块关系。
- `tag` / `corpus_block_tag`：受控标签。

### 部门政策目录与正式政策证据

- `policy_catalog` / `policy_catalog_entry`：部门整理的政策、标准或评价文件目录；允许暂缺官方 URL、文号或条款，但只能作为候选线索。原始索引号可重复，记录以来源行稳定标识；`index_occurrence` / `index_conflict` 显式记录目录质量冲突。
- `policy_document`：已核验正式文件，保存类型、文号、发布机关、效力、地域、官方 URL、文件哈希、审核和发布状态。
- `policy_clause`：已核验条款原文、审慎概括、适用边界和使用角色。
- `policy_topic` / `policy_clause_topic`：政策条款主题。
- `policy_relation`：替代、废止、修订、配套和上位依据关系。
- `policy_verification_event`：核验过程和证据记录。

### 发布与审计

- `import_run`：每次导入的输入哈希、状态和统计。
- `review_event`：审核、发布、撤回和理由。
- `runtime_*` 视图：只暴露 `published`、`approved`、`verified` 的知识，是查询和项目同步的唯一服务器入口。

共享库迁移顺序：

1. `001_clean_document_schema.sql`：兼容早期清洗文档表。
2. `002_shared_knowledge_policy_schema.sql`：完整共享知识、部门政策目录和正式政策表。
3. `003_runtime_snapshot_views.sql`：项目同步所需的发布视图和约束调整。
4. `004_policy_catalog_duplicate_index.sql`：允许保留重复来源索引号并增加冲突标记。
5. `005_policy_catalog_runtime_view.sql`：重建政策目录发布视图，使新增冲突字段可查询。
6. `006_runtime_policy_catalog.sql`：发布政策目录元数据，供项目建立候选目录快照。

项目 SQLite 对应迁移为 `009_policy_catalog_candidates.sql`、`010_policy_catalog_duplicate_index.sql` 和 `011_policy_catalog_snapshot.sql`。目录以 `(catalog_id, source_row)` 保证来源行唯一，不再把索引号错误地当成唯一键；服务器目录以 `policy_catalog` 快照同步后才能参与项目候选匹配。

## 6. 迁移规则

1. 不在 Python 文件中维护一段不断增长的 `SCHEMA_SQL`。
2. 每个迁移文件一经应用不得修改；需要调整时新增迁移。
3. `init_knowledge_db.py` 记录迁移哈希，已应用文件内容变化时停止执行。
4. FTS5 为可选迁移；结构化过滤和状态门禁不依赖全文检索。
5. 删除项目时允许级联删除项目工作记录；共享政策、格式和语料不得随项目删除。
6. PostgreSQL 和 SQLite 的迁移都记录文件哈希；已经应用的迁移文件发生变化时必须停止，新增修订只能追加新迁移。唯一例外是代码中同时锁定“历史哈希、当前规范哈希和迁移版本”的已审等价对；任一值不匹配仍立即停止，禁止通配或命令行跳过。
7. 只有显式 `--publish` 的导入才进入运行视图；未发布记录不得被项目同步。

## 7. 稳定状态

事实状态只使用：

- `confirmed` / `【已确认】`
- `material_explicit` / `【材料明确】`
- `pending_confirmation` / `【待确认】`
- `pending_supplement` / `【待补充】`
- `conflict` / `【冲突】`
- `analysis_recommendation` / `【分析建议】`
- `reference_only` / `【仅作参考】`
- `not_applicable` / `【不适用】`

其他对象的状态必须使用迁移文件中的检查约束，不得在生成脚本中临时发明同义状态。
