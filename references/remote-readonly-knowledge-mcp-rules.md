# 团队只读知识 MCP 部署与使用规则

## 1. 目标和边界

`remote_readonly_knowledge_mcp.py`是独立的团队知识服务，不是本机项目文件 MCP 的公网版本。它只查询 PostgreSQL 中已发布的`runtime_*`视图，不接收项目路径、客户材料、报告正文或任意 SQL，也不提供新增、修改、删除、迁移和发布工具。知识更新仍由管理员在现有后台链路完成。

调用链如下：

`Skill/插件 → 本机stdio凭据桥 → HTTPS → 远程只读MCP → PostgreSQL runtime_*视图`

本机桥只保存访问凭据和转发只读调用；项目 SQLite、清单匹配、正文装配与 Word 生成继续在用户电脑上运行。

## 2. 最小权限

服务端数据库账号必须是独立的运行时只读账号，并同时满足：

- 允许连接知识数据库并使用`medical_report_kb`schema；
- 只对服务使用的`runtime_knowledge_package`、`runtime_package_source`、`runtime_corpus_document`、`runtime_corpus_block`、`runtime_product_capability`、`runtime_capability_block`、`runtime_policy_catalog`、`runtime_policy_catalog_entry`、`runtime_policy_clause`视图授予`SELECT`；
- 设置`default_transaction_read_only=on`；
- 不授予基础表写权限、建表权限、迁移权限或其他schema权限。

应用层还会把每个数据库连接设为只读并设置语句超时。数据库权限是最终边界，不能只依赖工具名称或提示词。

## 3. 服务端初始化

1. 复制`assets/mcp/remote-readonly-knowledge-mcp.example.json`到服务器私有配置目录，调整监听地址、数据库名和凭据文件绝对路径。
2. 通过进程环境提供`MEDICAL_REPORT_MCP_HASH_SECRET`和`MEDICAL_REPORT_RUNTIME_DB_PASSWORD`；配置文件不得写入密码、激活码或令牌。
3. 检查配置：

```bash
python3 scripts/remote_readonly_knowledge_mcp.py \
  --config /absolute/path/remote-readonly-knowledge-mcp.json \
  --check-config
```

4. 启动服务时默认只监听`127.0.0.1`，再由现有网关或反向代理提供`https://<domain>/mcp`和`https://<domain>/activate`。公网部署必须启用TLS；不要直接暴露脚本提供的明文HTTP端口。
5. 网关健康检查使用`GET /health`。MCP请求使用`POST /mcp`，未携带有效Bearer令牌时返回401。

## 4. 发码、激活与失效

管理员发放一次性码：

```bash
python3 scripts/remote_readonly_knowledge_mcp.py \
  --config /absolute/path/remote-readonly-knowledge-mcp.json \
  --issue-activation-code \
  --label zhangsan \
  --valid-hours 168
```

激活码在兑换前不绑定硬件、应用实例或数据库权限；`label`仅用于管理员识别。它在首次成功兑换或到达`valid-hours`后失效。成功兑换会生成一个独立访问令牌，服务端只保存激活码和令牌的HMAC哈希，不保存明文。

访问令牌逻辑上绑定“服务地址 + 当前操作系统用户的本机凭据文件”，不是硬件强绑定。默认保存在`~/.codex/credentials/medical-report-knowledge.json`，权限为0600。只要文件仍在、令牌未到期且未吊销，关闭并重新打开Codex或其他AI应用都不需要再次激活。更换电脑、更换系统用户、删除凭据文件、令牌到期、管理员吊销或服务地址变更后，需要重新申请一次性码。

令牌默认90天有效，可在服务配置中设置1至365天。管理员从服务器凭据库按`label`找到对应`token_id`后执行：

```bash
python3 scripts/remote_readonly_knowledge_mcp.py \
  --config /absolute/path/remote-readonly-knowledge-mcp.json \
  --revoke-token-id TOK-xxxxxxxxxxxxxxxx
```

本版刻意不实现角色、部门、知识分级和硬件绑定。所有已激活用户看到同一组已发布只读知识。

## 5. 用户首次使用

服务地址应由管理员在分发包中预先配置。用户安装Skill/插件后调用知识能力：

1. Skill先调用`knowledge_access_status`；
2. 未激活时向用户索取一次性码，并调用`activate_knowledge_access`；
3. 激活成功后调用`knowledge_service_status`；
4. 日常检索使用`knowledge_query`，同步完整快照时使用`knowledge_page`并读取到`next_offset=null`；
5. 激活码和长期令牌不得写入对话、项目目录、日志、报告或共享配置。

`policy-catalog`仍只是待核验目录，不能直接作为正式政策证据。远程服务只负责提供已发布知识，不能绕过原Skill的人工确认、事实核验和交付门禁。

## 6. 运维验收

上线前至少确认：配置检查通过；数据库账号无法执行写语句；无令牌访问返回401；一次性码第二次兑换失败；有效令牌可在应用重启后复用；吊销后立即返回401；服务日志不含激活码、令牌和数据库密码；HTTPS证书有效；`knowledge_page`分页无重复、无遗漏并在末页返回空`next_offset`。
