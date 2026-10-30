# Anonymous Usage Data

Nojoin sends two anonymous reports to its collector: a **daily heartbeat** that counts installations, and optional **usage diagnostics** every six hours that describe how an installation is configured and used. This page is the complete description of both.

In short: neither contains any of your meeting content, and the data is never sold. The heartbeat is a random install ID and a version number. Diagnostics can be switched off in **Settings > Privacy**. The heartbeat stops only with `NOJOIN_TELEMETRY_ENABLED=false` or a network block, and an installation that turned telemetry off before 2.7.0 does not send it at all.

## Why This Exists

A self-hosted deployment is otherwise invisible. The heartbeat shows how many deployments exist, how many are still running, and how quickly they update. The diagnostics show whether Nojoin is used day to day, which features deserve further work, and which LLM providers and transcription engines to prioritise.

The data is used to guide development. **It is never sold, and it is never shared with a third party for their own purposes.** The only processor involved is Cloudflare, which hosts the receiving endpoint.

### Why the heartbeat has no switch in the app

I build Nojoin in my spare time, and how much of that time it gets depends on how many installations exist. Until 2.7.0 all telemetry was optional, and it reported almost no installations besides my own while the 2.5.1 container images were downloaded more than a hundred times in the 24 days after release. Optional telemetry undercounts, so the heartbeat carries only what a count needs: the install ID tells two installations apart, and the version shows which releases are still running.

## What Is Sent

### The heartbeat

One `POST` to `https://telemetry.nojoin.co.uk/v1/heartbeat`, containing exactly this:

```json
{
  "schema": 1,
  "install_id": "3f2a5c1e-9b4d-4a7f-8e1c-2d6b0a9f4e33",
  "version": "2.7.0"
}
```

| Field | Meaning |
| --- | --- |
| `install_id` | A random UUID generated once on your server. See the diagnostics table below |
| `version` | The running Nojoin version |

The receiving service stores the install ID, the version, the UTC day and the time it was received, one row per install per day. **It discards any other field it is sent**, so the heartbeat cannot be widened without a visible change to the open-source collector.

### Usage diagnostics

One `POST` every six hours to `https://telemetry.nojoin.co.uk/v1/ping`, while diagnostics are switched on, containing exactly this:

```json
{
  "schema": 1,
  "install_id": "3f2a5c1e-9b4d-4a7f-8e1c-2d6b0a9f4e33",
  "version": "2.7.0",
  "install_age_days": 42,
  "local_origin": false,

  "users_total": 4,
  "users_recording_28d": 3,
  "recordings_total": 271,
  "recordings_28d": 19,
  "recordings_24h": 2,
  "recording_hours_28d": 15.7,

  "llm_provider": "anthropic",
  "secondary_configured": true,
  "cli_oauth_in_use": false,
  "meeting_edge_enabled": true,

  "asr_engine": "whisper",
  "whisper_model_size": "turbo",
  "gpu": true,

  "calendar_connected": true,
  "mcp_in_use": false,
  "chat_used_28d": true,
  "documents_used": true,
  "tasks_used": true,
  "people_library_used": true
}
```

| Field | Meaning |
| --- | --- |
| `install_id` | A random UUID generated once on your server, and the same one the heartbeat carries. It is not derived from your hostname, IP, licence, hardware or anything else. It only lets pings from one install be recognised as one deployment |
| `version` | The running Nojoin version |
| `install_age_days` | Days since the first account was created, or, before any account exists, since the install ID was generated |
| `local_origin` | Whether the configured web address (`WEB_APP_URL`) is a localhost address, as on a deployment reached only from the machine it runs on |
| `users_total` | Number of active user accounts |
| `users_recording_28d` | How many of those users recorded anything in the last 28 days |
| `recordings_total` | Total recordings held, not counting the bin |
| `recordings_28d`, `recording_hours_28d` | Recordings, and their total hours, in the last 28 days |
| `recordings_24h` | Recordings made in the last 24 hours, a measure of how often an installation is used |
| `llm_provider` | The install-wide AI provider *family*: `gemini`, `openai`, `anthropic` or `ollama`. Never the key, the endpoint or the model name |
| `secondary_configured`, `cli_oauth_in_use`, `meeting_edge_enabled` | Whether a fallback provider is set, whether any user has connected a CLI OAuth subscription, and whether Meeting Edge is on for the installation |
| `asr_engine`, `whisper_model_size`, `gpu` | The transcription engine (`whisper`, `parakeet` or `canary`), the Whisper model size setting, and whether a GPU is configured or detected |
| `calendar_connected`, `mcp_in_use`, `chat_used_28d`, `documents_used`, `tasks_used`, `people_library_used` | Whether each feature is in use. Yes or no only, never contents |

