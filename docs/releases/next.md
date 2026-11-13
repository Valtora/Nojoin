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

### Meetings, tags and search

### Notes and AI providers

- **Test connection** under **Settings > AI providers** with no model selected says the provider is reachable and asks you to choose a model, in grey, where it read as a passed test (#1009).
- With AI on a Claude or ChatGPT subscription, the live speaker naming ask stops after 90 s and **Generate structure** after 180 s, where both ran for up to 300 s (#1013).
- A slow AI provider no longer lets a second Meeting Edge refresh or live speaker naming ask start for a meeting while the first is still waiting on the provider and its fallback (#1010, #1011).
- Meeting Edge and the live speaker naming ask send each AI provider one request per run, held to their 90 s timeout. A failing provider was retried inside the call, waiting out any rate-limit delay it asked for, and an Anthropic or Gemini name ask had no 90 s limit at all (#994).
- The **Analytics** note comparing a person with their usual delivery reads only the meetings their **Person Details** counts. It also read meetings in the bin and meetings where all their lines had moved to other speakers or their speaker was merged into another, so the meeting count behind it could run high (#985).

### People, tasks and calendar

- On a phone, **Tasks** no longer scrolls sideways while **Create Task** or a task's **Edit** form is open. A long meeting name in the form's recording list is cut short to fit the screen, as it is on a wider one (#1001).

### Settings and administration

- The users table under **Settings > Users and access** has a **Terms accepted** column with the day each account accepted, or **Not yet accepted** (#1007).

### API and MCP

- `POST /api/v1/users/me/legal-acceptance` records the signed-in user's acceptance from a browser session, and `/users/me`, the users list and both sign-in answers carry `legal_acceptance_required`. The MCP consent page shows the terms first to an account that has not accepted them (#1007).

### Deployment and dependencies

- The worker moves to Transformers 5.22.1. A capture running across the upgrade continues its live speaker labels with clustering instead of Nemotron (#947).

## Upgrade

<!-- Steps beyond the usual upgrade commands, which the release body and DEPLOYMENT.md give. -->

- Upgrade the API and the workers together, as the upgrade commands do: a 3.2.0 worker fails on the missing `users.legal_accepted_at` column until the API has migrated (#1007).

## Migration

<!-- Each Alembic revision by id and what it adds or changes, new or retired config keys and environment variables, and the backup format. -->

- One Alembic revision runs. `ed5d42732014` adds the nullable `users.legal_accepted_at`, with no backfill, so every account except the setup account is asked to accept the terms of use at its next visit (#1007).
- No config key or environment variable changed, and `.env.example`, `docker-compose.example.yml` and `nginx/` need no action.
- Backups stay in format 3 and carry each user's acceptance time. An archive made before 3.2.0 restores its users with none, so they are asked at their next visit (#1007).

## Rollback

<!-- The revision a downgrade goes back to, the order to roll back in, and what a downgrade loses. -->

- Roll the schema back before the images, because the 3.1.0 API refuses to start against a revision it does not know. With no browser recording in progress and 3.2.0 still running, run `docker compose exec api alembic downgrade bb518dd1f469`, then stop the stack and start the 3.1.0 image tags.
- The downgrade drops `users.legal_accepted_at` and every recorded acceptance, so upgrading again asks every account except the setup account once more.
- 3.1.0 restores an archive made on 3.2.0, without the acceptance times.

## Known issues

<!-- Only problems Nojoin can fix, each with its open issue. A limit set by a dependency or a provider belongs in the guide for that feature. -->

- Live lines can move words between speakers at a turn change before they lock, and past eight voices a live reply's first words can still go to the previous speaker (#776, #813, #888).
- In a processed transcript, the first words of a reply that follows a line you edited stay with the previous speaker (#1035).

## Browser capture

<!-- Changes to the supported browsers and operating systems, or to how capture behaves. -->
