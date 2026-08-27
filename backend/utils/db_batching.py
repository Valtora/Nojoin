"""Batching for statements whose bind parameter count scales with the data.

PostgreSQL's wire protocol carries the bind parameter count in a signed 16-bit
field, so a single statement accepts at most 32767 of them, and SQLite applies
an equivalent ceiling. Nothing in SQLAlchemy enforces this. A multi-row insert
or a large ``IN`` clause builds without complaint and fails at execution time
once the row or item count is high enough, which means the failure arrives in
production on the first large recording, calendar or batch rather than in
review.

The limit is also driver-dependent, and that asymmetry is what let it reach
production here. The Celery workers reach Postgres through psycopg2, which
interpolates parameters client-side and sends literal SQL, so no bind limit
applies on their path. The API reaches it through asyncpg, which always uses
the extended query protocol, so the limit does apply. Shared code that a worker
exercises on every segment upload can therefore be broken on the API path while
passing every test the worker path has.

Use :func:`bind_batches` wherever the number of rows or items comes from the
data rather than from a fixed-size literal.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from typing import TypeVar

T = TypeVar("T")

# Held below the 32767 that PostgreSQL and SQLite each allow in one statement,
# with headroom so that widening a table cannot creep over the edge between the
# batch being sized and the statement being built.
MAX_BIND_PARAMS = 30_000


def bind_batch_size(params_per_item: int) -> int:
    """Largest item count that fits in one statement at this parameter width.

    ``params_per_item`` is the number of bind parameters each item contributes:
    the column count for a multi-row insert, or 1 for an ``IN`` clause. Deriving
    the batch size from it, rather than hardcoding a row count, keeps the bound
    correct when a table gains a column.

    Always at least 1, so an item wider than the whole budget is attempted on
    its own rather than producing an empty batch and an endless loop.
    """
    return max(1, MAX_BIND_PARAMS // max(1, params_per_item))


def bind_batches(
    items: Sequence[T],
    *,
    params_per_item: int = 1,
) -> Iterator[Sequence[T]]:
    """Split ``items`` into slices that each stay under the bind limit.

    Yields nothing for an empty sequence, so a caller can loop without a
    separate emptiness check.
    """
    batch_size = bind_batch_size(params_per_item)
    for start in range(0, len(items), batch_size):
        yield items[start : start + batch_size]
