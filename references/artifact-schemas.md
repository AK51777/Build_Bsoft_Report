# 中间产物、字段和编号

## 目录

1. 工作台结构
2. 稳定编号
3. 项目任务书
4. 资料登记表
5. 事实台账
6. 标准化建设清单
7. 参考方案复用地图
8. 项目贯通矩阵
9. 章节任务包
10. 校验问题与变更记录

## 1. 工作台结构

```text
方案工作台/
├── 00-项目任务书.md
├── 01-资料登记表.md
├── 02-事实台账.md
├── 03-项目事实卡.md
├── 04-缺失资料与确认问题.md
├── 05-标准化建设清单.xlsx
├── 06-清单映射与差异表.xlsx
├── 07-参考方案复用地图.md
├── 08-项目贯通矩阵.xlsx
├── 09-确认版目录.md
├── 10-章节任务包/
├── 11-正文工作稿/
├── 12-校验问题清单.md
├── 13-版本变更记录.md
└── 14-交付稿/
```

项目已有结构时复用现有目录，不强制搬移。

## 2. 稳定编号

| 对象 | 格式 |
|---|---|
| 来源文件 | `SRC-001` |
| 项目事实 | `FACT-001` |
| 冲突事项 | `CONFLICT-001` |
| 缺失资料 | `MISSING-001` |
| 确认问题 | `QUESTION-001` |
| 建设范围 | `SCOPE-001` |
| 需求事项 | `REQ-001` |
| 参考复用 | `REF-001` |
| 绩效指标 | `KPI-001` |
| 校验问题 | `ISSUE-001` |

编号一经建立不得因排序变化重编。作废项保留编号并标记状态。

## 3. 项目任务书

至少包含：正式项目名称、文档类型、使用阶段、建设单位、目标读者、范围最高依据、核心验收目标、参考资料限制、未知事项处理、输出格式、项目目录、版本规则和确认记录。

## 4. 资料登记表

| 字段 | 说明 |
|---|---|
| `source_id` | 来源编号 |
| `file_name` | 文件名 |
| `file_type` | DOCX、XLSX、PDF、MD等 |
| `path` | 当前项目路径 |
| `sha256` | 文件校验标识 |
| `source_class` | 正式材料、医院材料、清单、厂商材料、参考稿等 |
| `document_date` | 文件日期 |
| `statistical_date` | 数据统计时点 |
| `issuer` | 出具单位；无依据时留空 |
| `usage_scope` | 允许支持的内容 |
| `restriction` | 使用限制 |
| `contains_personal_data` | 是否包含个人信息 |
| `status` | 当前有效、历史版本、仅作参考等 |

## 5. 事实台账

| 字段 | 说明 |
|---|---|
| `fact_id` | 稳定事实编号 |
| `fact_category` | 名称、单位、现状、范围、投资、周期、指标等 |
| `fact_content` | 一个可独立判断真假的原子事实 |
| `value` | 数值或标准值 |
| `unit` | 数据单位 |
| `statistical_date` | 统计时点 |
| `source_id` | 来源编号 |
| `source_location` | 页码、表名、单元格或段落 |
| `status` | 统一事实状态 |
| `conflict_id` | 冲突组编号 |
| `allowed_chapters` | 可使用章节 |
| `sensitivity` | 普通、内部、个人信息等 |
| `notes` | 使用限制 |

项目事实卡只汇总影响项目名称、边界、目标、投资、周期、技术路线和结论的核心事实，不替代台账。

## 6. 标准化建设清单

| 字段 | 说明 |
|---|---|
| `scope_id` | 稳定范围编号 |
| `source_id` | 来源文件 |
| `original_name` | 原清单名称 |
| `standard_name` | 标准化名称 |
| `domain` | 集成平台、临床、医技、管理等 |
| `item_type` | 软件、硬件、服务、云资源、接口迁移等 |
| `construction_mode` | 新建、升级、利旧、替换、迁移、待确认 |
| `quantity` | 数量 |
| `unit` | 单位 |
| `customer_scope` | 是否属于本期客户范围 |
| `company_capability` | 公司能力映射 |
| `mapping_type` | 一对一、一对多、多对一、缺口等 |
| `investment_category` | 投资归属 |
| `acceptance_target` | 验收目标 |
| `chapter_location` | 正文承载章节 |
| `status` | 已确认、待确认、不适用等 |

## 7. 参考方案复用地图

