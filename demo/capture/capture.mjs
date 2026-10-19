// Captures the API responses the public demo replays. See demo/README.md.
//
// Signs in to a seeded Nojoin instance, walks the app the way a visitor would,
// and saves every GET answered under /api/v1 to FIXTURES_DIR: an index.json of
// (path, query) pairs, and one body file per distinct response. A recording's
// audio is fetched whole afterwards, because the player only ever asks for
// ranges of it.
//
//   SEED_URL=https://localhost:16443 SEED_PASSWORD=... node capture.mjs

import { createHash } from "node:crypto";
import { mkdirSync, rmSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { chromium } from "playwright";

const BASE = process.env.SEED_URL ?? "https://localhost:16443";
const USERNAME = process.env.SEED_USERNAME ?? "demo";
const PASSWORD = process.env.SEED_PASSWORD;
const OUT = process.env.FIXTURES_DIR ?? "fixtures";
const SETTLE_MS = Number(process.env.SETTLE_MS ?? 2500);

// Admin pages are left out: the demo account is not an administrator, so the
// demo frontend never asks for what they show.
const PAGES = [
  "/",
  "/tasks",
  "/people",
  "/recordings",
  "/settings",
  ...["profile", "appearance", "capture", "recording", "transcription", "notes", "your-ai", "integrations", "privacy", "help"].map(
    (name) => `/settings/${name}`,
  ),
];
const RECORDING_TABS = ["#tab-notes", "#tab-documents", "#tab-analytics", "#tab-transcript"];

if (!PASSWORD) {
  console.error("Set SEED_PASSWORD to the seeded account's password.");
  process.exit(1);
}

// Clear the previous capture's files rather than the directory, which may be a
// mount point.
rmSync(join(OUT, "bodies"), { recursive: true, force: true });
rmSync(join(OUT, "index.json"), { force: true });
mkdirSync(join(OUT, "bodies"), { recursive: true });

const entries = new Map();

function save(path, query, status, contentType, disposition, buffer) {
  const normalisedPath = path.replace(/\/+$/, "") || "/";
  const extension = contentType.startsWith("application/json") ? "json" : "bin";
  const digest = createHash("sha256").update(buffer).digest("hex").slice(0, 20);
  const body = `bodies/${digest}.${extension}`;
  writeFileSync(join(OUT, body), buffer);
  entries.set(`${normalisedPath}?${query}`, {
    path: normalisedPath,
    query,
    status,
    content_type: contentType,
    ...(disposition ? { content_disposition: disposition } : {}),
    body,
  });
}

const browser = await chromium.launch();
const context = await browser.newContext({
  baseURL: BASE,
  ignoreHTTPSErrors: true,
  viewport: { width: 1600, height: 1000 },
});
const page = await context.newPage();

page.on("response", async (response) => {
  const request = response.request();
  const url = new URL(response.url());
  if (request.method() !== "GET" || !url.pathname.startsWith("/api/v1/")) return;
  // Partial audio is fetched whole at the end; a 206 here is one slice of it.
  if (response.status() === 206 || url.pathname.endsWith("/stream")) return;
  try {
    const headers = response.headers();
    save(
      url.pathname,
      url.search.replace(/^\?/, ""),
      response.status(),
      headers["content-type"] ?? "application/json",
      headers["content-disposition"],
      await response.body(),
    );
  } catch {
    // A response whose page navigated away has no body; the next visit refetches it.
  }
});

async function visit(path) {
  await page.goto(path, { waitUntil: "load" });
  await page.waitForTimeout(SETTLE_MS);
}

await page.goto("/login");
await page.fill("#login-username", USERNAME);
await page.fill("#login-current-password", PASSWORD);
await Promise.all([page.waitForURL((url) => !url.pathname.startsWith("/login")), page.click("button[type=submit]")]);
await page.waitForTimeout(SETTLE_MS);

for (const path of PAGES) {
  await visit(path);
}

const api = context.request;
const list = await (await api.get("/api/v1/recordings")).json();
const recordings = Array.isArray(list) ? list : (list.items ?? []);

for (const recording of recordings) {
  await visit(`/recordings/${recording.id}`);
  for (const tab of RECORDING_TABS) {
    if (await page.locator(tab).count()) {
      // Clicked through the DOM, because the first-visit tour's overlay
      // covers the page and swallows a pointer click.
      await page.locator(tab).evaluate((element) => element.click());
      await page.waitForTimeout(SETTLE_MS);
    }
  }
}

// Views a visitor reaches through filters, requested directly so a filtered
// list never falls back to the unfiltered one.
const tags = await (await api.get("/api/v1/tags/")).json().catch(() => []);
const filtered = [
  "only_archived=true",
  "only_deleted=true",
  ...(Array.isArray(tags) ? tags.map((tag) => `tag_ids=${tag.id}`) : []),
];
for (const query of filtered) {
  const response = await api.get(`/api/v1/recordings?${query}`);
  save("/api/v1/recordings", query, response.status(), response.headers()["content-type"], undefined, await response.body());
}

const peopleTags = await (await api.get("/api/v1/people-tags/")).json().catch(() => []);
for (const tag of Array.isArray(peopleTags) ? peopleTags : []) {
  const query = `tags=${tag.id}`;
  const response = await api.get(`/api/v1/speakers/?${query}`);
  save("/api/v1/speakers", query, response.status(), response.headers()["content-type"], undefined, await response.body());
}

for (const recording of recordings) {
  const path = `/api/v1/recordings/${recording.id}/stream`;
  const response = await api.get(path);
  if (response.ok()) {
    save(path, "", 200, response.headers()["content-type"] ?? "audio/mp4", undefined, await response.body());
  }
}

await browser.close();

// The seeded account owns the library, so it is the instance's administrator.
// The demo account is not, and the demo frontend must not offer admin pages.
const me = entries.get("/api/v1/users/me?");
if (me) {
  const { readFileSync } = await import("node:fs");
  const user = JSON.parse(readFileSync(join(OUT, me.body), "utf8"));
  user.is_superuser = false;
  const buffer = Buffer.from(JSON.stringify(user));
  save(me.path, me.query, me.status, me.content_type, undefined, buffer);
}

const sorted = [...entries.values()].sort((a, b) => `${a.path}?${a.query}`.localeCompare(`${b.path}?${b.query}`));
writeFileSync(
  join(OUT, "index.json"),
  `${JSON.stringify({ captured_on: new Date().toISOString().slice(0, 10), entries: sorted }, null, 2)}\n`,
);
console.log(`Captured ${sorted.length} responses from ${recordings.length} recordings.`);
