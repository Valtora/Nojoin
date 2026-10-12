# Anonymous Usage Data

Nojoin sends two small, anonymous things to its collector: a **daily heartbeat** that counts installations, and optional **usage diagnostics** every six hours that describe how an installation is configured and used. This page is the complete and canonical description of both: what is sent, what is never sent, when it is sent, how long it is kept, and how to turn it off.

If you would rather not read the whole page: both are anonymous, neither contains any of your meeting content, and the data is never sold. The heartbeat is a random install ID and a version number. Diagnostics can be switched off in **Settings > Privacy**. The heartbeat stops only with `NOJOIN_TELEMETRY_ENABLED=false` or a network block, and it is not sent at all by an installation that turned telemetry off before 2.7.0.

## Why This Exists

Nojoin is self-hosted, which means there is no other way to know that a deployment exists. Without this, decisions about what to build, what to keep supporting, and what to retire are guesswork.

The questions it is meant to answer are:

- How many Nojoin deployments are there, and how many are still running?
- How quickly do people update, and which versions are still in use?
- Is Nojoin used day to day, or installed and abandoned?
- Which features are worth further investment, and which are not used?
- Which LLM providers and transcription engines should be prioritised?

The heartbeat answers the first two. The diagnostics answer the rest.

The data is used to guide development. **It is never sold, and it is never shared with a third party for their own purposes.** The only processor involved is Cloudflare, which hosts the receiving endpoint.

### Why the heartbeat has no switch in the app

I build Nojoin in my spare time, and how much of that time it gets depends on how many installations exist. Until 2.7.0 all telemetry was optional and waited for an administrator to agree, so it could not tell me. It reported almost no installations besides my own, while the 2.5.1 container images were downloaded more than a hundred times in the 24 days after release. Optional telemetry undercounts by design, and the heartbeat is how I find out which of those figures is closer to the truth.

It is kept as small as it can be and still count installations: the random install ID tells two installations apart, and the version shows which releases are still running. Everything else stays in the optional diagnostics.

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

The receiving service stores the install ID, the version, the UTC day, and the time it was received. **It discards any other field it is sent**, so the heartbeat cannot be widened without a change to the open-source collector that anyone can see. One row is kept per install per day.

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
| `install_id` | A random UUID generated once on your server. It is not derived from your hostname, IP, licence, hardware, or anything else. It is a random number, so pings from the same install can be recognised as one deployment rather than many. The heartbeat and the diagnostics carry the same one |
| `version` | The running Nojoin version |
| `install_age_days` | How long this deployment has existed, from the date the first account was created |
| `local_origin` | Whether `WEB_APP_URL` is a localhost address, which is the default for a deployment reached only on the machine it runs on |
| `users_total` | Number of active user accounts |
| `users_recording_28d` | How many of those users recorded anything in the last 28 days |
| `recordings_total` | Total recordings held |
| `recordings_28d`, `recording_hours_28d` | Recording volume in the last 28 days |
| `recordings_24h` | Recordings made in the last 24 hours, which shows how often an installation is used rather than only whether it is running |
| `llm_provider` | Which provider *family* is configured: `gemini`, `openai`, `anthropic`, `ollama`, or `cli_oauth`. Never the key, the endpoint, or the model name |
| `secondary_configured`, `cli_oauth_in_use`, `meeting_edge_enabled` | Whether a fallback provider is set, whether a subscription is connected, and whether Meeting Edge is on |
| `asr_engine`, `whisper_model_size`, `gpu` | Transcription engine, model size, and whether a GPU is present |
| `calendar_connected`, `mcp_in_use`, `chat_used_28d`, `documents_used`, `tasks_used`, `people_library_used` | Whether each feature is in use. Yes or no only, never contents |

There is no timestamp in either payload. The receiving service uses its own clock to decide which day a request belongs to, so a wrong clock on your server cannot corrupt the data.

