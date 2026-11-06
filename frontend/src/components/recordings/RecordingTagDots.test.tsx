import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { COLOR_PALETTE, getColorByKey } from "@/lib/constants";
import type { Tag } from "@/types";

import RecordingTagDots, {
  TAG_DOT_ROWS,
  TAG_DOT_SLOTS,
  tagDotsLabel,
  tagDotsLayout,
} from "./RecordingTagDots";

const tag = (id: number, name: string, color?: string): Tag => ({
  id,
  name,
  color,
  created_at: "2026-10-01T09:00:00Z",
  updated_at: "2026-10-01T09:00:00Z",
});

const NAMES = [
  "Clients", "Hiring", "Board", "Q4", "Renewals", "Offsite", "Legal", "Budget",
  "Roadmap", "Support", "Design", "Ops", "Sales", "Finance", "Partners",
];
const COLOURS = COLOR_PALETTE.map((option) => option.key).slice(0, NAMES.length);
const tags = (count: number) =>
  NAMES.slice(0, count).map((name, index) => tag(index + 1, name, COLOURS[index]));

const dots = (container: HTMLElement) =>
  Array.from(container.querySelectorAll<HTMLElement>("[data-tag-dot]"));
const colourOf = (key: string) => expect.stringContaining(getColorByKey(key).dot);
/** Each column's slots: a colour key per dot, the text of the count. */
const columns = (container: HTMLElement) =>
  Array.from(container.querySelectorAll<HTMLElement>("[data-tag-dot-column]")).map((column) =>
    Array.from(column.children).map((slot) =>
      slot.hasAttribute("data-tag-dot")
        ? COLOURS.find((key) => slot.classList.contains(getColorByKey(key).dot))
        : slot.textContent,
    ),
  );

describe("tagDotsLayout", () => {
  it.each([
    [0, { columns: 0, rows: 0, dots: 0, more: 0 }],
    [1, { columns: 1, rows: 1, dots: 1, more: 0 }],
    [6, { columns: 1, rows: 6, dots: 6, more: 0 }],
    [7, { columns: 2, rows: 4, dots: 7, more: 0 }],
    [8, { columns: 2, rows: 4, dots: 8, more: 0 }],
    [12, { columns: 2, rows: 6, dots: 12, more: 0 }],
    [13, { columns: 2, rows: 6, dots: 11, more: 2 }],
    [40, { columns: 2, rows: 6, dots: 11, more: 29 }],
  ])("lays out %i tag(s)", (count, layout) => {
    expect(tagDotsLayout(count)).toEqual(layout);
  });

  it("holds six to a column and twelve in all", () => {
    expect(TAG_DOT_ROWS).toBe(6);
    expect(TAG_DOT_SLOTS).toBe(12);
  });
});

describe("RecordingTagDots", () => {
  it.each([
    ["no tags", []],
    ["tags not loaded", undefined],
  ])("draws nothing for %s", (_, value) => {
    const { container } = render(<RecordingTagDots tags={value} />);

    expect(container).toBeEmptyDOMElement();
    expect(screen.queryByRole("img")).not.toBeInTheDocument();
  });

  it.each([1, 2, TAG_DOT_ROWS])(
    "draws %i tag(s) as one column, one dot per tag, in order, in its colour",
    (count) => {
      const { container } = render(<RecordingTagDots tags={tags(count)} />);

      expect(columns(container)).toEqual([COLOURS.slice(0, count)]);
      expect(dots(container).map((dot) => dot.className)).toEqual(
        COLOURS.slice(0, count).map(colourOf),
      );
      expect(screen.queryByText(/^\+/)).not.toBeInTheDocument();
    },
  );

  it.each([
    [7, 4],
    [8, 4],
    [11, 6],
    [TAG_DOT_SLOTS, 6],
  ])(
    "splits %i tags evenly over two columns, %i in the first, in order down each",
    (count, rows) => {
      const { container } = render(<RecordingTagDots tags={tags(count)} />);

      expect(columns(container)).toEqual([
        COLOURS.slice(0, rows),
        COLOURS.slice(rows, count),
      ]);
      expect(dots(container)).toHaveLength(count);
      expect(screen.queryByText(/^\+/)).not.toBeInTheDocument();
    },
  );

  it.each([
    [13, "+2"],
    [15, "+4"],
  ])(
    "fills both columns for %i tags and counts the rest in the last slot",
    (count, more) => {
      const { container } = render(<RecordingTagDots tags={tags(count)} />);

      expect(columns(container)).toEqual([
        COLOURS.slice(0, TAG_DOT_ROWS),
        [...COLOURS.slice(TAG_DOT_ROWS, TAG_DOT_SLOTS - 1), more],
      ]);
      expect(dots(container)).toHaveLength(TAG_DOT_SLOTS - 1);
      expect(screen.getByText(more)).toBeInTheDocument();
    },
  );

  it("names every tag, the hidden ones included, for a screen reader and on hover", () => {
    render(<RecordingTagDots tags={tags(15)} />);

    const name = `Tags: ${NAMES.join(", ")}`;
    const group = screen.getByRole("img", { name });
    expect(group).toHaveAttribute("title", name);
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
