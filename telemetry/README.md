# Nojoin Telemetry Ingest

The Cloudflare Worker that receives Nojoin's anonymous telemetry: the daily install heartbeat and the optional usage diagnostics.

This code is public on purpose. The claim in [docs/TELEMETRY.md](../docs/TELEMETRY.md) about what Nojoin collects is only worth as much as your ability to check it, so both halves — the client that sends and the ingest that stores — are readable in the same repository.

## What it does

| | |
| --- | --- |
| Endpoints | `POST https://telemetry.nojoin.co.uk/v1/heartbeat` and `POST https://telemetry.nojoin.co.uk/v1/ping` |
| Response | `204` with no body. Anything else returns `400`, `404`, `405`, `413`, `429`, or `500`, all with empty bodies |
| Cadence | Heartbeat: on first start, on each new version, and once per UTC day. Diagnostics: one ping every six hours, while switched on |
| Storage | Cloudflare D1, one row per install per UTC day in each of `heartbeats` and `pings` |
| Retention | Indefinite. Nothing is rolled up or deleted |

Every request is anonymous. The ingest stores only the fields the client sent plus a server-side `received_at`, and for the heartbeat only the install ID and version out of whatever was sent. **No IP address, User-Agent, or geolocation is read into a row.** The connecting address is used for one thing, the rate-limit decision, and is never persisted.

## Layout

| Path | Purpose |
| --- | --- |
| `src/index.ts` | Request routing, rate limit, D1 upsert |
| `src/payload.ts` | Validation and normalisation for both routes. Pure, no Worker APIs, fully unit tested |
| `schema.sql` | D1 schema |
| `wrangler.jsonc` | Bindings, custom domain, rate limit |

`payload.ts` is deliberately free of Worker runtime APIs so the whole decision surface runs under plain `vitest` on Node, as it does in CI. Only the thin glue in `index.ts` needs a real Worker.

## Design notes

**The primary key is the dedupe strategy.** `PRIMARY KEY (install_id, day)` with an upsert means a repeated ping rewrites its own row rather than appending. That is simultaneously the deduplication, the cost control (D1 bills by rows), and the reason a network retry cannot inflate the install count. Last write wins for a given day.

It is also what makes the client's six-hour cadence free of consequence here. The client sends four times a day because its scheduler re-anchors on every worker restart and a daily interval could therefore skip a calendar day outright; the fourfold sending arrives as the same single row, four times overwritten. Storage and the install count are unchanged. Only the request and write budgets move, and the table below already accounts for that.

**The heartbeat keeps two values and discards the rest.** It is the part of Nojoin's telemetry an operator cannot switch off in the app, so `docs/TELEMETRY.md` promises it stores the install ID and the version and nothing else. `parseHeartbeat` enforces that here: unknown fields do not cause a rejection, but there is no raw payload column for them to land in. Widening the heartbeat therefore needs a visible change to this file.

**Unknown diagnostics fields are never rejected.** A newer Nojoin will always ship fields before this Worker is redeployed. Rejecting them would turn a routine client release into an outage of our own telemetry, so unknown fields are stored verbatim in the `payload` JSON column and can be queried retroactively with `json_extract`. The same applies to unknown *enum values*: the typed column gets `NULL`, the raw payload keeps the real value.

**Only two things cause a rejection,** on either route: an unrecognised `schema` version, and a malformed `install_id`. The first means we would be guessing at the meaning of every field; the second is the primary key, which must be trustworthy for dedupe to work at all.

**The response carries no body.** The ingest is one-way by design. If it ever told an install something useful — a latest version, say — operators would have a reason to keep telemetry on that has nothing to do with consent.

## Local development

Requires Node 20 or newer for tests, and Node 22 or newer for `wrangler`.

```bash
cd telemetry
npm install
npm run typecheck
npm test
```

To run the Worker locally against a local D1:

```bash
npx wrangler dev
npx wrangler d1 execute nojoin-telemetry --local --file=./schema.sql
```

## Deployment

The normal path is wrangler:

```bash
cd telemetry
npx wrangler deploy
```

