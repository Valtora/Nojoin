"""give every people tag an owner, and keep tags within their owner's library

People tags (p_tags) belong to one user, as recording tags and people do. The
People page used to list people tags with no owner to every user, and tag ids
from a request were not checked against the user's own tags. This revision
repairs what that could leave. It deletes no tag.

1. A people tag with no owner goes to the user who uses it most: a use is a
   link from one of their people, or one of their own tags directly beneath
   it. A tie goes to the lowest user id. A tag nobody uses goes to the
   install's first Owner account (failing that its first superuser, then its
   first user).
2. A tag given to a user who already has one of that name is renamed
   "<name> (2)", or the next free number, so each user holds a name once.
3. A link from a person to a tag outside their owner's library moves to that
   user's own tag of the same name, created when they have none with the same
   name and colour and nested as step 4 places it. Where the person already
   holds that tag, the redundant link is removed.
4. A people tag nested under a tag outside its owner's library moves under the
   owner's own tag of that name when the parent had no owner before this
   revision, so its tree stays as the user saw it. Nested under another
   user's tag, which its owner never saw listed, it moves to the top level.
5. A recording tag nested under another user's tag, or under one with no
   owner, moves to the top level. Recording tags have always been listed to
   their owner only.

Links from people with no owner are left as they are, as is a tag with no
owner when there are no users. The log gives counts and ids, never names. A
second run changes nothing.

Downgrade leaves the data as it is: the previous revision reads owners the
same way, and taking an owner away again would show the tag to every user.

Revision ID: f704bbd87f98
Revises: 7cee4b4388a7
Create Date: 2026-10-22 12:00:00.000000

"""

import logging
from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Optional

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "f704bbd87f98"
down_revision = "7cee4b4388a7"
branch_labels = None
depends_on = None

# Alembic's own logger, so the summary prints beside "Running upgrade".
logger = logging.getLogger("alembic.runtime.migration")

_NOW = "now() AT TIME ZONE 'utc'"


@dataclass
class _Tag:
    name: str
    color: Optional[str]
    user_id: Optional[int]
    parent_id: Optional[int]


def _free_name(name: str, taken: set[str]) -> str:
    if name not in taken:
        return name
    number = 2
    while f"{name} ({number})" in taken:
        number += 1
    return f"{name} ({number})"


