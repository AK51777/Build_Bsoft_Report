---
name: build-medical-it-feasibility-report
description: Create evidence-based Chinese government-investment medical informationization feasibility reports from mixed project materials and reusable company knowledge. Use for medical IT feasibility studies, construction-list normalization, cleaned knowledge corpora, standard-solution reuse, split local SQLite or shared PostgreSQL knowledge packages, project SQLite snapshot synchronization, cross-project knowledge reuse, fact and policy ledgers, scope-capability mapping, dynamic construction chapters, complete evidence-bound drafts, validation, or style-preserving Word delivery. Also use for any single stage when prior artifacts already exist.
---

# 医疗信息化可研生成

## 目标

把可研编制作为一套可追溯的项目文档生产流程，不把任务简化为一次性长文生成。将每项关键事实、建设范围、投资分项、绩效指标和效益结论追溯到来源和当前确认状态。

面向政府投资或政府管理要求下的医疗信息化可行性研究报告。不得自动替代院方确认、投资决策、采购决策或专业评审。

## 开始前

**先选择任务边界。** 用户只提供建设清单、要求清单对照/标准方案拼接/建设内容Word时，默认进入 `construction_only` 专项流程，先读 [清单到Word专项流程](references/construction-only-workflow.md)。该模式只交付建设清单和建设内容，不执行十阶段完整可研流程，不以投资、政策、效益等无关材料作为进入条件。先在对话中展示待确认目录归属和匹配例外；用户确认后持续完成装配、校验、内置格式Word生成及渲染检查，不能停在MD。已有明确确认直接复用。下面完整项目初始化、阶段产物和正式报批门禁仅按任务适用性执行。

1. 读取 `references/knowledge-connection-rules.md` 和 `references/local-knowledge-package-rules.md`。若用户级本地知识配置或 `MEDICAL_REPORT_LOCAL_KB_CONFIG` 存在，先运行 `local_knowledge_packages.py status`；两个包有效时优先用本地包同步项目快照。配置存在但必需包缺失或损坏时阻断，不得静默换用远程数据。
2. 本地路径固定使用 `standard-knowledge.sqlite` 和 `policy-knowledge.sqlite`。新设备先用 `local_knowledge_bootstrap.py` 校验发布方提供的两个 SHA-256 并一次配置；统一入口 `run_project_pipeline.py` 自动发现本地配置、原子同步新项目并以 `snapshot_required` 生成。已有项目保留已固定快照；显式远程参数和项目已有离线/禁用/快照模式不被自动路由覆盖，优先级见本地知识规则。独立阶段可使用 `sync-project`。远程只读 MCP 继续保留；用户显式选择远程或未配置本地包时，若环境提供`knowledge_access_status`，检查激活状态并按需调用`activate_knowledge_access`或`knowledge_service_status`，不得回显或记录激活码。否则解析`knowledge.mode`和用户级profile；不得要求每个新项目重复填写数据库主机、端口、库名和用户。
3. `server_required`必须先运行知识诊断并从发布运行视图同步。远程只读路径先用`knowledge_query`明确选择package/catalog ID，再运行`sync_remote_knowledge_snapshot.py`写入项目SQLite并验证快照；不得在正文阶段直接以远程查询结果替代项目快照。`snapshot_required`必须验证本地快照；`offline_pack`必须导入受审离线包；`disabled`必须明确记录未使用共享知识。任一知识门禁失败时在S0阻断。
4. 同步或验证后报告 profile、连接状态、使用来源、package/catalog IDs、发布 ID、版本、内容哈希、同步时间、权限范围及语料、能力、目录、正式政策条款和编制标准数量。数据量为零时禁止声称已经使用对应知识。
   当前部署的共享政策连接对象是数据库 `hrr_feedback`、账号 `hrr_feedback`、schema `medical_report_kb`；密码只从 profile 指定的环境变量读取。`policy_catalog_entry` 是候选目录数据表，`medical_report_reader` 若存在也只是可选数据库角色名，不得把它解释为数据表或当前登录账号。不得把连接对象写死进报告正文。
