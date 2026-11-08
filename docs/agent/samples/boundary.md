# 边界与异常测试

- 作品：雨夜来信（fe3cdb26-66d8-4f4c-adca-a64a70a5eff7）
- 生成时间：2026-11-08 13:48:36

| 用例 | 状态 | 原因 | 步数 | 工具调用 | 关键工具状态 |
|---|---|---|---|---|---|
| 参数错误后纠正 | success | - | 4 | 3 | get_project_detail=VALIDATION_ERROR, get_project_detail=ok, compute_storyboard_stats=ok |
| 工具异常后继续 | success | - | 3 | 2 | __test_boom=TOOL_EXCEPTION, compute_storyboard_stats=ok |
| 工具超时后继续 | success | - | 3 | 2 | __test_slow=TOOL_TIMEOUT, compute_storyboard_stats=ok |
| 达到最大步数 | stopped | MAX_STEPS | 2 | 2 | list_projects=ok, list_projects=ok |
| 总时限停止 | stopped | TOTAL_TIMEOUT | 1 | 0 | - |
