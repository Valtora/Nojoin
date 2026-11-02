import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { getColorByKey } from "@/lib/constants";
import type { Tag } from "@/types";

import RecordingTagDots, { TAG_DOT_SLOTS, tagDotsLabel } from "./RecordingTagDots";

const tag = (id: number, name: string, color?: string): Tag => ({
  id,
  name,
  color,
  created_at: "2026-10-01T09:00:00Z",
  updated_at: "2026-10-01T09:00:00Z",
});

const NAMES = ["Clients", "Hiring", "Board", "Q4", "Renewals", "Offsite"];
const COLOURS = ["teal", "amber", "red", "blue", "rose", "violet"];
const tags = (count: number) =>
  NAMES.slice(0, count).map((name, index) => tag(index + 1, name, COLOURS[index]));

const dots = (container: HTMLElement) =>
  Array.from(container.querySelectorAll<HTMLElement>("[data-tag-dot]"));

describe("RecordingTagDots", () => {
  it.each([
    ["no tags", []],
    ["tags not loaded", undefined],
  ])("draws nothing for %s", (_, value) => {
    const { container } = render(<RecordingTagDots tags={value} />);

    expect(container).toBeEmptyDOMElement();
    expect(screen.queryByRole("img")).not.toBeInTheDocument();
  });

  it.each([1, 2, TAG_DOT_SLOTS])(
    "draws one dot per tag, in order, in its colour, for %i tag(s)",
    (count) => {
      const { container } = render(<RecordingTagDots tags={tags(count)} />);

      expect(dots(container).map((dot) => dot.className)).toEqual(
        COLOURS.slice(0, count).map((key) => expect.stringContaining(getColorByKey(key).dot)),
      );
      expect(screen.queryByText(/^\+/)).not.toBeInTheDocument();
    },
  );

  it.each([
    [4, "+2"],
    [6, "+4"],
  ])(
    "caps %i tags at the slots with a count of the rest in the last one",
    (count, more) => {
      const { container } = render(<RecordingTagDots tags={tags(count)} />);

      expect(dots(container)).toHaveLength(TAG_DOT_SLOTS - 1);
      expect(dots(container).map((dot) => dot.className)).toEqual(
        COLOURS.slice(0, TAG_DOT_SLOTS - 1).map((key) =>
          expect.stringContaining(getColorByKey(key).dot),
        ),
      );
      expect(screen.getByText(more)).toBeInTheDocument();
    },
  );

  it("names every tag, the hidden ones included, for a screen reader and on hover", () => {
    render(<RecordingTagDots tags={tags(5)} />);

    const group = screen.getByRole("img", {
      name: "Tags: Clients, Hiring, Board, Q4, Renewals",
    });
    expect(group).toHaveAttribute("title", "Tags: Clients, Hiring, Board, Q4, Renewals");
  });

  it("says Tag for a single tag", () => {
    render(<RecordingTagDots tags={tags(1)} />);

    expect(screen.getByRole("img", { name: "Tag: Clients" })).toHaveAttribute(
      "title",
      "Tag: Clients",
    );
    expect(tagDotsLabel(tags(2))).toBe("Tags: Clients, Hiring");
  });

  it("draws a tag with no colour or an unknown one in the default colour", () => {
    const { container } = render(
      <RecordingTagDots tags={[tag(1, "Loose"), tag(2, "Odd", "chartreuse")]} />,
    );

    const fallback = getColorByKey(undefined).dot;
    expect(dots(container).map((dot) => dot.className)).toEqual([
      expect.stringContaining(fallback),
      expect.stringContaining(fallback),
    ]);
  });
});