class _PeopleTags:
    """The p_tags rows, kept in step with every write this revision makes."""

    def __init__(self, bind: sa.engine.Connection) -> None:
        self.bind = bind
        self.tags = {
            row.id: _Tag(row.name, row.color, row.user_id, row.parent_id)
            for row in bind.execute(
                sa.text("SELECT id, name, color, user_id, parent_id FROM p_tags")
            )
        }
        self.had_no_owner = {
            tag_id for tag_id, tag in self.tags.items() if tag.user_id is None
        }
        # The name each tag had before this revision, for finding a user's own.
        self.first_name = {tag_id: tag.name for tag_id, tag in self.tags.items()}
        self.names_by_owner: dict[int, set[str]] = defaultdict(set)
        self.by_owner_and_name: dict[tuple[int, str], int] = {}
        for tag_id in sorted(self.tags):
            self._index(tag_id)
        self.created: list[int] = []

    def _index(self, tag_id: int) -> None:
        tag = self.tags[tag_id]
        if tag.user_id is not None:
            self.names_by_owner[tag.user_id].add(tag.name)
            self.by_owner_and_name.setdefault((tag.user_id, tag.name), tag_id)

    def owner(self, tag_id: int) -> Optional[int]:
        tag = self.tags.get(tag_id)
        return tag.user_id if tag else None

    def give(self, tag_id: int, owner: int) -> bool:
        """Give an ownerless tag to ``owner``; True when it had to be renamed."""
        tag = self.tags[tag_id]
        name = _free_name(tag.name, self.names_by_owner[owner])
        self.bind.execute(
            sa.text(
                "UPDATE p_tags SET user_id = :owner, name = :name,"
                f" updated_at = {_NOW} WHERE id = :id AND user_id IS NULL"
            ),
            {"owner": owner, "name": name, "id": tag_id},
        )
        renamed = name != tag.name
        tag.user_id, tag.name = owner, name
        self._index(tag_id)
        return renamed

    def parent_for(
        self, parent_id: Optional[int], owner: int, seen: frozenset[int]
    ) -> Optional[int]:
        """Where a tag of ``owner`` nested under ``parent_id`` should sit."""
        if parent_id is None or parent_id not in self.tags:
            return None
        if self.owner(parent_id) == owner:
            return parent_id
        if parent_id in self.had_no_owner and parent_id not in seen:
            return self.own_copy(parent_id, owner, seen)
        return None

    def own_copy(
        self, tag_id: int, owner: int, seen: frozenset[int] = frozenset()
    ) -> int:
        """The user's own tag standing for ``tag_id``, created when missing."""
        if self.owner(tag_id) == owner:
            return tag_id
        name = self.first_name.get(tag_id, self.tags[tag_id].name)
        existing = self.by_owner_and_name.get((owner, name))
        if existing is not None:
            return existing
        source = self.tags[tag_id]
        parent_id = self.parent_for(source.parent_id, owner, seen | {tag_id})
        new_id = self.bind.execute(
            sa.text(
                "INSERT INTO p_tags (created_at, updated_at, name, color, user_id,"
                f" parent_id) VALUES ({_NOW}, {_NOW}, :name, :color, :owner,"
                " :parent) RETURNING id"
            ),
            {
                "name": name,
                "color": source.color,
                "owner": owner,
                "parent": parent_id,
            },
        ).scalar_one()
        self.tags[new_id] = _Tag(name, source.color, owner, parent_id)
        self.first_name[new_id] = name
        self._index(new_id)
        self.created.append(new_id)
        return new_id


def _first_owner_account(bind: sa.engine.Connection) -> Optional[int]:
    return bind.execute(
        sa.text(
            "SELECT id FROM users"
            " ORDER BY (role = 'owner') DESC, is_superuser DESC, id LIMIT 1"
        )
    ).scalar()


def _give_owners(
    bind: sa.engine.Connection,
    tags: _PeopleTags,
    links: list,
    person_owner: dict[int, Optional[int]],
) -> None:
    uses: dict[int, Counter] = defaultdict(Counter)
    for link in links:
        owner = person_owner.get(link.global_speaker_id)
        if link.tag_id in tags.had_no_owner and owner is not None:
            uses[link.tag_id][owner] += 1
    children: dict[int, list[int]] = defaultdict(list)
    for tag_id, tag in tags.tags.items():
        if tag.parent_id is not None:
            children[tag.parent_id].append(tag_id)

    given: list[str] = []
    renamed: list[int] = []
    pending = sorted(tags.had_no_owner)
    # Repeated so a tag above one given an owner in this pass counts it.
    while pending:
        still_pending = []
        for tag_id in pending:
            counts = Counter(uses[tag_id])
            for child_id in children[tag_id]:
                child_owner = tags.owner(child_id)
                if child_owner is not None:
                    counts[child_owner] += 1
            if not counts:
                still_pending.append(tag_id)
                continue
            owner = min(counts, key=lambda user_id: (-counts[user_id], user_id))
            if tags.give(tag_id, owner):
                renamed.append(tag_id)
            given.append(f"{tag_id}->{owner}")
        if len(still_pending) == len(pending):
            break
        pending = still_pending

    unused: list[int] = []
    kept: list[int] = []
    first_owner = _first_owner_account(bind)
    for tag_id in pending:
        if first_owner is None:
            kept.append(tag_id)
            continue
        if tags.give(tag_id, first_owner):
            renamed.append(tag_id)
        unused.append(tag_id)
        given.append(f"{tag_id}->{first_owner}")

    logger.info(
        "People tag owners: %d given an owner [%s], %d of them used by nobody "
        "and given to the first Owner account %s, %d renamed to keep a name "
        "once per user %s; %d kept with no owner because there are no users %s",
        len(given),
        ", ".join(given),
        len(unused),
        unused,
        len(renamed),
        renamed,
        len(kept),
        kept,
    )


