## Nojoin {{VERSION}}

Container images for this release. All images are cosign-signed and ship build-provenance and SBOM attestations; verification steps are in the [deployment guide](https://github.com/Valtora/Nojoin/blob/main/docs/DEPLOYMENT.md#verifying-an-image-before-deploying). Pin to a digest for reproducible deployments.

{{IMAGE_DIGESTS}}

### Breaking Changes

<!-- Maintainer: what stops working or behaves differently after the upgrade, and what to do about it. -->

- **Your notes are now timeline notes.** Each note sits at a moment in the meeting, survives reprocessing and reaches the AI at that point. Free-text notes from earlier releases become one note before the transcript (#494).
- **Integrations that use notes need updating.** `/transcripts/{id}/timeline-notes` replaces `/transcripts/{id}/user-notes`, MCP `get_meeting_notes` returns `timeline_notes`, and `append_meeting_notes` adds one note per call, at `at_seconds` when given. Backups are written in format 3, which earlier releases refuse (#494, #684).
- **Some other API answers changed.** `POST /tags/` answers 400 for a name already in use (#604), batch recording routes answer with `count`, `skipped` and `recording_ids` (#739), and edits answer 409 while a reprocess runs (#756). The OAuth token endpoint answers a missing `grant_type` with an OAuth error (#648), and every retired Companion route answers 410 (#665).
- **Actions that could lose a meeting now refuse.** Retry Speaker Inference answers 409 unless the meeting has finished processing, and Discard answers 409 for any meeting that has finished once, so move it to the bin instead. Reprocess answers 409 for a paused capture or while notes generate, and notes generation answers 409 during a capture. Notes no longer move a recording to processing, and MCP tools pass on a route's reason when it refuses (#850, #852, #855, #857, #859).
- **Parakeet word ends are capped, at 0.4 s by default.** A word also ends where the next one starts or its window ends, so a window's last word no longer runs into the next speaker's turn. **Word length cap** (`asr_word_length_cap_s`) replaces the retired `asr_word_end_padding_s` (#473).
- **Several defaults changed.**
  - Browser noise suppression is off, and a setting saved before this release reads as off once (#711).
  - Live speaker labels and live word settling are on with a CUDA worker (#729, #735).
  - Speaker separation hears the whole recording, which tells people apart better and can take up to 1.5 times as long on a CPU (#658).
  - Suggested speaker names wait for **Confirm**, and only confirmed names teach a voiceprint (#683).
  - New transcript lines run to about 30 seconds, up from 10 (#735).
  - **Edit text** moved to each line's ⋯ menu, and clicking a line selects its text (#616).

### Highlights

<!-- Maintainer: one bullet per item, one or two sentences each. What an operator would notice, not how it works. Detail belongs in the docs. Remove the section if a release has nothing to lead with. -->

- **One recording view for every state.** Recording, paused, processing and finished meetings open on the same page, with the recorder in the player's place and one side column for Speakers, My notes, Captures and Meeting Edge (#756).
- **Live words settle before they lock.** On a GPU server recent words are decoded again until three passes agree, so they appear about 2.3 seconds sooner and are as accurate or better. Live lines can be edited and split once they lock, and reprocessing keeps your corrections (#735).
- **Live lines name their speakers.** With a CUDA worker live lines show "Speaker N", and you can name a speaker while recording. NVIDIA's Nemotron model labels meetings of up to seven people, live and final, and a voiceprint match of 0.85 or more shows its name without asking (#543, #696, #705, #725, #729).
- **Live speakers can be picked and merged.** During a capture **Change speaker** lists the meeting's live speakers first, and one live speaker can be merged into another from a line or from Speakers. Both carry through to the processed meeting. People appear in the picker only when you search (#863, #866).
- **Notes go on lines, selections and moments.** Add one under the live transcript, from a line's ⋯ menu or on selected text, move it between lines, and find it again in the My notes tab. Unsaved notes survive a reload, and exports include your notes unless you untick **Include my notes** (#517, #553, #613, #616, #684, #690, #694).
- **Captures put screen stills and files on notes.** Alt+Shift+C or the camera button saves a still of the shared screen, and a file dropped into the note box goes with its note. Notes, Meeting Chat and search read only the files you switch on, and a file can be removed from its note (#699, #738).
- **A recording that needs recovery gets a badge.** Pausing in one tab no longer opens a dialog in every other tab, and a reloaded capturing tab takes its capture back at once (#760).
- **The microphone can be released while paused.** A per-browser setting, off by default, frees it for other apps during a pause and asks for it again on Resume (#755).
- **Browser recordings hold up through more failures.** A capture survives an ended sign-in, an outage and a takeover by another tab, and audio a tab could not upload is offered as a download. The live transcript says when it is behind, and moves past a missing chunk where it used to stop (#518, #581, #591, #629, #790, #791).
- **Each recording can have its own language.** Set it at import or on reprocess. With Whisper and voice activity detection on, Nojoin detects the language from speech and offers to reprocess when it hears a different one. Canary on automatic now reads as English (#673, #680).
- **Transcripts lose fewer words and invent fewer.** Transcription hears the audio inside every speaker turn, Parakeet decodes speech it skipped a second time, and Whisper's "Thank you." in digital silence is dropped (#496, #609, #675).
- **Voiceprints are harder to pull off course.** A new voiceprint needs 20 seconds of clear speech from one recording, folding in another recording needs a similarity of 0.70, up from 0.60, and a voiceprint can be locked (#498, #499, #515).
- **Two appearance styles join Default.** Classic and Depth are under **Settings > Appearance > Style**, saved per browser (#723).
- **Dates and times follow the time-zone setting everywhere.** Outside UTC they were shifted by the browser's offset, and "Revised" stuck to finished lines. With no zone chosen Nojoin shows UTC, or `DEFAULT_TIMEZONE` where the administrator set one, so pick yours in **Settings > Appearance** (#751, #753).
- **Recordings lists share one selection.** Recordings, Archived and Deleted take Shift ranges and a ⋯ menu on each row, and every action but **Delete Permanently** offers **Undo** for 10 seconds. Phones get Speakers and My notes tabs and a **Start** button (#655, #684, #739, #802).
- **Security gaps are closed.** The bundled proxy sends HSTS on every HTTPS response (#493), a password change or revoke-all ends MCP access, refresh tokens included (#500), and file paths stay inside their folders (#630, #632). The images drop the distrusted eMudhra root (#588), logs mask one-time codes (#674), people and tags stay in each user's own library (#544, #548), and the last active Owner or superuser can no longer be removed (#641, #642).
- **Meetings are harder to lose.** Retry Speaker Inference during a capture used to end it and lose its audio, and Discard while notes regenerated deleted the finished meeting. Notes, AI analysis and delivery left behind by a worker restart now read as failed after 15 minutes and can run again, where they stayed generating for good, and a retry whose audio is missing stays failed instead of waiting in the queue (#850, #852, #859).
- **Setup follows a pinned telemetry setting.** With `NOJOIN_TELEMETRY_ENABLED` set, setup shows its value read-only and stores no choice, so removing a `false` pin later leads to the usual notice instead of diagnostics starting unasked (#854, #862).
- **Smaller fixes.** Regenerating notes on several recordings no longer fails with "No model selected" (#471), and Stop no longer fails with a deadlock while the last segment is transcribed (#784). Meeting Chat no longer offers to watch the meeting or act later (#786). Images fit the vision providers' limits (#713, #721). Setup accepts a non-ASCII `FIRST_RUN_PASSWORD`, and two browsers finishing it at once no longer make two Owners (#727). Calendars stop retrying a refused sign-in, and tags hold each name once per user (#593, #667). A second find and replace now changes the lines an earlier one changed (#857). A speaker correction carries only onto the words its line covers, so one near a change of speaker no longer takes the next person's speech with it (#864). The Reprocess dialog opens on the install's model, and unnamed buttons and the notes editor have names for screen readers (#855). Permanent and batch delete take their locks in the same order as Discard, so the two can no longer deadlock (#857).
- **Dependency changes.** The worker pins Transformers 5.20.2 for Nemotron, and Pillow moves to 13.0.0 (#532, #725). PDFs are parsed with pypdfium2 and pdfplumber in place of PyMuPDF, so multi-column pages read column by column and a truncated PDF fails to parse. Documents attached earlier keep their text until parsed again (#651). Claude Sonnet 4.6, Opus 4.6 and Haiku 4.5 now use Anthropic's default temperature, which the current Anthropic API requires (#556).

### Upgrade

Update your checkout, pull the new images, stop the stack, and start it again.

```bash
git pull
docker compose pull
docker compose down
docker compose up -d
```

- Stop the stack before starting the new images. A 2.10.0 worker still running after the migration fails, because it reads the `transcripts.user_notes` column this release drops (#494).
- `git pull` brings the bundled proxy's configuration, which now includes two new files, `nginx/hsts.conf` and `nginx/access-log.conf`. Stopping the stack recreates the nginx container, which it needs to read the new configuration. If an edge proxy in front of Nojoin already adds `Strict-Transport-Security`, remove one of the two, because a browser reads only the first (#493, #674).
- A Canary install downloads the pinned Qwen3 forced aligner, about 1.8 GB, on `worker-cpu` at first start, and needs about 3.7 GB more worker RAM while it is loaded. Canary transcribes without word timings until it is ready (#735).
- A CUDA install downloads the Nemotron diarisation model, 397 MB, after its first eligible recording, which keeps the existing speaker pass. Prepare it ahead in **Settings > AI providers > Advanced > Model downloads**. CPU workers, `speaker_engine` set to `classic` and recordings with **Max Speakers** set never use it (#725, #729).
- Final processing of long recordings can take longer on a CPU host, because speaker separation now hears the whole recording. Voiceprint match scores may shift slightly. **Mute non-speech for speaker separation** in **Settings > Recording > Advanced** brings back the old behaviour (#658).
- The example compose file now passes the four `NOJOIN_STALL_WATCHDOG_*` variables to `api` and `NOJOIN_STORAGE_HYGIENE` to every app service. A `docker-compose.yml` copied from an earlier example ignores those values in `.env` until you copy the lines across (#511, #533).
- An empty `LLM_PROVIDER` now means the provider of the only API key set, and `gemini` otherwise. A `docker-compose.yml` copied from an earlier example turns an empty value into `gemini`, so change its line to `${LLM_PROVIDER:-}` to use the new rule. If the provider changes, choose a model in **Settings > AI providers** (#521, #728).
- Compose reads a `.env` line such as `KEY= # note` as the value `# note`. Nojoin now reads such a value as empty and logs the variables it emptied, so move each comment to its own line. If `BACKUP_EXPORT_DIR` had that shape, exports went inside the worker container and were lost when it was recreated, so export again any archive you need (#562).
- Reprocess a recording to apply the Parakeet word cap, the longer lines and the new speaker pass to it. Recordings keep their stored transcripts until then.

### Migration

Database migrations run automatically on the first API start after upgrading. Back up your instance before upgrading.

<!-- Maintainer: note any blocking first-boot migration, longer startup, or manual step. Keep it to bullets. -->

- Fourteen Alembic revisions run in order on the first API start.
  - `7c3e5a91d2f4` adds `recording_speakers.embedding_speech_seconds`, the speech a voiceprint was built from (#498).
  - `63005cac6492` turns each non-blank `transcripts.user_notes` into one note before the transcript, owned by the recording's owner, and drops the column (#494).
  - `7ff0bbdadad8` adds the table `recording_speaker_drift_refusals`, empty at first (#499).
  - `7cee4b4388a7` gives each person with no owner the owner of the recordings that link them, and moves a link into another user's library to the linking user's person of the same name. People made by **Create Voiceprint** or the demo meeting then show in their owner's People page. It deletes nothing (#544).
  - `f704bbd87f98` gives each people tag with no owner an owner, and keeps tag links and parents within one user's library. It deletes no tag (#548).
  - `edb5059e5d68` merges each user's repeated tag and people tag names into the oldest tag of that name, then adds a unique index on `(user_id, name)` (#593).
  - `8f7a09a9d7f3` adds `recordings.capture_epoch`, which names the tab that holds a capture (#591).
  - `fb5a849e7f5b` deletes repeated links between a meeting and a tag, keeping the oldest, then adds a unique constraint (#601).
  - `76243f67da02` adds `recordings.transcription_language`, empty, so every recording follows its owner's setting (#680).
  - `b13e4912f23a` adds `transcripts.language_detection` (#680).
  - `0f3b502561a6` adds the table `speaker_name_events` and `recording_speakers.name_source`. Names given before the upgrade show as they did (#683).
  - `c4d81e6a2f90` adds `documents.note_id` and `documents.use_in_notes` for files on notes (#699).
  - `7bb9c74f9946` adds `timeline_notes.file_removed` (#738).
  - `f2a6e1d9c735` adds `transcripts.change_sequence` and `transcripts.settling_state` (#735).
- `asr_word_end_padding_s` is retired. A stored value is ignored, and dropped from `config.json` on the next save (#473).
- New install-wide keys in `data/config.json` are `live_speaker_labels` (`auto` or `off`) and `speaker_engine` (`auto` or `classic`), which have no settings page, and `live_text_settling` (`off`, `auto` or `always`) with `live_text_settling_k` (3) and `live_text_settling_window_s` (10). Without a stored value the three modes take `auto`, and a stored `off` stays off. `always` settles on a CPU worker too, at a high CPU and RAM cost. The `live_max_segment_s` default rises from 20 to 30 seconds (#725, #729, #735).
- New per-user settings are `asr_word_length_cap_s` (0.4 s), `line_break_pause_s` (1 s), `vad_mute_transcription` (on), `vad_mute_diarization` (off), `confirm_suggested_names` (off) and `voiceprint_learning` (`confirmed`). Setting `voiceprint_learning` to `automatic` lets unconfirmed matches teach voiceprints again (#473, #658, #683, #735).
- `NOJOIN_STORAGE_HYGIENE` is a new environment variable. Empty means `flag`, which only reports, and `delete` removes the files (#533).
- `.env.example`, `docker-compose.example.yml` and `nginx/` changed, as **Upgrade** describes.
- Backups are written in format 3. Restoring an older archive turns its notes into one note before the transcript, as the migration does (#494). On recordings made without live word settling, a backup forgets the names you dismissed with **Not Alex**, so after a restore they can be suggested again (#683).

### Rollback

<!-- Maintainer: state whether rollback is code-only or requires data steps. Default below. -->

- Roll the schema back before the images, because the 2.10.0 API refuses to start against a revision it does not know. With no browser recording in progress and 3.0.0 still running, run `docker compose exec api alembic downgrade 3b804f4435a1`, then stop the stack and start the 2.10.0 image tags.
- Keep a backup made on 2.10.0. That release refuses the format 3 archives 3.0.0 writes.
- The downgrade folds each meeting's notes back into one free-text field in timeline order, losing each note's kind, time, quote and author. Files on notes, screen stills included, stay on their meetings as ordinary documents, and every notes run reads them.
- It also drops speaker name sources, so a name still waiting for **Confirm** shows as a plain name. Drift guard refusals, each recording's own language, language detection results and voiceprint speech figures go too. Merged tags, deleted duplicate tag links and the owners given to people and tags stay as they are.

### Known Issues

<!-- Maintainer: list only problems Nojoin can fix, each with its open issue. A limit set by a dependency or a provider is documented in the guide for that feature and does not belong here. -->

- After a pause, the transcript's line break can land a few seconds early, mid-speech, when audio from before the pause was still waiting to upload, for example after an upload stall, an offline pause or a reload. The final words are unaffected (#815).
- The first words of a reply can go to the previous speaker, and live lines can move words between speakers at a turn change before they lock. The phantom speaker filter can also drop a short interjection with its words, and setting either of its limits to 0 turns it off (#776, #813).
- After a damaged audio segment the live transcript waits about 10 seconds before it moves on (#808).
- With Canary, untimed words after a line you corrected can go into that line's review and out of the transcript. Within ten minutes of a failed aligner download, preparing Canary reports done without fetching the aligner (#788).
- While the Canary aligner and Nemotron download at the same time, **Model downloads** can show one's progress against the other (#783).

### Browser-Capture Compatibility

<!-- Maintainer: note any change to supported browsers/OSes or capture behaviour. Default below. -->

- Supported browsers, operating systems and audio sources are unchanged. Recording needs Chrome or another Chromium browser on desktop, or Chrome on Android or iOS for the microphone alone, and Firefox and Safari still cannot record.
- Capture asks the browser for echo cancellation and auto gain, with noise suppression off unless you turn it on under **Settings > Recording > Advanced** (#711).
- Stop releases the microphone before the upload, and **Release the microphone while paused** frees it during a pause. Shared tab or screen audio is never released, and the release is untested on iOS (#518, #755).
- Start can use a saved microphone that the browser lists without an id, as Chrome does after **Allow this time** and a restart (#809).
- Only the tab that holds a capture shows its controls. Other tabs and devices read Live or Paused with the server's clock, and a refused Start says why, including when the recording is open in another tab (#756, #760).
- Each Resume starts a new transcript line, so speech either side of a pause no longer joins into one line (#802).

### Changes

<!-- Filled in when the release is published, from the commits since the previous tag. Keep this heading and the placeholder below when rewriting the notes: Docs validation fails without them. -->

{{CHANGELOG}}