Provisioning steps, needed only once and already done for the live deployment:

```bash
npx wrangler d1 create nojoin-telemetry
npx wrangler d1 execute nojoin-telemetry --remote --file=./schema.sql
```

The `database_id` in `wrangler.jsonc` must match the created database. The custom domain and the trigger list, which is empty, are both declared in `wrangler.jsonc`, so `deploy` reconciles them. `schema.sql` is idempotent, so applying it again after a schema change only creates what is missing.

## Querying

Daily active installs over the last 30 days:

```sql
SELECT day, COUNT(*) AS installs
  FROM pings
 WHERE day >= date('now', '-30 days')
 GROUP BY day
 ORDER BY day;
```

Version spread today, which is how update cadence is measured — there is no separate "did they update" field, it falls out of the daily version rows:

```sql
SELECT version, COUNT(*) AS installs
  FROM pings
 WHERE day = date('now')
 GROUP BY version
 ORDER BY installs DESC;
```

LLM provider popularity, excluding local development stacks:

```sql
SELECT llm_provider, COUNT(*) AS installs
  FROM pings
 WHERE day = date('now')
   AND local_origin = 0
 GROUP BY llm_provider
 ORDER BY installs DESC;
```

Feature adoption:

```sql
SELECT SUM(calendar_connected) AS calendar,
       SUM(mcp_in_use)         AS mcp,
       SUM(chat_used_28d)      AS chat,
       SUM(tasks_used)         AS tasks,
       COUNT(*)                AS installs
  FROM pings
 WHERE day = date('now');
```

A field a newer client sends that this Worker predates:

```sql
SELECT json_extract(payload, '$.some_new_field') AS value, COUNT(*)
  FROM pings
 WHERE day = date('now')
 GROUP BY value;
```

Installs seen, and live installs, from the heartbeat. Before 2.7.0 only the diagnostics existed, so a complete count unions both tables:

```sql
SELECT COUNT(DISTINCT install_id) AS installs_ever,
       COUNT(DISTINCT CASE WHEN day >= date('now', '-7 days') THEN install_id END) AS live_7d
  FROM (SELECT install_id, day FROM heartbeats
        UNION SELECT install_id, day FROM pings);
```

When each install first reported each version, which is how upgrade lag is measured:

```sql
SELECT version, install_id, MIN(day) AS first_seen
  FROM heartbeats
 GROUP BY version, install_id
 ORDER BY first_seen;
```

## Retention

Every row is kept indefinitely, so installs and their use can be followed over several years. There is no scheduled handler: the roll-up that used to reduce rows older than 13 months to daily totals was removed before any row reached that age, so nothing was ever deleted under it. `"crons": []` in `wrangler.jsonc` is explicit so a deploy removes the old trigger.

The ceiling this implies is D1's per-database size limit: 500 MB on the Workers Free plan and 10 GB on Workers Paid. A diagnostics row is roughly 1 KB with its indexes, the raw payload being about half of it, and a heartbeat row under 200 bytes, so one install that sends both costs about half a megabyte a year. That is around 20 years at 50 installs and about five at 200 on the free plan. The dashboard shows the current size against the limit. Moving to Workers Paid raises the limit twentyfold; deleting rows is not the plan.

## Free-tier headroom

An install that sends both writes one heartbeat and four diagnostics pings a day. The binding constraints are D1 writes and, over the long run, the storage ceiling described under Retention.

| Limit | Free tier | Meaning here |
| --- | --- | --- |
| Workers requests | 100,000 / day | ~20,000 installs at five requests each |
| D1 rows written | 100,000 / day | ~10,000 installs (each write also updates the `day` index, so ten rows per install per day) |
| D1 rows read | 5,000,000 / day | Well clear at this scale |
| D1 database size | 500 MB | See Retention. This is the limit that arrives first |

Since 1 September 2026 a free-plan account that exceeds the daily read or write limit gets errors until midnight UTC, so pings are dropped rather than billed. If the fleet ever approaches five figures, the cheap move is to lengthen the diagnostics interval rather than to change anything here.
