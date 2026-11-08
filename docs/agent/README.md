# 分镜节奏分析 Agent 与运行时 Harness

本目录记录实验 6 的实现与运行证据。Agent 面向“漫剧工厂”的分镜确认环节：读取一个作品的分镜数据，计算节奏指标，找出问题，输出可核对的 JSON 结论；需要时可以经用户确认后把结论保存为作品备注。

## 文件位置

| 路径 | 作用 |
|---|---|
| `app/agent/tools.py` | 工具 Schema、允许列表、参数校验、访问范围检查、超时执行 |
| `app/agent/model.py` | DeepSeek 对话接口，支持 OpenAI 风格 `tools` 与 `tool_calls` |
| `app/agent/prompts.py` | 系统提示、任务消息、程序检查失败后的纠正消息 |
| `app/agent/harness.py` | Agent 循环、工具分发、人工确认、停止控制、成功检查、日志 |
| `app/agent/testing.py` | 脚本化模型与测试专用工具，只用于边界测试 |
| `app/agent_cli.py` | 命令行入口，支持正常运行、工具直测、确认同意/拒绝 |
| `tests/test_agent.py` | Agent 单元测试 |
| `scripts/agent_boundary_tests.py` | 参数错误、工具异常、超时、步数上限、总时限测试 |
| `scripts/agent_evidence_md.py` | 把 JSON 证据转成 Markdown 摘要 |
| `docs/agent/samples/` | 工具直测、正常运行、确认同意/拒绝、边界测试的证据 |
| `docs/agent/runs/` | 每次运行的完整 JSON 日志 |

## 工具

| 工具 | 输入 | 返回 | 读写 | 确认 |
|---|---|---|---|---|
| `list_projects` | `limit`（可选，1—50） | 作品编号、名称、状态、目标时长、分镜数量 | 只读 | 不需要 |
| `get_project_detail` | `project_id`（必填，字符串） | 作品信息与分镜摘要，长文本截断 | 只读 | 不需要 |
| `compute_storyboard_stats` | `project_id`（必填，字符串） | 镜头数、总时长、平均时长、标准差、对白覆盖率、情绪分布、问题清单 | 只读 | 不需要 |
| `save_analysis_note` | `project_id`、`summary`（10—800 字） | 备注编号与保存结果 | 写入 | 必须用户确认 |

`save_analysis_note` 以 `(project_id, run_id)` 为唯一键做插入或更新。同一次运行重复调用只更新同一条备注，不会重复写入；不同运行会各留一条记录。

## 运行命令

在项目根目录执行，使用项目虚拟环境：

```powershell
# 列出作品，取得 project_id
python -m app.agent_cli --list-projects

# 直接测试工具的正确参数与错误参数
python -m app.agent_cli --tool-probe --save docs/agent/samples/tool_probe.json

# 正常完成：只读分析
python -m app.agent_cli --project-id fe3cdb26-66d8-4f4c-adca-a64a70a5eff7 --save docs/agent/samples/run_normal.json

# 写入任务：同意保存备注
python -m app.agent_cli --project-id fe3cdb26-66d8-4f4c-adca-a64a70a5eff7 --require-note --confirm approve --task "分析该作品的分镜节奏与完整性问题，给出结论，并把结论保存为作品备注。" --save docs/agent/samples/run_confirm_approve.json

# 写入任务：拒绝保存备注
python -m app.agent_cli --project-id fe3cdb26-66d8-4f4c-adca-a64a70a5eff7 --require-note --confirm deny --task "分析该作品的分镜节奏与完整性问题，给出结论，并把结论保存为作品备注。" --save docs/agent/samples/run_confirm_deny.json

# 交互式确认：在终端输入 y 或 n
python -m app.agent_cli --project-id fe3cdb26-66d8-4f4c-adca-a64a70a5eff7 --require-note --confirm ask

# 边界测试与全部测试
python scripts/agent_boundary_tests.py
python -m unittest discover -s tests -v

# 生成 Markdown 证据摘要
python scripts/agent_evidence_md.py
```

## 程序执行的限制

| 限制 | 默认值 | 计数方式 |
|---|---|---|
| 最大步数 | 8 | 一次模型响应算一步 |
| 最大工具调用 | 12 | 每次模型提出的工具请求算一次 |
| 单工具上限 | 4 | 按工具名分别计数 |
| 模型超时 | 40 秒 | 单次请求等待时间 |
| 工具超时 | 10 秒 | 单次工具等待时间 |
| 总时限 | 90 秒 | 从运行开始到结束 |
| 模型重试 | 1 次 | 仅网络错误与 429/5xx |
| 成功检查纠正 | 1 次 | 最终回答未通过程序检查时 |
| 确认等待 | 60 秒 | 超时按拒绝处理 |

这些限制由 `app/agent/harness.py` 执行，不写在提示词里。

## 日志与隐私

每次运行生成 `docs/agent/runs/<run_id>.json`，包含任务编号、开始与结束时间、任务文本、作品编号、步数、模型与工具调用记录、工具结果摘要、错误类型、耗时、最终状态、成功检查和 Token 用量。`storage/agent.log` 追加一行运行摘要。

日志不记录 API 密钥。`get_project_detail` 的完整分镜文本只进入模型上下文，日志里只保留作品编号、名称、分镜数量与截断标记；备注摘要只保留前 60 字。这样既能定位问题，也不会把剧情正文长期写进日志。

## 正常流程与模拟边界的分工

正常完成、确认同意、确认拒绝三次运行使用真实 DeepSeek 接口与真实工具。参数错误、工具异常、工具超时、最大步数、总时限五类边界情况使用脚本化模型与测试专用工具触发，测试专用工具不在生产允许列表中。这样异常可以稳定复现，正常流程仍然是真实调用。
