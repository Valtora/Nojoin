// Renders site/public/images/og-card.png, the one Open Graph card every page
// shares. It exists because the card was hand-made and then drifted: the
// headline changed twice while the card kept the original, so every share of
// the new site advertised a tagline the site no longer used.
//
// Run it whenever the hero headline changes:
//
//   node site/scripts/build-og-card.mjs
//
// Playwright is deliberately NOT a dependency of this repository -- the same
// decision the screenshot pipeline made, for a job that runs about once a year.
// Point PLAYWRIGHT_CHROMIUM at a Chromium that a Playwright install already
// manages, or install playwright-core somewhere outside the repo and pass its
// executable path.
//
// Colours are the app's dark-theme tokens, quoted rather than imported because
// this renders a bitmap outside the stylesheet's reach. If tokens.css moves,
// this must follow -- there is no gate that will catch it.

import { chromium } from "playwright-core";
import { readFileSync, writeFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";

const here = dirname(fileURLToPath(import.meta.url));
const site = resolve(here, "..");
const out = resolve(site, "public/images/og-card.png");

const EXECUTABLE =
  process.env.PLAYWRIGHT_CHROMIUM ||
  `${process.env.HOME}/.cache/ms-playwright/chromium-1234/chrome-linux64/chrome`;

// Dark theme, from frontend/src/app/tokens.css.
const SURFACE_PAGE = "#161616";
const FOREGROUND = "#ededed";
const CONTRAST_HELPER = "#d1d5db";
const ACTION_TEXT = "#fb923c";

const HEADLINE = "Transcription is easy. Agentic meeting intelligence isn't.";
const SUBTITLE = "No bot in the call. Nothing off your server. Nothing capped.";

const geist = readFileSync(resolve(site, "public/fonts/geist-latin.woff2")).toString("base64");

const MARK = `<svg viewBox="0 0 128 128" xmlns="http://www.w3.org/2000/svg" width="64" height="64">
  <defs><linearGradient id="g" x1="28" y1="14" x2="101" y2="116" gradientUnits="userSpaceOnUse">
    <stop offset="0" stop-color="#ffb31a"/><stop offset="0.52" stop-color="#ff6a13"/><stop offset="1" stop-color="#ff3e2f"/>
  </linearGradient></defs>
  <path d="M64 16c-11.6 0-21 9.4-21 21v23c0 11.6 9.4 21 21 21s21-9.4 21-21V37c0-11.6-9.4-21-21-21Z" fill="url(#g)"/>
  <path d="M41 58v9c0 12.7 10.3 23 23 23s23-10.3 23-23v-9" fill="none" stroke="url(#g)" stroke-width="8" stroke-linecap="round"/>
  <path d="M64 90v16" fill="none" stroke="url(#g)" stroke-width="8" stroke-linecap="round"/>
  <path d="M46 109h36" fill="none" stroke="url(#g)" stroke-width="8" stroke-linecap="round"/>
  <path d="M55 38v22M64 30v38M73 40v20" fill="none" stroke="#070b16" stroke-width="5.5" stroke-linecap="round"/>
  <path d="M33 48v13M95 48v13" fill="none" stroke="url(#g)" stroke-width="7" stroke-linecap="round"/>
</svg>`;

const html = `<!doctype html><html><head><meta charset="utf-8"><style>
@font-face {
  font-family: "Geist";
  font-weight: 100 900;
  src: url(data:font/woff2;base64,${geist}) format("woff2");
}
* { margin: 0; padding: 0; box-sizing: border-box; }
body {
  width: 1200px; height: 630px;
  background: ${SURFACE_PAGE};
  font-family: "Geist", sans-serif;
  display: flex; flex-direction: column; justify-content: center;
  padding: 0 90px;
  /* The hero's wash, so the card and the page it links to look related. */
  background-image: radial-gradient(60rem 34rem at 82% -12%, rgba(249,115,22,0.16), transparent 66%);
}
.brand { display: flex; align-items: center; gap: 14px; margin-bottom: 46px; }
.brand span { color: ${ACTION_TEXT}; font-size: 34px; font-weight: 600; letter-spacing: -0.01em; }
h1 {
  color: ${FOREGROUND};
  font-size: 68px; font-weight: 600; line-height: 1.08; letter-spacing: -0.025em;
  max-width: 15ch;
}
p { color: ${CONTRAST_HELPER}; font-size: 27px; margin-top: 34px; line-height: 1.45; }
</style></head><body>
  <div class="brand">${MARK}<span>Nojoin</span></div>
  <h1>${HEADLINE}</h1>
  <p>${SUBTITLE}</p>
</body></html>`;

const browser = await chromium.launch({ executablePath: EXECUTABLE });
const page = await browser.newPage({ viewport: { width: 1200, height: 630 }, deviceScaleFactor: 1 });
await page.setContent(html, { waitUntil: "networkidle" });
await page.evaluate(() => document.fonts.ready);
const png = await page.screenshot({ type: "png" });
writeFileSync(out, png);
await browser.close();

console.log(`wrote ${out} (${png.length} bytes)`);
console.log(`headline: ${HEADLINE}`);
console.log("Remember: og:image:alt in site/src/layouts/Base.astro must match.");
