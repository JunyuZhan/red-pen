# -*- coding: utf-8 -*-
"""把 tests/ 下的脚本式测试（模块级脚本 + sys.exit）纳入 pytest 收集。

每个脚本以子进程 `PYTHONPATH=. python <file>` 运行，退出码非 0 即失败。
脚本清单动态发现（含 smoke_*），新增脚本测试无需登记。
"""
import os
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TESTS = os.path.join(ROOT, "tests")


def _script_tests():
    out = []
    for name in sorted(os.listdir(TESTS)):
        if not name.endswith(".py"):
            continue
        if not (name.startswith("test_") or name.startswith("smoke_")):
            continue
        if name == os.path.basename(__file__):
            continue
        with open(os.path.join(TESTS, name), encoding="utf-8") as f:
            if "def test_" in f.read():
                continue  # 已是 pytest 原生格式，由 pytest 直接收集
        out.append(name)
    return out


@pytest.mark.parametrize("script", _script_tests())
def test_script_suite(script):
    env = dict(os.environ, PYTHONPATH=ROOT, PYTHONDONTWRITEBYTECODE="1")
    r = subprocess.run(
        [sys.executable, os.path.join(TESTS, script)],
        env=env, capture_output=True, text=True, timeout=600,
    )
    assert r.returncode == 0, (
        f"{script} 退出码 {r.returncode}\n--- 输出末尾 ---\n"
        + "\n".join((r.stdout + "\n" + r.stderr).splitlines()[-40:])
    )
