# -*- coding: utf-8 -*-
"""屏蔽 pytest 原生收集器对脚本式测试的 import。

仓库历史原因：多数 tests/*.py 是模块级脚本（print + sys.exit），
被 pytest 原生 import 时模块级代码会执行并触发 sys.exit。
脚本式测试由 tests/test_script_suite.py 以子进程方式统一运行。
"""
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_TESTS = os.path.join(ROOT, "tests")


def _is_script_test(name):
    if not name.endswith(".py"):
        return False
    if not (name.startswith("test_") or name.startswith("smoke_")):
        return False
    try:
        with open(os.path.join(_TESTS, name), encoding="utf-8") as f:
            return "def test_" not in f.read()
    except OSError:
        return False


collect_ignore = [name for name in os.listdir(_TESTS) if _is_script_test(name)]
