# Public Demo

`demo.nojoin.co.uk` lets a visitor sign in with one click and look around a Nojoin library: the dashboard, transcripts with named speakers, meeting notes, analytics, people, tasks and the calendar. It is read-only. Nothing a visitor does is saved, recording and import are switched off, and nothing calls an AI provider.

It runs no Nojoin backend. The frontend is the normal one built in demo mode, and the API behind it replays responses captured from a seeded instance.

## How It Fits Together

- **Frontend in demo mode.** `NEXT_PUBLIC_DEMO_MODE=true` at build time (see `frontend/src/lib/demo.ts`) adds a read-only banner, replaces the sign-in form with an "Enter the demo" button, disables Start Meeting and Import Audio, and switches off account changes. The flag is inlined at build time, so a normal build compiles the demo branches away and no server response can turn a real install into a demo.
- **Fixture API** (`server/app.py`). Serves captured GET responses, matched on path and query, with the path alone as a fallback. Every write gets a 403 with a read-only message, except sign-in and sign-out, which set and clear a cookie so the frontend's session handling works as it does on a real install. A search the capture never ran is narrowed to the list items that mention the term. Dates in responses move forward by the whole weeks since the capture, so tasks, meetings and calendar events stay near the visitor's today and keep their weekdays. The process holds no database, credential or model.
- **nginx** (`nginx.conf`) sends `/api/` to the fixture API and everything else to the frontend, with a per-visitor rate limit. It is the only container on the tunnel's network.

## The Seeded Library

The fixtures come from a throwaway instance built from the "Localhost Dev Compose Template" in [docs/DEVELOPMENT.md](../docs/DEVELOPMENT.md), with telemetry and MCP off and no public route. Its content is public-domain material only:

- six NASA recordings from the NASA Image and Video Library, processed by the real pipeline on a GPU;
- speakers named as the people they are, and no invented names over real people's words;
- notes and the AI reading of each meeting generated once, at seed time, by the same model a production install would use;
- tags, tasks and calendar events written around those meetings.

Keep it that way. Nothing from a real meeting library goes into the demo.

## Capturing Fixtures

With the seed instance running, from this directory:

```bash
docker run --rm --network host --user "$(id -u):$(id -g)" -e HOME=/tmp \
  -v "$PWD/capture:/capture:ro" -v "$FIXTURES:/fixtures" \
  -e SEED_URL=https://localhost:16443 -e SEED_PASSWORD -e FIXTURES_DIR=/fixtures \
  mcr.microsoft.com/playwright:v1.62.1-noble \
  sh -c 'cd /tmp && npm i -s playwright@1.62.1 && cp /capture/capture.mjs . && node capture.mjs'
```

`capture/capture.mjs` signs in, visits every page and every tab of each recording, requests the archived, deleted and per-tag recording lists directly, fetches each recording's audio whole, and writes `index.json` plus one file per distinct response body. It marks the captured account as a non-administrator, because the demo account is one, and records the instance as healthy, because a refresh captures with the seed's workers stopped and the demo has no background work for a warning to describe.

The fixtures are a snapshot of the API at capture time. A release that changes a response the frontend reads breaks the matching panel until the capture is rerun. `refresh.sh` reruns it for every release.

## Refreshing on Each Release

`refresh.sh` keeps the demo on the latest release. It runs hourly from cron and does nothing unless GitHub reports a release newer than the one it last built. When one appears it:

1. pulls that release's API and frontend images and points the seed instance at them;
2. checks out the release tag in the demo's clone;
3. starts the seed instance without its workers, so a refresh never touches the GPU, and captures into a fresh directory;
4. swaps the new fixtures in only if the capture is complete, then rebuilds and recreates the demo from the tag;
5. checks the public demo answers, and if it does not, puts the previous fixtures back;
6. stops the seed instance, records the release and sends a notification either way.

```bash
41 * * * * DEMO_REFRESH_ENV=$HOME/nojoin-demo/refresh.env $HOME/nojoin-demo/repo/demo/refresh.sh >> $HOME/nojoin-demo/refresh.log 2>&1
```

The configuration file is the host's and holds the seed password, so it is not in this repository. `refresh.sh` lists the variables it needs at the top; `NOTIFY_URL` and `NOTIFY_TAG` point it at an Apprise endpoint. The seed instance's compose file takes its API and frontend images from `SEED_API_IMAGE` and `SEED_FRONTEND_IMAGE`, which the script sets in the seed's `.env`.

`refresh.sh --recapture` recaptures and rebuilds at the current checkout without upgrading anything, for after the seeded library itself changes. `--force` reruns the latest release.

## Running It

```bash
cp .env.example .env   # set DEMO_FIXTURES_DIR to the captured fixtures
docker compose up -d --build
```

The frontend image builds from `../frontend`, so run it from a clean checkout of `main`, not a working copy that changes branch.

## Tests

```bash
cd server
pip install -r requirements.txt pytest httpx2
pytest test_app.py
```