Each diagnostics ping replaces the previous one for the same day rather than adding to it. The receiving service keys a row on your install ID and the UTC date, so the four pings a day leave **one stored row per install per day**, holding whatever the most recent one said. Sending four times is about reliability, not about recording more: see [When It Is Sent](#when-it-is-sent).

## What Is Never Sent

Nothing about your meetings, your people, or your server's identity ever leaves your installation:

- **No meeting content.** No audio, transcripts, meeting notes, titles, summaries, chat messages, or uploaded documents.
- **No people.** No usernames, email addresses, display names, speaker names, voiceprints, or calendar attendees.
- **No identity.** No hostname, domain, URL, IP address, or TLS certificate details.
- **No secrets.** No API keys, tokens, passwords, OAuth credentials, or encryption keys.
- **No configuration detail.** No `.env` contents, no Ollama URL, no model names, no file paths.

The receiving service additionally stores **nothing derived from the connection**. Your server's IP address is used only to decide whether a request is part of a flood, and is never written to the database. No User-Agent, and no geolocation, is recorded.

## When It Is Sent

Both are sent by scheduled tasks on the `worker-io` container.

### The heartbeat

A task checks every fifteen minutes whether a heartbeat is due, and sends one when:

- this installation has never sent one, so a new installation is counted within about fifteen minutes of starting, before the setup wizard has been completed;
- it has started on a version it has not sent a heartbeat for yet;
- it has not sent one yet this UTC day.

Otherwise the check does nothing. If a heartbeat fails, the next attempt waits an hour, so an installation that blocks the collector makes one failed lookup an hour rather than four. A successful heartbeat is written to the `worker-io` log at info level, naming the endpoint.

### Usage diagnostics

A task attempts a ping every six hours, counted from when the `worker-io` container last started. Nothing is aligned to a fixed hour, so installs are spread across the clock rather than arriving together.

**Why six hours and not a day.** The scheduler measures the interval from its own last run, which means restarting the container restarts the clock. On a daily interval a single restart moves the send time and can skip a calendar day entirely, and a day with no ping is indistinguishable from an install that was switched off. Four attempts a day bound that gap to a few hours. Because the receiving service keeps one row per install per day regardless, this changes how reliably a day is recorded and not how much is recorded.

Whether diagnostics are sent at all differs depending on whether you installed Nojoin fresh or upgraded into this feature.

### New installations

The first-run setup wizard shows a checkbox for usage diagnostics, ticked by default, on the same screen as the legal disclaimer. That tick is your consent to diagnostics, and pings begin from the next cycle. Unticking it means no diagnostics are ever sent. The same screen says that the heartbeat is sent separately.

### Existing installations that upgraded

**No diagnostics are sent until an administrator has seen the notice.** On upgrade, an admin sees a banner explaining the heartbeat and the diagnostics, with three options:

| You choose | What happens |
| --- | --- |
| **Keep diagnostics on** | Pings start straight away |
| **Turn diagnostics off** | No diagnostics are ever sent |
| **Dismiss**, or ignore it | No diagnostics for 7 days. If you have not decided by then, pings start |

The 7-day clock starts when the banner first appears on screen, not when you upgraded, so you always get the full week to decide.

The heartbeat does not wait for the notice. It starts on the first start of 2.7.0 or later, unless one of the exemptions in the next section applies. An installation nobody ever signs in to therefore sends the heartbeat and no diagnostics.

## How to Turn It Off

**Diagnostics, in the app.** Go to **Settings > Privacy** and switch **Share usage diagnostics** off. This takes effect immediately: the next cycle sends nothing. You do not need to restart. The heartbeat continues.

**Everything, before you ever start Nojoin.** Set this in `.env`:

```dotenv
NOJOIN_TELEMETRY_ENABLED=false
```

This is a hard switch for both. It stops the heartbeat and the diagnostics, overrides the in-app setting, cannot be overridden from the UI, and the Settings toggle shows as read-only. Use it when telemetry must be off as a matter of policy rather than preference, for example on an air-gapped or regulated network.

The same variable set to `true` is the opposite instruction, and it is treated as consent to both in its own right: diagnostics start on the next cycle without waiting for the notice, because the notice is suppressed while the environment pins the setting and would otherwise never be answered. Leaving the variable empty, which is the default, leaves the diagnostics decision to the app.

**If you said no before 2.7.0, that still holds.** An installation that turned telemetry off before 2.7.0, by unticking the wizard checkbox, choosing **Turn it off** on the notice, or switching it off in Settings, sends no heartbeat either. **Settings > Privacy** says so. The exemption lasts until diagnostics are switched back on; from then on, switching them off again stops the diagnostics only.

**At the network layer.** Block or null-route `telemetry.nojoin.co.uk`. Sending is best-effort and failures are ignored, so nothing else in Nojoin is affected.

Turning telemetry off does not degrade Nojoin in any way. No feature depends on it.

## How to Verify Any of This

You do not have to take this page's word for it.

**Read the code.** The heartbeat is assembled in one function, `build_heartbeat`, and the diagnostics in another, `build_payload`, both in [backend/utils/telemetry.py](../backend/utils/telemetry.py). Tests assert that each contains exactly the fields listed above and nothing else, so this page cannot drift out of date without a test failing.

**Read the receiving code.** The collector is open source in the [telemetry/](../telemetry/) directory of this repository, including the database schema and the code that discards any heartbeat field other than the install ID and version. What is stored is therefore verifiable rather than a promise.

**Watch the traffic.** Both requests go to `telemetry.nojoin.co.uk` over HTTPS from the `worker-io` container. They are ordinary JSON POSTs and you can inspect them with any network tool. Each successful heartbeat is also logged by `worker-io`.

**Check the app.** **Settings > Privacy** shows your install ID, both endpoints, and when the heartbeat and the diagnostics were last sent. If an attempt failed it says so there too, with the reason: a rejection, a timeout, or an unreachable host. Blocking the endpoint at the network layer will show up here as a failure, which is the expected result of doing that rather than a fault.

## Retention

Everything received is kept indefinitely. Nothing is rolled up or deleted, so the number of installations, and how it changes, can be followed over several years. Both tables hold only the fields described above, keyed on the random install ID.

Before 2.7.0 the policy was to keep individual rows for 13 months and then reduce them to daily totals. The first rows were received in July 2026, so none reached that age before the policy changed, and nothing was deleted under it.

## Legal Basis and Your Rights

The data is anonymous: it contains no personal data and no identifier that can be linked back to a person or an organisation. The install ID is a random number generated on your own server and is not connected to any account, name, address, or network identifier. The connecting IP address is processed by Cloudflare for the flood check and never stored.

That is also why the heartbeat is sent without asking first. It is a random number and a version string, which identifies nobody, and consent is still asked for the diagnostics, which say more about how an installation is used.

Because there is no personal data, there is nothing to subject-access or erase. If you want a specific install to stop contributing, set `NOJOIN_TELEMETRY_ENABLED=false` or block the endpoint; from that moment nothing further is sent. Rows already received are kept.

## Related Docs

- [DEPLOYMENT.md](DEPLOYMENT.md): the `NOJOIN_TELEMETRY_ENABLED` environment variable
- [ADMIN.md](ADMIN.md): the Settings area for administrators
- [SECURITY.md](SECURITY.md): outbound connections and the wider security policy
- [LEGAL.md](LEGAL.md): legal disclaimer and terms of use
