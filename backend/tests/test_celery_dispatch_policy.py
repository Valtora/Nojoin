"""How the API behaves when Redis is unreachable.

Dispatching is a blocking socket call made from `async def` handlers, so an
unbounded wait does not slow one request, it stalls the process. These assert
the bounds that make that impossible, and the process asymmetry behind them.
"""

import ast
import inspect
import pathlib

from backend.api.v1.endpoints.transcripts import helpers
from backend.celery_app import (
    REDIS_CONNECT_TIMEOUT_SECONDS,
    apply_api_dispatch_limits,
    celery_app,
)


def test_connect_attempts_are_capped_on_both_redis_subsystems():
    """The broker and the result backend are configured through different keys.

    This is the trap: the broker reads `broker_transport_options`, but the
    result backend reads top-level `redis_*` keys and silently ignores a
    `socket_connect_timeout` placed in `result_backend_transport_options`. A
    cap that lands on only one of them leaves the other waiting out the
    kernel's TCP connect timeout, which is around two minutes per attempt.
    """
    assert (
        celery_app.conf.broker_transport_options["socket_connect_timeout"]
        == REDIS_CONNECT_TIMEOUT_SECONDS
    )
    assert (
        celery_app.backend.connparams["socket_connect_timeout"]
        == REDIS_CONNECT_TIMEOUT_SECONDS
    )


def test_api_limits_bound_both_retry_policies():
    """Worst case per dispatch is (1 + max_retries) * the connect cap."""
    apply_api_dispatch_limits()

    assert celery_app.conf.task_publish_retry_policy["max_retries"] <= 1
    # Not reachable through any top-level Celery setting; the backend keeps its
    # own policy, which defaults to 20 retries roughly a second apart.
    assert celery_app.backend.retry_policy["max_retries"] <= 1


def test_api_limits_rebuild_an_already_created_backend():
    """The backend is built once and cached, so applying limits must drop it.

    Without this the API would keep whichever policy happened to be in force
    the first time anything touched `celery_app.backend`, which is ordering
    dependent and would silently be the unbounded default.
    """
    celery_app.conf.result_backend_transport_options = {
        **celery_app.conf.result_backend_transport_options,
        "retry_policy": {"max_retries": 20},
    }
    celery_app._backend_cache = None
    celery_app._local.__dict__.pop("backend", None)
    assert celery_app.backend.retry_policy["max_retries"] == 20

    apply_api_dispatch_limits()

    assert celery_app.backend.retry_policy["max_retries"] <= 1


def test_best_effort_refresh_does_not_dispatch_on_the_event_loop():
    """Publishing blocks, so the one dispatch every mutation makes is offloaded.

    A coroutine function is the observable half of that: a plain `def` here
    would mean the publish ran inline on the loop, stalling every other request
    the process is serving rather than only this one.
    """
    assert inspect.iscoroutinefunction(helpers._dispatch_meeting_edge_refresh)
    assert "dispatch_task_best_effort" in inspect.getsource(
        helpers._dispatch_meeting_edge_refresh
    )


def test_no_api_code_dispatches_celery_work_on_the_event_loop():
    """The whole point, asserted over the tree rather than one call site.

    Every dispatch reachable from a request handler must go through
    `backend.core.task_dispatch`. A plain `send_task` inside an `async def`
    puts a blocking socket call back on the loop, where an unreachable Redis
    stalls every concurrent request rather than just its own.
    """
    offenders = []
    for path in pathlib.Path("backend").rglob("*.py"):
        if "/tests/" in str(path):
            continue
        tree = ast.parse(path.read_text())
        stack: list[bool] = []

        class Visitor(ast.NodeVisitor):
            def visit_FunctionDef(self, node):
                stack.append(False)
                self.generic_visit(node)
                stack.pop()

            def visit_AsyncFunctionDef(self, node):
                stack.append(True)
                self.generic_visit(node)
                stack.pop()

            def visit_Call(self, node):
                func = node.func
                if (
                    isinstance(func, ast.Attribute)
                    and func.attr in {"send_task", "apply_async", "delay"}
                    and stack
                    and stack[-1]
                ):
                    offenders.append(f"{path}:{node.lineno}")
                self.generic_visit(node)

        Visitor().visit(tree)

    assert not offenders, (
        "Celery dispatched inline from an async def. Use "
        "backend.core.task_dispatch.dispatch_task instead:\n  " + "\n  ".join(offenders)
    )
