# 建设清单对照与标准建设方案完整装配规则

## 1. 模块边界

本模块只负责四件事：冻结客户建设清单显示快照、生成模块级标准候选、记录核对决定、按客户顺序完整装配标准建设方案。它继承 Skill 的事实状态、权限、快照、审计和范围红线，但不复用通用语料改写、章节扩写、摘要或字数补齐规则。

以下职责仍归其他模块：

- 客户范围是否纳入本期、建设方式和投资分类：`scope-mapping-rules.md`；
- 公司标准方案和参考可研的清洗、审核、发布：`reference-corpus-cleaning-rules.md`；
- 非建设章节的语料复用和改写：`corpus-reuse-rules.md`；
- Word 样式、编号、表格宽度和分页：`word-delivery-rules.md`。

不得让通用章节生成器再次处理本模块已经装配的标准方案正文。

## 2. 数据和隐私边界

1. 公司标准清单、标准方案、能力—内容关系和发布元数据来自 PostgreSQL `medical_report_kb.runtime_*` 视图，经内容哈希锁定后同步到项目 SQLite。
2. 客户清单原件、显示快照、项目范围、候选、问题、人工决定和装配结果只保存在项目本地；不得上传 PostgreSQL。
3. 服务器不可用时遵守知识连接模式。`server_required` 不得假装完成在线核验；已存在的项目快照只能用于明确允许快照的运行。
4. 每次对照必须同时锁定 `scope_snapshot_hash`、`package_id`、`package_content_hash` 和 `matcher_version`。
5. 匹配前必须验证项目知识快照中的 `standard_solution_coverage`；缺少证明的历史快照、任一数量不相等或结构/内容哈希不相等时立即阻断，先重建、发布并同步标准知识包。

## 3. 客户建设清单原样导入

1. 原文件是建设清单章节的内容权威。规范化名称只参与匹配，不得回写原显示值。
2. 冻结完整提取负载，包括工作表顺序、表头、来源行、单元格显示值、合并区域和源文件 SHA-256。
3. 另建按来源顺序排列的模块行，保存 `scope_id`、上级路径和显示行哈希。不得再按稳定 ID 或标准名称排序。
4. Markdown 中可机械展开原表供审阅；最终 Word 应按显示快照重建表格结构。规范化后的清单不得替换原表进入清单章节。
5. 原文件变化后必须形成新快照并重跑对照，旧决定不得静默套用。

## 4. 模块级匹配

### 4.1 最小颗粒度

客户清单和公司标准清单的最小匹配单位均为模块。候选必须同时返回：软件大类或领域、标准软件系统、标准模块、能力 ID、标准方案根标题路径和完整子树块 ID。

### 4.2 三种业务结果

- `exact`：客户软件系统名称与标准 `product_name` 精确一致，且客户模块与标准 `module_name/capability_name` 精确一致；标题符号、全半角和空白可规范化比较。
- `similar`：模块名称相似、别名可能一致、系统层级不一致，或虽模块同名但上级软件系统未精确匹配；必须人工确认。
- `content_missing`：没有达到候选阈值的能力；必须询问标准方案是否实际包含该系统/模块。人工可指定能力和唯一根标题，也可确认缺口。

精确匹配只有在标准方案根标题唯一且非空时才能自动确认。只匹配模块、上级不一致、根标题缺失或存在多个同名根标题时，均不得自动确认。

候选排序必须把模块名称精确匹配作为第一优先级，其次才是上级系统精确、子树可用、模块相似度和上级相似度；模块同名候选即使根标题暂缺，也不得被前五个字符相似候选截断。低于强相似阈值的候选只作为排查线索展示，必须提示人工先核对标准方案是否真的包含，不能仅凭字符相似推荐。

标准能力名称与标准正文标题存在“整合/集成”、系统或软件后缀等通用措辞差异时，可以使用确定性归一只扩大候选召回，并在同一知识包内解析唯一标题根。此类结果必须标记为相似且要求人工确认，不得提升为精确自动确认；归一后仍不唯一时继续阻断。归一规则不得写入单体医院名称，也不得改变客户范围。

### 4.3 标准方案子树解析

`standard_block_ids` 只作为历史关联和发现线索，不能直接作为装配边界。必须按以下顺序解析：

1. 在已发布、已审核的标准方案块中查找标题路径元素等于模块名称的位置。
2. 要求该位置之前存在等于标准软件系统 `product_name` 的上级标题。
3. 以“截至模块标题”的完整路径作为唯一根路径。
4. 只选择同一标准文档中标题路径以该根路径为前缀的全部块。
5. 按 `source_order` 排序；旧数据缺少该字段时，按来源段落/表格序号回退，再以本地导入顺序稳定兜底。
6. 根路径为零个或多个时阻断自动装配，转人工选择或确认缺口。

人工发现“标准清单模块名”和“标准方案根标题”略有差异时，可以在决定中同时提交能力 ID/候选 ID 与完整 `root_heading_path`。该路径必须在本次匹配锁定的同一知识包内解析为一个且仅一个非空完整子树；不得回退到全库检索，也不得把标题别名写成自动通用同义词。

例如匹配“医院信息基础平台 / 主数据管理”时，必须装入该标题及其“基础数据管理、国家标准管理、标准值域字典、临床术语管理、主数据维护、订阅发布、字典对照分析”等全部后代内容；不得带入“耗材管理系统 / 主数据管理”等其他同名子树。

