"""Giving an older archive's people and people tags owners, as the upgrade does.

Revisions 7cee4b4388a7 and f704bbd87f98 give every person (``global_speakers``)
and people tag (``p_tags``) an owner, move each link into another user's library
to the linking user's own person or tag of the same name, and keep tag parents
within one user's library. An archive made before them still holds what they
repair. The restore skips a row with no owner, since ``user_id`` is an ownership
link (``format.RESTORE_FOREIGN_KEYS``), and with it every speaker link, tag link
and drift refusal that named the row; a link into another user's library would
come back as it was.

So the restore applies the same rules to the archive's rows before its insert
loop, keyed by the archive's own ids, and an old archive restores as an archive
of the upgraded installation would, through the same per-row logic: the Skip
and Overwrite modes, the identity matching, and nothing written but the rows the
restore inserts or merges. The steps follow the two revisions one for one and
in their order, and ``test_backup_legacy_owners_postgres.py`` holds them to the
real chain. The revisions stay self-contained, as every revision is, so
the rules are written here a second time rather than shared.

An archive made since then holds nothing to repair, and the plan is empty. A
person with no owner and no link from an owned recording keeps no owner, as on
the upgraded installation, and is skipped as before. The archive format is
unchanged.
"""

import json
import logging
import zipfile
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)

Row = Dict[str, Any]

USERS = "users"
RECORDINGS = "recordings"
PEOPLE = "global_speakers"
SPEAKERS = "recording_speakers"
PEOPLE_TAGS = "p_tags"
PEOPLE_TAG_LINKS = "people_tag_links"
RECORDING_TAGS = "tags"

# The columns the repairs read, so a large member is not held whole.
_COLUMNS: Dict[str, Tuple[str, ...]] = {
    USERS: ("role", "is_superuser"),
    RECORDINGS: ("user_id",),
    PEOPLE: ("name", "color", "user_id"),
    SPEAKERS: ("recording_id", "global_speaker_id"),
    PEOPLE_TAGS: ("name", "color", "user_id", "parent_id"),
    PEOPLE_TAG_LINKS: ("global_speaker_id", "tag_id"),
    RECORDING_TAGS: ("user_id", "parent_id"),
}


def _free_name(name: str, taken: Set[str]) -> str:
    if name not in taken:
        return name
    number = 2
    while f"{name} ({number})" in taken:
        number += 1
    return f"{name} ({number})"


def _row_id(row: Row) -> Optional[int]:
    value = row.get("id")
    return value if isinstance(value, int) and not isinstance(value, bool) else None


@dataclass
class _TableRepairs:
    """The changes to one archive table, keyed by the archive's ids."""

    updates: Dict[int, Row] = field(default_factory=dict)
    added: List[Row] = field(default_factory=list)
    removed: Set[int] = field(default_factory=set)


@dataclass
class OwnerRepairs:
    """What the restore changes in an archive's rows before inserting them."""

    tables: Dict[str, _TableRepairs] = field(default_factory=dict)

    def table(self, table_name: str) -> _TableRepairs:
        return self.tables.setdefault(table_name, _TableRepairs())

    def apply(self, table_name: str, rows: List[Row]) -> List[Row]:
        """The archive's rows for this table with the repairs made."""
        repairs = self.tables.get(table_name)
        if repairs is None:
            return rows
        repaired: List[Row] = []
        for row in rows:
            row_id = _row_id(row)
            if row_id in repairs.removed:
                continue
            changes = repairs.updates.get(row_id) if row_id is not None else None
            repaired.append({**row, **changes} if changes else row)
        repaired.extend(dict(row) for row in repairs.added)
        return repaired


class _Ids:
    """Archive ids for the rows the repairs create.

    Negative, so no id or reference anywhere in the archive, all of which a
    database sequence issued, can name one of them.
    """

    def __init__(self) -> None:
        self._last = 0

    def take(self) -> int:
        self._last -= 1
        return self._last


