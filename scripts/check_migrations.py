#!/usr/bin/env python3
"""Execute the Alembic migration chain against a real Postgres.

``scripts/validate_alembic.py`` only proves the revision graph resolves to one
head. It cannot catch a migration that is well-formed but fails on real data --
which is how a backfill using ``jsonb_array_length`` on a column holding JSON
``null`` reached production and took the API down on startup.

Three phases, each catching a different failure mode:

1. **Fresh install** -- base to head on an empty database. Catches ordering
   errors and anything that assumes a table or column already exists.
2. **Seeded round trip** -- hostile values are inserted, then the newest
   migration is rolled back and re-applied *with that data present*. This is the
   phase that catches data-dependent failures, and it keeps working for future
   migrations without being rewritten.
3. **Head assertion** -- the database really is at head afterwards.

Usage:
    python scripts/check_migrations.py                # uses DATABASE_URL
    python scripts/check_migrations.py --database-url postgresql://...
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# Values that are legal in the column's type but break naive predicates. The
# jsonb `null` entries are the ones that mattered: they are what the manual
# speaker-merge flow writes when it clears a source voiceprint, so they exist in
# real installs, and `jsonb_typeof(col) = 'array' AND jsonb_array_length(col)`
# still aborts on them because Postgres may evaluate the AND in either order.
HOSTILE_SEED = """
-- Idempotent: the check may be re-run against a database that is already seeded.
DELETE FROM recording_speakers WHERE recording_id IN (900001, 900002);
DELETE FROM global_speakers
  WHERE user_id IN (900001, 900002) OR name LIKE 'mc-owner-%';
DELETE FROM recordings WHERE id IN (900001, 900002);
DELETE FROM p_tags WHERE name LIKE 'mc-%';
DELETE FROM tags WHERE name LIKE 'mc-%';

INSERT INTO users (id, created_at, updated_at, username, hashed_password,
                   is_active, is_superuser, role, force_password_change)
VALUES (900001, now(), now(), 'migration-check', 'x', true, true, 'owner', false)
ON CONFLICT (id) DO NOTHING;

INSERT INTO recordings (id, created_at, updated_at, name, public_id, meeting_uid,
                        audio_path, status, upload_progress, processing_progress,
                        is_archived, is_deleted, user_id)
VALUES (900001, now(), now(), 'migration-check', 'mc-public', 'mc-uid',
        '/migration-check.wav', 'PROCESSED', 0, 0, false, false, 900001)
ON CONFLICT (id) DO NOTHING;

INSERT INTO global_speakers (created_at, updated_at, name, embedding,
                             is_voiceprint_locked, user_id)
VALUES
  (now(), now(), 'mc-array',   '[0.1,0.2]'::jsonb, false, 900001),
  (now(), now(), 'mc-json-null','null'::jsonb,     false, 900001),
  (now(), now(), 'mc-sql-null', NULL,              false, 900001),
  (now(), now(), 'mc-empty',   '[]'::jsonb,        false, 900001),
  (now(), now(), 'mc-object',  '{"a":1}'::jsonb,   false, 900001),
  (now(), now(), 'mc-string',  '"s"'::jsonb,       false, 900001),
  (now(), now(), 'mc-number',  '7'::jsonb,         false, 900001),
  (now(), now(), 'mc-bool',    'true'::jsonb,      false, 900001);

INSERT INTO recording_speakers (created_at, updated_at, public_id, recording_id,
                                diarization_label, embedding, speaker_status,
                                speaker_kind, identity_locked)
VALUES
  (now(), now(), 'mc-rs1', 900001, 'SPEAKER_00', '[0.4,0.5]'::jsonb, 'active', 'automated', false),
  (now(), now(), 'mc-rs2', 900001, 'SPEAKER_01', 'null'::jsonb,      'active', 'automated', false),
  (now(), now(), 'mc-rs3', 900001, 'SPEAKER_02', NULL,               'active', 'automated', false),
  (now(), now(), 'mc-rs4', 900001, 'SPEAKER_03', '[]'::jsonb,        'active', 'automated', false),
  (now(), now(), 'mc-rs5', 900001, 'SPEAKER_04', '{"b":2}'::jsonb,   'active', 'automated', false);

