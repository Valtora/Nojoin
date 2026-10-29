import { render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { MeetingEdgePayload } from "@/types";
import MeetingEdgePanel from "./MeetingEdgePanel";

const FIELDS = ["read", "question", "point", "term", "explanation"] as const;
type ConceptSource = "concepts" | "empty history" | "concept_history";
const CONCEPT_SOURCES: ConceptSource[] = ["concepts", "empty history", "concept_history"];

function formatted(field: string): string {
  return `**${field} bold** *${field} italic* \`${field} code\``;
}

function payloadFor(source: ConceptSource, text = formatted): MeetingEdgePayload {
  const concept = { term: text("term"), explanation: text("explanation") };
  return {
    summary: text("read"),
    questions: [text("question")],
    points: [text("point")],
    concepts: source === "concept_history" ? [{ term: "Old term", explanation: "Old explanation" }] : [concept],
    ...(source === "empty history" ? { concept_history: [] } : {}),
    ...(source === "concept_history" ? { concept_history: [concept] } : {}),
  };
}

describe("Meeting Edge model Markdown", () => {
  it.each(CONCEPT_SOURCES)(
    "renders inline formatting in every field with %s technical context",
    async (source) => {
      const { container } = render(
        <MeetingEdgePanel readOnly payload={payloadFor(source)} onSaveFocus={vi.fn()} />,
      );

      for (const field of FIELDS) {
        await waitFor(() => expect(screen.getByText(`${field} bold`).tagName).toBe("STRONG"));
        expect(screen.getByText(`${field} italic`).tagName).toBe("EM");
        expect(screen.getByText(`${field} code`).tagName).toBe("CODE");
      }
      expect(container.textContent).not.toContain("**");
      expect(container.querySelector("p div")).toBeNull();
      expect(screen.queryByText("Old term")).not.toBeInTheDocument();
    },
  );

  it.each(CONCEPT_SOURCES)(
    "keeps raw HTML inert and rejects unsafe links in every field with %s",
    async (source) => {
      const unsafe = (field: string) =>
        `${formatted(field)} <strong data-raw-edge="${field}">${field} raw</strong> ` +
        `<img src="x" onerror="alert(1)"> ` +
        `[${field} unsafe](javascript:alert(1)) [${field} safe](https://example.com/${field})`;
      const { container } = render(
        <MeetingEdgePanel payload={payloadFor(source, unsafe)} onSaveFocus={vi.fn()} />,
      );

      for (const field of FIELDS) {
        await waitFor(() => expect(screen.getByText(`${field} safe`)).toHaveAttribute(
          "href", `https://example.com/${field}`,
        ));
        expect(screen.getByText(`${field} bold`).tagName).toBe("STRONG");
        expect(container.textContent).toContain(`<strong data-raw-edge="${field}">${field} raw</strong>`);
      }
      expect(container.querySelector("[data-raw-edge], img, script, [onerror]")).toBeNull();
      expect(container.querySelector('a[href^="javascript:"]')).toBeNull();
      expect(container.querySelectorAll("a")).toHaveLength(FIELDS.length);
    },
  );

  it("updates formatted guidance when a new model payload arrives", async () => {
    const onSaveFocus = vi.fn();
    const { rerender } = render(
      <MeetingEdgePanel payload={payloadFor("concepts")} onSaveFocus={onSaveFocus} />,
    );
    await waitFor(() => expect(screen.getByText("read bold")).toBeInTheDocument());

    rerender(
      <MeetingEdgePanel payload={payloadFor("concept_history", (field) => formatted(`new ${field}`))}
        onSaveFocus={onSaveFocus} />,
    );

    for (const field of FIELDS) {
      await waitFor(() => expect(screen.getByText(`new ${field} bold`).tagName).toBe("STRONG"));
      expect(screen.queryByText(`${field} bold`)).not.toBeInTheDocument();
    }
  });
});
