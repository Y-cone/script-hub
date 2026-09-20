# -*- mode: python ; coding: utf-8 -*-
"""ScriptHub sidecar PyInstaller spec（PRD-V5 / Phase V5-A）。

构建（Linux 本机自用验证；Windows 安装包在 Windows 打包机上以同名 spec 构建）：
    cd backend && pyinstaller scripthub-server.spec

产物：dist/scripthub-server（onefile；Tauri externalBin 需按 target triple 重命名，
如 scripthub-server-x86_64-unknown-linux-gnu / scripthub-server-x86_64-pc-windows-msvc.exe）

注意：
- pywinpty 为 V5-C 的 Windows 依赖，spec 内显式 hiddeninclude——Windows 构建时若
  缺失会在此暴露（Linux 构建不含，属预期）
- SCRIPTHUB_PORT / SCRIPTHUB_DATA_DIR 运行时由壳注入，spec 不固化
"""
import os
import sys

# Windows：显式收集 pywinpty(2.x) 的 winpty.dll / winpty-agent.exe 到包内
# winpty/ 目录（agent 由 _winpty.pyd 按 <pkg_dir>/winpty-agent.exe 定位）
_winpty_binaries = []
if sys.platform.startswith("win"):
    import winpty as _wp
    _pkg_dir = os.path.dirname(_wp.__file__)
    for _f in ("winpty.dll", "winpty-agent.exe"):
        _p = os.path.join(_pkg_dir, _f)
        if os.path.exists(_p):
            _winpty_binaries.append((_p, "winpty"))

a = Analysis(
    ["entry.py"],
    pathex=["."],
    binaries=_winpty_binaries,
    datas=[],
    hiddenimports=[
        # uvicorn 拆分模块，PyInstaller 常见漏采
        "uvicorn.logging",
        "uvicorn.loops",
        "uvicorn.loops.asyncio",
        "uvicorn.protocols",
        "uvicorn.protocols.http",
        "uvicorn.protocols.http.h11_impl",
        "uvicorn.protocols.websockets",
        "uvicorn.protocols.websockets.wsproto_impl",
        "uvicorn.lifespan",
        "uvicorn.lifespan.on",
        # app 内动态引用
        "app.main",
        "app.routers.terminal",
        # SQLAlchemy 方言/驱动为字符串动态加载，PyInstaller 必漏，显式列出
        "aiosqlite",
        "sqlalchemy.dialects.sqlite",
        "sqlalchemy.dialects.sqlite.aiosqlite",
        # paramiko 依赖链（crypto 后端动态加载）
        "paramiko",
        "cryptography.hazmat.bindings._rust",
        # Windows ConPTY（V5-C）；Windows 上缺包时构建报错即暴露
        # 注意：pywinpty>=3.0 的 ConPTY 后端（conpty.dll+OpenConsole.exe）在
        # PyInstaller 冻结环境下管道不通（子进程零输出/退出），须用 2.x
        # winpty-agent 架构并显式收集二进制（见下方 binaries）
        *(["winpty"] if sys.platform.startswith("win") else []),
    ],
    excludes=[
        # 明确排除无关大件（体积）
        "tkinter",
        "matplotlib",
        "PIL",
        "pytest",
    ],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="scripthub-server",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,          # sidecar：日志走 stdout 供壳诊断
    disable_windowed_traceback=False,
)
