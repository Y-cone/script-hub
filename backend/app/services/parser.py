"""Static script parameter parser: Python argparse, shell getopts, batch positional."""

import ast
import json
import re
from pathlib import Path


def _read_source(filepath: Path) -> str:
    """读脚本源码文本：按**探测出的编码**解码（复用批次 AA 的 probe_script_text，唯一探测实现）。

    此前一律 `read_text(encoding="utf-8")`：GBK 的 .bat/.ps1/.sh（Windows 目标上按系统 ANSI 存的
    就是 GBK）直接 UnicodeDecodeError。
    探测不出 → **ValueError 上抛**，不引入 latin-1 之类静默兜底（那正是批次 AA 刚删掉的东西）：
    本函数的上游是 routers/script.py 的 POST /{id}/parse，已把 ValueError 转成 400。
    """
    from .script_text import probe_script_text
    return probe_script_text(filepath.read_bytes())[0]


def _parse_python(filepath: Path) -> list[dict]:
    """Extract argparse params from a Python script via AST."""
    try:
        tree = ast.parse(_read_source(filepath))
    except SyntaxError:
        # 语法错 → 没有可提取的参数（编码探测失败仍会 ValueError 上抛，不被这里吞掉）
        return []

    params = []

    # 1) argparse: add_argument() calls
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
            continue
        if node.func.attr != "add_argument":
            continue

        name = None
        ptype = "string"
        default = None
        required = False
        description = None
        choices = None
        nargs_found = False

        for kw in node.keywords:
            if kw.arg == "action":
                if isinstance(kw.value, ast.Constant) and kw.value.value == "store_true":
                    ptype = "bool"
            elif kw.arg == "type":
                if isinstance(kw.value, ast.Name):
                    type_map = {"str": "string", "int": "int", "float": "float", "bool": "bool"}
                    ptype = type_map.get(kw.value.id, "string")
            elif kw.arg == "default":
                if isinstance(kw.value, ast.Constant):
                    default = str(kw.value.value)
            elif kw.arg == "required":
                if isinstance(kw.value, ast.Constant):
                    required = bool(kw.value.value)
            elif kw.arg == "help":
                if isinstance(kw.value, ast.Constant):
                    description = kw.value.value
            elif kw.arg == "choices":
                if isinstance(kw.value, ast.List):
                    choices = [e.value for e in kw.value.elts if isinstance(e, ast.Constant)]

        if node.args:
            first = node.args[0]
            if isinstance(first, ast.Constant):
                name = str(first.value)
            elif isinstance(first, ast.Starred) and isinstance(first.value, ast.List):
                names = [e.value for e in first.value.elts if isinstance(e, ast.Constant)]
                name = names[0] if names else None

        for kw in node.keywords:
            if kw.arg == "nargs":
                nargs_found = True

        if name and not nargs_found:
            params.append({
                "name": name,
                "type": ptype,
                "default": default,
                "required": required,
                "description": description,
                "choices": choices,
            })

    # 2) click: @click.option / @click.argument decorators
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef):
            continue
        for dec in node.decorator_list:
            if not (isinstance(dec, ast.Call) and isinstance(dec.func, ast.Attribute)):
                continue
            if dec.func.attr not in ("option", "argument"):
                continue

            name = None
            ptype = "string"
            default = None
            required = dec.func.attr == "argument"
            description = None
            choices = None
            is_flag = False

            # first positional arg is the name (--option-name or -n)
            if dec.args and isinstance(dec.args[0], ast.Constant):
                name = str(dec.args[0].value)

            for kw in dec.keywords:
                if kw.arg == "type":
                    if isinstance(kw.value, ast.Name):
                        type_map = {"str": "string", "int": "int", "float": "float"}
                        ptype = type_map.get(kw.value.id, "string")
                    elif isinstance(kw.value, ast.Call) and isinstance(kw.value.func, ast.Attribute):
                        # click.Choice([...])
                        if kw.value.func.attr == "Choice" and kw.value.args:
                            if isinstance(kw.value.args[0], ast.List):
                                choices = [e.value for e in kw.value.args[0].elts if isinstance(e, ast.Constant)]
                elif kw.arg == "default":
                    if isinstance(kw.value, ast.Constant):
                        default = str(kw.value.value)
                elif kw.arg == "required":
                    if isinstance(kw.value, ast.Constant):
                        required = bool(kw.value.value)
                elif kw.arg == "help":
                    if isinstance(kw.value, ast.Constant):
                        description = kw.value.value
                elif kw.arg == "is_flag":
                    if isinstance(kw.value, ast.Constant) and kw.value.value:
                        is_flag = True

            if is_flag:
                ptype = "bool"

            if name:
                params.append({
                    "name": name,
                    "type": ptype,
                    "default": default,
                    "required": required,
                    "description": description,
                    "choices": choices,
                })

    return params


def _parse_shell(filepath: Path) -> list[dict]:
    """Extract params from a shell script: getopts first, then $N / ${N:-default} fallback."""
    source = _read_source(filepath)

    # 1) getopts
    m = re.search(r"getopts\s+['\"]?\s*([a-zA-Z0-9:]+)\s*['\"]?", source)
    if m:
        optstr = m.group(1)
        params = []
        i = 0
        while i < len(optstr):
            char = optstr[i]
            has_arg = (i + 1 < len(optstr) and optstr[i + 1] == ":")
            params.append({
                "name": f"-{char}",
                "type": "string" if has_arg else "bool",
                "default": None,
                "required": False,
                "description": None,
                "choices": None,
            })
            i += 2 if has_arg else 1
        return params

    # 2) positional: $1, $2, ... or ${1:-default}
    pos: dict[int, dict] = {}
    for m in re.finditer(r"\$\{?(\d+)(?::-([^}]*))?\}?", source):
        n = int(m.group(1))
        if n in pos:
            continue
        raw_default = m.group(2)
        default_val = raw_default.strip().strip("'\"") if raw_default else None
        pos[n] = {
            "name": f"${n}",
            "type": "string",
            "default": default_val,
            "required": n == 1,  # ponytail: naive heuristic, first positional often required
            "description": None,
            "choices": None,
        }

    return [pos[k] for k in sorted(pos)]


def _parse_batch(filepath: Path) -> list[dict]:
    """Extract positional params (%1, %2, ...) from batch files."""
    source = _read_source(filepath)

    pos = set()
    for m in re.finditer(r"%(\d+)", source):
        pos.add(int(m.group(1)))

    return [
        {
            "name": f"%{n}",
            "type": "string",
            "default": None,
            "required": False,
            "description": None,
            "choices": None,
        }
        for n in sorted(pos)
    ]


_PARSERS = {
    "python": _parse_python,
    "shell": _parse_shell,
    "bat": _parse_batch,
    "powershell": _parse_python,  # rough: powershell often uses param() blocks, AST fallback
}


def parse_script(path: Path, category: str) -> list[dict]:
    """Parse a script file and return its parameter list."""
    parser = _PARSERS.get(category, lambda _p: [])
    return parser(path)