Neither payload carries a timestamp. The receiving service uses its own clock to decide which day a request belongs to, so a wrong clock on your server cannot corrupt the data.

Each diagnostics ping replaces the previous one for the same day. The receiving service keys a row on your install ID and the UTC date, so the four pings a day leave **one stored row per install per day**, holding whatever the most recent one said.

## What Is Never Sent

Nothing about your meetings, your people, or your server's identity leaves your installation:

- **No meeting content.** No audio, transcripts, meeting notes, titles, summaries, chat messages, or uploaded documents.
- **No people.** No usernames, email addresses, display names, speaker names, voiceprints, or calendar attendees.
- **No identity.** No hostname, domain, URL, IP address, or TLS certificate details.
- **No secrets.** No API keys, tokens, passwords, OAuth credentials, or encryption keys.
- **No configuration detail.** No `.env` contents, no Ollama URL, no model names, no file paths.

The receiving service also stores **nothing derived from the connection**. Your server's IP address is used only to decide whether a request is part of a flood, and is never written to the database. No User-Agent or geolocation is recorded.

ONNX Runtime, which runs the Parakeet and Canary engines and the search embeddings, sends its own usage telemetry to Microsoft by default in its Linux builds from 1.29. Nojoin turns that off: the worker image sets `ORT_DISABLE_TELEMETRY=1`, and the backend sets the same default before ONNX Runtime loads, which covers an install built from the requirements files too.

## When It Is Sent

Both are sent by scheduled tasks on the `worker-io` container.

### The heartbeat

A task checks every fifteen minutes and sends a heartbeat when:

- this installation has never sent one, so a new installation is counted within about fifteen minutes of starting, before the setup wizard is finished;
- it has started on a version it has not sent a heartbeat for yet;
- it has not sent one yet this UTC day.

After a failed heartbeat the next attempt waits an hour, so an installation that blocks the collector makes one failed lookup an hour. Each successful heartbeat is logged by `worker-io` at info level, naming the endpoint.

### Usage diagnostics

A task attempts a ping every six hours. The interval is not aligned to a fixed hour, so installations are spread across the clock. Four attempts a day mean a restart or a short outage rarely leaves a day with no row, and since the collector keeps one row per install per day, they record no more than a single daily ping would.

Whether diagnostics are sent at all depends on whether you installed Nojoin fresh or upgraded into this feature.

### New installations

The first-run setup wizard shows a **Share anonymous usage diagnostics** checkbox, ticked by default, on the same screen as the legal disclaimer. Leaving it ticked is your consent, and pings begin from the next cycle. Unticking it means no diagnostics are sent unless you turn them on later in Settings. The same screen says that the heartbeat is sent separately.

If `NOJOIN_TELEMETRY_ENABLED` is set, the checkbox shows its value and cannot be changed, as in **Settings > Privacy**, and says what that value sends: both reports for `true`, nothing for `false`. The wizard records no choice in that case, so if you remove the variable later, the installation is treated like an upgraded one: no diagnostics until an administrator has seen the notice.

### Existing installations that upgraded

**No diagnostics are sent until an administrator has seen the notice.** After the upgrade, an admin sees a banner explaining the heartbeat and the diagnostics, with three options:

| You choose | What happens |
| --- | --- |
| **Keep diagnostics on** | Pings start from the next cycle |
| **Turn diagnostics off** | No diagnostics are sent |
| **Dismiss**, or ignore it | The banner returns on the next page load. No diagnostics for 7 days, and if you have not decided by then, pings start |