字段：`reference_id`、`source_id`、`source_chapter`、`target_chapter`、`structure_similarity`、`business_similarity`、`reuse_type`、`allowed_content`、`forbidden_content`、`residual_terms`、`notes`。

## 8. 项目贯通矩阵

字段：问题ID、需求ID、事实依据、范围ID、建设内容、投资分项、指标ID、目标值及状态、评价方式、预期效益、对应章节、完整性和缺失项。

## 9. 章节任务包

```text
章节编号：
章节名称：
章节目的：
必须回答的问题：
允许使用的事实ID：
对应建设清单ID：
允许使用的参考来源：
禁止继承内容：
必备表格：
待补充项：
跨章节共用口径：
建议篇幅：
行文要求：
完成条件：
校验规则：
```

## 10. 校验问题与变更记录

校验问题字段：`issue_id`、`severity`、`issue_type`、`location`、`description`、`related_ids`、`suggested_action`、`status`、`owner`、`resolution`。

版本变更记录至少包含：版本号、日期、修改文件、修改原因、依据、重要口径变化、执行人或确认人、校验结果。

## 11. V0.2知识库最小数据契约

P0/P1知识库以SQLite实现，字段与关系的正式定义见 `assets/knowledge-base/migrations/001_core_schema.sql`。下列对象是Skill编排层必须理解的稳定接口：

- `project`：项目身份、文种、建设单位和地域，不与语料场景混用。
- `source_document`、`evidence_record`：来源文件及页码、段落、单元格、网页条款等证据定位。
- `project_fact`、`fact_evidence`：原子事实及其支持或反驳证据。
- `inference_record`：与事实分离的分析判断，必须记录前提和转正条件。
- `confirmation_question`、`confirmation_record`：待用户确认问题和追加式确认记录；不得覆盖历史确认。
- `policy_document`、`policy_clause`、`policy_match_run`、`project_policy_match`、`policy_citation`：政策原文、条款、匹配批次、项目适用性和“依据—背景—章节”引用链。
- `document_standard`、`format_profile`、`style_rule`、`project_document_profile`：文种标准、参考文档画像、可执行样式规则和项目选用结论。
- `corpus_document`、`corpus_block`、`corpus_tag`、`product_capability`、`scope_product_map`：高质量方案语料与公司能力的受控复用层。
- `section_blueprint`、`section_composition_plan`、`section_plan_source`、`draft_section_version`：个性化章节的结构、证据配比、来源和版本。
- `validation_run`、`validation_issue`、`stage_gate_result`、`audit_log`：校验、门禁和审计记录。

稳定ID应基于业务键计算，不应依赖数据库自增序号。原始文件保留内容哈希；导入程序不得通过删除已有事实、确认、审核或草稿记录来实现重跑。

## 12. 事实确认包

事实确认包至少包含：项目编号、事实基线版本、事实ID、事实类别、事实内容、数据值及单位、统计时点、事实状态、重要性、来源文件、证据定位、冲突组和用户确认栏。工作表固定为“使用说明、事实核验、推断核验、问题清单、确认记录”；确认记录只追加，不回写覆盖原材料事实。用户填写后先提取 `fact_confirmation` 决定JSON，再校验项目编号和基线版本后回写SQLite。

## 13. 政策证据包

政策证据包至少包含：项目编号、政策匹配运行ID、政策ID、层级、发布机关、完整名称、文号、发布日期、生效/废止日期、效力状态、地域、官方URL、核验状态、核验时间、项目主题、适用结论、编制依据序号、背景序号、原文条款、条款定位、允许用途、禁止表述、用户决定、用户顺序和调整理由。用户填写后先提取 `policy_confirmation` 决定JSON；只允许回写该项目最新一次已完成政策匹配运行。

确定性排序键为：`authority_group`（法律法规/规划/政策/评价标准/地方文件分组）→ 地域层级 → `authority_rank` → `publish_date` → 文号 → 标题 → 条款位置。同一项目的编制依据和政策背景必须复用该排序结果，不得人工另排。

## 14. 格式画像与样式契约

格式画像记录页面、节、标题、正文、列表、表格、题注、页眉页脚和目录字段的观察结果；样式契约把观察结果转为可执行规则。每条规则至少包含：目标对象、Word样式ID/名称、标题级别、编号级别、字体、字号、对齐、首行缩进、左/右缩进、段前段后、行距、是否强制及来源。

不得用空格或Tab模拟标题层级和正文缩进。Word/WPS中的层级应通过段落样式、`outlineLvl` 和多级列表定义表达。
