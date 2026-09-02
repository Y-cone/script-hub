# ScriptHub

本地脚本管理与执行平台，将"记住一堆脚本路径、参数格式、环境依赖"变成"选脚本 → 填参数 → 点运行"。

## 功能特性

- **脚本扫描**：自动扫描指定目录，识别 Python/Shell/Batch/PowerShell 脚本
- **脚本上传**：Web 界面拖拽/选择上传脚本，自动入库
- **自定义标签**：为脚本打标签，按标签筛选（AND 语义）
- **在线编辑**：内置轻量编辑器，修改脚本内容并保存落盘
- **参数解析**：自动解析 argparse/getopts 参数，支持手动配置
- **脚本执行**：支持工作目录、环境变量、超时配置
- **环境检测**：执行前校验平台兼容性与运行时版本，不达标可确认后放行
- **实时输出**：WebSocket 实时推送执行输出
- **运行历史**：记录每次执行（含耗时），支持日志文件下载
- **本机信息**：查看主机/OS/IP 与运行时版本
- **安全机制**：高危脚本标记 + 二次确认

## 技术栈

| 层级 | 技术 |
|------|------|
| 后端 | Python 3.12 + FastAPI + SQLAlchemy + SQLite |
| 前端 | React 19 + TypeScript + Ant Design 6 + Vite |
| 实时通信 | WebSocket |
| 终端模拟 | xterm.js |

## 快速开始

### 环境要求

- Python 3.10+
- Node.js 18+
- npm 或 yarn

### 安装

```bash
# 克隆项目
git clone https://github.com/Y-cone/script-hub.git
cd script-hub

# 后端依赖
cd backend
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -r requirements.txt

# 前端依赖
cd ../frontend
npm install
```

### 启动

```bash
# 方式一：使用启动脚本
./start.sh

# 方式二：手动启动
# 终端1：启动后端
cd backend && source .venv/bin/activate
python -m uvicorn app.main:app --host 0.0.0.0 --port 8001 --reload

# 终端2：启动前端
cd frontend
npm run dev
```

访问 http://localhost:5173

## 项目结构

```
script-hub/
├── backend/
│   ├── app/
│   │   ├── main.py          # FastAPI 入口
│   │   ├── database.py      # 数据库配置
│   │   ├── models/          # SQLAlchemy 模型
│   │   ├── schemas/         # Pydantic 数据模式
│   │   ├── routers/         # API 路由
│   │   └── services/        # 业务逻辑
│   └── requirements.txt
├── frontend/
│   ├── src/
│   │   ├── pages/           # 页面组件
│   │   ├── components/      # 通用组件
│   │   ├── stores/          # Zustand 状态管理
│   │   └── services/        # API 服务
│   └── package.json
├── data/
│   ├── scripts/             # 脚本存放目录
│   └── runs/                # 运行日志目录
└── start.sh                 # 启动脚本
```

## API 文档

启动后端后访问 http://localhost:8001/docs 查看 Swagger 文档

### 主要接口

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | /api/scripts | 获取脚本列表（含可用性/标签） |
| POST | /api/scripts/scan | 扫描脚本目录 |
| POST | /api/scripts/upload | 上传脚本 |
| GET | /api/scripts/{id} | 获取脚本详情 |
| PUT | /api/scripts/{id} | 更新脚本配置 |
| PUT | /api/scripts/{id}/content | 保存脚本内容 |
| POST | /api/scripts/{id}/move | 移动脚本目录 |
| DELETE | /api/scripts/{id} | 删除脚本（保留历史） |
| GET | /api/scripts/{id}/env-check | 环境检测 |
| GET | /api/tags | 标签列表 |
| POST | /api/tags | 创建标签 |
| GET | /api/system/info | 本机信息 |
| POST | /api/run | 执行脚本（含环境检测拦截） |
| GET | /api/run/history | 获取运行历史 |
| POST | /api/run/{id}/kill | 终止运行 |
| WS | /api/run/ws/{id} | 实时输出 |

## 配置

### 脚本目录

默认扫描 `data/scripts/` 目录，可在前端修改。

### 端口配置

- 后端：8001（修改 `start.sh` 或启动命令）
- 前端：5173（修改 `vite.config.ts`）

## 开发

```bash
# 后端开发
cd backend
source .venv/bin/activate
python -m uvicorn app.main:app --reload

# 前端开发
cd frontend
npm run dev

# 类型检查
npm run build
```

## License

MIT
