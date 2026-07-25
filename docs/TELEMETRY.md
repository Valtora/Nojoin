# Anonymous Usage Data

Nojoin sends one small, anonymous ping per day describing how the installation is configured and how much it is used. This page is the complete and canonical description of that: what is sent, what is never sent, when it is sent, and how to turn it off.

If you would rather not read the whole page: it is anonymous, it contains none of your meeting content, it can be switched off in **Settings > Administration > Anonymous usage data**, and the data is never sold.

## Why This Exists

Nojoin is self-hosted, which means there is no other way to know that a deployment exists. Without this, decisions about what to build, what to keep supporting, and what to retire are guesswork.

The questions it is meant to answer are:

- How many Nojoin deployments are there?
- How quickly do people update, and which versions are still in use?
- Is Nojoin actually being used day to day, or installed and abandoned?
- Which features are worth further investment, and which are not used?
- Which LLM providers and transcription engines should be prioritised?

The data is used to guide development. **It is never sold, and it is never shared with a third party for their own purposes.** The only processor involved is Cloudflare, which hosts the receiving endpoint.

## What Is Sent

One `POST` per day to `https://telemetry.nojoin.co.uk/v1/ping`, containing exactly this:

```json
{
  "schema": 1,
  "install_id": "3f2a5c1e-9b4d-4a7f-8e1c-2d6b0a9f4e33",
  "version": "1.6.0",
  "install_age_days": 42,
  "local_origin": false,

  "users_total": 4,
  "users_recording_28d": 3,
  "recordings_total": 271,
  "recordings_28d": 19,
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
| `install_id` | A random UUID generated once on your server. It is not derived from your hostname, IP, licence, hardware, or anything else — it is simply a random number, so pings from the same install can be recognised as one deployment rather than many |
| `version` | The running Nojoin version |
| `install_age_days` | How long this deployment has existed, from the date the first account was created |
| `local_origin` | Whether `WEB_APP_URL` is a localhost address. Development stacks are counted like any other install; this flag just makes them separable later |
| `users_total` | Number of active user accounts |
| `users_recording_28d` | How many of those users recorded anything in the last 28 days |
| `recordings_total` | Total recordings held |
| `recordings_28d`, `recording_hours_28d` | Recording volume in the last 28 days |
| `llm_provider` | Which provider *family* is configured: `gemini`, `openai`, `anthropic`, `ollama`, or `cli_oauth`. Never the key, the endpoint, or the model name |
| `secondary_configured`, `cli_oauth_in_use`, `meeting_edge_enabled` | Whether a fallback provider is set, whether a subscription is connected, and whether Meeting Edge is on |
| `asr_engine`, `whisper_model_size`, `gpu` | Transcription engine, model size, and whether a GPU is present |
| `calendar_connected`, `mcp_in_use`, `chat_used_28d`, `documents_used`, `tasks_used`, `people_library_used` | Whether each feature is in use. Yes or no only, never contents |

There is no timestamp in the payload. The receiving service uses its own clock to decide which day a ping belongs to, so a wrong clock on your server cannot corrupt the data.

## What Is Never Sent

Nothing about your meetings, your people, or your server's identity ever leaves your installation:

- **No meeting content.** No audio, transcripts, meeting notes, titles, summaries, chat messages, or uploaded documents.
- **No people.** No usernames, email addresses, display names, speaker names, voiceprints, or calendar attendees.
- **No identity.** No hostname, domain, URL, IP address, or TLS certificate details.
- **No secrets.** No API keys, tokens, passwords, OAuth credentials, or encryption keys.
- **No configuration detail.** No `.env` contents, no Ollama URL, no model names, no file paths.

The receiving service additionally stores **nothing derived from the connection**. Your server's IP address is used only to decide whether a request is part of a flood, and is never written to the database. No User-Agent, and no geolocation, is recorded.

## When It Is Sent

This differs depending on whether you installed Nojoin fresh or upgraded into this feature.

### New installations

The first-run setup wizard shows a checkbox, ticked by default, on the same screen as the legal disclaimer. That tick is your consent, and pings begin from the next daily cycle. Unticking it means nothing is ever sent.

### Existing installations that upgraded

**Nothing is sent until an administrator has actually seen the notice.** On upgrade, an admin sees a banner explaining what telemetry is, with three options:

| You choose | What happens |
| --- | --- |
| **Keep it on** | Pings start straight away |
| **Turn it off** | Nothing is ever sent |
| **Dismiss**, or ignore it | Nothing is sent for 7 days. If you have not decided by then, pings start |

The 7-day clock starts when the banner first appears on screen, not when you upgraded, so you always get the full week to decide.

One consequence is deliberate and worth stating plainly: **if nobody ever signs in to your installation, no notice is ever shown and nothing is ever sent.** Unattended deployments are therefore undercounted, and that is accepted as the price of not sending data before a human has been told.

## How to Turn It Off

Any one of these is sufficient.

**In the app.** Go to **Settings > Administration > Anonymous usage data** and switch it off. This takes effect immediately — the next daily cycle sends nothing. You do not need to restart.

**Before you ever start Nojoin.** Set this in `.env`:

```dotenv
NOJOIN_TELEMETRY_ENABLED=false
```

This is a hard switch. It overrides the in-app setting, cannot be overridden from the UI, and the Settings toggle shows as read-only. Use it when telemetry must be off as a matter of policy rather than preference.

**At the network layer.** Block or null-route `telemetry.nojoin.co.uk`. Sending is best-effort and failures are ignored, so nothing else in Nojoin is affected.

Turning telemetry off does not degrade Nojoin in any way. No feature depends on it.

## How to Verify Any of This

You do not have to take this page's word for it.

**Read the code.** The entire payload is assembled in one function, `build_payload` in [backend/utils/telemetry.py](../backend/utils/telemetry.py). A test asserts that the payload contains exactly the fields listed above and nothing else, so this page cannot silently drift out of date.

**Read the receiving code.** The collector is open source in the [telemetry/](../telemetry/) directory of this repository, including the database schema. What is stored is therefore verifiable rather than a promise.

**Watch the traffic.** The request goes to `telemetry.nojoin.co.uk` over HTTPS, once a day, from the `worker-io` container. It is an ordinary JSON POST and you can inspect it with any network tool.

**Check the app.** Settings shows your install ID, the endpoint, and when the last ping was sent.

## Retention

Individual daily rows are kept for 13 months. After that they are automatically reduced to daily totals and per-version or per-feature counts, and the individual rows are deleted. The aggregates contain no install IDs.

## Legal Basis and Your Rights

The data is anonymous: it contains no personal data and no identifier that can be linked back to a person or an organisation. The install ID is a random number generated on your own server and is not connected to any account, name, address, or network identifier.

Because there is no personal data, there is nothing to subject-access or erase. If you want a specific install to stop contributing, turn telemetry off using any of the methods above; from that moment nothing further is sent, and the existing rows age out under the retention policy.

## Related Docs

- [DEPLOYMENT.md](DEPLOYMENT.md): the `NOJOIN_TELEMETRY_ENABLED` environment variable
- [ADMIN.md](ADMIN.md): the Settings area for administrators
- [SECURITY.md](SECURITY.md): outbound connections and the wider security policy
- [LEGAL.md](LEGAL.md): legal disclaimer and terms of use
- [adr/0004-anonymous-opt-out-telemetry.md](adr/0004-anonymous-opt-out-telemetry.md): why it was built this way
