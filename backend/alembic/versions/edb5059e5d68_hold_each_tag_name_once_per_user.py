"""hold each tag name once per user

Recording tags (tags) and people tags (p_tags) each belong to one user, and the
routes refuse a name the user already holds, but the database did not. Two
requests creating the same name at once could both pass the check, and a people
tag renamed to a taken name before #547 is still held twice. This revision
merges each user's repeated names, then adds a unique index on (user_id, name)
to both tables, so a race ends at the index instead of in a second copy.

Names match exactly, as the routes compare them: "Alpha" and "alpha" stay two
tags. A tag with no owner is left alone; no user lists it, and the index treats
each NULL owner as distinct.

For each user and name held more than once, the oldest tag (lowest id) is kept,
and each other copy is merged into it in id order:

1. The copy's meetings (recording_tags) and tasks (user_task_tags), or for a
   people tag its people (people_tags), move to the kept tag. A link the kept
   tag already has is removed instead, as is a second link from the copy to the
   same meeting.
2. The copy's sub-tags move under the kept tag. When the kept tag sits beneath
   the copy, it first takes the copy's place, so neither becomes its own
   ancestor. A move that would still close a loop, which only a loop saved
   before #547 can cause, goes to the top level instead.
3. The copy is deleted.

The kept tag keeps its own name, colour and place. The log gives counts and ids,
never names. A second run changes nothing.

Downgrade drops the two indexes and leaves the merged tags as they are.

Revision ID: edb5059e5d68
Revises: f704bbd87f98
Create Date: 2026-10-22 20:00:00.000000

"""

import logging
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Optional

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "edb5059e5d68"
down_revision = "f704bbd87f98"
branch_labels = None
depends_on = None

# Alembic's own logger, so the summary prints beside "Running upgrade".
logger = logging.getLogger("alembic.runtime.migration")

_NOW = "now() AT TIME ZONE 'utc'"

# table -> (index name, [(link table, the column a link is unique on)])
_TABLES = {
    "tags": (
        "uq_tags_user_name",
        [("recording_tags", "recording_id"), ("user_task_tags", "task_id")],
    ),
    "p_tags": ("uq_p_tags_user_name", [("people_tags", "global_speaker_id")]),
}


def _reaches(
    parent_of: dict[int, Optional[int]], start: Optional[int], target: int
) -> bool:
    """Whether walking up from ``start`` meets ``target``, stopping at a repeat."""
    passed: set[int] = set()
    node = start
    while node is not None and node not in passed:
        if node == target:
            return True
        passed.add(node)
        node = parent_of.get(node)
    return False


@dataclass
class _Summary:
    merged: list[str] = field(default_factory=list)
    links_moved: int = 0
    links_removed: int = 0
    moved: list[str] = field(default_factory=list)
    to_top: list[int] = field(default_factory=list)


class _Merge:
    """One table's tags, kept in step with every write this revision makes."""

    def __init__(self, bind: sa.engine.Connection, table: str) -> None:
        self.bind = bind
        self.table = table
        self.links = _TABLES[table][1]
        rows = list(
            bind.execute(sa.text(f"SELECT id, user_id, name, parent_id FROM {table}"))
        )
        self.parent_of: dict[int, Optional[int]] = {r.id: r.parent_id for r in rows}
        copies: dict[tuple[int, str], list[int]] = defaultdict(list)
        for row in rows:
            if row.user_id is not None:
                copies[(row.user_id, row.name)].append(row.id)
        self.groups = sorted(sorted(ids) for ids in copies.values() if len(ids) > 1)
        self.summary = _Summary()

    def _set_parent(self, tag_id: int, parent_id: Optional[int]) -> None:
        self.bind.execute(
            sa.text(
                f"UPDATE {self.table} SET parent_id = :parent, updated_at = {_NOW}"
                " WHERE id = :id"
            ),
            {"parent": parent_id, "id": tag_id},
        )
        self.summary.moved.append(f"{tag_id}:{self.parent_of[tag_id]}->{parent_id}")
        self.parent_of[tag_id] = parent_id

    def _place(self, tag_id: int, parent_id: Optional[int], *avoid: int) -> None:
        """Move ``tag_id`` under ``parent_id``, or to the top level if that loops."""
        if any(_reaches(self.parent_of, parent_id, node) for node in (tag_id, *avoid)):
            self.summary.to_top.append(tag_id)
            parent_id = None
        self._set_parent(tag_id, parent_id)

    def _move_links(self, dropped: int, kept: int) -> None:
        params = {"dropped": dropped, "kept": kept}
        for link_table, column in self.links:
            self.summary.links_removed += self.bind.execute(
                sa.text(
                    f"DELETE FROM {link_table} AS link WHERE link.tag_id = :dropped"
                    f" AND (EXISTS (SELECT 1 FROM {link_table} AS held"
                    f" WHERE held.tag_id = :kept AND held.{column} = link.{column})"
                    f" OR EXISTS (SELECT 1 FROM {link_table} AS twin"
                    f" WHERE twin.tag_id = :dropped AND twin.{column} = link.{column}"
                    " AND twin.id < link.id))"
                ),
                params,
            ).rowcount
            self.summary.links_moved += self.bind.execute(
                sa.text(
                    f"UPDATE {link_table} SET tag_id = :kept, updated_at = {_NOW}"
                    " WHERE tag_id = :dropped"
                ),
                params,
            ).rowcount

    def merge(self, dropped: int, kept: int) -> None:
        if _reaches(self.parent_of, self.parent_of[kept], dropped):
            # The kept tag sits beneath the copy, so it takes the copy's place.
            self._place(kept, self.parent_of[dropped], dropped)
        for child in sorted(c for c, p in self.parent_of.items() if p == dropped):
            self._place(child, kept)
        self._move_links(dropped, kept)
        self.bind.execute(
            sa.text(f"DELETE FROM {self.table} WHERE id = :id"), {"id": dropped}
        )
        del self.parent_of[dropped]
        self.summary.merged.append(f"{dropped}->{kept}")

    def run(self) -> None:
        for kept, *dropped in self.groups:
            for copy in dropped:
                self.merge(copy, kept)
        summary = self.summary
        logger.info(
            "%s: %d repeated names merged, %d copies removed [%s]; %d links moved "
            "to the kept tag, %d removed because it held them already; %d tags "
            "moved to a new parent [%s], %d of them to the top level to avoid a "
            "loop %s",
            self.table,
            len(self.groups),
            len(summary.merged),
            ", ".join(summary.merged),
            summary.links_moved,
            summary.links_removed,
            len(summary.moved),
            ", ".join(summary.moved),
            len(summary.to_top),
            summary.to_top,
        )


def upgrade() -> None:
    bind = op.get_bind()
    for table, (index_name, _) in _TABLES.items():
        _Merge(bind, table).run()
        op.create_index(index_name, table, ["user_id", "name"], unique=True)


def downgrade() -> None:
    # See the module docstring: the merged tags stay merged.
    for table, (index_name, _) in _TABLES.items():
        op.drop_index(index_name, table_name=table)
