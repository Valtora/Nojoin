"""The integer ranges Postgres stores, for request fields that reach a column.

Pydantic parses an integer into a Python ``int`` of any size, and Postgres
refuses a value beyond its column type only when the query runs, which answered
500 (#923, #927). A field typed with these refuses such a value in validation
instead, with the 422 body every other invalid field gets.

They live here rather than in ``backend/api/params.py``, which re-exports them,
because the worker imports the models and has no FastAPI.
"""

from typing import Annotated

from annotated_types import Ge, Le

# Every primary key is a BIGINT, numbered from 1.
BIGINT_MAX = 2**63 - 1
# The largest INTEGER, the type of counters such as recordings.capture_epoch,
# and of LIMIT and OFFSET as SQLAlchemy binds them on Postgres.
INTEGER_MAX = 2**31 - 1

RowId = Annotated[int, Ge(1), Le(BIGINT_MAX)]
"""The id of a row: a person, tag, task, user, document or other record."""
