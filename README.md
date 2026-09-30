# ScriptHub

本地脚本管理与执行平台，将"记住一堆脚本路径、参数格式、环境依赖"变成"选脚本 → 填参数 → 点运行"。

## 功能特性

- **脚本扫描**：自动扫描指定目录，识别 Python/Shell/Batch/PowerShell 脚本
- **脚本上传**：桌面端拖拽/选择上传脚本，自动入库
- **自定义标签**：为脚本打标签，按标签筛选（AND 语义）
- **在线编辑**：内置轻量编辑器，修改脚本内容并保存落盘
- **参数解析**：自动解析 argparse/getopts 参数，支持手动配置、类型校验与必填检查
- **脚本执行**：支持工作目录、环境变量、超时配置、Shell 覆盖（同族白名单校验）
- **环境检测**：执行前校验平台兼容性与运行时版本，不达标可确认后放行
- **实时输出**：WebSocket 实时推送执行输出，长输出自动截取头尾、完整日志可下载
- **内置终端**：xterm.js 多标签终端（PowerShell/CMD/bash），支持窗口尺寸跟随
- **远程设备**：多设备注册与凭据管理（凭据管理器加密存储），远程脚本执行与终端
- **运行历史**：记录每次执行（含耗时/退出码），支持筛选与日志文件下载
- **本机信息**：查看主机/OS/IP 与运行时版本（探活按平台裁剪，防假解释器误报）
- **安全机制**：高危脚本自动扫描标记 + 二次确认，参数注入校验
- **自动同步**：文件系统变更自动更新数据库（每 30s 轮询）
- **深度依赖分析**：解析 requirements.txt / package.json / pyproject.toml，检测安装状态
- **脚本导入导出**：ZIP 打包（脚本文件 + 配置 manifest），跨机器迁移含标签
- **定时调度**：APScheduler 驱动 cron/间隔 执行，看板管理 + 立即触发；退出后任务保留不丢失
- **数据迁移**：升级安装自动迁移旧版数据目录，二次启动不重复迁移
- **JSON 美化复制**：执行输出一键格式化为缩进 JSON 复制

## 技术栈

| 层级 | 技术 |
|------|------|
| 后端 | Python 3.12 + FastAPI + SQLAlchemy + SQLite |
| 前端 | React 19 + TypeScript + Ant Design 6 + Vite |
| 桌面壳 | Tauri 2（Rust），PyInstaller 打包后端为 sidecar |
| 实时通信 | WebSocket |
| 终端模拟 | xterm.js + ConPTY（pywinpty）|

## 桌面版安装（Windows / Linux）

从 [Releases](https://github.com/Y-cone/script-hub/releases) 下载安装包：

- **Windows**：`ScriptHub_*_x64-setup.exe`（NSIS 安装包）
- **Linux**：`ScriptHub_*_amd64.deb`（Debian/Ubuntu）或 `.AppImage`（免安装）

安装即用，**无需 Python / Node 环境**（后端内核已打包为 sidecar 随壳分发）。

> **Windows 首次运行**：若 SmartScreen 弹出「已保护你的电脑」，点「更多信息」→「仍要运行」（安装包未做代码签名，属预期）。

数据目录：Windows 在 `%APPDATA%\com.scripthub.app`（升级安装自动迁移旧 `data\` 目录），Linux 在 `~/.local/share/com.scripthub.app`；Windows 卸载时可选删除用户数据（默认保留，两种删除入口等效），Windows 凭据管理器中的设备凭据随卸载可选清理。后端仅监听 `127.0.0.1`，不对局域网开放。

## 从源码构建（开发）

### 环境要求

- Python 3.10+、Node.js 18+、Rust（tauri CLI）
- Linux 需 tauri 系统依赖；Windows 需 MSVC 工具链

```bash
# 后端依赖
cd backend
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -r requirements.txt

# 前端依赖 + 桌面端开发调试
cd ../frontend
npm install
npm run tauri dev
```

打包：`bash backend/build_sidecar.sh && cd frontend && npm run tauri build`（产物在 `src-tauri/target/release/bundle/`）。

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
│   │   └── services/        # 业务逻辑（scanner/envcheck/depscan/scheduler_service/executor）
│   └── requirements.txt
├── frontend/
│   ├── src/
│   │   ├── components/desktop/  # 桌面端 UI 组件
│   │   ├── stores/          # Zustand 状态管理
│   │   ├── services/        # API 服务
│   │   └── styles/          # 样式（proto/desktop）
│   ├── src-tauri/           # Tauri 壳（Rust）
│   └── package.json
├── data/
│   ├── scripts/             # 脚本存放目录
│   └── runs/                # 运行日志目录
└── docs/                    # 构建/测试文档
```

## API 文档

后端启动后访问 http://127.0.0.1:8001/docs 查看 Swagger 文档（仅本机监听）。

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
| GET | /api/scripts/{id}/deps | 深度依赖分析 |
| GET | /api/scripts/{id}/export | 导出脚本 ZIP（含配置 manifest） |
| POST | /api/scripts/import | 导入脚本 ZIP（恢复配置/标签） |
| GET | /api/tags | 标签列表 |
| POST | /api/tags | 创建标签 |
| PUT / DELETE | /api/tags/{id} | 重命名/删除标签 |
| GET | /api/system/info | 本机信息 |
| POST | /api/run | 执行脚本（含环境检测拦截） |
| GET | /api/run/history | 获取运行历史（支持 schedule_id 过滤） |
| POST | /api/run/{id}/kill | 终止运行 |
| WS | /api/run/ws/{id} | 实时输出 |
| GET | /api/schedules | 调度列表 |
| POST | /api/schedules | 创建调度（cron/间隔二选一） |
| PUT / DELETE | /api/schedules/{id} | 修改/删除调度 |
| POST | /api/schedules/{id}/run | 立即触发调度执行 |

## 配置

### 脚本目录

默认扫描 `data/scripts/` 目录，可在前端设置中修改。

### 前端 API 地址

桌面端默认直连 `http://127.0.0.1:8001`（壳注入）。开发调试可覆盖：

```bash
# frontend/.env
VITE_API_BASE=http://127.0.0.1:8001
```

WebSocket 地址由 `VITE_API_BASE` 自动推导（http→ws/https→wss）。参考 `frontend/.env.example`。

## 开发

```bash
# 后端开发（热重载）
cd backend
source .venv/bin/activate
python -m uvicorn app.main:app --reload

# 桌面端开发（Tauri dev 窗口）
cd frontend
npm run tauri dev

# 类型检查
cd frontend && npx tsc -p tsconfig.app.json --noEmit

# 后端测试
cd backend && python -m pytest tests -q
```

## License

MIT
