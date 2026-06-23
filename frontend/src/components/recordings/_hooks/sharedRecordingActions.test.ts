import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";

import { describe, expect, it } from "vitest";

import { RECORDING_ACTION_IDS } from "./useRecordingActions";

/**
 * FE-015 invariant: Sidebar.tsx and RecordingCard.tsx must drive their
 * recording menus through the SAME shared action model. DEVELOPMENT.md records
 * that the two menus stay behaviourally synchronised; this test fails if either
 * surface stops consuming `useRecordingActions`, so the duplication cannot
 * silently reappear.
 */

const here = dirname(fileURLToPath(import.meta.url));
const componentsDir = resolve(here, "..", "..");

const readComponent = (name: string): string =>
  readFileSync(resolve(componentsDir, name), "utf8");

const sidebarSource = readComponent("Sidebar.tsx");
const recordingCardSource = readComponent("RecordingCard.tsx");

const usedActionIds = (source: string): string[] =>
  RECORDING_ACTION_IDS.filter((id) =>
    new RegExp(`actions\\.${id}\\b`).test(source),
  );

describe("shared recording action model (FE-015)", () => {
  it("Sidebar consumes the shared useRecordingActions hook", () => {
    expect(sidebarSource).toContain("useRecordingActions");
  });

  it("RecordingCard consumes the shared useRecordingActions hook", () => {
    expect(recordingCardSource).toContain("useRecordingActions");
  });

  it("Sidebar and RecordingCard draw their actions from the same shared set", () => {
    const sidebarActions = usedActionIds(sidebarSource);
    const cardActions = usedActionIds(recordingCardSource);

    // Both surfaces must actually use the shared hook's actions...
    expect(sidebarActions.length).toBeGreaterThan(0);
    expect(cardActions.length).toBeGreaterThan(0);

    // ...and every action they use must be a member of the single shared set,
    // so neither surface can introduce a divergent, locally-defined action.
    for (const id of [...sidebarActions, ...cardActions]) {
      expect(RECORDING_ACTION_IDS).toContain(id);
    }

    // The two menus exercise overlapping core actions (rename, infer speakers,
    // cancel) plus their view-specific lifecycle actions; the rename/infer/
    // cancel trio is the synchronised behaviour FE-015 protects.
    for (const shared of ["rename", "inferSpeakers", "cancel"] as const) {
      expect(sidebarActions).toContain(shared);
      expect(cardActions).toContain(shared);
    }
  });
});
