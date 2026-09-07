# 本地拆分知识包规则

## 适用边界

本规则用于不依赖授权码和在线服务的团队知识调用。远程只读 MCP 继续保留，作为显式选择的远程同步路径；本地知识包不是对远程 MCP 的删除或协议替换。

本地共享层只包含两个受审 SQLite 文件：

- `standard-knowledge.sqlite`：智慧医院标准方案、标准能力和能力—语料关系；按月或新版本发布事件更新。
- `policy-knowledge.sqlite`：政策目录、已核验政策条款和报告编制标准；按周或政策发布、废止、修订事件更新。

客户事实、客户原始材料、项目确认记录和项目正文不得写入这两个共享文件。项目生成仍只消费项目自身的 `数据包/数据库/knowledge.sqlite` 快照。

## 配置与路由

默认配置是用户级 `~/.codex/config/medical-report-local-kb.json`，也可用环境变量 `MEDICAL_REPORT_LOCAL_KB_CONFIG` 或命令行 `--config` 指定。配置格式见 `assets/knowledge-base/local-medical-report-kb.example.json`。

新设备把两个受审文件放在固定的项目外目录，以发布方从可信渠道提供的 SHA-256 调用 `local_knowledge_bootstrap.py`，一次生成用户级配置。不能把收到的文件自行计算的摘要当成独立信任证明。配置仅引用文件，不移动原包、不覆盖已有不同配置、不写令牌；相同配置重复运行幂等。

统一入口用 `--local-knowledge-config` 显式选择该配置；自动发现按环境变量、用户默认文件排序。显式远程参数或显式 `--knowledge-mode` 不触发自动本地发现；项目已设为 `disabled/offline_pack/snapshot_required` 时亦不触发。初始化默认的 `server_required` 在存在本地配置时优先走本地，项目可设置 `knowledge.prefer_local_packages=false` 关闭自动发现。显式本地参数与冲突的远程/禁用参数同时出现时报错，不猜测意图。

当本地配置存在时，调用顺序固定为：

1. 运行 `local_knowledge_packages.py status` 校验两个文件、版本、内容哈希、数量和更新时效。
2. 需要预览时只读查询共享文件；不得在查询时迁移或改写共享文件。
3. 新项目由统一入口自动执行本地准备（独立调用可用 `sync-project`），把选定发布版本完整同步并固定到项目 SQLite。
4. 后续流水线使用 `snapshot_required`，正文阶段不得直接读取共享文件或远程查询结果。

本地配置缺失时，继续遵循 `knowledge-connection-rules.md` 的现有 `server_required`、`snapshot_required`、`offline_pack` 或 `disabled` 路径。配置存在但必需知识包缺失、损坏或类型错误时应阻断，不得静默切换到远程 MCP；如需远程更新或同步，必须显式选择远程路径并记录来源。

## 构建和发布门禁

每个文件必须只有一个 `local_knowledge_release` 发布记录，并保存构建所用的规范化原始载荷、载荷哈希、发布版本、发布时间、权限范围、数量和更新周期。发布文件必须满足：

- `PRAGMA quick_check` 为 `ok`；发布内容哈希与内嵌载荷一致；数据库实际数量与发布记录一致。
- 外键检查通过；将内嵌载荷重新导入隔离临时库，对消费用业务表逐行规范化比较正文、能力、关系顺序/根路径/逐字资格、权限、政策和编制标准。不再仅依赖条数或载荷哈希，改正文而条数不变也必须拒绝。只忽略无业务意义的自动导入时间；显式证据日期仍比较。检查只读，不迁移共享文件，兼容现有1.0发布包。该一致性校验不能替代发布方摘要的真实性验证。
- 标准包通过标准方案完整导入门禁，覆盖状态为 `pass`，且至少有一个已批准语料块和一个已批准能力。
- 政策包至少包含一个可用的政策目录记录、已核验政策条款或已核验编制标准。
- 目录候选不等于正式政策证据。正式政策条款为零时必须返回 `formal_policy_clauses_empty` 警告，政策正式章节门禁继续阻断。
- 仅 `verification_status='verified'` 的正式政策条款和文档标准可进入相应正式消费链。

不得在 Skill 仓库提交真实公司标准包、政策库文件、客户材料、数据库密码或访问令牌。仓库只保存脚本、规则、配置模板和合成测试。

## 更新机制

当前体量统一采用完整文件发布，不实现增量 SQL 补丁：

1. 在新路径构建候选文件，构建过程不得覆盖当前包。
2. 独立校验候选文件并发布 SHA-256 摘要。
3. 安装时必须同时提供候选文件和预期 SHA-256。
4. 安装器复制到同目录临时文件后再次校验，再原子替换目标；旧文件保留为 `*.previous.sqlite`。替换失败必须回滚。
5. 标准包按月检查，或在标准方案正式发布新版本时立即更新；政策包每周检查，或在政策新发、修订、废止、效力变化、属地适用变化时立即更新。

已有项目默认固定创建时同步的发布 ID 与内容哈希。共享文件更新不得悄悄改变在制项目；发现项目已有不同发布快照时，`sync-project` 必须拒绝。需要升级时应另行执行受审 rebase，重新匹配范围与政策并重跑章节和交付门禁。

统一入口发现已有快照时只验证并保留，不调用同步器覆盖；返回 `kept_project_snapshot`。自动项目 rebase 尚未实现，升级需单独设计和确认，不能删除快照绕过保护。

项目同步中的标准语料、能力关系、目录、正式条款、编制标准、快照登记和最终权限/内容校验共用一个事务；任一阶段失败全部回滚，可修复原因后重试。同步前项目库必须已初始化到当前 schema；旧 schema 先备份并单独执行项目迁移，同步事务不夹带迁移。迁移及项目初始化不属于该知识导入回滚范围。

## 调用命令

```text
python scripts/local_knowledge_bootstrap.py <package-directory> --standard-sha256 <trusted-sha256> --policy-sha256 <trusted-sha256> [--config <medical-report-local-kb.json>]
python scripts/run_project_pipeline.py <project-directory> --project-code <project-code> [--local-knowledge-config <medical-report-local-kb.json>]
python scripts/local_knowledge_packages.py build-standard <reviewed-standard-pack.json> <candidate-standard-knowledge.sqlite> --release-version <version>
python scripts/local_knowledge_packages.py build-policy <candidate-policy-knowledge.sqlite> --release-version <version> [--catalog <policy-catalog.json>] [--verified-policies <verified-policies.json>] [--document-standards <document-standards.json>]
python scripts/local_knowledge_packages.py status [--config <medical-report-local-kb.json>]
python scripts/local_knowledge_packages.py query <corpus|capability|policy-catalog|policy-clause|document-standard> [filters] [--config <medical-report-local-kb.json>]
python scripts/local_knowledge_packages.py install <standard|policy> <candidate.sqlite> --expected-sha256 <sha256> [--config <medical-report-local-kb.json>]
python scripts/local_knowledge_packages.py sync-project <knowledge.sqlite> <project-code> [--config <medical-report-local-kb.json>]
```

输出必须报告知识来源、发布 ID、版本、内容哈希、文件哈希、数量、更新时间、时效状态和警告；数量为零时不得声称已经调用对应类型知识。
