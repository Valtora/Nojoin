/** Compile the canonical style sources without adding Tailwind base/utilities. */
import { watch } from "node:fs";
import { mkdir, rename, writeFile } from "node:fs/promises";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

import tailwindcss from "@tailwindcss/postcss";
import postcss from "postcss";

const sourceDirectory = fileURLToPath(new URL("../src/app/", import.meta.url));
const output = fileURLToPath(new URL("../public/styles/optional.css", import.meta.url));
const sources = ["styles.css", "depth-dark.css", "depth-gradient.css", "classic.css"];
const entry = sources.map((source) => `@import "./${source}";`).join("\n");
let watcher;

/** Awaited by Next's configuration hook, before either build or dev can serve. */
export async function buildOptionalStyles({ optimize }) {
  const result = await postcss([tailwindcss({ optimize })]).process(entry, {
    from: join(sourceDirectory, "optional.css"),
    to: output,
    map: false,
  });
  await mkdir(dirname(output), { recursive: true });
  const temporary = `${output}.${process.pid}.tmp`;
  await writeFile(temporary, result.css);
  await rename(temporary, output);
}

/** Watch in-process: no child server or watcher survives the Next process. */
export function watchOptionalStyles() {
  if (watcher) return;
  let debounce;
  let pending = Promise.resolve();
  watcher = watch(sourceDirectory, { persistent: false }, (_event, filename) => {
    if (!sources.includes(String(filename))) return;
    clearTimeout(debounce);
    debounce = setTimeout(() => {
      pending = pending.then(() => buildOptionalStyles({ optimize: false })).catch((error) => {
        console.error("Failed to regenerate optional styles:", error);
      });
    }, 50);
    debounce.unref();
  });
}
