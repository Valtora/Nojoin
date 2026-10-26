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

/** Audit literal host recipes; representative dynamic recipes are checked in the rendered suites. */
function sourceViolations(source: string, fileName: string): { errors: string[]; checked: number } {
  const tree = ts.createSourceFile(fileName, source, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
  const errors: string[] = [];
  let checked = 0;
  const visit = (node: ts.Node) => {
    if (ts.isJsxOpeningElement(node) || ts.isJsxSelfClosingElement(node)) {
      const attributes = node.attributes.properties.filter(ts.isJsxAttribute);
      const className = attributes.find((attribute) => attribute.name.getText(tree) === "className");
      const marker = attributes.find((attribute) => attribute.name.getText(tree) === "data-ui-fill");
      const classes = className ? literalValue(className) : "";
      const fill = marker && literalValue(marker);
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
  return { errors, checked };
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