def _move_links(
    bind: sa.engine.Connection,
    tags: _PeopleTags,
    links: list,
    person_owner: dict[int, Optional[int]],
) -> None:
    held = {(link.global_speaker_id, link.tag_id) for link in links}
    moved: list[str] = []
    removed: list[int] = []
    for link in sorted(links, key=lambda row: row.id):
        owner = person_owner.get(link.global_speaker_id)
        if owner is None or link.tag_id not in tags.tags:
            continue
        if tags.owner(link.tag_id) == owner:
            continue
        target = tags.own_copy(link.tag_id, owner)
        if (link.global_speaker_id, target) in held:
            bind.execute(
                sa.text("DELETE FROM people_tags WHERE id = :id"), {"id": link.id}
            )
            removed.append(link.id)
        else:
            bind.execute(
                sa.text(
                    f"UPDATE people_tags SET tag_id = :target, updated_at = {_NOW}"
                    " WHERE id = :id"
                ),
                {"target": target, "id": link.id},
            )
            held.add((link.global_speaker_id, target))
            moved.append(f"{link.id}:{link.tag_id}->{target}")
        held.discard((link.global_speaker_id, link.tag_id))

    logger.info(
        "People tag links: %d moved to the person's owner's own tag [%s], %d "
        "removed because the person already held that tag %s",
        len(moved),
        ", ".join(moved),
        len(removed),
        removed,
    )


def _fix_people_tag_parents(bind: sa.engine.Connection, tags: _PeopleTags) -> None:
    changed: list[str] = []
    for tag_id in sorted(tags.tags):
        tag = tags.tags[tag_id]
        if tag.user_id is None or tag.parent_id is None:
            continue
        parent_id = tags.parent_for(tag.parent_id, tag.user_id, frozenset({tag_id}))
        if parent_id == tag.parent_id:
            continue
        bind.execute(
            sa.text(
                f"UPDATE p_tags SET parent_id = :parent, updated_at = {_NOW}"
                " WHERE id = :id"
            ),
            {"parent": parent_id, "id": tag_id},
        )
        changed.append(f"{tag_id}:{tag.parent_id}->{parent_id}")
        tag.parent_id = parent_id

    logger.info(
        "People tag parents: %d moved to their owner's own tag or the top "
        "level [%s]; %d people tags created for links and parents %s",
        len(changed),
        ", ".join(changed),
        len(tags.created),
        tags.created,
    )


def _fix_recording_tag_parents(bind: sa.engine.Connection) -> None:
    moved = sorted(
        bind.execute(
            sa.text(
                f"UPDATE tags AS child SET parent_id = NULL, updated_at = {_NOW}"
                " FROM tags AS parent"
                " WHERE parent.id = child.parent_id AND child.user_id IS NOT NULL"
                " AND parent.user_id IS DISTINCT FROM child.user_id"
                " RETURNING child.id"
            )
        ).scalars()
    )
    logger.info(
        "Recording tag parents: %d moved to the top level %s", len(moved), moved
    )


def upgrade() -> None:
    bind = op.get_bind()
    person_owner = {
        row.id: row.user_id
        for row in bind.execute(sa.text("SELECT id, user_id FROM global_speakers"))
    }
    links = list(
        bind.execute(
            sa.text(
                "SELECT id, global_speaker_id, tag_id FROM people_tags"
                " WHERE global_speaker_id IS NOT NULL AND tag_id IS NOT NULL"
            )
        )
    )
    tags = _PeopleTags(bind)
    _give_owners(bind, tags, links, person_owner)
    _move_links(bind, tags, links, person_owner)
    _fix_people_tag_parents(bind, tags)
    _fix_recording_tag_parents(bind)


def downgrade() -> None:
    # See the module docstring: the owners, links and parents set here stay.
    pass
