# 参考可研语料清洗与发布规则

## 1. 两条知识链

公司标准建设方案与参考可研不得共用一个无类型召回池：

- `standard_solution`：沿“客户清单—确认能力—标准方案块—全量建设章节”使用；
- `reference_feasibility`：沿“目标章节—语义槽位—论证块—轻量参数化”使用；
- 政策、标准仍进入独立政策证据链，不从历史可研正文反向认定有效性。

## 2. 双轴分类

文档级 `source_corpus_type` 记录来源资产类型：`standard_solution`、`reference_feasibility`、`generic_reference`、`legacy_unspecified`。

文本块级 `content_type` 记录允许用途：

- `construction_solution`：系统、模块、功能、接口和实施能力正文；
- `feasibility_narrative`：目标、背景、需求、必要性、可行性和效益等可研论证；
- `common_narrative`：建设原则、标准规范、技术路线、实施保障等跨项目通用说明；
- `project_specific`：依赖原项目现状、投资、范围或结论；
- `structure_only`：只复用标题、论证顺序或表格结构；
- `legacy_unspecified`：历史数据待重新分类，发布前必须补标。

`document_type` 表示目标文种，不能替代上述来源和内容类型。

## 3. 清洗流程

1. 登记原件、权限、哈希和项目类型，不修改原文件。
2. 从 Word `styles.xml` 解析真实标题层级；标题路径为空时停止语义清洗。
3. 按“一个子标题及其连续论证段落”形成论证功能块，不按单段或整章切分。
4. 映射 `semantic_section` 与 `content_slot`；使用 `reference_corpus_taxonomy_v1.json` 的受控代码。
5. 标记适用项目类型、测评目标、变量槽位、禁用词、原顺序和复用级别。
6. 客户名称、地域、项目名称、投资、工期、现状和既有结论不得进入可直接复用文本。
7. 所有候选初始为 `pending`、`publish_eligible=false`；人工确认块边界、变量和用途后才能发布。
8. 用 `build_reference_corpus_review_pack.py` 生成带原工作包哈希的决定表；只有 `confirmation.status=confirmed` 且逐块状态为 `approved/prohibited/retired`，才能用 `build_reference_knowledge_pack.py` 构建受审包。AI建议、脚本默认值和 `pending` 均不等于人工确认。
9. 人工决定表是禁用与退役候选的完整审计记录；运行知识包只携带 `approved` 正文。`prohibited/retired` 候选的全文不得进入 PostgreSQL、项目 SQLite 快照或章节任务包。
10. 运行知识包使用文档ID构造匿名来源文件名，只保存原件哈希；客户原文件名、完整路径和禁止残留词明文仅保留在仓库外审计工作包。构包器完成明文残留校验后，运行知识包只保存禁止词哈希，不把参考客户标识写入共享数据库。

### 3.1 标准方案专用无损规则

标准方案与参考可研的清洗规则必须分开管理。`standard_solution` 最小源单位是“一次标题出现及其直接正文”，不得套用参考语料的最短字数、摘要或跨段去重规则：

1. 每次标题出现生成独立 `source_section_id`、`source_order`、`source_location`、`heading_path` 和标题标志；重复同名路径不得合并。
2. 少于 120 字的正文、长段最后一个短尾块和标题无直接正文的结构节点必须全部保留；`min_chars` 只能兼容长度分类，不能控制输出。
3. 空正文标题生成 `structure_only` 块。同一源段落的块使用连续 `chunk_index`，按序拼接后必须与源段落清洗文本逐字相等。
4. 知识包必须包含 `corpus.source_sections` 和 `review_summary.standard_solution_coverage`，并从源段落与块重新计算段落数、标题数、块数、短正文保留数、空标题保留数、结构哈希和内容哈希。
5. 标准知识包导入前必须复算证明；删块、换序、改标题、改正文、改块 ID 或伪造 `status=pass` 均应阻断。
6. PostgreSQL 保存源段落清单和块级源段落字段；项目快照只同步审核通过的运行投影，但必须同时保留源发布包证明，并对运行投影重新计算 100% 完整性证明。
7. 历史标准包或快照若缺少证明，必须从源 Word 用当前构建器重建、重新发布和同步，不得让语言模型补写缺失段落。

## 4. 组装约束

- 非建设章节只召回 `feasibility_narrative/common_narrative`，`structure_only` 只提供结构。
- 建设内容只使用客户已确认清单及其能力绑定的 `standard_solution/construction_solution` 块。
- `project_specific` 不进入目标项目正文；可作为结构复用或事实提取线索另行处理。
- B级只做确定性变量替换和小范围语态调整；C级基于目标项目事实重建，不复制原文。
- `1.1.5` 建设摘要从 `4.2` 同一确认版目标、规模与内容派生，禁止维护两套平行口径。
- 目标模板编号变化时使用语义映射而不是硬编码历史编号：当前三级模板以 `4.1.2` 承载 `overall_objective_scope` 主口径，以 `1.1.2` 承载派生摘要；二者必须绑定同一批 `block_id` 和同一项目目标事实。
- 参考可研知识包允许零产品能力；标准方案包仍必须包含产品能力。两类包可共同进入同一项目快照，但不得交叉召回。

## 5. 项目类型扩展

当前只发布 `smart_hospital/hospital_informationization` 医院链。`medical_consortium` 和 `regional_health_platform` 只保留 `project_type` 与分类注册标记，状态为 `reserved_not_implemented`；不得发布其语料、套用医院规则或声称链路已实现。新增类型先补充高质量参考可研、完成语义映射和人工审核。
