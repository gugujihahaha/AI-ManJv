# 工具直接测试

- 作品：雨夜来信（fe3cdb26-66d8-4f4c-adca-a64a70a5eff7）

| 用例 | 工具 | 状态 | 错误类型 | 说明 |
|---|---|---|---|---|
| 正确参数：列出作品 | list_projects | ok | - | - |
| 正确参数：读取作品与分镜 | get_project_detail | ok | - | - |
| 正确参数：计算分镜统计 | compute_storyboard_stats | ok | - | - |
| 错误参数：project_id 类型错误 | get_project_detail | error | VALIDATION_ERROR | 字段 project_id 应为字符串，实际为 int |
| 错误参数：缺少必填字段 | get_project_detail | error | VALIDATION_ERROR | 工具 get_project_detail 缺少必填字段：project_id |
| 错误参数：未知工具 | delete_project | error | UNKNOWN_TOOL | 工具 delete_project 不在允许列表中 |
| 错误参数：作品不存在 | get_project_detail | error | ACCESS_DENIED | 作品不存在或无权访问：not-exist |
