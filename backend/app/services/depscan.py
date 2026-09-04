"""深度依赖分析：解析脚本所在目录的依赖文件，检测安装状态。

目录级归属：依赖文件(requirements.txt 等)服务同目录所有脚本。
目录无依赖文件 → 不返回任何依赖（前端不渲染依赖区）。
"""
from pathlib import Path
import asyncio
import json
import re

# 脚本根目录下的依赖文件名 → 文件类型
DEP_FILES = ["requirements.txt", "package.json", "pyproject.toml"]


def _find_dep_files(script_dir: Path):
    """返回脚本目录中存在的所有依赖文件名"""
    return [f for f in DEP_FILES if (script_dir / f).exists()]


def _parse_requirements(text: str):
    """解析 requirements.txt → [(name, constraint)]，忽略注释/空行/-r/URL"""
    deps = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith(("-r", "--", "http://", "https://", "git+")):
            continue
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        match = re.match(r"^([A-Za-z0-9_.\-]+)(?:\[[^\]]+\])?\s*(.*)$", line)
        if match:
            name = match.group(1)
            constraint = match.group(2).strip() or ">=0"
            deps.append((name, constraint))
    return deps


def _parse_package_json(text: str):
    """解析 package.json dependencies + devDependencies → [(name, constraint)]"""
    try:
        data = json.loads(text)
    except Exception:
        return []
    deps = []
    for section in ("dependencies", "devDependencies"):
        deps.extend(data.get(section, {}).items())
    seen, result = set(), []
    for name, constraint in deps:
        if name not in seen:
            seen.add(name)
            result.append((name, str(constraint)))
    return result


def _parse_pyproject(text: str):
    """解析 pyproject.toml [project].dependencies → [(name, constraint)]"""
    try:
        import tomllib
        data = tomllib.loads(text)
    except Exception:
        return []
    deps = []
    project = data.get("project", {})
    for item in project.get("dependencies", []):
        m = re.match(r"^([A-Za-z0-9_.\-]+)(.*)$", item.strip())
        if m:
            deps.append((m.group(1), m.group(2).strip() or ">=0"))
    return deps


_PARSERS = {
    "requirements.txt": _parse_requirements,
    "package.json": _parse_package_json,
    "pyproject.toml": _parse_pyproject,
}

_DEP_TYPE = {
    "requirements.txt": "python",
    "pyproject.toml": "python",
    "package.json": "node",
}


async def _installed_pip(pkg: str) -> tuple[bool, str | None]:
    proc = await asyncio.create_subprocess_exec(
        "pip", "show", pkg,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
    )
    stdout, _ = await proc.communicate()
    if proc.returncode != 0:
        return False, None
    m = re.search(r"^Version:\s*(.+)$", stdout.decode(), re.M)
    return True, m.group(1).strip() if m else None


async def _installed_npm(pkg: str, workdir: Path) -> tuple[bool, str | None]:
    proc = await asyncio.create_subprocess_exec(
        "npm", "list", pkg, "--depth=0",
        cwd=str(workdir),
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
    )
    stdout, _ = await proc.communicate()
    if proc.returncode != 0:
        return False, None
    m = re.search(rf"{re.escape(pkg)}@([\w.\-]+)", stdout.decode())
    return (True, m.group(1)) if m else (True, None)


async def scan_deps(script_dir: Path) -> list[dict]:
    """扫描脚本目录的所有依赖文件并检测安装状态。返回依赖列表(可能为空)。"""
    dep_files = _find_dep_files(script_dir)
    if not dep_files:
        return []

    items = []
    seen = set()
    for dep_file in dep_files:
        text = (script_dir / dep_file).read_text(encoding="utf-8", errors="ignore")
        parser = _PARSERS[dep_file]
        dep_type = _DEP_TYPE[dep_file]
        raw = parser(text)
        for name, constraint in raw:
            if name in seen:
                continue
            seen.add(name)
            if dep_type == "python":
                installed, version = await _installed_pip(name)
            else:
                installed, version = await _installed_npm(name, script_dir)
            items.append({
                "name": name,
                "constraint": constraint,
                "type": dep_type,
                "installed": installed,
                "installed_version": version,
            })
    return items