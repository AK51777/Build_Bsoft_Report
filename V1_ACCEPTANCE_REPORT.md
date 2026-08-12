# V1.0 验收报告

## 1. 验收结论

截至 2026-08-12，`build-medical-it-feasibility-report` 已达到 **V1.0 正式版**：本地优先主链、从零项目统一入口、十阶段产物与阻断机制、数据库约束、自动测试和 Word 逐页视觉复核均已闭环。

脱敏 Word 样稿已使用经官方 SHA-256 校验的 LibreOffice 26.2.4 Portable 导出为 4 页 PDF/PNG。封面、目录页、标题、正文、列表、表格、页眉、页码、分页和空白页均已逐页检查，无裁切、重叠、溢出或异常空白页；复核结果与当前 DOCX 及全部 PNG 的哈希绑定。

## 2. 已完成能力

- 幂等本地项目初始化、配置、manifest 和 SQLite；
- 资料清单、来源角色隔离、DOCX/Markdown/TXT 清洗分块和本地语料入库；
- XLSX 清单提取、稳定范围 ID、建设方式和费用分类；
- 公司产品能力受审入库和不扩范围的候选映射；
- 候选事实工作包、原始证据定位和参考材料事实隔离；
- 追加式范围基线、幂等确认和持久化贯通矩阵；
- 全国通用政策种子、项目政策匹配和文档标准匹配；
- 参考复用地图、残留词候选和安全默认禁止直接复用；
- 12 类章节蓝图和 28 个三级目录节点；
- 项目章节组合计划、来源权限和独立章节任务包；
- AI/人工草稿版本、幂等保存、采纳、废弃、恢复、调用日志和审计；
- 已采纳章节全文组装；
- 事实、范围、投资、指标、逻辑、跨章、政策、语言、污染、占位十类校验；
- A4 中文候选 DOCX、三类模板继承、模板正文/媒体/元数据清理、目录域、命名标题样式、页眉页码和固定表格几何；
- 与当前 DOCX 和全部页面 PNG 哈希绑定的人工渲染复核记录；
- 纯文本、Excel 清单、PDF/OCR 阻断三类从零项目回归；
- 统一入口对不支持材料明确阻断，不静默跳过。

## 3. 自动验证结果

| 验证项 | 结果 |
| --- | --- |
| Python 单元与集成测试 | 33 项通过 |
| Python 脚本编译检查 | 通过 |
| Node 确认包相关脚本语法检查 | 通过 |
| Node Excel→决策→SQLite→重导出→阶段门禁前向回归 | 通过 |
| DOCX OOXML 结构检查 | 通过 |
| 目录域、标题样式、字段更新标记 | 通过 |
| 三项目从零回归 | 通过 |
| 三种 Word 模板继承与污染清理回归 | 通过 |
| Skill 客户地域/凭据/IP/联系方式残留扫描 | 通过，零命中 |
| Word 逐页视觉渲染 | 通过：4 页全部检查，复核记录见 `tests/rendered-word-smoke/visual-qa/word-render-review.json` |

## 4. 数据库存储验收

默认数据库位于 `<项目目录>/数据包/数据库/knowledge.sqlite`。

清洗文档通过 `extract_clean_document_blocks.py` 生成可追溯 JSON，再由 `ingest_clean_documents_sqlite.py` 写入：

- `source_document`：原文件身份、路径、哈希和权限；
- `corpus_document`：文种、地域、质量、权限和审核状态；
- `corpus_block`：清洗文本、来源位置、哈希和复用等级。

测试已验证重复导入不重复建档，并保留人工 `approved`/`prohibited` 状态。`source_document.source_class` 同时确保参考可研、厂商方案、政策线索和格式模板不会进入目标项目候选事实池。

## 5. 三项目回归

| 场景 | 材料结构 | 预期 | 结果 |
| --- | --- | --- | --- |
| `REG-TEXT-001` | Markdown 现状材料 | 清洗入库、重复运行幂等、生成 28 个计划 | 通过 |
| `REG-SCOPE-001` | XLSX 建设清单 | 标准化范围、升级识别、模糊“优化”待确认 | 通过 |
| `REG-PDF-001` | PDF 扫描占位 | 明确 OCR/转换阻断、仍生成运行记录和工作台 | 通过 |

## 6. 已知限制

1. 当前自动清洗入口直接支持 DOCX、Markdown、TXT 和 XLSX；PDF、扫描图片、旧 DOC/XLS 需要可信转换或 OCR 后再运行。
2. 统一入口负责生成证据、范围、门禁、章节计划和任务包；正文内容仍由 Codex 按章节任务包生成并保存版本，而不是脚本私自调用外部模型 API。缺少采纳章节时统一入口会明确阻断 S6。
3. PostgreSQL 仅为可选共享存储，本机未安装 `psycopg`，不影响 SQLite 主链。
4. RAG 尚未启用；现阶段使用 SQLite 结构化过滤和可选 FTS5。增加向量检索前应先建立召回质量基准。
5. 每个真实项目的最终 Word 候选稿仍必须单独渲染并逐页复核；本次通过证明的是 V1.0 生成器和复核门禁可用，不替代具体项目的交付验收。

## 7. 正式 V1.0 闭环证据

1. 样稿：`tests/rendered-word-smoke/word-smoke.docx`；
2. PDF：`tests/rendered-word-smoke/visual-qa/word-smoke.pdf`；
3. 逐页 PNG：`tests/rendered-word-smoke/visual-qa/pages-final/`，共 4 页；
4. 哈希化复核记录：`tests/rendered-word-smoke/visual-qa/word-render-review.json`；
5. 当前样稿 SHA-256：`f8437a5e6f27a6aec16afcb85de4e2cd1c9a87d348022380e11a2bdbb821379e`；
6. `record_word_render_review.py` 与交付门禁的匹配、失效和阻断行为由 `test_report_assembly_delivery.py` 覆盖。
