## Nojoin {{VERSION}}

Container images for this release. All images are cosign-signed and ship build-provenance and SBOM attestations; verification steps are in the [deployment guide](https://github.com/Valtora/Nojoin/blob/main/docs/DEPLOYMENT.md#verifying-an-image-before-deploying). Pin to a digest for reproducible deployments.

{{IMAGE_DIGESTS}}

### Breaking Changes

<!-- Maintainer: what stops working or behaves differently after the upgrade, and what to do about it. -->

- **Mute non-speech for transcription is retired.** With voice activity detection on, transcription always hears what it kept plus what speaker separation heard as speech. Anyone who had turned the switch off gets the default from the next recording processed, and turning off **Voice activity detection** under **Settings > Recording > Advanced** makes transcription hear everything (#966).
- **Some API answers changed.** An integer past its column's range answers 422 where it answered 500, and an id of 0 answers 422 rather than 404. An invitation lasts at most 36,500 days (#928, #935).
- **Some defaults changed.**
  - Live speaker name suggestions are on for every user. With an AI provider set up, recent transcript lines go to it during a capture whether Meeting Edge is on or off, until the user turns the suggestions off (#803).
  - Ollama models no longer think before they answer unless an administrator turns on **Thinking** (#937).
  - **Corner style** follows **Style**, so a browser on the old default, Rounded, shows square corners under Classic (#967).

### Highlights

<!-- Maintainer: one bullet per item, one or two sentences each. What an operator would notice, not how it works. Detail belongs in the docs. Remove the section if a release has nothing to lead with. -->

- **Live speakers get suggested names.** A speaker who introduces themselves is offered that name to **Accept** or **Dismiss**, and with an AI provider set up Nojoin asks the AI, on the **Meeting Edge model** where one is set, about an unnamed speaker after a possible introduction or a minute of their speech, then at doubling intervals up to every eight minutes. Nothing applies until you accept it, and **Suggest speaker names during a meeting** under **Settings > Notes and live assistance** turns it off (#803, #983).
- **People show their meetings and talk time.** **Person Details** opens on **Meetings**, with their meeting count, talk time and newest 50 meetings. The People count, the recordings list's person filter and the MCP `get_person` tool follow one rule: a meeting counts while one of the person's speakers there still has lines. `get_person` reads only the signed-in user's recordings, and the sample lines offered when recalibrating a voiceprint no longer come from a speaker merged away or one whose lines all moved to someone else (#798, #976, #982, #986).
- **A meeting shows its tasks.** A **Tasks** tab on a meeting with linked tasks lets you complete each one or open it in **Tasks**, and archived ones are read-only (#797, #978).
- **A reply's first words go to the person replying.** Where the speaker model gave up to eight words of a reply to the previous speaker, processing and live Nemotron labels move them, leaving locked words and edited lines alone. A speaker the brief-speaker check folds into another keeps their words, and unticking **Check speakers heard only briefly** skips the check (#922).
- **Firefox and Safari can record.** They record the microphone where they were refused, and desktop Chromium can record it alone, without screen stills (#804).
- **Recording source is a setting.** **Recording source** under **Settings > Recording** offers **Microphone and system audio**, the default, **Microphone only** or **Ask each time**, saved per browser (#963).
- **Tags show on cards, the dashboard and tasks.** Meeting cards show colour dots, **Recent Meetings** and dashboard tasks show chips, and tags selected in the navigation filter **Tasks** (#799, #899, #933).
- **The meeting list covers the whole library.** It loads 50 meetings at a time as you scroll, where it stopped at the newest 100, and **Search...** looks through titles, tags, speaker names and transcripts on the server. Typing redraws only the cards that changed (#905, #912).
- **Settings say what they do.** **Live word settling** is **Refine live words**, **Mute non-speech for speaker separation** is **Faster speaker separation**, and the tuning rows are **Longest word (Parakeet only)**, **Speakers heard only briefly** and **Stray one-word speaker changes**. Search still finds the old names. A meeting's notes tabs read **Meeting notes** and **Your notes** (#965, #966).
- **Ollama thinking is a setting.** With **Thinking** off, the default, a thinking model no longer spends minutes before its first word and times notes out. A fallback Ollama at its own private address now runs, and saving **Settings > AI providers** no longer fails when a stored Ollama host stops resolving (#937, #952, #957).
- **Smaller fixes.** A name you give a meeting survives processing, a rename shows at once on the page, the list and the dashboard, and a meeting started without a name is named in your time zone (#889, #890, #902). A tag or calendar change no longer hides an edit made in another tab (#903). Search finds a phrase that repeats a word across lines, and the task link picker searches every recording, not the newest 100 (#910, #911). A tour is marked seen however you close it, waits while you type, and stays open when a click lands as it opens, and a recording's tour covers every tab (#883, #916, #919, #984). The note box loses its placement select, and **⋯** beside it puts a note before or after the transcript (#964). A failed **Stop** or **Discard** reports its error once, **Discard** asks in the app's own dialog, and discarding a capture no longer logs errors with tracebacks (#874, #883, #934). **Revised** marks only lines changed after they first showed, the side column opens wide enough for its tabs, and a failed recording's **Transcript** tab shows the reason with **Retry processing** (#883, #890). Notes generation no longer fails when the AI names a speaker label it was not offered, and document, invitation and CLI dates read alike in every browser language (#885). With **Max Speakers** set, a CUDA install keeps the combined speaker pass when it fits the limit (#894). Long-meeting notes name Nemotron's speakers and split on Ollama 0.40.11's context refusal (#955, #956). A damaged audio segment no longer holds the live transcript for 10 seconds (#875). With Canary, untimed words after a corrected line stay in the transcript, and the aligner and Nemotron downloads each show their own progress (#820). The health check no longer stalls other API requests for a second while no worker answers, and Enter or Escape that chooses a Chinese or Japanese candidate no longer acts on the page (#913, #914). With no recent meetings, the dashboard's task list starts under **Meet Now** (#969).
- **Dependency changes.** The worker moves to PyTorch 2.15.0 on a CUDA 13.2 base and to Transformers 5.21.0, and Nemotron and the Canary aligner no longer turn off on a Transformers release other than the one they were checked on (#868, #893, #896). The frontend carries follow-redirects 1.16.2, a runtime dependency through axios, for GHSA-mrwp-f3q8-3g9p, and other pins follow Dependabot (#873, #891, #943).

### Upgrade

Update your checkout, pull the new images, stop the stack, and start it again.

```bash
git pull
docker compose pull
docker compose down
docker compose up -d
```

- The GPU worker image is built on CUDA 13.2. The NVIDIA driver floor stays at 580 (#893).
- On the first start `worker-io` moves Celery Beat's schedule from `data/celerybeat-schedule.db` to `data/celerybeat-schedule.json` with each job's last run. If the old file is unreadable, every periodic job waits a full interval this once. Leave the `-s` argument in `docker-compose.yml` as it is (#920).
- An install with Nemotron or the Canary aligner prepared checks their files again on the first start, because their manifests changed. Nothing downloads (#868, #896).
- Export file names changed. The date is the meeting's day in your time zone, an export of two parts is named for them, as in `20261102_Team sync_NotesAndTranscript.pdf`, where it read `FullExport`, and the audio file starts with the date too. Update anything that picks up exports by name (#890).

### Migration

Database migrations run automatically on the first API start after upgrading. Back up your instance before upgrading.

<!-- Maintainer: note any blocking first-boot migration, longer startup, or manual step. Keep it to bullets. -->

- One Alembic revision runs. `bb518dd1f469` adds `recordings.title_user_edited` and sets it on every recording whose name is not a default one such as "Monday 2nd Nov, Morning Meeting". A meeting renamed before the upgrade then keeps its name through a reprocess, and one still under a default name takes the AI title (#889).
- `vad_mute_transcription` is retired, and a stored value is dropped on the user's next save (#966). New install-wide keys in `data/config.json` are `ollama_think` and `secondary_ollama_think` (`false`), shown as **Thinking**, and `sentence_start_correction` (`true`), which has no settings page (#922, #937).
- New per-user settings are `suggest_live_speaker_names` (on) and `phantom_filter_off` (unset, shown as **Check speakers heard only briefly** ticked) (#803, #922).
- No environment variable changed, and `.env.example`, `docker-compose.example.yml` and `nginx/` need no action.
- Backups stay in format 3. An older archive restores with its names counted as chosen, as the migration does (#889).

### Rollback

<!-- Maintainer: state whether rollback is code-only or requires data steps. Default below. -->

- Roll the schema back before the images, because the 3.0.0 API refuses to start against a revision it does not know. With no browser recording in progress and 3.1.0 still running, run `docker compose exec api alembic downgrade 53b4bc17207f`, then stop the stack and start the 3.0.0 image tags.
- The downgrade drops `recordings.title_user_edited`, so 3.0.0 writes the AI title over every meeting's name again at its next processing or reprocess.
- 3.0.0 ignores `data/celerybeat-schedule.json`, so every periodic job waits a full interval once. It restores an archive made on 3.1.0, without the record of chosen names.

### Known Issues

<!-- Maintainer: list only problems Nojoin can fix, each with its open issue. A limit set by a dependency or a provider is documented in the guide for that feature and does not belong here. -->

- Live lines can move words between speakers at a turn change before they lock, and past eight voices a live reply's first words can still go to the previous speaker (#776, #813, #888).
- Editing the text of the previous speaker's line sends a reply's moved first words back to that line (#975).
- A live name the AI suggested stays when its line moves to another speaker, and turning off **Suggest speaker names during a meeting** mid-meeting leaves names already offered on screen (#981).
- When AI runs on a Claude or ChatGPT subscription, a live naming ask or a Meeting Edge call can run for 300 s where Nojoin allows 90 s (#980).
- The **Analytics** note comparing a person with their usual delivery can still read meetings where all their lines moved to other speakers, and meetings in the bin (#985).

### Browser-Capture Compatibility

<!-- Maintainer: note any change to supported browsers/OSes or capture behaviour. Default below. -->

- Firefox on Windows, Linux and macOS, Safari on macOS, and every browser on Android and iOS record the microphone only, with no share picker. Chrome on desktop and other Chromium-family browsers on Windows and Linux record system audio with the microphone, or the microphone alone, and other Chromium-family browsers on macOS record system audio best-effort. Recording needs microphone access and AudioWorklet support, which plain HTTP lacks (#804).
- In desktop Chromium, cancelling the share picker offers **Record microphone only**, and a refused or silent share asks again before anything records. A **Resume** after a reload records from the meeting's own source (#963).
- **Start Meeting** gives up after 10 seconds when the browser never starts audio, releases the microphone and any share, and removes the meeting it began, as Firefox on Linux does without a sound server. A **Resume** after a reload leaves the recording paused, and the input test in **Settings > Recording** releases the microphone (#929, #931).
- After a pause, the line break lands where the new audio begins, also after an upload stall, an offline pause or a reload (#878).

### Changes

<!-- Filled in when the release is published, from the commits since the previous tag. Keep this heading and the placeholder below when rewriting the notes: Docs validation fails without them. -->

{{CHANGELOG}}
