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
# HTTP client libraries whose call sites must carry an explicit timeout. If a
# future module imports one of these, this test starts checking its calls too.
_HTTP_LIBS = {"requests", "httpx", "urllib3"}


def _http_client_names(tree: ast.AST) -> set[str]:
    """Local names bound to an HTTP client module or a Session/Client object
    built from one — `import requests`, `import httpx as h`,
    `s = requests.Session()`, `c = httpx.Client()`."""
    names: set[str] = set(_HTTP_LIBS)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split(".")[0] in _HTTP_LIBS:
                    names.add(alias.asname or alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom) and (node.module or "").split(".")[0] in _HTTP_LIBS:
            for alias in node.names:
                names.add(alias.asname or alias.name)
        elif isinstance(node, ast.Assign):
            call = node.value
            if (
                isinstance(call, ast.Call)
                and isinstance(call.func, ast.Attribute)
                and call.func.attr in {"Session", "session", "Client", "AsyncClient"}
                and isinstance(call.func.value, ast.Name)
                and call.func.value.id in names
            ):
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        names.add(target.id)
    return names


def _unbounded_http_calls(tree: ast.AST) -> list[int]:
    clients = _http_client_names(tree)
    bad: list[int] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not (isinstance(func, ast.Attribute) and func.attr in _HTTP_METHODS):
            continue
        recv = func.value
        # `requests.get(...)` / `client.get(...)` — receiver is a known client name.
        if not (isinstance(recv, ast.Name) and recv.id in clients):
            continue
        kwargs = {kw.arg for kw in node.keywords}
        if "timeout" not in kwargs and None not in kwargs:  # None => **kwargs splat
            bad.append(node.lineno)
    return bad


def test_no_unbounded_http_call_in_src():
    offenders: list[str] = []
    for path in _SRC.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for lineno in _unbounded_http_calls(tree):
            offenders.append(f"{path.relative_to(_SRC.parent)}:{lineno}")
    assert not offenders, "outbound HTTP call(s) missing an explicit timeout=:\n" + "\n".join(
        offenders
    )
