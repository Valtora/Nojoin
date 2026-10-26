import { readdirSync, readFileSync } from "node:fs";
import { join } from "node:path";
import ts from "typescript";
import { describe, expect, it } from "vitest";

import { fillMarkerViolations } from "@/test/fillMarkerContracts";

const baseOwners: Record<string, string> = {
  SurfaceBase: "surface", ButtonBase: "button", InputBase: "input",
  SelectBase: "select", TextareaBase: "textarea",
};

function literalValue(attribute: ts.JsxAttribute): string | undefined {
  const value = attribute.initializer;
  if (value && ts.isStringLiteral(value)) return value.text;
  if (value && ts.isJsxExpression(value) && value.expression
    && (ts.isStringLiteral(value.expression) || ts.isNoSubstitutionTemplateLiteral(value.expression))) {
    return value.expression.text;
  }
  return undefined;
}

/** Audit literal host recipes; dynamic recipes require the rendered branch contracts listed below. */
function sourceViolations(source: string, fileName: string): { errors: string[]; checked: number; dynamicMarkers: number } {
  const tree = ts.createSourceFile(fileName, source, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
  const errors: string[] = [];
  let checked = 0;
  let dynamicMarkers = 0;
  const visit = (node: ts.Node) => {
    if (ts.isJsxOpeningElement(node) || ts.isJsxSelfClosingElement(node)) {
      const attributes = node.attributes.properties.filter(ts.isJsxAttribute);
      const className = attributes.find((attribute) => attribute.name.getText(tree) === "className");
      const marker = attributes.find((attribute) => attribute.name.getText(tree) === "data-ui-fill");
      const classes = className ? literalValue(className) : "";
      const fill = marker && literalValue(marker);
      if (marker && fill === undefined) dynamicMarkers += 1;
      // Expressions and inherited component recipes need the rendered contract, not a partial static guess.
      if (classes !== undefined && (!marker || fill !== undefined)) {
        const host = document.createElement("div");
        for (const attribute of attributes) {
          const name = attribute.name.getText(tree);
          const value = literalValue(attribute);
          if ((name.startsWith("data-ui") || name === "type") && value !== undefined) host.setAttribute(name, value);
          if (name === "data-ui-tooltip") host.setAttribute(name, "");
        }
        const owner = baseOwners[node.tagName.getText(tree)];
        if (owner) host.setAttribute("data-ui", owner);
        host.className = classes;
        const line = tree.getLineAndCharacterOfPosition(node.getStart(tree)).line + 1;
        errors.push(...fillMarkerViolations(host).map((error) => `${fileName}:${line}: ${error}`));
        checked += 1;
      }
    }
    ts.forEachChild(node, visit);
  };
  visit(tree);
  return { errors, checked, dynamicMarkers };
}

function productionSources(directory: string): string[] {
  return readdirSync(directory, { withFileTypes: true }).flatMap((entry) => {
    const path = join(directory, entry.name);
    return entry.isDirectory() ? productionSources(path)
      : path.endsWith(".tsx") && !path.endsWith(".test.tsx") ? [path] : [];
  });
}

describe("literal production fill recipes", () => {
  it("checks the real component and app trees in both directions", () => {
    const results = ["src/components", "src/app"].flatMap(productionSources)
      .map((path) => sourceViolations(readFileSync(path, "utf8"), path));
    expect(results.flatMap(({ errors }) => errors)).toEqual([]);
    expect(results.reduce((total, { checked }) => total + checked, 0)).toBeGreaterThan(300);
  });

  it("requires new dynamic recipes to join the rendered branch contracts", () => {
    const actual = Object.fromEntries(["src/components", "src/app"].flatMap(productionSources)
      .map((path) => [path.replace(/^src\//, ""), sourceViolations(readFileSync(path, "utf8"), path).dynamicMarkers])
      .filter(([, count]) => count !== 0));
    // This inventory is a review gate, not proof of rendering: each entry must have
    // every reachable branch checked against its real classes in the named suite.
    expect(actual).toEqual({
      "components/ChatPanel.tsx": 1, // ChatPanel.test: loaded user and assistant history
      "components/MonthCalendar.tsx": 1, // MonthCalendar.test: select, move selection
      "components/Sidebar.tsx": 1, // Sidebar.test: ordinary, active, batch-selected
      "components/TableMenu.tsx": 2, // TableMenu.test: the toggle closed and open; hover and leave the size grid
      "components/people/PeopleTable.tsx": 1, // PeopleTable.test: select and deselect
      "components/people/RecalibrateModal.tsx": 3, // RecalibrateModal.test: play, reject, approve, clear
      "components/people/SplitPersonModal.tsx": 2, // SplitPersonModal.test: select, play, stop, deselect
      "components/settings/NotesTemplatesSection.tsx": 1, // NotesTemplatesSection.test: loaded selection
      "components/settings/SettingsBlock.tsx": 1, // fillMarkerContracts.test: inset false/true
      "components/settings/SettingsCallout.tsx": 1, // fillMarkerContracts.test: all five tones
      "components/transcript/SearchSettingsMenu.tsx": 1, // transcriptControls.test: closed/open/closed
      "components/transcript/TranscriptGroup.tsx": 1, // playbackFollow.test: inactive/active/inactive
      "components/transcript/TranscriptToolbar.tsx": 2, // transcriptControls.test: search and replace toggles
      "components/ui/Button.tsx": 1, // fillMarkerContracts.test: all four variants
      "components/ui/IconButton.tsx": 1, // fillMarkerContracts.test: all four variants
      "components/ui/Switch.tsx": 1, // fillMarkerContracts.test: checked false/true
      "components/ui/TaskDeadlineModal.tsx": 2, // TaskDeadlineModal.test: day and time preset clicks
      // calendarModules.test: today, selected day, ordinary day. The outer
      // inCurrentMonth guard renders adjacent-month cells as unmarked divs,
      // so its nested false arms cannot reach this ButtonBase marker.
      "components/upcomingMeetings/MonthGridCard.tsx": 1,
      "app/(dashboard)/recordings/[id]/_components/RecordingHeader.tsx": 1, // page.test: actions closed/open
    });
  });

  it.each([
    '<SurfaceBase data-ui-fill="card" className="bg-surface-inset" />',
    '<SurfaceBase data-ui-fill="card" className="" />',
    '<SurfaceBase data-ui-fill="card" />',
    '<SurfaceBase className="bg-surface-card" />',
    '<aside data-ui-reading="pane" className="bg-surface-inset" />',
  ])("rejects a broken source fixture: %s", (source) => {
    expect(sourceViolations(source, "broken.tsx").errors).toHaveLength(1);
    expect(sourceViolations(source, "broken.tsx").errors[0]).toContain("broken.tsx:1:");
  });
});