class _Library:
    """Each user's names in one table, and the first row holding each name."""

    def __init__(self) -> None:
        self.names: Dict[int, Set[str]] = defaultdict(set)
        self.by_name: Dict[Tuple[int, str], int] = {}

    def add(self, owner: int, name: str, row_id: int) -> None:
        self.names[owner].add(name)
        self.by_name.setdefault((owner, name), row_id)


@dataclass
class _Named:
    name: str
    color: Optional[str]
    user_id: Optional[int]
    parent_id: Optional[int] = None


def _named(rows: Dict[int, Row]) -> Dict[int, _Named]:
    return {
        row_id: _Named(
            name=row.get("name") or "",
            color=row.get("color"),
            user_id=row.get("user_id"),
            parent_id=row.get("parent_id"),
        )
        for row_id, row in rows.items()
    }


@dataclass
class _Archive:
    """The columns the repairs read, by table and archive id."""

    tables: Dict[str, Dict[int, Row]]

    def __getitem__(self, table_name: str) -> Dict[int, Row]:
        return self.tables[table_name]

    def recording_owner(self, speaker: Row) -> Optional[int]:
        recording = self.tables[RECORDINGS].get(speaker["recording_id"])
        return recording["user_id"] if recording else None


def _archived_rows(zipf: zipfile.ZipFile, table_name: str) -> List[Row]:
    member = f"{table_name}.json"
    if member not in zipf.namelist():
        return []
    try:
        rows = json.loads(zipf.read(member))
    except (ValueError, OSError, zipfile.BadZipFile) as e:
        # The preflight refuses an unreadable member, so this is a backstop only.
        logger.warning("Owner repairs cannot read %s: %s", member, e)
        return []
    if not isinstance(rows, list):
        return []
    return [row for row in rows if isinstance(row, dict)]


def _read_archive(zipf: zipfile.ZipFile) -> _Archive:
    tables: Dict[str, Dict[int, Row]] = {}
    for table_name, columns in _COLUMNS.items():
        tables[table_name] = {
            row_id: {column: row.get(column) for column in columns}
            for row in _archived_rows(zipf, table_name)
            if (row_id := _row_id(row)) is not None
        }
    return _Archive(tables)


# 7cee4b4388a7: give every person an owner, in the library of the recordings
# that link them.


@dataclass
class _People:
    """The archive's people, kept in step with every repair made here."""

    people: Dict[int, _Named]
    library: _Library = field(default_factory=_Library)
    ids: _Ids = field(default_factory=_Ids)
    given: List[str] = field(default_factory=list)
    renamed: List[int] = field(default_factory=list)
    created: List[Row] = field(default_factory=list)

    @classmethod
    def from_archive(cls, archive: _Archive) -> "_People":
        people = cls(_named(archive[PEOPLE]))
        for person_id in sorted(people.people):
            person = people.people[person_id]
            if person.user_id is not None:
                people.library.add(person.user_id, person.name, person_id)
        return people

    def give(self, person_id: int, owner: int) -> None:
        person = self.people[person_id]
        name = _free_name(person.name, self.library.names[owner])
        if name != person.name:
            self.renamed.append(person_id)
        person.user_id, person.name = owner, name
        self.library.add(owner, name, person_id)
        self.given.append(f"{person_id}->{owner}")

    def own_person(self, owner: int, name: str, color: Optional[str]) -> int:
        """The user's person of this name, created with name and colour only."""
        existing = self.library.by_name.get((owner, name))
        if existing is not None:
            return existing
        person_id = self.ids.take()
        self.people[person_id] = _Named(name, color, owner)
        self.library.add(owner, name, person_id)
        self.created.append(
            {
                "id": person_id,
                "name": name,
                "color": color,
                "user_id": owner,
                "is_voiceprint_locked": False,
            }
        )
        return person_id


