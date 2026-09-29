"""运行历史筛选自检（SPEC §3 / §7.3 · V5-G v2.7）。

跑法：先起后端（cd backend && .venv/bin/python -m uvicorn app.main:app --port 8001），再
      cd backend && .venv/bin/python tests/test_history_filters.py

唯一职责：保证 `/api/run/history` 的筛选**真的作用于返回的行**，而不只是作用于 total。
背景（v2.7 修掉的既有缺陷）：items 的 query 在 JOIN 处被整体重建、丢掉全部 where，
于是 total 正确而行数据未过滤 —— 表现为「筛选后条数变了、行还是那些行」。
"""
import json
import sys
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:8001/api/run/history?page=1&page_size=50&"

# (查询串, 行内必须满足的字段, 期望值)；期望值为 None = 不过滤，只查自洽
CASES = [
    ("", None, None),
    ("status=success", "status", "success"),
    ("status=failed", "status", "failed"),
    ("status=running", "status", "running"),
    ("status=killed", "status", "killed"),
    ("status=timeout", "status", "timeout"),          # 库里无该状态 → 必须 items=0
    ("source=sched", "is_scheduled", 1),
    ("source=manual", "is_scheduled", 0),
    ("script_id=3", "script_id", 3),
    # 注意：script_id=0/缺省 = **不筛选**（后端 `if script_id:` 语义，前端空选择正是传 undefined）
    ("script_id=99", "script_id", 99),                # 不存在的脚本 → 必须 items=0
]


def get(query: str) -> dict:
    with urllib.request.urlopen(BASE + query, timeout=10) as resp:
        return json.load(resp)


def main() -> int:
    try:
        get("")
    except (urllib.error.URLError, OSError):
        print("跳过：未检测到后端 http://127.0.0.1:8001（先起 uvicorn 再跑本检查）")
        return 0

    failures = []
    for query, key, want in CASES:
        data = get(query)
        items, total = data["items"], data["total"]
        rows_ok = all(it.get(key) == want for it in items) if key else True
        count_ok = len(items) == min(total, 50)       # 条数与返回行必须自洽
        settled = total == 0 or len(items) > 0        # total>0 却一行不给 = 条件只作用于 count
        if not (rows_ok and count_ok and settled):
            failures.append((query, total, len(items), rows_ok, count_ok, settled))
        print(f"{'OK ' if rows_ok and count_ok and settled else '>> '}"
              f"{query or '(无筛选)':16} total={total:4d} items={len(items):3d} "
              f"行内字段一致={rows_ok} 条数自洽={count_ok}")

    if failures:
        print("\n失败：", failures)
        print("提示：若 total 对而行数据不变，检查 items 的 query 是否在 JOIN 处丢了 .where(*filters)")
        return 1
    print("\n全部通过：筛选同时作用于 count 与 items")
    return 0


if __name__ == "__main__":
    sys.exit(main())