5. 正文阶段只从已验证的项目级 `数据包/数据库/knowledge.sqlite` 读取知识。共享本地文件和远程查询都只负责同步，不得成为正文阶段的旁路。已有项目固定同步时的发布 ID 与内容哈希；共享包更新不得静默改变在制项目。
6. 读取用户指定的全部材料和已有中间产物，不依赖当前对话记忆；识别当前阶段并复用已经确认的产物。
7. 在项目目录内建立或复用独立工作台；不得搬移、覆盖或修改原始材料。
8. 锁定正式项目名称、文档类型、范围最高依据、核心验收目标、未知事项处理方式和目标交付格式。缺失时先形成任务书和少量实质性问题，不直接声称形成正式报告。
9. 存在公司标准方案和标准清单时，先构建位于项目外部的受审知识包；不得把公司原文、客户材料或知识包提交到公开 Skill 仓库。

复制 `assets/project-workbench-template/` 作为新项目工作台起点。项目已有目录结构时，只复制需要的模板，不强制改名或搬迁。

专项流程优先运行 `scripts/construction_workflow.py` 的 `prepare-review → confirm-and-generate → 逐页视觉复核`；中断用 `resume`，状态用 `status`，参数见专项规则。只有一个主核对入口，确认后默认DOCX；原始行与展开项分别计数，重复映射提前展示。专项只验证标准知识包，不检查无关政策包。

## 按需读取规则

- 执行完整流程或判断阶段门禁时，读取 `references/workflow.md`。
- 建立文件、表格和编号时，读取 `references/artifact-schemas.md`。
- 提取、确认或引用事实时，读取 `references/source-and-fact-rules.md`。
- 生成事实核验包、处理隐性知识或写入用户确认时，同时读取 `references/fact-verification-rules.md`。
- 检索、入库、排序或引用政策时，读取 `references/policy-evidence-rules.md`。
- 识别地方编制标准、提取Word格式画像或处理缩进时，读取 `references/document-profile-rules.md`。
- 建库、迁移或解释数据对象时，读取 `references/knowledge-base-schema.md`。
- 解析 profile、诊断服务器、选择知识包、同步或验证快照时，读取 `references/knowledge-connection-rules.md`。
- 构建、安装、查询、更新或向项目同步拆分本地 SQLite 知识包时，同时读取 `references/local-knowledge-package-rules.md`；不得把目录候选当成正式政策证据，也不得静默升级在制项目。
- 通过团队远程只读MCP激活、检索或同步知识时，同时读取`references/remote-readonly-knowledge-mcp-rules.md`；不得向用户索取数据库密码，也不得把远程服务用于传输客户项目材料。
- 处理客户清单、公司能力清单或投资对应关系时，读取 `references/scope-mapping-rules.md`。
- 从公司标准 Word 构建、发布或同步标准知识包时，读取 `references/reference-corpus-cleaning-rules.md` 的标准方案专用规则；标准方案执行完整导入，不得套用参考语料的最短字数过滤或去重规则。
- 对照客户与公司模块清单、发起相似/缺失核对或完整装配标准建设方案时，读取 `references/construction-alignment-rules.md`；该链路不得交给通用语料改写器处理。
- 需要让其他 AI 通过本机 MCP 只调用清单对照、人工确认装配和 Word 生成时，读取 `references/mcp-service-rules.md`；MCP 只作为现有确定性脚本的本机适配层，不得在接口层改写标准正文或绕过门禁。
- 使用外地可研、历史方案或厂商方案时，读取 `references/reference-reuse-rules.md`。
- 清洗参考可研、区分标准方案与可研论证语料或发布参考语料时，读取 `references/reference-corpus-cleaning-rules.md`。
- 将高质量方案拆为语料块、映射产品能力或形成章节组合计划时，读取 `references/corpus-reuse-rules.md`。
- 形成目录、任务包或逐章编写时，读取 `references/chapter-task-rules.md` 和 `references/medical-it-feasibility-writing.md`。
- 按章节完善生成规则、建立脱敏问题卡或执行增量回归时，读取 `references/chapter-development-rules.md`。
- 复核正文或交付稿时，读取 `references/validation-rules.md`。
- 合并或排版 Word 时，读取 `references/word-delivery-rules.md`，并遵守当前环境的文档处理技能和渲染验证要求。

