# 共享知识连接与快照规则

## 1. 固定架构

运行链固定为：`用户级 profile → PostgreSQL 已发布运行视图 → 项目 knowledge.sqlite 快照 → 建设清单映射 → 章节任务包 → 正文`。

- PostgreSQL 只保存清洗、审核、发布后的文本块、产品能力、政策目录和元数据，不保存项目原始 Word、PDF、Excel BLOB。
- 运行时账户只能读取 `runtime_*` 发布视图；导入、审核、发布和迁移使用单独管理凭据。
- 写作阶段不得持续查询 PostgreSQL，也不得读取草稿表或审核中数据。
- 密码不得写入 Git、项目配置、profile、日志或诊断 JSON，只能从 `password_env` 指定的环境变量读取。

## 2. 用户级 profile

默认配置路径为 `%USERPROFILE%/.codex/config/medical-report-kb.json`，可用 `MEDICAL_FEASIBILITY_KB_CONFIG` 覆盖。模板见 `assets/knowledge-base/medical-report-kb.example.json`。

配置路径优先级：

1. 命令行 `--knowledge-config` 或诊断脚本的 `--config`；
2. `MEDICAL_FEASIBILITY_KB_CONFIG`；
3. 默认用户级配置路径；
4. 旧项目的内联 `knowledge.server`，仅用于兼容迁移。

profile 选择优先级：

1. 命令行 `--knowledge-profile`；
2. 项目配置 `knowledge.profile`；
3. 用户级配置 `default_profile`。

项目配置只保存 profile 引用、知识模式、选择条件及最终 package/catalog IDs，不复制连接密码。旧项目初始化时只补充缺失字段，不覆盖已有 `catalog_ids` 或其他用户设置。

## 3. 四种知识模式

| 模式 | 进入条件 | 失败行为 |
| --- | --- | --- |
| `server_required` | profile、密码环境变量、网络、只读权限、迁移、运行视图、唯一候选和非空同步全部通过 | S0 阻断，不生成误导性正式报告，不使用旧快照降级 |
| `snapshot_required` | 项目 SQLite 中存在通过哈希、数量、ID、状态和权限校验的快照 | S0 阻断；仅在 `allow_stale_cache=true` 时接受有效 stale 快照 |
| `offline_pack` | 显式提供仓库外受审标准知识包；导入后语料块和能力均大于零 | S0 阻断，不以 seed 冒充公司知识 |
| `disabled` | 用户或项目明确不使用共享知识 | 记录 disabled；正文不得声称使用公司标准知识 |

有效旧快照必须同时满足：同步完成、未损坏、package/catalog ID 非空、单项及聚合哈希正确、知识包语料和能力非零、政策目录记录非零（若选择目录）、permission scope 匹配。`allow_stale_cache` 不是绕过完整性检查的开关。

## 4. 诊断与退出码

先运行：

```powershell
python scripts/knowledge_doctor.py --project-root "D:\path\new-project" --profile default --json
```

诊断依次检查 profile、密码环境变量、TCP、PostgreSQL、schema migration、发布运行视图、SELECT-only 权限和可见的非空发布内容。不得输出密码，不得自动创建 SSH 隧道，不得用只读账户执行迁移。

稳定退出码：`0` 通过，`10` 配置错误，`11` 缺少密码环境变量，`12` 网络不可达，`13` 数据库连接失败，`14` schema/迁移/视图错误，`15` 权限错误，`16` 发布内容为空。

### 运行时只读账户

运行时 profile 不得使用数据库 owner、超级用户或导入/发布账户。优先使用默认 dry-run 的确定性配置器生成计划；没有 `--apply` 时脚本不连接数据库、不要求密码，也不修改任何状态：

```powershell
python scripts/provision_postgres_runtime_reader.py `
  --host 127.0.0.1 --port 15432 `
  --database <数据库名> --user <管理员账号> `
  --schema medical_report_kb --output reader-plan.json
```

审查计划后，分别在管理员和运行时密码环境变量中设置凭据。只有同时给出 `--apply` 和与 `--database` 完全一致的 `--confirm-database` 才会执行；脚本拒绝把当前管理员、数据库 owner 或 schema owner 改造成只读角色，拒绝带角色继承关系或自有数据库对象的既有角色，并在同一事务中验证全部运行视图可读、全部基础表无有效写权限：

```powershell
$env:MEDICAL_FEASIBILITY_ADMIN_DB_PASSWORD = "<管理员密码>"
$env:MEDICAL_FEASIBILITY_DB_PASSWORD = "<新只读账号密码>"

python scripts/provision_postgres_runtime_reader.py `
  --host 127.0.0.1 --port 15432 `
  --database <数据库名> --user <管理员账号> `
  --schema medical_report_kb `
  --apply --confirm-database <数据库名> `
  --output reader-apply-result.json
```

两个密码都只从环境变量读取，输出只记录环境变量名称和是否存在，不包含密码值。若现场不允许使用该脚本，由数据库管理员登录目标数据库后，可按以下最小权限模板手工创建或修复专用账户。密码必须通过 `psql` 的 `\password medical_report_reader` 交互设置，不得写入 SQL 文件、shell 历史或项目配置。以下示例的数据库名和 schema 应按现场环境复核：

