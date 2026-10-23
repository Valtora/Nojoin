"""Which routes the superuser alone may call, as the web app assumes.

Settings shows Backup and restore, the logs and the full user edit dialog only
to the superuser, the account created at first-run setup, and gives other
Owners and Admins a role-only dialog on the role endpoint (#483). These tests
fail if a route moves between the two groups without the UI following it.
"""

from collections.abc import Callable, Iterator
from typing import Any

import pytest
from fastapi.routing import APIRoute, APIWebSocketRoute

from backend.api.deps import (
    get_current_active_superuser,
    get_current_active_superuser_ws,
)
from backend.api.v1.endpoints import backup, system, users

SUPERUSER_DEPENDENCIES = {
    get_current_active_superuser,
    get_current_active_superuser_ws,
}


def _dependency_calls(dependant: Any) -> Iterator[Callable[..., Any]]:
    for dependency in dependant.dependencies:
        if dependency.call is not None:
            yield dependency.call
        yield from _dependency_calls(dependency)


def _requires_superuser(route: APIRoute | APIWebSocketRoute) -> bool:
    return any(
        call in SUPERUSER_DEPENDENCIES for call in _dependency_calls(route.dependant)
    )


def _routes(router: Any) -> dict[tuple[str, str], APIRoute | APIWebSocketRoute]:
    """A router's routes by method and path, the path relative to its prefix."""
    found: dict[tuple[str, str], APIRoute | APIWebSocketRoute] = {}
    for route in router.routes:
        if isinstance(route, APIRoute):
            for method in route.methods:
                found[(method, route.path)] = route
        elif isinstance(route, APIWebSocketRoute):
            found[("WS", route.path)] = route
    return found


def test_every_backup_route_requires_the_superuser() -> None:
    routes = _routes(backup.router)

    unguarded = [key for key, route in routes.items() if not _requires_superuser(route)]

    assert routes, "no backup routes found"
    assert unguarded == []


@pytest.mark.parametrize(
    ("router", "key"),
    [
        (system.router, ("GET", "/logs/download")),
        (system.router, ("WS", "/logs/live")),
        (users.router, ("PATCH", "/{user_id}")),
    ],
)
def test_logs_and_user_edit_require_the_superuser(router, key) -> None:
    assert _requires_superuser(_routes(router)[key])


@pytest.mark.parametrize(
    ("router", "key"),
    [
        # The role-only dialog, the users table and its create and delete.
        (users.router, ("PATCH", "/{user_id}/role")),
        (users.router, ("GET", "/")),
        (users.router, ("POST", "/")),
        (users.router, ("DELETE", "/{user_id}")),
        # Service health on Settings > System and logs.
        (system.router, ("GET", "/admin-health")),
    ],
)
def test_other_administrator_routes_do_not_require_the_superuser(router, key) -> None:
    assert not _requires_superuser(_routes(router)[key])