不要一次加载全部参考文件。只加载当前阶段必需的规则。

## 十阶段主流程

按顺序执行以下阶段。用户明确要求单阶段工作，或前序产物已经存在并通过门禁时，可以从对应阶段开始。

0. 任务定义：形成项目任务书，明确名称、文档类型、范围依据、验收目标、参考限制、未知事项处理和版本规则。
1. 资料与事实：登记来源，提取原子事实，登记单位、时点、位置、状态、冲突、缺失和确认问题。
1P. 政策证据：先从已确认项目事实和 `status='confirmed'` 的本期建设范围建立地域、机构、文种、投资制度和建设主题画像，再从共享政策库召回候选；不得用医院名称、参考稿、待确认范围或模型记忆反推政策。部门目录的全部记录可先进入 `policy_source_capture` 原文采集暂存层；缺链接、失效链接、反爬响应、附件待提取、草案、内部材料、共识和会议资料必须保留独立状态，采集正文一律保持 `unverified/pending`，不得自动进入正式运行视图。智慧医院项目按“政策类依据、行业标准依据、安全类标准依据、投资估算编制依据”四组生成，并执行 `smart_hospital_basis_profile_v1.json` 的16/20/16/5硬下限；地方政策背景执行国家8、省/自治区2、市/项目地区1的硬下限。政策背景只能从同一政策类依据选择，保持精确同序，按国家—省/自治区—市/项目地区展开。目录候选与正式条款匹配运行都必须绑定完整输入签名，属地、确认事实/范围、主题、画像、目录或正式政策语料任一变化即重匹配。标题级 `policy_catalog_entry`、采集暂存正文和固定基准清单只能进入待核验工作稿；未经 `policy_document`、已发布核验条款、官方来源、效力、用途许可及用户确认不得进入交付稿。
2. 清单与范围：标准化客户建设清单，区分建设方式和费用类型，映射公司能力但不改变客户边界。
3. 参考方案：按章节评估结构和写法复用，形成允许内容、禁止内容和残留扫描词表。
4. 贯通矩阵与目录：建立“问题—需求—建设—投资—指标—效益”链条，再形成三级主目录；建设内容按客户范围、确认能力和受审语料动态展开到四至七级。
5. 章节任务拆解：先判断章节适用性，再为适用章节分配目的、问题、事实ID、范围ID、素材、限制、表格、篇幅和完成条件。
6. 内容生成：按章节任务包生成证据约束初稿，政策依据、标准规范、建设范围、能力和语料均绑定来源；政策材料签名同时覆盖实际分组、交付资格、阻断项和诊断明细，签名一致不能替代可交付性检查。逐章保存并运行章节校验，只有校验通过且哈希一致的版本才能采纳；1.2.1和2.1.1的正式可见内容必须与当前材料完全闭合，不得追加“参考采用”条目、未绑定政策陈述或伪装标题。
7. 多维校验：检查事实、范围、投资、指标、政策、逻辑、跨章一致性、语言、参考污染和占位，并以真人可研为基准检查章节完整度、正文规模、表格、重复率和模板化表达。
8. 文档工程与交付：使用已确认的脱敏模板合并 Word，把 Markdown 标记转换为真实 Word 标题样式，处理目录、编号、横向节、页码和元数据，执行独立交付审计并完成逐页渲染复核。
9. 复盘沉淀：只沉淀通用规则、脚本和脱敏模板，不把客户事实或个人信息打包进 Skill。

每一阶段都必须有进入条件、标准产物、检查和门禁。详细规则见 `references/workflow.md`。

阶段0同时产生文档类型和格式画像候选；阶段1同时产生推断台账、事实核验包和确认记录；阶段5开始必须用章节组合计划约束事实、政策、范围、语料和篇幅。AI推荐不等于用户确认。

单章生成或规则修订默认使用 `--chapter`，只读取、生成和校验目标章节；章节通过后再运行同章节类型回归。只有公共红线、数据库结构、目录签名、全文组装发生变化或准备发布时才运行全量回归和Word链路。

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

