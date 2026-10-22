"""give every person an owner, in the library of the recordings that link them

People (global_speakers) belong to one user, and the People library, the
pipeline's identification and every rename read only the user's own. Two
writes did not keep to that: the voiceprint dialog's "create new" and the demo
meeting's seed made people with no owner, and the dialog's link could point a
speaker at a person outside its recording owner's library. This revision
repairs what they left. It deletes no person, voiceprint or link.

1. A person with no owner, linked from one user's recordings, goes to that
   user.
2. A person with no owner, linked from several users' recordings, goes to the
   user whose recordings hold most of the links, the lowest user id on a tie.
   Step 4 then moves the other users' links.
3. A person with no owner and no link from an owned recording keeps no owner.
   No user sees or matches them, as before. They are counted in the log.
4. A link from one user's recording to a person in another user's library
   moves to the linking user's own person of the same name, created when they
   have none: same name and colour, no voiceprint, contact details or tags.
   The scheduled voiceprint rebuild can found their voiceprint from that
   user's own speakers. The original person keeps theirs.
5. A person given to a user who already has someone of that name is renamed
   "<name> (2)", or the next free number, because each library holds a name
   once and several lookups expect that. The user can merge the two in People.

Links from recordings with no owner are left as they are. The log gives counts
and ids, never names. A second run changes nothing.

Downgrade leaves the data as it is. The previous revision reads owners and
links the same way, and taking an owner away again would hide a person their
user can now see.

Revision ID: 7cee4b4388a7
Revises: 7ff0bbdadad8
Create Date: 2026-10-22 00:00:00.000000

"""

import logging
from collections import defaultdict

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "7cee4b4388a7"
down_revision = "7ff0bbdadad8"
branch_labels = None
depends_on = None

# Alembic's own logger, so the summary prints beside "Running upgrade".
logger = logging.getLogger("alembic.runtime.migration")


def _free_name(name: str, taken: set[str]) -> str:
    if name not in taken:
        return name
    number = 2
    while f"{name} ({number})" in taken:
        number += 1
    return f"{name} ({number})"


def upgrade() -> None:
    bind = op.get_bind()
    people = {
        row.id: row
        for row in bind.execute(
            sa.text("SELECT id, name, color, user_id FROM global_speakers")
        )
    }
    # Links per person and recording owner. A merged row keeps its link, and
    # the People routes follow every linked row, so merged rows count too.
    link_counts: dict[int, dict[int, int]] = defaultdict(dict)
    for row in bind.execute(
        sa.text(
            "SELECT rs.global_speaker_id AS person_id, r.user_id AS owner_id,"
            " count(*) AS links"
            " FROM recording_speakers rs JOIN recordings r ON r.id = rs.recording_id"
            " WHERE rs.global_speaker_id IS NOT NULL AND r.user_id IS NOT NULL"
            " GROUP BY rs.global_speaker_id, r.user_id"
        )
    ):
        link_counts[row.person_id][row.owner_id] = row.links

    owner_of = {person_id: row.user_id for person_id, row in people.items()}
    names_by_owner: dict[int, set[str]] = defaultdict(set)
    person_by_owner_and_name: dict[tuple[int, str], int] = {}
    for person_id in sorted(people):
        row = people[person_id]
        if row.user_id is not None:
            names_by_owner[row.user_id].add(row.name)
            person_by_owner_and_name.setdefault((row.user_id, row.name), person_id)

    given: list[str] = []
    renamed: list[int] = []
    shared: list[int] = []
    unlinked: list[int] = []
    for person_id in sorted(p for p, row in people.items() if row.user_id is None):
        counts = link_counts.get(person_id)
        if not counts:
            unlinked.append(person_id)
            continue
        owner = min(counts, key=lambda user_id: (-counts[user_id], user_id))
        if len(counts) > 1:
            shared.append(person_id)
        name = _free_name(people[person_id].name, names_by_owner[owner])
        if name != people[person_id].name:
            renamed.append(person_id)
        bind.execute(
            sa.text(
                "UPDATE global_speakers SET user_id = :owner, name = :name,"
                " updated_at = now() AT TIME ZONE 'utc'"
                " WHERE id = :id AND user_id IS NULL"
            ),
            {"owner": owner, "name": name, "id": person_id},
        )
        owner_of[person_id] = owner
        names_by_owner[owner].add(name)
        person_by_owner_and_name.setdefault((owner, name), person_id)
        given.append(f"{person_id}->{owner}")

    moved_links = 0
    moved: list[str] = []
    created: list[int] = []
    for person_id in sorted(link_counts):
        for linking_owner in sorted(link_counts[person_id]):
            if owner_of.get(person_id) in (None, linking_owner):
                continue
            # The name the link showed before this revision, not a suffix
            # step 5 may just have given the person.
            name = people[person_id].name
            target = person_by_owner_and_name.get((linking_owner, name))
            if target is None:
                target = bind.execute(
                    sa.text(
                        "INSERT INTO global_speakers (created_at, updated_at, name,"
                        " color, user_id, is_voiceprint_locked)"
                        " VALUES (now() AT TIME ZONE 'utc', now() AT TIME ZONE 'utc',"
                        " :name, :color, :owner, false) RETURNING id"
                    ),
                    {
                        "name": name,
                        "color": people[person_id].color,
                        "owner": linking_owner,
                    },
                ).scalar_one()
                person_by_owner_and_name[(linking_owner, name)] = target
                names_by_owner[linking_owner].add(name)
                created.append(target)
            result = bind.execute(
                sa.text(
                    "UPDATE recording_speakers SET global_speaker_id = :target,"
                    " updated_at = now() AT TIME ZONE 'utc'"
                    " WHERE global_speaker_id = :person AND recording_id IN"
                    " (SELECT id FROM recordings WHERE user_id = :owner)"
                ),
                {"target": target, "person": person_id, "owner": linking_owner},
            )
            moved_links += result.rowcount
            moved.append(f"{person_id}->{target}")

    logger.info(
        "People owners: %d given an owner [%s], %d of them linked from several "
        "users' recordings %s, %d renamed to keep a name once per library %s; "
        "%d with no owner and no link from an owned recording kept as they are %s",
        len(given),
        ", ".join(given),
        len(shared),
        shared,
        len(renamed),
        renamed,
        len(unlinked),
        unlinked,
    )
    logger.info(
        "People links: %d speaker links moved to the linking user's own person "
        "[%s], %d people created for them %s",
        moved_links,
        ", ".join(moved),
        len(created),
        created,
    )


def downgrade() -> None:
    # See the module docstring: the owners and links this revision set stay.
    pass