```sql
CREATE ROLE medical_report_reader LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION;
ALTER ROLE medical_report_reader SET default_transaction_read_only = on;

GRANT CONNECT ON DATABASE medical_report_knowledge TO medical_report_reader;
GRANT USAGE ON SCHEMA medical_report_kb TO medical_report_reader;
REVOKE ALL ON ALL TABLES IN SCHEMA medical_report_kb FROM medical_report_reader;
REVOKE ALL ON ALL SEQUENCES IN SCHEMA medical_report_kb FROM medical_report_reader;

GRANT SELECT ON medical_report_kb.schema_migration TO medical_report_reader;
GRANT SELECT ON
  medical_report_kb.runtime_knowledge_package,
  medical_report_kb.runtime_package_source,
  medical_report_kb.runtime_corpus_document,
  medical_report_kb.runtime_corpus_block,
  medical_report_kb.runtime_product_capability,
  medical_report_kb.runtime_capability_block,
  medical_report_kb.runtime_policy_catalog,
  medical_report_kb.runtime_policy_catalog_entry,
  medical_report_kb.runtime_policy_clause
TO medical_report_reader;
```

若账户已存在，不重复执行 `CREATE ROLE`，只复核属性、重设密码并执行后续收权/授权语句。授权后用该账户运行 `knowledge_doctor.py`；只有 `READ_ONLY_ACCOUNT`、`RUNTIME_VIEWS`、`PERMISSION_SCOPE` 和 `PUBLISHED_CONTENT` 均通过时才可同步。新增运行时视图后由管理员显式追加 `GRANT SELECT`，不对草稿表、审核表或导入表设置默认全量授权。

## 5. 知识包和政策目录选择

1. 优先验证配置中的 `package_ids` 和 `catalog_ids`。
2. 未配置时，用 document type、project type、permission scope、版本、章节角色、模块、主题和 jurisdiction 硬过滤已发布候选。
3. 唯一候选才可自动选择；没有候选时阻断；多个候选时列出 ID、名称、版本、发布时间、适用类型、权限和内容哈希后阻断。
4. 不得默认选择最新知识包，也不得默认同步全部政策目录。
5. 最终 ID 和哈希必须写入项目配置、运行清单和 SQLite 快照。

## 6. 同步后门禁与写作证据

同步完成后必须校验并记录：knowledge mode、profile、连接状态、实时同步或已有快照、package/catalog IDs、内容哈希、语料块数、能力数、政策目录记录数、政策条款数、`synced_at` 和 permission scope。

建设清单先形成客户范围项，再映射已批准能力；能力通过 `standard_block_ids` 精确召回公司语料。公司能力不能自动扩大客户范围。建设章节的四级标题对应客户范围，五至七级内容来自确认能力和标准块；章节任务包、建设知识覆盖报告及正文组装链必须保留实际数据库 block IDs。未映射范围、零记录同步、缺失 ID/哈希或权限不匹配均不得宣称已使用公司知识。

本地检索先做 package/catalog、文档类型、项目类型、章节角色、模块、主题、复用级别、权限、版本、标签和前置条件等硬过滤，再进行确定性排序并限制结果数量。断开服务器后，同一快照应返回相同 block IDs。

## 7. 新项目入口

```powershell
python scripts/init_project_workbench.py "D:\path\new-project" --project-code PROJECT-001
python scripts/knowledge_doctor.py --project-root "D:\path\new-project" --profile default --json
python scripts/run_project_pipeline.py "D:\path\new-project" --project-code PROJECT-001 --knowledge-profile default --output pipeline-result.json
```

`server_required` 是新项目默认值。只有在明确选择其他模式时才改变；不得因为数据库暂时不可用而静默切换为 `disabled` 或 seed 数据。

## 8. 团队与远程边界

本版提供的`medical_report_mcp_server.py`只是在同一台电脑上通过`stdio`暴露清单对照、人工决定装配和Word生成。它不监听网络，且仍要求标准知识已经按本规则同步到项目SQLite；不得把它解释为服务器数据库代理，也不得在MCP配置中放入数据库密码或SSH密钥。具体接口边界见`mcp-service-rules.md`。

团队成员在不知道数据库密码的情况下访问服务器知识时，使用独立的`remote_readonly_knowledge_mcp.py`和本机`remote_knowledge_mcp_bridge.py`。服务只查询已发布`runtime_*`视图，不接收项目文件；一次性激活后，长期令牌仅保存在当前系统用户目录。完整报告仍必须运行`sync_remote_knowledge_snapshot.py`把明确选择的package/catalog写入项目SQLite并通过快照门禁。部署、发码、失效和最小权限规则见`remote-readonly-knowledge-mcp-rules.md`。

当前团队版默认所有已激活用户共享同一只读知识范围，不实现角色、部门、租户或硬件绑定。公网TLS反向代理、运行进程托管和数据库只读账号由部署环境负责；知识迁移、更新和发布仍只允许管理员走既有后台链路。
