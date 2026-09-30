<div align="center">

# ScriptHub

**本地脚本管理与执行平台** — 把"记住一堆脚本路径、参数格式、环境依赖"变成"选脚本 → 填参数 → 点运行"。

[![Release](https://img.shields.io/github/v/release/Y-cone/script-hub?display_name=tag&logo=github)](https://github.com/Y-cone/script-hub/releases)
[![Platform](https://img.shields.io/badge/platform-Windows%20%7C%20Linux-blue)](#-安装)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![Backend](https://img.shields.io/badge/Python-3.12-3776AB?logo=python&logoColor=white)](backend/)
[![Frontend](https://img.shields.io/badge/React-19-61DAFB?logo=react&logoColor=black)](frontend/)

下载即用的桌面应用（Tauri 2），无需 Python / Node 环境。

</div>

---

## 📌 简介

ScriptHub 解决的问题：散落在各处的运维/开发脚本，路径难记、参数格式各异、依赖环境不明。

| 领域 | 能力 |
|------|------|
| **脚本管理** | 目录扫描（Python/Shell/Batch/PowerShell）、拖拽上传、标签筛选（AND 语义）、在线编辑、ZIP 导入导出（含配置与标签） |
| **参数化执行** | 自动解析 argparse/getopts 参数 + 手动配置、类型校验、Shell 同族白名单 |
| **环境与安全** | 执行前平台/运行时校验、高危命令自动扫描（shutdown/format/rm -rf 等）+ 二次确认 |
| **内置终端** | xterm.js 多标签（PowerShell / CMD / bash），窗口尺寸实时跟随 |
| **远程设备** | 多设备注册、凭据管理器加密存储、远程执行与远程终端 |
| **定时调度** | cron / 固定间隔，看板与周视图管理，应用退出后任务保留不丢失 |
| **运行审计** | 全量执行历史（耗时/退出码）、长输出自动截取头尾、完整日志可下载 |
| **数据迁移** | 升级安装自动迁移旧数据目录，幂等不重复 |

## 📦 安装

从 [**Releases**](https://github.com/Y-cone/script-hub/releases/latest) 下载：

| 平台 | 文件 |
|------|------|
| Windows x64 | `ScriptHub_*_x64-setup.exe`（NSIS 安装向导） |
| Linux amd64 | `ScriptHub_*_amd64.deb`（Debian/Ubuntu）或 `ScriptHub_*_amd64.AppImage`（免安装） |

> **Windows 首次运行**：若 SmartScreen 弹出「已保护你的电脑」，点「更多信息」→「仍要运行」（安装包未做代码签名，属预期）。

数据目录：Windows `%APPDATA%\com.scripthub.app`（升级自动迁移旧 `data\`），Linux `~/.local/share/com.scripthub.app`。后端仅监听 `127.0.0.1`，不对局域网开放。

## 🚀 快速开始

1. 启动 ScriptHub，自动打开主界面
2. **脚本库**：设置脚本目录（默认 `data/scripts/`）或直接拖拽上传
3. **运行**：点开脚本 → 填参数 → 执行，实时查看输出
4. **终端**：底部终端栏拉起 PowerShell / CMD / bash
5. **调度**：为脚本配置 cron 或间隔任务

## 🏗️ 架构

```
┌─────────────────────────────────────────────┐
│  Tauri 2 壳（Rust）                          │
│  WebView（React 19 + TS + AntD 6）           │
│  自绘标题栏 / 托盘 / 端口协商 8001~8020        │
└──────────────────┬──────────────────────────┘
                   │ window.__SCRIPTHUB_API__
                   ▼
┌─────────────────────────────────────────────┐
│  sidecar 后端（PyInstaller 打包随壳分发）      │
│  FastAPI + SQLAlchemy + SQLite (aiosqlite)  │
│  ├─ executor           执行/输出解码/引号包装  │
│  ├─ session_manager    ConPTY 终端会话        │
│  ├─ scheduler_service  APScheduler 定时调度   │
│  ├─ scanner            扫描/高危命令检测       │
│  ├─ envcheck           平台与运行时探测        │
│  └─ win_runtime        解释器实跑甄别          │
└─────────────────────────────────────────────┘
```

- **进程模型**：Tauri 壳拉起 sidecar 子进程，动态协商首个空闲端口，`window.__SCRIPTHUB_API__` 注入前端；壳退出时随行退出
- **技术栈**：Python 3.12 · FastAPI · SQLite · React 19 · TypeScript · Ant Design 6 · Vite · xterm.js · Tauri 2 (Rust) · pywinpty (ConPTY) · APScheduler
- **安全边界**：仅监听回环地址；远程设备凭据存操作系统凭据管理器（Windows Credential Manager / Linux Secret Service）

## 🔌 API 参考

后端启动后访问 `http://127.0.0.1:8001/docs` 查看交互式 Swagger 文档。常用接口：

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/scripts` | 脚本列表（含可用性/标签） |
| POST | `/api/scripts/scan` | 扫描脚本目录 |
| POST | `/api/scripts/upload` | 上传脚本 |
| PUT | `/api/scripts/{id}` · `/content` | 更新配置 / 保存内容 |
| DELETE | `/api/scripts/{id}` | 删除脚本（保留历史） |
| GET | `/api/scripts/{id}/env-check` · `/deps` | 环境检测 / 依赖分析 |
| POST | `/api/scripts/import` · GET `/{id}/export` | ZIP 导入导出（含配置/标签） |
| POST | `/api/run` | 执行脚本（环境检测拦截 + 高危确认） |
| GET | `/api/run/history` | 运行历史（支持筛选） |
| GET | `/api/run/{id}/download` | 下载完整日志 |
| POST | `/api/run/{id}/kill` | 终止运行 |
| WS | `/api/run/ws/{run_id}` | 实时输出推送 |
| WS | `/api/terminal/ws` | 终端会话（本机/远程） |
| GET/POST | `/api/devices` · `/{id}/test` · `/{id}/probe` | 远程设备管理与探活 |
| GET/POST/PUT/DELETE | `/api/schedules`（`/{id}/run` 立即触发） | 调度管理 |
| GET | `/api/system/info` | 本机信息与运行时版本 |
| GET/POST/PUT/DELETE | `/api/tags` · `/api/settings` | 标签与设置管理 |

## 🛠️ 从源码构建

```bash
git clone https://github.com/Y-cone/script-hub.git
cd script-hub

# 后端
cd backend
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt

# 前端 + 桌面壳（需 Rust 工具链与 Tauri 系统依赖）
cd ../frontend
npm install
npm run tauri dev                # 开发调试
```

打包发布：

```bash
bash backend/build_sidecar.sh    # PyInstaller 打包 sidecar
cd frontend && npm run tauri build   # 产物在 src-tauri/target/release/bundle/
```

质量门禁：

```bash
cd backend && python -m pytest tests -q               # 后端测试
cd frontend && npx tsc -p tsconfig.app.json --noEmit  # 类型检查
```

## 🤝 参与贡献

欢迎 Issue 与 PR：

1. Fork → 新建分支（`feat/xxx` 或 `fix/xxx`）
2. 提交前跑通质量门禁（pytest + tsc）
3. PR 描述写清动机、改动点与验证方式；行为改动请附复现步骤
4. 提交信息用 [Conventional Commits](https://www.conventionalcommits.org/zh-hans/)（`feat:` / `fix:` / `docs:` / `refactor:`）

Bug 报告请附：平台（Windows/Linux）、版本（Release tag）、复现步骤与预期/实际行为。

## 📄 许可证

[MIT](LICENSE) © [Y-cone](https://github.com/Y-cone)
