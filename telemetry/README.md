# Nojoin Telemetry Ingest

The Cloudflare Worker that receives Nojoin's anonymous, opt-out telemetry pings.

This code is public on purpose. The claim in [docs/TELEMETRY.md](../docs/TELEMETRY.md) about what Nojoin collects is only worth as much as your ability to check it, so both halves — the client that sends and the ingest that stores — are readable in the same repository.

## What it does

| | |
| --- | --- |
| Endpoint | `POST https://telemetry.nojoin.co.uk/v1/ping` |
| Response | `204` with no body. Anything else returns `400`, `404`, `405`, `413`, `429`, or `500`, all with empty bodies |
| Cadence | Four pings per install per day, one every six hours |
| Storage | Cloudflare D1, one row per install per UTC day |
| Retention | Raw rows for 13 months, then rolled up into daily aggregates and deleted |

Every request is anonymous. The ingest stores only the fields the client sent plus a server-side `received_at`. **No IP address, User-Agent, or geolocation is read into a row.** The connecting address is used for one thing — the rate-limit decision — and is never persisted.

## Layout

| Path | Purpose |
| --- | --- |
| `src/index.ts` | Request routing, rate limit, D1 upsert, cron entry point |
| `src/payload.ts` | Validation and normalisation. Pure, no Worker APIs, fully unit tested |
| `src/rollup.ts` | Retention sweep SQL. Pure, no Worker APIs, fully unit tested |
| `schema.sql` | D1 schema |
| `wrangler.jsonc` | Bindings, custom domain, cron trigger |

`payload.ts` and `rollup.ts` are deliberately free of Worker runtime APIs so the whole decision surface runs under plain `vitest` on Node 20, which is what CI has. Only the thin glue in `index.ts` needs a real Worker.

## Design notes

**The primary key is the dedupe strategy.** `PRIMARY KEY (install_id, day)` with an upsert means a repeated ping rewrites its own row rather than appending. That is simultaneously the deduplication, the cost control (D1 bills by rows), and the reason a network retry cannot inflate the install count. Last write wins for a given day.

It is also what makes the client's six-hour cadence free of consequence here. The client sends four times a day because its scheduler re-anchors on every worker restart and a daily interval could therefore skip a calendar day outright; the fourfold sending arrives as the same single row, four times overwritten. Storage and the install count are unchanged. Only the request and write budgets move, and the table below already accounts for that.

**Unknown fields are never rejected.** A newer Nojoin will always ship fields before this Worker is redeployed. Rejecting them would turn a routine client release into an outage of our own telemetry, so unknown fields are stored verbatim in the `payload` JSON column and can be queried retroactively with `json_extract`. The same applies to unknown *enum values*: the typed column gets `NULL`, the raw payload keeps the real value.

**Only two things cause a rejection:** an unrecognised `schema` version, and a malformed `install_id`. The first means we would be guessing at the meaning of every field; the second is the primary key, which must be trustworthy for dedupe to work at all.

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

The `database_id` in `wrangler.jsonc` must match the created database. The custom domain and the cron trigger are both declared in `wrangler.jsonc`, so `deploy` reconciles them.

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

After 13 months the raw rows are gone, and the same questions are answered from `daily_rollup` (totals) and `daily_rollup_dim` (breakdowns, as `dimension`/`value`/`installs`):

```sql
SELECT day, value AS version, installs
  FROM daily_rollup_dim
 WHERE dimension = 'version'
 ORDER BY day DESC;
```

## Free-tier headroom

At four pings per install per day, the binding constraint is D1 writes rather than the Workers request limit.

| Limit | Free tier | Meaning here |
| --- | --- | --- |
| Workers requests | 100,000 / day | ~25,000 installs |
| D1 rows written | 100,000 / day | ~12,500 installs (the `day` index doubles each write) |
| D1 rows read | 5,000,000 / day | Well clear at this scale |
| D1 storage | 5 GB total | Roughly 365,000 rows per year per 1,000 installs, unaffected by the cadence |

Storage is the only figure the six-hour cadence leaves alone, because the upsert overwrites rather than appends. The other two divide by four. If the fleet ever approaches five figures, the cheap move is to lengthen the client interval again rather than to change anything here.