def _link_counts(archive: _Archive) -> Dict[int, Dict[int, int]]:
    # Links per person and recording owner. A merged row keeps its link, so
    # merged rows count too.
    counts: Dict[int, Dict[int, int]] = defaultdict(dict)
    for speaker in archive[SPEAKERS].values():
        person_id = speaker.get("global_speaker_id")
        owner_id = archive.recording_owner(speaker)
        if person_id is None or owner_id is None:
            continue
        counts[person_id][owner_id] = counts[person_id].get(owner_id, 0) + 1
    return counts


def _give_people_owners(
    people: _People, link_counts: Dict[int, Dict[int, int]]
) -> None:
    ownerless = (p for p, person in people.people.items() if person.user_id is None)
    for person_id in sorted(ownerless):
        counts = link_counts.get(person_id)
        # With no link from an owned recording, a person keeps no owner.
        if counts:
            owner = min(counts, key=lambda user_id: (-counts[user_id], user_id))
            people.give(person_id, owner)


def _move_people_links(
    archive: _Archive, people: _People, link_counts: Dict[int, Dict[int, int]]
) -> Dict[Tuple[int, int], int]:
    """Where each link from one user's recording to another's person goes."""
    targets: Dict[Tuple[int, int], int] = {}
    for person_id in sorted(p for p in link_counts if p in archive[PEOPLE]):
        person = people.people[person_id]
        for linking_owner in sorted(link_counts[person_id]):
            if person.user_id in (None, linking_owner):
                continue
            # The name the link showed, not a suffix the person was just given.
            name = archive[PEOPLE][person_id].get("name") or ""
            targets[(person_id, linking_owner)] = people.own_person(
                linking_owner, name, person.color
            )
    return targets


def _repair_people(archive: _Archive, repairs: OwnerRepairs) -> Dict[int, Any]:
    """Settle every person's owner. Returns each person's owner afterwards."""
    people = _People.from_archive(archive)
    link_counts = _link_counts(archive)
    _give_people_owners(people, link_counts)
    targets = _move_people_links(archive, people, link_counts)

    archived = _named(archive[PEOPLE])
    for person_id, before in archived.items():
        after = people.people[person_id]
        if (after.user_id, after.name) != (before.user_id, before.name):
            repairs.table(PEOPLE).updates[person_id] = {
                "user_id": after.user_id,
                "name": after.name,
            }
    if people.created:
        repairs.table(PEOPLE).added.extend(people.created)
    moved = 0
    for speaker_id, speaker in archive[SPEAKERS].items():
        key = (speaker.get("global_speaker_id"), archive.recording_owner(speaker))
        if key in targets:
            repairs.table(SPEAKERS).updates[speaker_id] = {
                "global_speaker_id": targets[key]
            }
            moved += 1

    if people.given or moved:
        logger.info(
            "Archive people: %d given an owner [%s], %d renamed to keep a name "
            "once per library %s; %d speaker links moved to the linking user's "
            "own person, %d people created for them",
            len(people.given),
            ", ".join(people.given),
            len(people.renamed),
            people.renamed,
            moved,
            len(people.created),
        )
    return {person_id: person.user_id for person_id, person in people.people.items()}


# f704bbd87f98: give every people tag an owner, and keep tags within their
# owner's library.


