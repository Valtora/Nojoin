"""Batching for statements whose bind parameter count scales with the data.

PostgreSQL accepts at most 65535 bind parameters in one statement, asyncpg
stops at 32767, and SQLite applies a ceiling of its own. Nothing in SQLAlchemy
enforces these. A multi-row insert or a large ``IN`` clause builds without
complaint and fails at execution time once the row or item count is high
enough, which means the failure arrives in production on the first large
recording, calendar or batch rather than in review.

The ceiling also depends on the driver, and that asymmetry is what let it reach
production here. The API reaches Postgres through asyncpg. The Celery workers
then ran psycopg2, which wrote parameters into the SQL text and had no ceiling,
so shared code that a worker exercised on every segment upload broke on the API
path while passing every test the worker path had. The workers now use
psycopg 3, which fails past 65535, so a statement between the two ceilings
still passes through a worker and fails through the API.

Use :func:`bind_batches` wherever the number of rows or items comes from the
data rather than from a fixed-size literal.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from typing import TypeVar

T = TypeVar("T")

# Held below asyncpg's 32767, the lowest ceiling of the drivers in use, with
# headroom so that widening a table cannot creep over the edge between the batch
# being sized and the statement being built.
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