`build_report_docx.py` 默认只生成带“工作稿（未通过正式交付门禁）”标识的工作稿。正式 `delivery` 模式必须同时绑定当前项目数据库、最新通过的交付校验、当前数据库组装内容和已确认 Word 模板；生成后还必须通过独立制品审计与哈希绑定的逐页渲染复核。不得直接调用转换脚本绕过数据库链。

## 确定性脚本

优先使用以下脚本完成机械工作，避免每次重写临时代码：

```text
python scripts/init_project_workbench.py <target-dir> --project-code <project-code> [--official-name <name>] [--owner-name <name>]
python scripts/check_dependencies.py --knowledge-mode <server_required|snapshot_required|offline_pack|disabled> --output dependency-check.json
python scripts/knowledge_doctor.py --project-root <target-dir> --profile default --json --output knowledge-doctor.json
python scripts/provision_postgres_runtime_reader.py --database <database> --user <admin-user> --schema <schema> --output reader-plan.json  # 默认 dry-run；apply 需管理员明确批准
python scripts/run_project_pipeline.py <target-dir> --project-code <project-code> [--official-name <name>] [--owner-name <name>] [--knowledge-profile default] [--knowledge-mode <mode>] [--standard-knowledge-pack <reviewed-pack.json>] [--word-template <confirmed-template.docx>] --output pipeline-result.json
python scripts/query_local_knowledge.py <target-dir>/数据包/数据库/knowledge.sqlite corpus --package-id <package-id> --section-role construction_content --search <keyword> --output local-knowledge-query.json
python scripts/local_knowledge_packages.py build-standard <reviewed-standard-pack.json> <candidate-standard-knowledge.sqlite> --release-version <version>
python scripts/local_knowledge_packages.py build-policy <candidate-policy-knowledge.sqlite> --release-version <version> [--catalog <policy-catalog.json>] [--verified-policies <verified-policies.json>] [--document-standards <document-standards.json>]
python scripts/local_knowledge_packages.py status [--config <medical-report-local-kb.json>]
python scripts/local_knowledge_packages.py query <corpus|capability|policy-catalog|policy-clause|document-standard> [--config <medical-report-local-kb.json>] [filters]
python scripts/local_knowledge_packages.py install <standard|policy> <candidate.sqlite> --expected-sha256 <sha256> [--config <medical-report-local-kb.json>]
python scripts/local_knowledge_packages.py sync-project <target-dir>/数据包/数据库/knowledge.sqlite <project-code> [--config <medical-report-local-kb.json>]
python scripts/inventory_sources.py <paths...> --output 01-资料清单.json
python scripts/classify_source_roles.py source-inventory.json [--overrides source-role-overrides.json] --output source-role-register.json
python scripts/extract_docx_structure.py <report.docx> --output docx-structure.json
python scripts/extract_clean_document_blocks.py <source.docx|source.md|source.txt> --project-code <project-code> --output clean-document-blocks.json
python scripts/build_reference_corpus_workpack.py clean-document-blocks.json --project-type smart_hospital --output-json reference-corpus-workpack.json --output-md reference-corpus-workpack.md
python scripts/build_reference_corpus_review_pack.py reference-corpus-workpack.json --semantic-section overall_objective_scope --output-json reference-review.json --output-md reference-review.md
python scripts/build_reference_knowledge_pack.py reference-corpus-workpack.json reference-review.json --version <version> --output <private-reference-pack.json>
python scripts/ingest_clean_documents_sqlite.py <knowledge.sqlite> <clean-document-blocks.json> --output sqlite-ingest-result.json
python scripts/build_candidate_fact_workpack.py <knowledge.sqlite> <project-code> --output candidate-fact-workpack.json
python scripts/build_reference_reuse_workpack.py <knowledge.sqlite> <project-code> --output-json reference-reuse-workpack.json --output-md 07-参考方案复用地图.md
MEDICAL_FEASIBILITY_DB_PASSWORD=<password> python scripts/ingest_clean_documents_postgres.py clean-document-blocks.json --host 127.0.0.1 --port <tunnel-port> --database <database> --user <user> --output postgres-ingest-result.json
python scripts/extract_xlsx_scope.py <scope.xlsx> --output xlsx-scope.json
python scripts/ingest_scope_items_sqlite.py <knowledge.sqlite> <xlsx-scope.json> --project-code <project-code> --output scope-ingest-result.json
python scripts/construction_alignment.py capture-scope <knowledge.sqlite> <project-code> <xlsx-scope.json> --output scope-display-snapshot.json
python scripts/construction_alignment.py match <knowledge.sqlite> <project-code> --package-id <package-id> --output-json construction-match-review.json --output-md construction-match-review.md
python scripts/construction_alignment.py apply-decisions <knowledge.sqlite> <construction-decisions.json> --output construction-decision-apply.json
python scripts/construction_alignment.py assemble <knowledge.sqlite> <project-code> <match-run-id> --output-json construction-assembly-manifest.json --output-scope-md software-construction-list.md --output-solution-md application-software-solution.md --output-md construction-assembly.md
python scripts/construction_alignment.py validate <knowledge.sqlite> <construction-assembly-manifest.json> --output construction-assembly-validation.json
python scripts/medical_report_mcp_server.py --config <local-medical-report-mcp.json> --check-config  # 去掉--check-config后作为stdio MCP启动；规则见references/mcp-service-rules.md
python scripts/sync_remote_knowledge_snapshot.py <knowledge.sqlite> <project-code> --config <remote-knowledge-bridge.json> --package-id <package-id> [--catalog-id <catalog-id>] --output remote-snapshot-sync.json
python scripts/build_scope_baseline.py <knowledge.sqlite> <project-code> --output scope-baseline.json
python scripts/apply_scope_item_decisions.py <knowledge.sqlite> <scope-decisions.json> --output scope-decision-apply.json
python scripts/confirm_scope_baseline.py <knowledge.sqlite> <project-code> <baseline-id> --confirmed-by <name> --confirmed-at <time> --output scope-baseline-confirmation.json
python scripts/build_traceability_matrix.py <knowledge.sqlite> <project-code> --links traceability-links.json --output-json traceability-matrix.json --output-csv traceability-matrix.csv
python scripts/ingest_product_capabilities.py <knowledge.sqlite> <product-capabilities.json> --output capability-ingest-result.json
python scripts/map_scope_capabilities.py <knowledge.sqlite> <project-code> --output scope-capability-map.json
python scripts/build_standard_knowledge_pack.py <company-standard.docx> <company-scope.xlsx> --output <local-private-pack.json>
python scripts/import_standard_knowledge_pack.py <knowledge.sqlite> <local-private-standard-or-reference-pack.json> --output knowledge-pack-import.json
python scripts/build_policy_catalog.py <department-policy-index.xlsx> --output <reviewed-policy-catalog.json>
python scripts/import_policy_catalog_sqlite.py <knowledge.sqlite> <reviewed-policy-catalog.json> --output policy-catalog-import.json
python scripts/match_policy_catalog_candidates.py <knowledge.sqlite> <project-code> --topic <topic> --output-json policy-catalog-candidates.json --output-md policy-catalog-candidates.md
MEDICAL_FEASIBILITY_DB_PASSWORD=<password> python scripts/init_postgres_knowledge_db.py --host 127.0.0.1 --port <tunnel-port> --database <database> --user <user> --output postgres-init.json
MEDICAL_FEASIBILITY_DB_PASSWORD=<password> python scripts/audit_postgres_schema.py --host 127.0.0.1 --port <tunnel-port> --database <database> --user <user> --output postgres-schema-audit.json
MEDICAL_FEASIBILITY_DB_PASSWORD=<password> python scripts/import_standard_knowledge_pack_postgres.py <local-private-pack.json> --publish --host 127.0.0.1 --port <tunnel-port> --database <database> --user <user> --output postgres-pack-import.json
MEDICAL_FEASIBILITY_DB_PASSWORD=<password> python scripts/import_policy_catalog_postgres.py <reviewed-policy-catalog.json> --publish --host 127.0.0.1 --port <tunnel-port> --database <database> --user <user> --output policy-catalog-import.json
MEDICAL_FEASIBILITY_DB_PASSWORD=<password> python scripts/capture_policy_sources_postgres.py --host 127.0.0.1 --port <tunnel-port> --database <database> --user <user> --output policy-source-capture-plan.json
MEDICAL_FEASIBILITY_DB_PASSWORD=<password> python scripts/capture_policy_sources_postgres.py --apply --confirm-database <database> --archive-dir <private-policy-archive> --host 127.0.0.1 --port <tunnel-port> --database <database> --user <user> --output policy-source-capture-result.json
MEDICAL_FEASIBILITY_DB_PASSWORD=<password> python scripts/import_verified_policies_postgres.py <verified-policy-documents.json> --publish --host 127.0.0.1 --port <tunnel-port> --database <database> --user <user> --output verified-policy-import.json
MEDICAL_FEASIBILITY_DB_PASSWORD=<password> python scripts/sync_postgres_knowledge_snapshot.py <knowledge.sqlite> <project-code> --package-id <package-id> --catalog-id <catalog-id> --policy-topic <topic> --host 127.0.0.1 --port <tunnel-port> --database <database> --user <user> --output snapshot-sync.json
MEDICAL_FEASIBILITY_DB_PASSWORD=<password> python scripts/query_postgres_knowledge.py package --host 127.0.0.1 --port <tunnel-port> --database <database> --user <user> --output published-packages.json
python scripts/apply_scope_capability_decisions.py <knowledge.sqlite> <mapping-decisions.json> --output mapping-apply-result.json
python scripts/build_section_composition_plan.py <knowledge.sqlite> <project-code> [--chapter <chapter-code>] --output section-composition-plan.json
python scripts/build_report_outline.py <knowledge.sqlite> <project-code> --output-json outline-candidate.json --output-md outline-candidate.md
python scripts/confirm_report_outline.py <knowledge.sqlite> <project-code> <outline-version-id> --confirmed-by <name> [--candidate-json outline-reviewed.json] --output-json outline-confirmation.json
python scripts/export_section_task_packages.py <knowledge.sqlite> <project-code> <10-章节任务包> [--chapter <chapter-code>] --output task-package-export.json
python scripts/build_policy_section_material.py <knowledge.sqlite> <project-code> --mode working --output-json policy-section-material.json --output-md policy-section-material.md
python scripts/build_evidence_bound_initial_drafts.py <knowledge.sqlite> <project-code> <10-章节任务包> <11-正文工作稿> [--chapter <chapter-code>] --output evidence-bound-drafts.json
python scripts/save_section_draft.py <knowledge.sqlite> <project-code> <chapter-code> <draft.md> <chapter-package.json> --model <model> --prompt-version <version> --output draft-save-result.json
python scripts/validate_section_draft.py <knowledge.sqlite> <project-code> <chapter-code> <version-no> --output section-validation.json
python scripts/manage_section_draft.py <knowledge.sqlite> <project-code> <chapter-code> <version-no> <adopt|discard|restore> --output draft-action-result.json
python scripts/run_chapter_regression.py <chapter-code> --tier <chapter|family|full> [--dry-run] --output chapter-regression.json
python scripts/assemble_report_markdown.py <knowledge.sqlite> <project-code> --mode <working|delivery> --output report.md --summary assembly-summary.json
python scripts/validate_full_report.py <knowledge.sqlite> <project-code> --mode <working|delivery> --residual-terms <参考残留词表.txt> --output validation-result.json
python scripts/build_report_docx.py <report.md> <report.docx> --project-name <name> --owner-name <owner> --mode delivery --database <knowledge.sqlite> --project-code <project-code> --template <confirmed-template.docx> --summary docx-build-summary.json
python scripts/audit_delivery_artifact.py <report.docx> --build-summary docx-build-summary.json --database <knowledge.sqlite> --project-code <project-code> --output delivery-artifact-audit.json
python scripts/benchmark_report_quality.py <report.docx> --reference <human-benchmark.docx> --database <knowledge.sqlite> --project-code <project-code> --output benchmark-report.json
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