-- People with no owner, and a link to another user's person: one linked from
-- one user's recording, one from two users', one from none.
INSERT INTO users (id, created_at, updated_at, username, hashed_password,
                   is_active, is_superuser, role, force_password_change)
VALUES (900002, now(), now(), 'migration-check-2', 'x', true, false, 'user', false)
ON CONFLICT (id) DO NOTHING;

INSERT INTO recordings (id, created_at, updated_at, name, public_id, meeting_uid,
                        audio_path, status, upload_progress, processing_progress,
                        is_archived, is_deleted, user_id)
VALUES (900002, now(), now(), 'migration-check-2', 'mc-public-2', 'mc-uid-2',
        '/migration-check-2.wav', 'PROCESSED', 0, 0, false, false, 900002)
ON CONFLICT (id) DO NOTHING;

INSERT INTO global_speakers (created_at, updated_at, name, embedding,
                             is_voiceprint_locked, user_id)
VALUES
  (now(), now(), 'mc-owner-one',    '[0.1,0.2]'::jsonb, false, NULL),
  (now(), now(), 'mc-owner-both',   'null'::jsonb,      false, NULL),
  (now(), now(), 'mc-owner-none',   NULL,               false, NULL),
  (now(), now(), 'mc-owner-theirs', '[0.3,0.4]'::jsonb, false, 900002);

INSERT INTO recording_speakers (created_at, updated_at, public_id, recording_id,
                                diarization_label, global_speaker_id,
                                speaker_status, speaker_kind, identity_locked)
SELECT now(), now(), seed.public_id, seed.recording_id, seed.label, gs.id,
       'active', 'automated', false
FROM (VALUES ('mc-rs6', 900001, 'SPEAKER_05', 'mc-owner-one'),
             ('mc-rs7', 900001, 'SPEAKER_06', 'mc-owner-both'),
             ('mc-rs8', 900002, 'SPEAKER_00', 'mc-owner-both'),
             ('mc-rs9', 900001, 'SPEAKER_07', 'mc-owner-theirs'))
     AS seed (public_id, recording_id, label, person)
JOIN global_speakers gs ON gs.name = seed.person;

-- A people tag with no owner linked from two users' people, one used by
-- nobody, a link to another user's tag, and a people tag and a recording tag
-- nested under another user's.
INSERT INTO p_tags (created_at, updated_at, name, color, user_id)
VALUES
  (now(), now(), 'mc-ptag-both',   'teal', NULL),
  (now(), now(), 'mc-ptag-unused', NULL,   NULL),
  (now(), now(), 'mc-ptag-theirs', 'teal', 900002);

INSERT INTO p_tags (created_at, updated_at, name, user_id, parent_id)
SELECT now(), now(), 'mc-ptag-nested', 900001, id
FROM p_tags WHERE name = 'mc-ptag-theirs';

INSERT INTO people_tags (created_at, updated_at, global_speaker_id, tag_id)
SELECT now(), now(), gs.id, t.id
FROM (VALUES ('mc-array', 'mc-ptag-both'),
             ('mc-owner-theirs', 'mc-ptag-both'),
             ('mc-array', 'mc-ptag-theirs'))
     AS seed (person, tag)
JOIN global_speakers gs ON gs.name = seed.person
JOIN p_tags t ON t.name = seed.tag;

INSERT INTO tags (created_at, updated_at, name, user_id)
VALUES (now(), now(), 'mc-tag-theirs', 900002);

INSERT INTO tags (created_at, updated_at, name, user_id, parent_id)
SELECT now(), now(), 'mc-tag-nested', 900001, id
FROM tags WHERE name = 'mc-tag-theirs';

