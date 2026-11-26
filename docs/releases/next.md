# Next release

<!--
Notes for the next release. A pull request that changes what a user, an
operator or an API client would notice adds its line here, under the heading
that fits, as a bullet:

- **Test connection** with no model selected asks you to choose one (#1009).

One or two sentences on what they would notice, with UI labels in bold as the
guides write them and the issue or pull request number in brackets at the end.
British English, no em dashes, no emojis, and no real people, companies or
meeting titles: use made-up names such as Alice Example. A dependency bump gets
a line only when an operator would notice it.

Keep every heading, empty or not. Cutting the release drops the empty ones
(scripts/cut_release_notes.py), and Docs validation fails when one is missing.
.github/CONTRIBUTING.md, "Release Notes", has the rule.
-->

## Breaking changes

<!-- What stops working or behaves differently after the upgrade, and what to do about it. Each also goes under Before you upgrade in the release body. -->

- **Each user accepts the terms of use.** Every account except the setup account sees the terms once, at its next page load, and cannot start a recording or import audio until it accepts. Until then the API answers that account's new captures and imports with 403 and the code `legal_acceptance_required`, so an API client acting for it needs the person to accept once in the web app (#1007).

## Changes

<!-- One line per change, under the area it touches. -->

### Recording and capture

- An import the server refuses with a coded error shows the server's message in the import dialog, where the page fell to "This page couldn't load". Elsewhere, an error the server explains, such as a recording already in progress, shows its message in place of a generic one (#1015).

### Transcription and speakers

- **Max Speakers** reads the same in the live recording toolbar, the import dialog and the reprocess dialog: its hint sits behind an "i" beside the box, which opens on hover, on keyboard focus or with a tap, and a rejected entry's message still shows under the box (#1014).
- A name the AI suggested for a live speaker is withdrawn when a line it quoted moves to another speaker, and merging a speaker into one whose suggestion was dismissed drops the pending name. Turning off **Suggest speaker names during a meeting** hides names already offered until it is turned back on, an answer that is not a plausible personal name is refused, and **Accept** and **Dismiss** sit beside the name on a transcript line (#1016).
- In the live transcript, editing a line no longer sends the first words of the reply after it back to that line's speaker. While your wording still ends with a full stop, question mark or exclamation mark, they stay with the person replying (#975).
- In a processed transcript, the first words of a reply after a line you edited now move to the person replying, as they do live, while your wording ends with a full stop, question mark or exclamation mark. After a line whose speaker you chose, they keep the speaker processing found for them (#1035).
- In a processed transcript, words without timings that follow two corrected lines, one inside the other, are placed after both. They could overlap the outer line and take the speaker chosen for it (#971).

### Meetings, tags and search

- An Escape pressed just before a first-visit tour appears, to close a menu say, no longer closes the tour when you let go of it once it is showing. The tour closed and counted as seen, so it did not come back. Escape pressed while the tour is showing still closes it (#988).
- When a search or filter in the recordings list hides recordings you had selected, a line above the list says how many are no longer selected, such as **2 recordings are no longer selected because they are not in these results.** They still leave the selection, so the batch bar acts only on recordings you can see, and the line goes once you change the selection or the search (#974).
- Collapsing the recordings list and expanding it again brings the list back where you left it, with the same recording at the top. It went back to the newest (#974).
- In a window narrower than 1024 px, such as a phone's, a double click on a recording's name in the list only opens the meeting. It also started a rename in the list behind the meeting. **Rename** in the recording's **⋯** menu renames it at any width (#974).
- Notifications rise above the recordings list's batch bar rather than cover its buttons, on a phone or wherever the two meet, and a recording's **⋯** menu opens over a notification rather than under it (#974).
- **Share** in a meeting's header gives other people on the installation access to it, to view or to edit, one by one or through a single sign-on group you are in. Editors change the transcript, speakers, notes and documents with you, and can generate notes and analyses on their own AI settings. A new speaker name an editor types stays in that meeting rather than joining the owner's **People**. Deleting, reprocessing, voiceprints and sharing stay with the owner. Documents and note pictures an editor adds are read with the editor's own AI settings. A meeting shared with you shows who shared it, and **Leave** removes it from your list when it was shared with you by name. While its owner has it archived or in the bin it is read-only for you, with a button that asks the owner to bring it back. When two people save the meeting notes at once, the second gets **Show their version** and **Keep mine** instead of overwriting the first (#1088).

### Notes and AI providers

- **Test connection** under **Settings > AI providers** with no model selected says the provider is reachable and asks you to choose a model, in grey, where it read as a passed test (#1009).
- With AI on a Claude or ChatGPT subscription, the live speaker naming ask stops after 90 s and **Generate structure** after 180 s, where both ran for up to 300 s (#1013).
- A slow AI provider no longer lets a second Meeting Edge refresh or live speaker naming ask start for a meeting while the first is still waiting on the provider and its fallback (#1010, #1011).
- Meeting Edge and the live speaker naming ask send each AI provider one request per run, held to their 90 s timeout. A failing provider was retried inside the call, waiting out any rate-limit delay it asked for, and an Anthropic or Gemini name ask had no 90 s limit at all (#994).
- When the AI provider behind Meeting Edge fails during a recording, Nojoin waits before asking it again, from 30 seconds up to every 5 minutes, and stops asking for the rest of the recording when a key is refused, a model is missing or the account is out of credit, while a fallback provider carries on. One notification per kind of failure names the provider and model and says why, and the **Meeting Edge** tab says why it is paused. Changing the AI settings lifts a stop without a restart. The recording read carries the notices as `live_ai_notices` during a capture (#994).
- With **Suggest speaker names during a meeting** on, a failing AI provider backs off for the speaker name ask as it does for Meeting Edge, and the two share the wait: neither asks a provider the other found failing until its wait is over. The notification names every feature a failure holds up, and the **Meeting Edge** tab says why it is paused when the failure came from a name ask (#994).
- A Claude or ChatGPT subscription no longer shows as disconnected in **Settings > Your AI** after a token refresh that failed on a network or server error, and another subscription cannot be connected beside it meanwhile; Nojoin refreshes it again on the next request. Only a refresh the provider refuses asks you to reconnect (#994).
- Text you type into a notes table row you have just added stays in that row. When the notes saved just after you added the row, the caret jumped to the end of the table's last cell and your text went there, and in notes that end with a table, text typed while a save was under way could be lost (#1074).
- The **Analytics** note comparing a person with their usual delivery reads only the meetings their **Person Details** counts. It also read meetings in the bin and meetings where all their lines had moved to other speakers or their speaker was merged into another, so the meeting count behind it could run high (#985).
- When two speakers in one meeting are the same person, the **Analytics** note comparing them with their usual delivery counts that meeting once, from all their speech in it. It counted the meeting once for each speaker, so the note could say "across 3 meetings" after two, and in the meeting you are viewing only one of the two speakers got the note (#1030).

### People, tasks and calendar

- On a phone, **Tasks** no longer scrolls sideways while **Create Task** or a task's **Edit** form is open. A long meeting name in the form's recording list is cut short to fit the screen, as it is on a wider one (#1001).
- When Nojoin rebuilds a person's voiceprint in the background, it leaves out a speaker whose lines were all moved to other speakers, as **Person Details** leaves out that meeting. That speaker's voiceprint came from those lines, so it could pull the person's voiceprint towards whoever now holds them (#1031).
- When **Split / Unmerge Speaker** leaves too little of a person to rebuild their voiceprint from, the fallback to their other meetings leaves out a speaker whose lines were all moved to other speakers, as the background rebuild does. That speaker's voiceprint came from those lines, so the person's new voiceprint could be built from someone else's voice (#1069).
- **Split / Unmerge Speaker** no longer treats a speaker whose lines were all moved to other speakers as a second speaker of the person in that meeting. The person's one speaker there now takes the voiceprint of the lines that remain, so the background rebuild can't blend the moved lines back into the person, and those remaining lines count towards the person's voiceprint even when the other speaker didn't sound like them (#1083).
- When Nojoin rebuilds a speaker's voiceprint in the background, it reads only the lines the transcript shows. A line you had split was still read whole beside its two halves, so the second half's speech counted for the original speaker even after you moved it to someone else (#1070).
- Opening a meeting from **Person Details** while the person has changes you have not saved asks first, and staying keeps the dialog open with your changes. The link used to leave at once and lose them (#972).
- **Person Details** shows a person's meetings when an older meeting's transcript holds a time too large to read, as one restored from an archive or edited by hand can, and counts that part of the transcript as no speech. The meetings failed to load (#972).
- In **Person Details**, the first Tab shows **Skip to contact details**, which takes keyboard focus straight to **Full Name**. Reaching the form used to take a Tab for every meeting listed, 53 presses with the full 50 (#972).

### Settings and administration

- The users table under **Settings > Users and access** has a **Terms accepted** column with the day each account accepted, or **Not yet accepted** (#1007).
- **Check speakers heard only briefly** in **Settings > Recording > Advanced > Speech and speaker tuning** stays where it is when you tick or untick it, and its description keeps a readable width beside the limits. On a wide screen the checkbox moved between the line below **Speakers heard only briefly** and the end of that line, and in a narrower window the limits could squeeze the description to a few characters (#975).
- In a narrower window, settings with a wide box beside them, such as **Colour palette**, **Timezone**, the AI model choices and the calendar sign-in fields, keep room to read their name and description, and the box narrows instead. The box had taken nearly the whole row, leaving a few characters a line (#1043).
- In **Settings > AI providers > Advanced > Model downloads**, the Nemotron row says **Waiting to retry** for the ten minutes after a failed preparation, as the word timing aligner's row does. A row in that wait no longer says **Retry** starts now while another preparation keeps the button disabled (#971).
- The **AI providers** and **Transcription** settings pages ask for a provider's model list only when the provider has a key or address and you are an administrator. The server refused each such request on a fresh install with no AI key, and from anyone who is not an administrator (#971).
- Nojoin can sign people in through an OpenID Connect provider such as Authentik or Keycloak, set up with the new `NOJOIN_OIDC_*` variables. New people get an account at their first sign-in, existing accounts link under **Settings > Profile and security > Single sign-on**, and the provider's groups can make people Admins and can be shared with. `NOJOIN_PASSWORD_LOGIN` keeps passwords for everyone, for Owners only, or for nobody (#1088).

### API and MCP

- `POST /api/v1/users/me/legal-acceptance` records the signed-in user's acceptance from a browser session, and `/users/me`, the users list and both sign-in answers carry `legal_acceptance_required`. The MCP consent page shows the terms first to an account that has not accepted them (#1007).
- `GET /api/v1/system/download-progress` answers 422 for a `model` it does not know, where it returned idle progress. Without `model`, a Nemotron or aligner download that a stopped worker left behind no longer outranks a newer preparation's progress. Two `POST /api/v1/system/models/prepare` requests sent together queue one preparation, and the second answers 409 (#971).
- New routes: `GET /api/v1/login/methods`, `GET /api/v1/login/oidc/start`, `GET /api/v1/login/oidc/callback` and `POST /api/v1/login/oidc/link` for single sign-on; `GET`, `PUT` and `DELETE /api/v1/recordings/{id}/shares` and `DELETE /api/v1/recordings/{id}/shares/me` and `POST /api/v1/recordings/{id}/shares/me/restore-request` for sharing; `GET /api/v1/users/directory` and `/users/directory/groups` for whom to share with; and `GET /api/v1/speakers/recordings/{id}/speaker-candidates`. `GET /api/v1/recordings` takes `scope=all|mine|shared` and each recording carries `access`, `owner` and `can_leave`. `PUT /api/v1/transcripts/{id}/notes` takes an optional `base_revision` and answers 409 with the code `notes_conflict` when the notes changed since. MCP tools act with the caller's access, so `list_recordings` and `search_context` also cover meetings shared with them, and each result of `list_recordings` carries `access` and, when shared, `owner` (#1088).

### Deployment and dependencies

- The worker moves to Transformers 5.22.1. A capture running across the upgrade continues its live speaker labels with clustering instead of Nemotron (#947).
- The frontend moves to Next.js 16.4.6, which carries the security fixes released in 16.4.5. The API and workers move to Celery 5.7.0, SQLAlchemy 2.1.8 and psycopg 3.3.8, and the AI SDKs, still uncapped, now need at least openai 3.49.0, anthropic 2.7.0 and google-genai 3.0.0 (#1048).
- The worker-io image carries one copy of the Claude Code CLI instead of two, about 360 MB less, and the api and worker images no longer store the application files twice, about 80 MB less each. An image built from a working checkout also leaves out the Python tool caches (#1053).
- When Whisper cannot launch its Triton GPU kernels and aligns word timestamps on the CPU instead, `worker-gpu` logs a warning the first time, where it said nothing. A worker image whose Triton does not import now fails to build (#1053).
- `worker-gpu` restarts itself in place once it has gone 15 minutes without a task, never while a capture is recording or paused, and hands back about 1.4 GB of memory on a CUDA install. The first live caption after such a restart takes about 14 seconds while the models load; final transcripts are unaffected. `NOJOIN_WORKER_IDLE_RECYCLE_MINUTES` in `.env` sets the minutes, and `0` turns the restart off (#1054).
- In `docker-compose.example.yml`, `worker-cpu`, `worker-io` and `worker-parse` autoscale from one child, and a child that has used more than 400,000 KiB is replaced after its task, so an idle install runs three prefork children instead of nine (#1054).
- The worker and worker-io images carry dnspython 2.9.2 in place of 2.9.0, which fixes CVE-2026-128089 (#1072).
- `alembic.ini` moves from the repository root to `backend/alembic.ini`, and the bundled Pyannote models from `bundled_models/` to `backend/bundled_models/`. `docker compose exec api alembic ...` works as before, and in a source checkout Alembic now runs as `alembic -c backend/alembic.ini ...` (#1089).

## Upgrade

<!-- Steps beyond the usual upgrade commands, which the release body and DEPLOYMENT.md give. -->

- Upgrade the API and the workers together, as the upgrade commands do: a 3.2.0 worker fails on the missing `users.legal_accepted_at` column until the API has migrated (#1007).
- To autoscale the prefork lanes, copy the `worker-cpu`, `worker-io` and `worker-parse` commands from `docker-compose.example.yml` into your `docker-compose.yml`, which otherwise keeps its fixed pools. The idle restart of `worker-gpu` needs no change (#1054).
- To use single sign-on, copy the `NOJOIN_OIDC_*` and `NOJOIN_PASSWORD_LOGIN` lines from `docker-compose.example.yml` into the `api` service of your `docker-compose.yml`, and set them in `.env` ([ADMIN.md](../ADMIN.md#single-sign-on)). Without them nothing changes (#1088).

## Migration

<!-- Each Alembic revision by id and what it adds or changes, new or retired config keys and environment variables, and the backup format. -->

- One Alembic revision runs. `ed5d42732014` adds the nullable `users.legal_accepted_at`, with no backfill, so every account except the setup account is asked to accept the terms of use at its next visit (#1007).
- One environment variable is new: `NOJOIN_WORKER_IDLE_RECYCLE_MINUTES`, empty for 15 minutes and `0` for off, which `docker-compose.example.yml` passes to `worker-gpu`. An existing `docker-compose.yml` does not pass it, so the default applies until you add it to `worker-gpu`'s `environment:` block. No config key changed, and `nginx/` needs no action (#1054).
- Backups stay in format 3 and carry each user's acceptance time. An archive made before 3.2.0 restores its users with none, so they are asked at their next visit (#1007).
- Two more Alembic revisions run after `ed5d42732014`. `04b28b70a1fb` adds the `user_identities` table, which links an account to its single sign-on identity and holds its groups. `3885627f5053` adds `recording_shares`. Neither changes an existing table. Eleven environment variables are new, all optional: `NOJOIN_OIDC_ISSUER`, `NOJOIN_OIDC_CLIENT_ID`, `NOJOIN_OIDC_CLIENT_SECRET`, `NOJOIN_OIDC_DISPLAY_NAME`, `NOJOIN_OIDC_SCOPES`, `NOJOIN_OIDC_USERNAME_CLAIM`, `NOJOIN_OIDC_GROUPS_CLAIM`, `NOJOIN_OIDC_ALLOWED_GROUPS`, `NOJOIN_OIDC_ADMIN_GROUPS`, `NOJOIN_OIDC_AUTO_CREATE` and `NOJOIN_PASSWORD_LOGIN`. Backups carry both tables (#1088).

## Rollback

<!-- The revision a downgrade goes back to, the order to roll back in, and what a downgrade loses. -->

- Roll the schema back before the images, because the 3.1.0 API refuses to start against a revision it does not know. With no browser recording in progress and 3.2.0 still running, run `docker compose exec api alembic downgrade bb518dd1f469`, then stop the stack and start the 3.1.0 image tags.
- The downgrade drops `users.legal_accepted_at` and every recorded acceptance, so upgrading again asks every account except the setup account once more.
- 3.1.0 restores an archive made on 3.2.0, without the acceptance times.
- The downgrade also drops `recording_shares` and `user_identities`: every share goes, and accounts created through single sign-on are left without a way to sign in until an administrator gives them a password (#1088).

## Known issues

<!-- Only problems Nojoin can fix, each with its open issue. A limit set by a dependency or a provider belongs in the guide for that feature. -->

- Live lines can move words between speakers at a turn change before they lock, and past eight voices a live reply's first words can still go to the previous speaker (#776, #813, #888).

## Browser capture

<!-- Changes to the supported browsers and operating systems, or to how capture behaves. -->
