# Skill 复杂性与模块地图

## 一、仓库定位

本仓库维护通用医疗信息化可研生成 Skill。任何真实医院项目均是外部运行实例，不是本仓库的一部分。

固定业务链为：

```text
用户级知识profile
→ 已发布知识或受审离线包
→ 项目SQLite快照
→ 资料与事实
→ 清单、范围和能力映射
→ 贯通矩阵与目录
→ 章节任务包与组合计划
→ 分章生成和采纳
→ 全文校验
→ Word构建、审计和渲染
```

## 二、开发模块

| 模块 | 主要位置 | 典型问题 | 最小测试范围 |
|---|---|---|---|
| Skill入口与路由 | `SKILL.md`、`agents/` | 触发范围、阶段路由、说明不清 | quick validate + 相关行为测试 |
| 业务规则 | `references/` | 事实、范围、政策、章节或交付规则冲突 | 目标规则对应测试 |
| 数据契约 | `assets/knowledge-base/`、迁移脚本 | schema、迁移、权限和哈希不一致 | 数据库单元测试 + 相关黄金案例 |
| 建设清单对照与方案装配 | `scripts/construction_alignment.py`、`references/construction-alignment-rules.md` | 原清单失真、同名标题串入、相似项未确认、标准正文被改写 | 模块级合成案例 + 真实快照只读核验 |
| 本机MCP适配 | `scripts/medical_report_mcp_server.py`、`references/mcp-service-rules.md`、`assets/mcp/` | 协议不兼容、路径越界、正文外发、调用顺序错误、工作稿被误报为交付稿 | MCP协议/UTF-8/路径隔离单测 + 清单和Word真实项目外部回归 |
| 团队只读知识MCP | `scripts/remote_*mcp*.py`、`scripts/sync_remote_knowledge_snapshot.py`、`references/remote-readonly-knowledge-mcp-rules.md`、`plugins/medical-report-knowledge/` | 越权写入、秘密入配置、激活码复用、分页缺失、未落本地快照即写作 | HTTP认证/只读工具/凭据持久化/分页适配单测 + 真实发布视图只读验收 |
| 确定性脚本 | `scripts/` | 参数、ID、计算、状态和输出错误 | 目标脚本测试 |
| 项目模板 | `assets/project-workbench-template/`等 | 初始化缺文件、模板残留 | 初始化测试 + 简单项目案例 |
| 章节链 | 章节规则、组合计划、生成、校验脚本 | 跨章漂移、范围漏载、语料缺块 | chapter → family；必要时full |
| 交付链 | Markdown组装、DOCX构建、审计和渲染 | 样式、目录、页码、残留 | Word冒烟 + 全页渲染 |
| 回归体系 | `tests/`、`tests/fixtures/`、未来`tests/golden_projects/` | 只测函数不测项目行为 | 对应黄金案例 |

## 三、排查规则

先选择一个模块和一个根因。只有证据显示公共依赖跨模块时才扩大范围，并把其他问题登记入池。

真实客户材料只可作为仓库外输入，用于验证某一明确问题；不得把客户项目目录当成Skill源代码目录，也不得从单个项目的结果直接推出通用规则。

本机MCP是接口适配模块：继承建设清单、知识快照、Word和交付链的全局业务规则，只自行管理协议、白名单路径、最小响应、工具编排和门禁状态表达。候选排序、正文装配、样式映射和数据库同步仍归各自原模块，禁止在MCP层复制一套分叉规则。