## 5. 人工复核契约

候选包按客户清单顺序展示，每项包括候选标准系统、模块、名称得分、上级得分、根标题路径、完整块数和阻断原因。

人工决定只使用：

- `confirmed`：确认候选或人工指定的唯一能力子树；
- `confirmed_gap`：确认标准知识当前没有对应内容；
- `rejected`：否决当前候选，仍需后续决定；
- `deferred`：暂缓，仍需后续决定。

决定必须追加保存，记录复核人、时间、说明和决定哈希。装配采用同一运行、同一清单行的最新决定；相似、缺失、否决和暂缓未闭环时不得生成完整装配稿。

## 6. 标准方案完整装配

1. 应用软件建设方案严格按客户清单来源顺序组装，不按公司标准目录或能力 ID 重排。
2. `confirmed` 项按“客户软件大类/领域 → 客户上级路径 → 数据库标准软件系统及中间祖先 → 客户模块”建立标题树，随后写入唯一标准方案根标题下的全部内容块。客户路径和标准路径中的等价重复标题只保留一次，相邻模块共享的上级标题只输出一次；切换客户大类后必须重新输出该大类及其标准上级标题。
3. 标准块 `clean_text` 必须逐字相等，块顺序必须完整一致；禁止改写、摘要、润色、参数替换、禁用词替换、项目语态归一、长度截断或 AI 补段。
4. 数据库标准上级标题由能力 `product_name` 在已确认 `root_heading_path` 中的位置确定，模块以下子标题由标准 `heading_path` 相对根路径确定；不得由 AI 凭名称补写祖先。表格、图片、编号或富文本存在时，优先使用 `content_payload/asset_manifest` 重建。仅有 `plain_text` 的旧数据必须显式记录保真能力限制，不能声称已保留原 Word 版式。
5. `confirmed_gap` 项仍建立客户模块标题，正文只写 `【待补充】`。
6. 装配清单必须保存每项能力 ID、客户领域与层级、客户分组路径、数据库标准祖先路径、最终显示父路径、根路径、块 ID 有序列表、逐块文本哈希和总内容哈希；校验时必须从清单快照和当前知识快照重新计算标题路径。
7. 清单片段绑定 `project_scope.software_construction_list` 和 `overall_design.software_construction_list` 两个语义位置，可在确认目录中分别导入；应用软件方案片段绑定 `overall_design.application_software_solution`。章节编号由确认版目录决定，模块不得硬编码任何单体项目编号。
8. 少量相似或缺失项未闭环时，可用 `--allow-unresolved-preview` 生成不可交付的工作预览：已确认项照常逐字装配，未确认项只建立客户模块标题并写 `【待确认】`。预览必须标记 `preview_only=true`、总状态 `blocked`，且 `validate` 必须返回阻断；它不能替代完整装配稿。已确认缺口仍使用 `【待补充】`。
9. 装配校验必须同时绑定并复算 `manifest_hash`、持久化装配清单、匹配运行、清单快照、知识包内容哈希和完整性证明；校验报告必须返回相同的 `manifest_id/manifest_hash/package_content_hash`。旧校验文件不得替代当前复算。

## 7. 强制门禁

- 客户清单显示快照哈希一致，来源顺序覆盖率 100%；
- 标准方案源段落、标题、短正文和空标题完整导入率均为 100%，结构和内容哈希分别相等；
- 每个客户模块恰有一个有效最新决定；
- 精确自动确认项的软件系统和模块名称均精确匹配；
- 每个已确认项只有一个 `module + product` 根标题；
- 已选块集合等于该根标题的完整子树，缺块和多块均为阻断；
- 装配清单哈希、数据库持久化记录、匹配运行和当前知识快照绑定一致；
- 标准正文逐块文本及哈希相等率 100%；
- 其他同名标题子树混入数为 0；
- 缺口项块数为 0 且出现 `【待补充】`；
- 装配顺序与客户清单来源顺序完全一致；
- 客户软件大类、客户上级路径和数据库标准祖先标题回填率均为 100%，相邻重复上级标题只输出一次；
- 项目数据向共享 PostgreSQL 写入次数为 0。

## 8. 标准命令

```text
python scripts/construction_alignment.py capture-scope <knowledge.sqlite> <project-code> <xlsx-scope.json> --output scope-display-snapshot.json
python scripts/construction_alignment.py match <knowledge.sqlite> <project-code> --package-id <package-id> --output-json construction-match-review.json --output-md construction-match-review.md
python scripts/construction_alignment.py apply-decisions <knowledge.sqlite> <construction-decisions.json> --output construction-decision-apply.json
python scripts/construction_alignment.py assemble <knowledge.sqlite> <project-code> <match-run-id> --output-json construction-assembly-manifest.json --output-scope-md software-construction-list.md --output-solution-md application-software-solution.md --output-md construction-assembly.md
python scripts/construction_alignment.py assemble <knowledge.sqlite> <project-code> <match-run-id> --allow-unresolved-preview --output-json construction-assembly-preview.json --output-md construction-assembly-preview.md
python scripts/construction_alignment.py validate <knowledge.sqlite> <construction-assembly-manifest.json> --output construction-assembly-validation.json
```
