# AI-ManJv AI 漫剧创作系统

输入故事文本剧情，由 AI 拆分漫画分镜、生成漫画图片，并提供 Web 页面浏览生成的漫剧作品。

## 技术栈

- Python 3.13 + FastAPI（后端接口）
- SQLite（存储漫剧名称、剧情文本、分镜描述、图片路径）
- 原生 HTML + JavaScript（前端展示）
- 大模型 API（后续接入：剧本分镜生成、AI 绘图提示词生成）

## 项目结构

```
AI-ManJv/
├── app/
│   ├── __init__.py
│   └── main.py        # FastAPI 入口，最小接口
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

# 4. 验证
# 浏览器访问 http://127.0.0.1:8000/ 与 http://127.0.0.1:8000/docs
```
