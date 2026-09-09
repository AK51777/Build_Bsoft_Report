# 清单对照与 Word 生成 MCP 服务规则

## 1. 模块定位

`medical_report_mcp_server.py`是现有确定性脚本的本机适配层，不重新实现清单匹配、人工决定、标准方案装配或 Word 排版算法。它继承 Skill 的全局事实、防编造、知识版本、逐字复用和交付门禁，只单独管理以下事项：

- MCP 协议初始化、工具发现和错误表达；
- 项目目录白名单及输入输出路径隔离；
- 清单对照、人工确认、内容装配和 Word 构建的调用顺序；
- 面向其他 AI 的最小返回字段和隐私边界；
- 自动结构校验与人工渲染复核之间的状态区分。

不得在 MCP 层修改候选排序、自动确认阈值、标准正文、标题树、用户决定或 Word 样式契约。相关业务变化应分别修改建设清单模块或 Word 模块并运行各自回归。

## 2. 部署边界

本版只允许本机 `stdio`：

- 不启动 HTTP/SSE 监听，不开放服务器端口；
- 不在配置中保存 PostgreSQL 密码、SSH 密钥或连接字符串；
- 标准知识应先由既有同步链写入项目 SQLite，MCP 运行时只读写该项目的本地文件和 SQLite；
- `allowed_project_roots`必须为一个或多个绝对目录，所有输入、配置、模板和输出都必须位于本次`project_root`内；
- 服务配置文件属于用户本机配置，不应提交真实客户路径或秘密。

远程、团队或公网 MCP 不是把本服务换成网络监听即可完成。那种部署必须另行实现身份认证、项目授权、租户隔离、只读知识查询、审计、限流、密钥托管和数据外发策略。

## 3. 工具与调用顺序

### 3.1 `service_status`

初始化后先调用，确认服务版本、`stdio`传输、响应模式、项目根白名单和交付模式开关。该工具不读取项目正文。

### 3.2 `construction_prepare_review`

输入项目根、本地 SQLite、用户 XLSX、项目代码和必填的已同步知识包 ID。缺少 `package_id` 时禁止退化为全库匹配。服务依次执行：

1. 机械提取 XLSX，并保留来源路径、哈希、工作表、原行和合并单元格语义；
2. 将清单标准化写入已初始化项目 SQLite；
3. 冻结可见清单快照；
4. 按模块最小颗粒度生成精确、相似、歧义和缺失结果；
5. 输出 JSON、人工核对 Markdown、路径和哈希。

在步骤 4 前，服务必须验证知识快照携带的标准方案完整性证明。旧快照缺证明、短正文/空标题未全量保留、结构哈希或内容哈希不一致时，工具必须返回错误并要求重建标准知识包；不得继续生成候选。

`review_metadata`响应模式可以向调用方返回人工对话必需的项目模块名、候选模块名、匹配分数、候选 ID 和方案根路径，但不得返回标准方案正文或块 ID。`paths_only`模式只返回摘要和本地制品路径。

调用方必须把 `hierarchy_review` 的目录归属与所有 `review_items` 集中成一轮人工核对，具体交互见 `construction-only-workflow.md`。`00-待确认-清单与目录.md` 是第一入口，先在对话中明确需确认的事项和确认后自动输出Word；不能藏在中间产物列表中。模块精确匹配不代表目录正确，`hierarchy_review_required` 大于0仍返回待确认状态。不得自行替用户确认相似、歧义或缺失项。

### 3.3 `construction_apply_and_assemble`

输入上一工具返回的`match_run_id`、人工身份和决定数组。决定记录是追加式证据，不得覆盖历史决定。服务必须：

- 精确命中或人工确认后，从匹配知识包快照读取该模块标题下完整子树；
- 标准段落逐字装入，禁止 AI 改写、压缩或补写；
- 按用户原清单顺序生成建设清单片段和应用软件建设方案片段；
- 已确认标准库确无内容的模块只保留标题和`【待补充】`；
- 仍有未确认项时，仅在显式`allow_unresolved_preview=true`下生成阻断型工作预览；
- 装配后校验清单快照哈希、知识包哈希、块哈希、标题根、顺序和缺失标记。
- 返回并锁定 `manifest_id`、`manifest_hash`、`package_id` 和 `package_content_hash`；校验必须重新计算这些绑定，不能复用旧校验结论。

传入确认后的 `hierarchy_review`；字段和哈希校验由核心装配模块执行。`validation.valid=true` 后默认调用 `build_construction_docx.py` 自动生成建设清单与建设内容两章Word；未指定格式配置时复用内置样式。可以传 `project_name`、`format_config_path`；仅在用户要求只对照/MD时传 `generate_word=false`，此时返回 `validated`。`blocked_working_preview` 不能进入Word。

自动Word状态：`word_structure_pass_render_required` 表示结构通过、待渲染；`word_structure_blocked` 表示格式阻断；`word_generation_failed` 提供 `word_error`，不得将其当作MD交付成功。生成成功时制品列表第一项是 `docx`，但 `delivery_ready=false` 直到真实渲染复核完成。该流程不进入完整可研报批门禁。

