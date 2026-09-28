# AI-ManJv AI 漫剧创作系统

输入故事文本，由 AI 拆分漫画分镜；创作者逐格修改并确认后，再进入画面、配音与合成阶段。

当前版本已完成参考 `ai_story` 的第一轮迁移：保留其“项目 + 分阶段流水线 + 分镜资产”的实现思想，使用本项目既定的 FastAPI、SQLite 和原生 Web 页面重新实现。

## 个性化方向

本项目不直接复刻参考项目的管理后台界面，而采用“镜流”导演工作台设计：

- 首页展示作品、镜头和已定稿分镜统计，可按名称与制作状态筛选。
- 新建作品时提供视觉气质预设和剧情完整度提示，不要求用户编写提示词。
- 分镜台展示预计成片时长、对白覆盖和平均镜长，辅助创作者判断节奏。
- 每格分镜具有未保存提示；人工确认后冻结，避免后续流程误改。

## 技术栈

- Python 3.13 + FastAPI（后端接口）
- SQLite（存储漫剧名称、剧情文本、分镜描述、图片路径）
- 原生 HTML + JavaScript（前端展示）
- 大模型 API（后续接入：剧本分镜生成、AI 绘图提示词生成）

## 项目结构

```
AI-ManJv/
├── app/
│   ├── api/           # 项目与分镜接口
│   ├── services/      # DeepSeek 分镜生成与结构校验
│   ├── static/        # 创作工作台页面
│   ├── database.py    # SQLite 初始化
│   ├── repository.py  # 数据读写
│   └── main.py        # FastAPI 入口
├── storage/           # 本地数据库与后续生成素材（不提交）
├── tests/             # 工作流与结构校验测试
├── .env.example       # 环境变量模板（真实密钥放 .env，不提交）
├── .gitignore
├── README.md
└── requirements.txt   # 依赖版本锁定
```

## 快速开始

```powershell
# 1. 创建并激活虚拟环境
python -m venv .venv
.\.venv\Scripts\Activate.ps1

# 2. 安装依赖
pip install -r requirements.txt

# 3. 启动服务
uvicorn app.main:app --host 127.0.0.1 --port 8000

# 4. 打开创作工作台
# 浏览器访问 http://127.0.0.1:8000/

# 5. 运行测试
python -m unittest discover -s tests -v
```

## 仓库地址

- GitHub：https://github.com/gugujihahaha/AI-ManJv
- Gitee：https://gitee.com/tianshigirlggj/ai-man-jv

## 开发进度

- [x] 实验1：开发环境搭建，最小 FastAPI 项目可启动（v0.1.0）
- [x] 实验2：剧情输入、DeepSeek 分镜拆分、结构校验与离线降级
- [x] 实验2.5：分镜逐格编辑、保存与人工确认
- [ ] 实验3：AI 漫画图片生成与存储
- [ ] 实验4：漫剧 Web 浏览页面
