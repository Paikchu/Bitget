"""
Full strategy code validator with structured error output (line numbers for Monaco markers).
"""
from __future__ import annotations
import ast
import re
from typing import List, Optional, TypedDict

from bitget_bot.sandbox.ast_validator import ALLOWED_IMPORTS, FORBIDDEN_BUILTINS, FORBIDDEN_ATTRS


class CodeError(TypedDict):
    type: str        # "syntax" | "security" | "interface"
    severity: str    # "error" | "warning"
    line: Optional[int]
    col: Optional[int]
    end_line: Optional[int]
    message: str


class ValidationResult(TypedDict):
    valid: bool
    errors: List[CodeError]


class TracebackInfo(TypedDict):
    line: Optional[int]
    error_type: str
    message: str
    full_traceback: str


class _DetailedValidator(ast.NodeVisitor):
    def __init__(self):
        self.errors: List[CodeError] = []
        self._defined_functions: set[str] = set()

    def _err(self, node, msg: str, severity="error", etype="security"):
        self.errors.append(CodeError(
            type=etype, severity=severity,
            line=getattr(node, "lineno", None),
            col=getattr(node, "col_offset", None),
            end_line=getattr(node, "end_lineno", None),
            message=msg,
        ))

    def visit_Import(self, node):
        for alias in node.names:
            top = alias.name.split(".")[0]
            if top not in ALLOWED_IMPORTS:
                self._err(node, f"不允许导入 '{alias.name}'（白名单外模块）")
        self.generic_visit(node)

    def visit_ImportFrom(self, node):
        top = (node.module or "").split(".")[0]
        if top not in ALLOWED_IMPORTS:
            self._err(node, f"不允许 'from {node.module} import ...'（白名单外模块）")
        self.generic_visit(node)

    def visit_Call(self, node):
        if isinstance(node.func, ast.Name) and node.func.id in FORBIDDEN_BUILTINS:
            self._err(node, f"禁止调用 '{node.func.id}()'")
        self.generic_visit(node)

    def visit_Attribute(self, node):
        if node.attr in FORBIDDEN_ATTRS:
            self._err(node, f"禁止访问 '{node.attr}'（潜在沙箱逃逸路径）")
        self.generic_visit(node)

    def visit_FunctionDef(self, node):
        self._defined_functions.add(node.name)
        self.generic_visit(node)

    def visit_AsyncFunctionDef(self, node):
        self._err(node, "不允许使用 async/await", etype="interface")
        self.generic_visit(node)


def validate_code_full(source: str) -> ValidationResult:
    """Validates strategy code, returns structured errors with line numbers."""
    errors: List[CodeError] = []

    try:
        tree = ast.parse(source)
    except SyntaxError as e:
        return ValidationResult(valid=False, errors=[CodeError(
            type="syntax", severity="error",
            line=e.lineno, col=e.offset, end_line=e.lineno,
            message=f"语法错误: {e.msg}",
        )])

    validator = _DetailedValidator()
    validator.visit(tree)
    errors.extend(validator.errors)

    if "add_indicators" not in validator._defined_functions:
        errors.append(CodeError(type="interface", severity="error", line=None, col=None, end_line=None,
            message="缺少必须的函数 'add_indicators(df)'"))
    if "get_signal" not in validator._defined_functions:
        errors.append(CodeError(type="interface", severity="error", line=None, col=None, end_line=None,
            message="缺少必须的函数 'get_signal(df, i, params)'"))

    return ValidationResult(valid=len(errors) == 0, errors=errors)


def parse_traceback(stderr: str) -> TracebackInfo:
    """Parses Python traceback from Docker stderr to extract line number."""
    strategy_line = None
    for match in re.finditer(r'File "<strategy>",\s+line\s+(\d+)', stderr):
        strategy_line = int(match.group(1))

    last_line = stderr.strip().split("\n")[-1] if stderr.strip() else ""
    err_match = re.match(r'^(\w+(?:Error|Exception|Warning)?):\s*(.*)', last_line)
    if err_match:
        error_type, message = err_match.group(1), err_match.group(2)
    else:
        error_type, message = "RuntimeError", last_line or "未知运行时错误"

    return TracebackInfo(
        line=strategy_line,
        error_type=error_type,
        message=f"{error_type}: {message}",
        full_traceback=stderr,
    )
