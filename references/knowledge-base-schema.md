# 知识库数据契约

## 1. 适用范围

本规则定义 Skill V0.2 的项目库和共享知识库边界。正式表结构以 `assets/knowledge-base/migrations/` 中按版本排序的 SQL 为准；本文只说明对象职责和调用约束。

## 2. 架构原则

1. `project` 表示一次具体项目；标准资产、政策、格式和语料不得再用“场景”表冒充项目。
2. `source_document` 统一登记项目资料和共享资料，必须保存来源范围、路径或官方URL、哈希和核验状态。
3. 原件保存在文件系统，数据库不保存 Word、PDF、Excel 或网页 BLOB。
4. 事实、证据、推断、确认分别建模；模型置信度不能替代证据状态。
5. 用户确认和审计日志为追加式记录，不允许覆盖历史决定。
6. 政策、格式、语料和项目事实都保存项目选择快照，保证知识库更新后仍可复现。
7. SQLite 是本地 Skill 的默认执行数据库；字段语义、稳定ID和迁移版本保持可迁移到 PostgreSQL。
8. 当项目指定外部 PostgreSQL 作为清洗文档库时，使用 `assets/knowledge-base/postgres/001_clean_document_schema.sql` 建立独立表；只保存清洗后的文本块、来源路径、哈希和处理元数据，不保存原始文件 BLOB。

## 3. 核心对象

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

## 4. 迁移规则

1. 不在 Python 文件中维护一段不断增长的 `SCHEMA_SQL`。
2. 每个迁移文件一经应用不得修改；需要调整时新增迁移。
3. `init_knowledge_db.py` 记录迁移哈希，已应用文件内容变化时停止执行。
4. FTS5 为可选迁移；结构化过滤和状态门禁不依赖全文检索。
5. 删除项目时允许级联删除项目工作记录；共享政策、格式和语料不得随项目删除。

## 5. 稳定状态

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
