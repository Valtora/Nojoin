"""Integer request parameters bounded to what Postgres accepts for them.

FastAPI parses an integer parameter into a Python ``int`` of any size, and
Postgres refuses a value beyond the type it binds the parameter as only when
the query runs, which answered 500 (#923). These types refuse such a value in
validation instead, with the 422 body every other invalid parameter gets.

They hold only constraints, so a route keeps a plain default and an MCP tool
that calls it directly passes plain values. Those calls skip FastAPI's
validation, so a tool that hands a caller's value on to a route takes the same
type in its own signature, where the MCP SDK validates it.
"""

from typing import Annotated

from annotated_types import Ge, Le
from fastapi import Query

# Every primary key is a BIGINT, numbered from 1.
BIGINT_MAX = 2**63 - 1
# SQLAlchemy binds LIMIT and OFFSET as INTEGER on Postgres.
INTEGER_MAX = 2**31 - 1

RowId = Annotated[int, Ge(1), Le(BIGINT_MAX)]
"""The id of a row: a person, tag, task, user, document or other record."""

RowIdsQuery = Annotated[list[RowId] | None, Query()]
"""A repeated query parameter of row ids, such as ``tag_ids=3&tag_ids=7``."""

RevisionCursor = Annotated[int, Ge(0), Le(BIGINT_MAX)]
"""A transcript's revision cursor, the id of the last utterance event read."""

PageOffset = Annotated[int, Ge(0), Le(INTEGER_MAX)]
"""How many rows a list skips, its ``skip``."""

PageLimit = Annotated[int, Ge(0), Le(INTEGER_MAX)]
"""The most rows a list returns, its ``limit``."""