class _PeopleTags:
    """The archive's people tags, kept in step with every repair made here."""

    def __init__(self, rows: Dict[int, Row]) -> None:
        self.tags = _named(rows)
        self.had_no_owner = {
            tag_id for tag_id, tag in self.tags.items() if tag.user_id is None
        }
        # The name each tag had in the archive, for finding a user's own.
        self.first_name = {tag_id: tag.name for tag_id, tag in self.tags.items()}
        self.library = _Library()
        for tag_id in sorted(self.tags):
            self._index(tag_id)
        self.ids = _Ids()
        self.created: List[int] = []
        self.given: List[str] = []
        self.renamed: List[int] = []

    def _index(self, tag_id: int) -> None:
        tag = self.tags[tag_id]
        if tag.user_id is not None:
            self.library.add(tag.user_id, tag.name, tag_id)

    def owner(self, tag_id: int) -> Optional[int]:
        tag = self.tags.get(tag_id)
        return tag.user_id if tag else None

    def give(self, tag_id: int, owner: int) -> None:
        """Give an ownerless tag to ``owner``, renamed if they hold its name."""
        tag = self.tags[tag_id]
        name = _free_name(tag.name, self.library.names[owner])
        if name != tag.name:
            self.renamed.append(tag_id)
        tag.user_id, tag.name = owner, name
        self._index(tag_id)
        self.given.append(f"{tag_id}->{owner}")

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
        existing = self.library.by_name.get((owner, name))
        if existing is not None:
            return existing
        source = self.tags[tag_id]
        parent_id = self.parent_for(source.parent_id, owner, seen | {tag_id})
        new_id = self.ids.take()
        self.tags[new_id] = _Named(name, source.color, owner, parent_id)
        self.first_name[new_id] = name
        self._index(new_id)
        self.created.append(new_id)
        return new_id


def _first_owner_account(users: Dict[int, Row]) -> Optional[int]:
    """The install's first Owner account, failing that its first superuser."""
    ranked = sorted(
        (row.get("role") != "owner", not row.get("is_superuser"), user_id)
        for user_id, row in users.items()
    )
    return ranked[0][2] if ranked else None


def _tag_uses(
    tags: _PeopleTags, links: Dict[int, Row], person_owner: Dict[int, Any]
) -> Dict[int, Counter[int]]:
    uses: Dict[int, Counter[int]] = defaultdict(Counter)
    for link in links.values():
        owner = person_owner.get(link["global_speaker_id"])
        if link["tag_id"] in tags.had_no_owner and owner is not None:
            uses[link["tag_id"]][owner] += 1
    return uses


def _tag_users(
    tags: _PeopleTags,
    tag_id: int,
    uses: Dict[int, Counter[int]],
    children: Dict[int, List[int]],
) -> Counter[int]:
    """A tag's uses: links from each user's people, and their tags beneath it."""
    counts = Counter(uses[tag_id])
    for child_id in children[tag_id]:
        child_owner = tags.owner(child_id)
        if child_owner is not None:
            counts[child_owner] += 1
    return counts


def _give_tag_owners(
    tags: _PeopleTags,
    links: Dict[int, Row],
    person_owner: Dict[int, Any],
    users: Dict[int, Row],
) -> None:
    uses = _tag_uses(tags, links, person_owner)
    children: Dict[int, List[int]] = defaultdict(list)
    for tag_id, tag in tags.tags.items():
        if tag.parent_id is not None:
            children[tag.parent_id].append(tag_id)

    pending = sorted(tags.had_no_owner)
    # Repeated so a tag above one given an owner in this pass counts it.
    while pending:
        still_pending = []
        for tag_id in pending:
            counts = _tag_users(tags, tag_id, uses, children)
            if counts:
                tags.give(tag_id, min(counts, key=lambda user: (-counts[user], user)))
            else:
                still_pending.append(tag_id)
        if len(still_pending) == len(pending):
            break
        pending = still_pending

    # A tag nobody uses goes to the first Owner account. With no users it
    # keeps no owner, and the restore skips it.
    first_owner = _first_owner_account(users)
    if first_owner is not None:
        for tag_id in pending:
            tags.give(tag_id, first_owner)


def _move_tag_links(
    tags: _PeopleTags,
    links: Dict[int, Row],
    person_owner: Dict[int, Any],
    repairs: OwnerRepairs,
) -> None:
    held = {(link["global_speaker_id"], link["tag_id"]) for link in links.values()}
    for link_id in sorted(links):
        person_id = links[link_id]["global_speaker_id"]
        tag_id = links[link_id]["tag_id"]
        owner = person_owner.get(person_id)
        if owner is None or tag_id not in tags.tags or tags.owner(tag_id) == owner:
            continue
        target = tags.own_copy(tag_id, owner)
        if (person_id, target) in held:
            repairs.table(PEOPLE_TAG_LINKS).removed.add(link_id)
        else:
            repairs.table(PEOPLE_TAG_LINKS).updates[link_id] = {"tag_id": target}
            held.add((person_id, target))
        held.discard((person_id, tag_id))