-- Notes on a recording with no transcript row (rolling the timeline notes
-- migration back has to create one), with text that breaks naive quoting and
-- a body of nothing but whitespace, which must not come back as a blank note.
INSERT INTO timeline_notes (created_at, updated_at, public_id, recording_id,
                            created_by_user_id, kind, body, anchor_ms, start_ms,
                            end_ms, quoted_text)
VALUES
  (now(), now(), 'mc-tn1', 900001, 900001, 'head', 'it''s "quoted" \\ back\\slash', NULL, NULL, NULL, NULL),
  (now(), now(), 'mc-tn2', 900001, NULL,   'point', E'\\n\\t \\n', 0, NULL, NULL, NULL),
  (now(), now(), 'mc-tn3', 900001, 900001, 'selection', '%s %% $1 — café', NULL, 0, 1, E'quote\\nwith newline'),
  (now(), now(), 'mc-tn4', 900001, 900001, 'tail', 'tail', NULL, NULL, NULL, NULL)
ON CONFLICT (public_id) DO NOTHING;

-- A note written with a write token, and a deleted note's tombstone: the
-- rollback must drop both the column and the table with rows in them.
UPDATE timeline_notes SET last_write_token = 'mc-token-1' WHERE public_id = 'mc-tn4';
INSERT INTO timeline_note_tombstones (public_id, recording_id, deleted_write_token, deleted_at)
VALUES ('mc-tt1', 900001, 'mc-token-2', now())
ON CONFLICT (public_id) DO NOTHING;

-- A document and a note's file switched on: rolling the note files migration
-- back has to keep both rows, as documents of the meeting.
INSERT INTO documents (created_at, updated_at, recording_id, title, file_path,
                       file_type, status, parse_mode, pages_parsed, note_id,
                       use_in_notes)
SELECT now(), now(), 900001, 'mc-doc', '/mc-doc.pdf', 'application/pdf',
       'READY'::documentstatus, 'VISUAL'::documentparsemode, 0, NULL, false
UNION ALL
SELECT now(), now(), 900001, 'mc-still', '/mc-still.png', 'image/png',
       'READY'::documentstatus, 'VISUAL'::documentparsemode, 0, id, true
FROM timeline_notes WHERE public_id = 'mc-tn2'
ON CONFLICT (file_path) DO NOTHING;
"""


def _alembic(database_url: str, *args: str) -> None:
    env = {**os.environ, "DATABASE_URL": database_url}
    result = subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        print(result.stdout)
        print(result.stderr, file=sys.stderr)
        raise SystemExit(f"FAILED: alembic {' '.join(args)}")


def _sql(database_url: str, statement: str) -> list[tuple]:
    import psycopg

    with psycopg.connect(database_url) as conn, conn.cursor() as cur:
        cur.execute(statement)
        try:
            return cur.fetchall()
        except psycopg.ProgrammingError:
            return []


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--database-url",
        default=os.getenv("DATABASE_URL"),
        help="Postgres URL to migrate. Defaults to $DATABASE_URL.",
    )
    args = parser.parse_args()

    if not args.database_url:
        raise SystemExit("A Postgres URL is required (--database-url or DATABASE_URL).")
    if args.database_url.startswith("sqlite"):
        raise SystemExit(
            "This check requires Postgres; the failures it targets "
            "are Postgres-specific."
        )

    print("1/3 fresh install: base -> head")
    _alembic(args.database_url, "upgrade", "head")

    print(
        "2/3 seeding hostile values, then rolling the newest migration back "
        "and re-applying it with that data present"
    )
    _sql(args.database_url, HOSTILE_SEED)
    _alembic(args.database_url, "downgrade", "-1")
    _alembic(args.database_url, "upgrade", "head")

    print("3/3 asserting the database is at head")
    heads = _sql(args.database_url, "SELECT version_num FROM alembic_version;")
    if len(heads) != 1:
        raise SystemExit(f"Expected exactly one head row, found: {heads}")

    print(f"Migration check passed. Head revision: {heads[0][0]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
