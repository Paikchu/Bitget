"""
Strategy code AST static analysis — first security gate.
Uses whitelist approach: only known-safe imports are allowed.
"""
from __future__ import annotations
import ast
from typing import List

ALLOWED_IMPORTS: frozenset[str] = frozenset({
    "numpy", "np", "pandas", "pd", "math", "cmath",
    "statistics", "dataclasses", "typing", "collections",
    "functools", "itertools", "operator", "datetime",
    "decimal", "fractions", "numbers", "abc", "copy",
})

FORBIDDEN_BUILTINS: frozenset[str] = frozenset({
    "exec", "eval", "compile", "open", "__import__",
    "breakpoint", "input", "memoryview", "vars", "dir",
})

FORBIDDEN_ATTRS: frozenset[str] = frozenset({
    "__class__", "__bases__", "__subclasses__",
    "__globals__", "__builtins__", "__code__",
    "__loader__", "__spec__", "__file__", "__dict__",
    "f_locals", "f_globals", "f_back",
    "tb_frame", "tb_next", "gi_frame", "gi_code",
    "co_consts", "co_code", "co_filename",
})


class _StrategyValidator(ast.NodeVisitor):
    def __init__(self):
        self.errors: List[str] = []
        self._defined_functions: set[str] = set()

    def visit_Import(self, node: ast.Import):
        for alias in node.names:
            top = alias.name.split(".")[0]
            if top not in ALLOWED_IMPORTS:
                self.errors.append(
                    f"第 {node.lineno} 行: 不允许导入 '{alias.name}'（白名单外模块）"
                )
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom):
        top = (node.module or "").split(".")[0]
        if top not in ALLOWED_IMPORTS:
            self.errors.append(
                f"第 {node.lineno} 行: 不允许 'from {node.module} import ...'（白名单外模块）"
            )
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call):
        if isinstance(node.func, ast.Name) and node.func.id in FORBIDDEN_BUILTINS:
            self.errors.append(
                f"第 {node.lineno} 行: 禁止调用 '{node.func.id}()'"
            )
        self.generic_visit(node)

    def visit_Attribute(self, node: ast.Attribute):
        if node.attr in FORBIDDEN_ATTRS:
            self.errors.append(
                f"第 {node.lineno} 行: 禁止访问属性 '{node.attr}'（潜在逃逸路径）"
            )
        self.generic_visit(node)

    def visit_FunctionDef(self, node: ast.FunctionDef):
        self._defined_functions.add(node.name)
        self.generic_visit(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef):
        self.errors.append(
            f"第 {node.lineno} 行: 不允许使用 async/await（策略必须是同步代码）"
        )
        self.generic_visit(node)


def validate_strategy_code(source: str) -> List[str]:
    """
    Validates strategy code statically.
    Returns list of error strings. Empty list = passed.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError as e:
        return [f"语法错误 (第 {e.lineno} 行): {e.msg}"]

    validator = _StrategyValidator()
    validator.visit(tree)

    if "add_indicators" not in validator._defined_functions:
        validator.errors.append("缺少必须的函数 'add_indicators(df)'")
    if "get_signal" not in validator._defined_functions:
        validator.errors.append("缺少必须的函数 'get_signal(df, i, params)'")

    return validator.errors
