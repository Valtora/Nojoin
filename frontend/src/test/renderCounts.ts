import { readFileSync } from "node:fs";
import { join } from "node:path";
import { Session } from "node:inspector/promises";

const SRC = join(import.meta.dirname, "..");

/**
 * Counts how often named functions ran, through V8's precise coverage, so a
 * test can count a memoised component's renders without wrapping or mocking
 * it (a wrapper outside `memo` counts its parent's renders instead). Each
 * `take()` returns a lookup of the counts since the previous one, and resets
 * them. Files are named from `src/`.
 */
export async function renderCounter() {
  const session = new Session();
  session.connect();
  await session.post("Profiler.enable");
  await session.post("Profiler.startPreciseCoverage", { callCount: true, detailed: false });
  return {
    async take() {
      const { result } = await session.post("Profiler.takePreciseCoverage");
      return (file: string, name: string): number => {
        // V8 leaves out functions that did not run, so a renamed file or
        // function would otherwise read as zero renders.
        if (!new RegExp(`function ${name}\\b`).test(readFileSync(join(SRC, file), "utf8"))) {
          throw new Error(`src/${file} declares no function ${name}`);
        }
        return result
          .filter((script) => script.url.includes(`/src/${file}`))
          .flatMap((script) => script.functions)
          .filter((fn) => fn.functionName === name)
          .reduce((total, fn) => total + fn.ranges[0].count, 0);
      };
    },
    async stop() {
      await session.post("Profiler.stopPreciseCoverage");
      session.disconnect();
    },
  };
}