def _fix_people_tag_parents(tags: _PeopleTags) -> None:
    for tag_id in sorted(tags.tags):
        tag = tags.tags[tag_id]
        if tag.user_id is None or tag.parent_id is None:
            continue
        tag.parent_id = tags.parent_for(tag.parent_id, tag.user_id, frozenset({tag_id}))


def _record_people_tags(
    archive: _Archive, tags: _PeopleTags, repairs: OwnerRepairs
) -> None:
    for tag_id, before in _named(archive[PEOPLE_TAGS]).items():
        after = tags.tags[tag_id]
        if (after.user_id, after.name, after.parent_id) != (
            before.user_id,
            before.name,
            before.parent_id,
        ):
            repairs.table(PEOPLE_TAGS).updates[tag_id] = {
                "user_id": after.user_id,
                "name": after.name,
                "parent_id": after.parent_id,
            }
    for tag_id in tags.created:
        tag = tags.tags[tag_id]
        repairs.table(PEOPLE_TAGS).added.append(
            {
                "id": tag_id,
                "name": tag.name,
                "color": tag.color,
                "user_id": tag.user_id,
                "parent_id": tag.parent_id,
            }
        )


def _repair_people_tags(
    archive: _Archive, repairs: OwnerRepairs, person_owner: Dict[int, Any]
) -> None:
    tags = _PeopleTags(archive[PEOPLE_TAGS])
    links = {
        link_id: link
        for link_id, link in archive[PEOPLE_TAG_LINKS].items()
        if link["global_speaker_id"] is not None and link["tag_id"] is not None
    }
    _give_tag_owners(tags, links, person_owner, archive[USERS])
    _move_tag_links(tags, links, person_owner, repairs)
    _fix_people_tag_parents(tags)
    _record_people_tags(archive, tags, repairs)

    link_repairs = repairs.tables.get(PEOPLE_TAG_LINKS, _TableRepairs())
    if tags.given or tags.created or link_repairs.updates or link_repairs.removed:
        logger.info(
            "Archive people tags: %d given an owner [%s], %d renamed to keep a "
            "name once per user %s; %d links moved to the person's owner's own "
            "tag, %d removed because the person already held it; %d tags "
            "created for links and parents",
            len(tags.given),
            ", ".join(tags.given),
            len(tags.renamed),
            tags.renamed,
            len(link_repairs.updates),
            len(link_repairs.removed),
            len(tags.created),
        )


def _repair_recording_tag_parents(archive: _Archive, repairs: OwnerRepairs) -> None:
    # A recording tag nested under another user's tag, or one with no owner,
    # moves to the top level, judged on the archive's parents as one UPDATE is.
    tags = archive[RECORDING_TAGS]
    moved = []
    for tag_id, tag in tags.items():
        parent = tags.get(tag["parent_id"])
        owner = tag["user_id"]
        if owner is not None and parent is not None and parent["user_id"] != owner:
            repairs.table(RECORDING_TAGS).updates[tag_id] = {"parent_id": None}
            moved.append(tag_id)
    if moved:
        logger.info(
            "Archive recording tag parents: %d moved to the top level %s",
            len(moved),
            sorted(moved),
        )


def plan_owner_repairs(zipf: zipfile.ZipFile) -> OwnerRepairs:
    """Work out, from the archive alone, the repairs its rows need."""
    archive = _read_archive(zipf)
    repairs = OwnerRepairs()
    person_owner = _repair_people(archive, repairs)
    _repair_people_tags(archive, repairs, person_owner)
    _repair_recording_tag_parents(archive, repairs)
    return repairs
