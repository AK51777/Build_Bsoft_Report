# 决策卡与运行记录模板

## 任务定义卡

```text
问题ID：
目标模块或阶段：
唯一根因假设：
计划读取的最小范围：
预计修改的源文件（1～3个）：
对应测试或黄金案例：
回归层级：局部 / 章节 / 章节族 / 全量
```

## 用户决策卡

```text
问题：
严重程度：P0 / P1 / P2 / P3
证据：文件、行号、来源ID、范围ID、错误码或测试输出
根因：
推荐修改：
备选方案：
修改范围：
预计影响：
是否建议现在修改：是 / 否
分类：A类可自主修 / B类需业务决策
需要用户决定什么：
若暂不决定，还能继续什么：
```

## 运行记录

保存到`test-results/<run_id>/run.json`：

```json
{
  "schema_version": "1.0",
  "run_id": "YYYYMMDD-HHMM-DEV-XXX-short-name",
  "started_at": "ISO-8601",
  "finished_at": "ISO-8601",
  "issue_id": "DEV-XXX",
  "severity": "P0|P1|P2|P3",
  "change_class": "A|B",
  "target_module": "",
  "root_cause_hypothesis": "",
  "inputs": [
    {"path": "", "business_id": "", "version": "", "sha256": ""}
  ],
  "commands": [
    {"command": "", "parameter_summary": "", "exit_code": 0, "duration_ms": 0}
  ],
  "outputs": [
    {"path": "", "sha256": "", "status": ""}
  ],
  "first_effective_error": null,
  "gate_results": [],
  "tests": [],
  "decision": "",
  "next_step": ""
}
```

不得记录明文密码、密钥、私人连接信息、客户正文或个人信息。

## 完成卡

```text
问题ID / run_id：
根因结论：已证实 / 已证伪 / 仍待证据
修改文件：
实际修改：
测试与门禁：命令 + 结果 + 输出路径
质量指标变化：
剩余问题：只列问题池ID
```
