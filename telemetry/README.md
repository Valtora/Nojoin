# Nojoin Telemetry Ingest

The Cloudflare Worker that receives Nojoin's anonymous telemetry: the daily install heartbeat and the optional usage diagnostics. It is public so the disclosure in [docs/TELEMETRY.md](../docs/TELEMETRY.md) can be checked against the code that stores the data.

## What it does

| | |
| --- | --- |
| Endpoints | `POST https://telemetry.nojoin.co.uk/v1/heartbeat` and `POST https://telemetry.nojoin.co.uk/v1/ping` |
| Response | `204` with no body. Failures return `400`, `404`, `405`, `413`, `429` or `500`, all with empty bodies |
| Cadence | Heartbeat: on first start, on each new version, and once per UTC day. Diagnostics: one ping every six hours, while switched on |
| Storage | Cloudflare D1, one row per install per UTC day in each of `heartbeats` and `pings` |
| Retention | Indefinite. Nothing is rolled up or deleted |
| Limits | 20 requests a minute per connecting address (`PING_LIMITER`). Bodies up to 1,024 bytes for a heartbeat and 8,192 bytes for a ping |

The ingest stores what the client sent plus a server-side `day` and `received_at`, and for the heartbeat only the install ID and version. **No IP address, User-Agent, or geolocation is stored.** The connecting address is used only for the rate-limit decision.

## Layout

| Path | Purpose |
| --- | --- |
| `src/index.ts` | Routing, rate limit, size cap, D1 upsert |
| `src/payload.ts` | Validation and normalisation for both routes. It uses no Worker runtime APIs, so it is unit tested on plain Node |
| `test/payload.test.ts` | The `vitest` suite, run in CI |
| `schema.sql` | D1 schema |
| `wrangler.jsonc` | Bindings, custom domain, rate limit, empty cron list |

## Design notes

**The primary key is the dedupe strategy.** Both tables use `PRIMARY KEY (install_id, day)` with an upsert, so a repeated request rewrites its own row. That gives deduplication, cost control (D1 bills by rows), and an install count no retry can inflate. The last write of the day wins, so the client's four diagnostics pings a day still leave one row.

**The heartbeat keeps two values and discards the rest.** It is the telemetry an operator cannot switch off in the app, and `docs/TELEMETRY.md` promises it stores the install ID and the version and nothing else. `parseHeartbeat` enforces that: unknown fields do not cause a rejection, but there is no column for them. Widening the heartbeat needs a visible change to this Worker.

**Unknown diagnostics fields are kept.** A newer Nojoin ships fields before this Worker is redeployed, so unknown fields are stored verbatim in the `payload` JSON column and can be queried with `json_extract`. An unknown enum value is stored as `NULL` in its typed column, with the real value in `payload`. `recordings_24h` has no typed column and lives only in `payload`.

**Rejections are rare.** Besides a body that is not a JSON object or is too large, either route rejects only an unrecognised `schema` version or an `install_id` that is not a UUID. The install ID is the primary key, so it has to be trustworthy.

**The response carries no body.** The ingest is one-way. If it ever told an install something useful, such as the latest version, operators would have a reason to keep telemetry on that has nothing to do with consent. Errors go to the Workers log, not the response.

## Local development

Requires Node 22.12 or newer, for both `vitest` and `wrangler`. CI uses Node 26.

```bash
cd telemetry
npm install
npm run typecheck
npm test
```

To run the Worker against a local D1:

```bash
npx wrangler d1 execute nojoin-telemetry --local --file=./schema.sql
npx wrangler dev
```

`wrangler` is not a dependency of this package, so `npx` downloads it.

## Deployment

```bash
cd telemetry
npx wrangler deploy
```

One-time provisioning, already done for the live deployment:

```bash
npx wrangler d1 create nojoin-telemetry
npx wrangler d1 execute nojoin-telemetry --remote --file=./schema.sql
```

The `database_id` in `wrangler.jsonc` must match the created database. The custom domain and the empty cron list are declared in `wrangler.jsonc`, so `deploy` reconciles both. `schema.sql` is idempotent: running it again creates any missing table or index, but it does not add a column to an existing table, which needs its own `ALTER TABLE`.

## Querying

Run any of these against the live database:

```bash
npx wrangler d1 execute nojoin-telemetry --remote --command "SELECT ..."
```

Daily active installs over the last 30 days:

```sql
SELECT day, COUNT(*) AS installs
  FROM pings
 WHERE day >= date('now', '-30 days')
 GROUP BY day
 ORDER BY day;
```

Version spread today. There is no "did they update" field; update cadence comes from these daily version rows:

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

A field with no typed column, such as `recordings_24h` or one a newer client sends:

```sql
SELECT json_extract(payload, '$.recordings_24h') AS value, COUNT(*)
  FROM pings
 WHERE day = date('now')
 GROUP BY value;
```

Installs ever seen, and installs live in the last seven days. Before 2.7.0 only the diagnostics existed, so a complete count unions both tables:

```sql
SELECT COUNT(DISTINCT install_id) AS installs_ever,
       COUNT(DISTINCT CASE WHEN day >= date('now', '-7 days') THEN install_id END) AS live_7d
  FROM (SELECT install_id, day FROM heartbeats
        UNION SELECT install_id, day FROM pings);
```

When each install first reported each version, which measures upgrade lag:

```sql
SELECT version, install_id, MIN(day) AS first_seen
  FROM heartbeats
 GROUP BY version, install_id
 ORDER BY first_seen;
```

## Retention

Every row is kept indefinitely, and there is no scheduled handler. Keep `"crons": []` in `wrangler.jsonc`: it is explicit so a deploy removes the 03:00 trigger of the retired 13-month roll-up, which never deleted anything.

The ceiling this implies is D1's per-database size limit: 500 MB on the Workers Free plan and 10 GB on Workers Paid. A diagnostics row is roughly 1 KB with its index, half of it the raw payload, and a heartbeat row is under 200 bytes, so an install that sends both costs about half a megabyte a year. That is around 20 years at 50 installs, or five at 200, on the free plan. The dashboard shows the current size against the limit. Moving to Workers Paid raises the limit twentyfold; deleting rows is not the plan.

## Free-tier headroom

An install that sends both writes one heartbeat and four diagnostics pings a day. The binding constraints are D1 writes and, in the long run, the storage ceiling above.

| Limit | Free tier | Meaning here |
| --- | --- | --- |
| Workers requests | 100,000 / day | ~20,000 installs at five requests each |
| D1 rows written | 100,000 / day | ~10,000 installs (each write also updates the `day` index, so ten rows per install per day) |
| D1 rows read | 5,000,000 / day | Well clear at this scale |
| D1 database size | 500 MB | See Retention. This is the limit that arrives first |

Since 1 September 2026, a free-plan account that exceeds the daily D1 read or write limit gets errors until midnight UTC, so pings are dropped rather than billed. If the fleet approaches five figures, lengthen the client's diagnostics interval rather than change anything here.
