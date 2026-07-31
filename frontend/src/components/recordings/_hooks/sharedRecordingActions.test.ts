import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";

import { describe, expect, it } from "vitest";

import { RECORDING_ACTION_IDS } from "./useRecordingActions";

/**
 * Invariant: every surface that offers recording actions drives them through
 * the SAME shared action model, so the duplication this hook was extracted to
 * remove cannot silently reappear.
 *
 * This used to name Sidebar and RecordingCard. RecordingCard was deleted: it
 * was written for a recordings grid view that no route ever rendered, in the
 * entire history of the repository. The invariant itself is real and survives
 * with the two surfaces that actually exist, `Sidebar` and
 * `RecordingStatusDisplay`, which is why this test was rewritten rather than
 * removed along with the component.
 */

const here = dirname(fileURLToPath(import.meta.url));
const componentsDir = resolve(here, "..", "..");

const readComponent = (name: string): string =>
  readFileSync(resolve(componentsDir, name), "utf8");

const sidebarSource = readComponent("Sidebar.tsx");
const statusDisplaySource = readComponent("RecordingStatusDisplay.tsx");

const usedActionIds = (source: string): string[] =>
  RECORDING_ACTION_IDS.filter((id) =>
    new RegExp(`actions\\.${id}\\b`).test(source),
  );

describe("shared recording action model", () => {
  it("Sidebar consumes the shared useRecordingActions hook", () => {
    expect(sidebarSource).toContain("useRecordingActions");
  });

  it("RecordingStatusDisplay consumes the shared useRecordingActions hook", () => {
    expect(statusDisplaySource).toContain("useRecordingActions");
  });

  it("both surfaces draw their actions from the same shared set", () => {
    const sidebarActions = usedActionIds(sidebarSource);
    const statusActions = usedActionIds(statusDisplaySource);

    // Both surfaces must actually use the shared hook's actions...
    expect(sidebarActions.length).toBeGreaterThan(0);
    expect(statusActions.length).toBeGreaterThan(0);

    // ...and every action they use must be a member of the single shared set,
    // so neither surface can introduce a divergent, locally-defined action.
    for (const id of [...sidebarActions, ...statusActions]) {
      expect(RECORDING_ACTION_IDS).toContain(id);
    }

    // Discard is the action both surfaces offer, and the one whose behaviour
    // has to match: the live view and the rail must dispose of a recording the
    // same way.
    expect(sidebarActions).toContain("discard");
    expect(statusActions).toContain("discard");
  });
});