The 7-day clock starts when the banner first appears on screen, not when you upgraded, so you always get the full week.

The heartbeat does not wait for the notice. It starts on the first start of 2.7.0 or later, unless one of the exemptions in the next section applies, so an installation nobody ever signs in to sends the heartbeat and no diagnostics.

## How to Turn It Off

**Diagnostics, in the app.** As an administrator, go to **Settings > Privacy** and switch **Share usage diagnostics** off. The next cycle sends nothing, with no restart needed. The heartbeat continues.

**Everything, before you ever start Nojoin.** Set this in `.env`:

```dotenv
NOJOIN_TELEMETRY_ENABLED=false
```

This is a hard switch for both. It stops the heartbeat and the diagnostics, overrides the in-app setting, and cannot be changed from the UI, where the setup wizard's checkbox and the Settings toggle show it read-only. Use it when telemetry must be off as a matter of policy, for example on an air-gapped or regulated network.

Set to `true`, the same variable is consent to both in its own right: diagnostics start on the next cycle without waiting for the notice, which is suppressed while the environment pins the setting. Left empty, the default, it leaves the diagnostics decision to the app.

**If you said no before 2.7.0, that still holds.** An installation that turned telemetry off before 2.7.0, in the wizard, on the notice or in Settings, sends no heartbeat either, and **Settings > Privacy** says so. The exemption lasts until diagnostics are switched back on. From then on, switching them off again stops the diagnostics only.

**At the network layer.** Block or null-route `telemetry.nojoin.co.uk`. Sending is best-effort and failures are ignored, so nothing else in Nojoin is affected.

No feature depends on telemetry, so turning it off does not degrade Nojoin in any way.

## How to Verify Any of This

**Read the code.** The heartbeat is assembled in one function, `build_heartbeat`, and the diagnostics in another, `build_payload`, both in [backend/utils/telemetry.py](../backend/utils/telemetry.py). Tests assert that each contains exactly the fields listed above, so this page cannot fall out of date without a test failing.

**Read the receiving code.** The collector is open source in the [telemetry/](../telemetry/) directory of this repository, including the database schema and the code that discards every heartbeat field except the install ID and version.

**Watch the traffic.** Both requests are ordinary JSON `POST`s over HTTPS from the `worker-io` container to `telemetry.nojoin.co.uk`, which you can inspect with any network tool. `worker-io` also logs each successful send.

**Check the app.** **Settings > Privacy** shows your install ID, both endpoints, and when the heartbeat and the diagnostics were last sent. If an attempt failed it says so, with the reason: a rejection, a timeout, or an unreachable host. Blocking the endpoint at the network layer shows up here as a failure, which is expected.

## Retention

Everything received is kept indefinitely, so the number of installations, and how it changes, can be followed over several years. Both tables hold only the fields described above, keyed on the random install ID.

Until 2.7.0 the stated policy was to keep individual rows for 13 months and then reduce them to daily totals. The first rows arrived in July 2026, so none reached that age before the policy changed, and nothing was deleted under it.

## Legal Basis and Your Rights

The data is anonymous: it contains no personal data and no identifier that can be linked to a person or an organisation. The install ID is a random number generated on your own server and is not connected to any account, name, address, or network identifier. Cloudflare processes the connecting IP address for the flood check, and it is never stored. That is why the heartbeat is sent without asking first, while the diagnostics, which say more about how an installation is used, ask for consent.

Because there is no personal data, there is nothing to subject-access or erase. To stop a specific install contributing, set `NOJOIN_TELEMETRY_ENABLED=false` or block the endpoint; from that moment nothing further is sent. Rows already received are kept.

## Related Docs

- [DEPLOYMENT.md](DEPLOYMENT.md): the `NOJOIN_TELEMETRY_ENABLED` environment variable
- [ADMIN.md](ADMIN.md): the Settings area for administrators
- [SECURITY.md](SECURITY.md): outbound connections and the wider security policy
- [LEGAL.md](LEGAL.md): legal disclaimer and terms of use
