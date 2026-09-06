"""Phase 2 Step 2.3 — every outbound HTTP call in the shipped backend must
pass an explicit ``timeout``. "Never an infinite spinner" (roadmap Step 2.3)
starts here: a hung request in the Python backend is a hung IPC call is a
frozen UI. This is an invariant lock — it passes today.
"""

import ast
import pathlib

import pytest

pytestmark = pytest.mark.unit

_SRC = pathlib.Path(__file__).resolve().parents[2] / "src"
_HTTP_METHODS = {"get", "post", "put", "patch", "delete", "head", "request"}


def _requests_calls_without_timeout(tree: ast.AST) -> list[int]:
    bad: list[int] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if (
            isinstance(func, ast.Attribute)
            and func.attr in _HTTP_METHODS
            and isinstance(func.value, ast.Name)
            and func.value.id == "requests"
        ):
            kwargs = {kw.arg for kw in node.keywords}
            if "timeout" not in kwargs and None not in kwargs:  # None => **kwargs splat
                bad.append(node.lineno)
    return bad


def test_no_unbounded_requests_call_in_src():
    offenders: list[str] = []
    for path in _SRC.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for lineno in _requests_calls_without_timeout(tree):
            offenders.append(f"{path.relative_to(_SRC.parent)}:{lineno}")
    assert not offenders, "requests.* call(s) missing an explicit timeout=:\n" + "\n".join(
        offenders
    )