### 3.4 `word_generate`

这是保留的已确认外部模板/片段兼容入口。清单专项默认使用上一节的自动两章出口，避免兼容入口的“第1章 专项预览”包装和可研封面被误当作专项成品。

输入装配 Markdown、输出 DOCX、项目数据库与代码、装配清单、装配校验报告、项目名和已确认的格式权威配置。服务必须现场复算装配校验，要求清单、校验文件和当前数据库的 `manifest_id/manifest_hash/package_content_hash/validation_hash` 全部一致，并要求输入 Markdown 与该装配清单确定性重建结果逐字相等。缺少任一证据或自行拼写的 Markdown 必须阻断。格式权威配置必须绑定：

- 权威模板路径及 SHA-256；
- 确认人和确认时间；
- Markdown 标题层级到模板 Word 样式的语义映射；
- 如存在，格式画像和样式契约路径及 SHA-256。

`markdown_mode`决定输入边界：

- `full_report`要求 Markdown 已含构建器支持的阿拉伯数字`第1章`类标题，否则阻断；
- `fragment`把清单/方案片段自动包裹在一个明确的“第1章 专项预览”工作章下，保证片段标题和正文实际进入 Word；
- `auto`在检测到`第1章`类标题时按全文处理，否则按片段处理。MCP 的单阶段调用默认使用`auto`。

自动包裹只解决构建器的全文入口契约，不改变片段内部标题相对层级、清单顺序或标准正文。返回值必须报告`fragment_wrapper_applied`，便于合入完整可研时识别并去掉专项工作章。无权威模板的工作预览仍必须由 Word 构建模块创建真实多级标题编号；MCP 不得用手写编号补偿。

服务必须先验证配置和证据哈希，再生成 Word，并执行 Markdown 标记残留检查、标题段落有效编号检查和样式结构 lint。返回状态解释如下：

- `structure_pass_render_required`：模板、配置哈希、标题样式映射和结构检查通过，但尚未完成整份视觉复核；
- `structure_blocked`：存在 Markdown 残留或阻断级格式问题；
- `delivery_ready`在本工具中固定为`false`，防止其他 AI 把结构通过误报为正式交付。

正式交付仍必须按`word-delivery-rules.md`渲染整份 DOCX，逐页检查标题编号、分页、孤行、表格、图片、页眉页脚和目录，并记录复核证据。

## 4. 初始化配置

本机另存配置，例如：

```json
{
  "schema_version": "1.0",
  "allowed_project_roots": ["D:/projects"],
  "response_mode": "review_metadata",
  "allow_delivery_mode": false
}
```

AI 应用中的 MCP 启动项使用 Python 解释器绝对路径、脚本绝对路径和本机配置绝对路径：

```json
{
  "mcpServers": {
    "medical-report-local": {
      "command": "C:/path/to/python.exe",
      "args": [
        "C:/path/to/build-medical-it-feasibility-report/scripts/medical_report_mcp_server.py",
        "--config",
        "D:/local-config/medical-report-mcp.json"
      ]
    }
  }
}
```

不同 AI 应用的外层配置字段可能不同，但传输方式必须为`stdio`，启动命令和参数不变。初始化完成后应能发现四个工具：`service_status`、`construction_prepare_review`、`construction_apply_and_assemble`和`word_generate`。

### 4.1 TRAE

将`assets/mcp/trae-mcp.example.json`复制为本机配置并替换三个绝对路径：Python解释器、MCP脚本和本机服务配置。然后在TRAE的“设置 → MCP → 手动添加 → 原始配置（JSON）”中粘贴`mcpServers`对象，或写入TRAE当前项目启用的`mcp.json`。使用时选择`Builder with MCP`，或把`medical-report-local`加入一个只负责建设清单和Word的自定义智能体。

弱模型必须依靠工具状态驱动，不得一次索取标准方案正文：先调用`service_status`，再用已同步的明确 `package_id` 调用`construction_prepare_review`；只把`review_items`组织成一轮人工确认；确认后调用`construction_apply_and_assemble`；最后把返回的 `combined_markdown`、`manifest`、`validation` 三个本地制品路径连同同一项目数据库和项目代码交给`word_generate`。任何`isError=true`、`blocked_working_preview`、`structure_blocked`或`delivery_ready=false`都必须原样报告，不得凭语言推断为已完成。

## 5. 失败与审计

- 工具级业务错误通过`isError=true`返回错误类型和简短信息，不回传堆栈或正文；
- 每次成功实质运行在输出目录写入`mcp-run-<run_id>.json`，只记录工具、状态和制品哈希；
- 匹配结果、决定、装配清单、校验报告和 Word 摘要保存在项目目录，便于人工复核；
- 任何`outside project_root`、哈希不一致、未确认格式权威、知识快照变化或正式交付门禁失败都必须阻断，不得降级绕过。
