// @vitest-environment node
import { describe, expect, it } from "vitest";

import type { Recording } from "@/types";

import {
  POLL_REFRESH_ROWS,
  RECORDINGS_PAGE_SIZE,
  appendPage,
  mergeRefresh,
  pageLinesUp,
  refreshLimit,
} from "./recordingListPages";

/** A meeting `minutesAgo` before a fixed noon, newest first by that. */
const meeting = (id: string, minutesAgo: number, name = id): Recording =>
  ({
    id,
    name,
    created_at: new Date(Date.UTC(2026, 10, 2, 12) - minutesAgo * 60_000)
      .toISOString()
      .replace("Z", ""),
  }) as Recording;

const ids = (rows: Recording[]) => rows.map((row) => row.id);

describe("mergeRefresh", () => {
  it("puts a new meeting on top and keeps the loaded rows past the answer", () => {
    const loaded = [meeting("a", 1), meeting("b", 2), meeting("c", 3), meeting("d", 4)];
    // Read with a limit of three: a new meeting pushes "c" past the answer.
    const fresh = [meeting("new", 0), meeting("a", 1), meeting("b", 2)];

    expect(ids(mergeRefresh(loaded, fresh, 3))).toEqual(["new", "a", "b", "c", "d"]);
  });

  it("drops a loaded row the answer's stretch leaves out, and takes the fresh copies", () => {
    const loaded = [meeting("a", 1), meeting("b", 2, "Old name"), meeting("c", 3), meeting("d", 4)];
    const fresh = [meeting("b", 2, "New name"), meeting("c", 3)];

    const merged = mergeRefresh(loaded, fresh, 2);

    expect(ids(merged)).toEqual(["b", "c", "d"]);
    expect(merged[0].name).toBe("New name");
  });

  it("hands back the rows it holds when nothing changed, so nothing is drawn again", () => {
    const loaded = [meeting("a", 1), meeting("b", 2), meeting("c", 3)];
    const fresh = loaded.map((row) => ({ ...row }));

    expect(mergeRefresh(loaded, fresh, 50)).toBe(loaded);
    expect(mergeRefresh(loaded, fresh.slice(0, 2), 2)).toBe(loaded);
  });

  it("keeps each unchanged row's object when another row changed", () => {
    const loaded = [meeting("a", 1), meeting("b", 2), meeting("c", 3)];
    const fresh = [{ ...loaded[0] }, meeting("b", 2, "Renamed"), { ...loaded[2] }];

    const merged = mergeRefresh(loaded, fresh, 50);

    expect(merged).not.toBe(loaded);
    expect(merged[0]).toBe(loaded[0]);
    expect(merged[1].name).toBe("Renamed");
    expect(merged[2]).toBe(loaded[2]);
  });

  it("takes a short answer as the whole list", () => {
    const loaded = [meeting("a", 1), meeting("b", 2), meeting("c", 3)];

    expect(ids(mergeRefresh(loaded, [meeting("a", 1)], 50))).toEqual(["a"]);
    expect(mergeRefresh(loaded, [], 50)).toEqual([]);
  });

  it("keeps a row that started at the same instant as the answer's last row", () => {
    const loaded = [meeting("a", 1), meeting("b", 2), meeting("twin", 2)];

    expect(ids(mergeRefresh(loaded, [meeting("a", 1), meeting("b", 2)], 2))).toEqual([
      "a",
      "b",
      "twin",
    ]);
  });

  it("orders times within one millisecond by their microseconds", () => {
    const last = { ...meeting("last", 5), created_at: "2026-11-02T11:55:00.000500" };
    const before = { ...meeting("before", 5), created_at: "2026-11-02T11:55:00.000900" };
    const after = { ...meeting("after", 5), created_at: "2026-11-02T11:55:00.000100" };

    expect(ids(mergeRefresh([before, last, after], [meeting("a", 1), last], 2))).toEqual([
      "a",
      "last",
      "after",
    ]);
  });
});

describe("appendPage", () => {
  it("adds each meeting once, so one that moved down a page is not shown twice", () => {
    const loaded = [meeting("a", 1), meeting("b", 2)];

    expect(ids(appendPage(loaded, [meeting("b", 2), meeting("c", 3)]))).toEqual([
      "a",
      "b",
      "c",
    ]);
  });

  it("returns the same rows when the page adds nothing", () => {
    const loaded = [meeting("a", 1)];

    expect(appendPage(loaded, [meeting("a", 1)])).toBe(loaded);
  });
});

describe("pageLinesUp", () => {
  const loaded = [meeting("a", 1), meeting("b", 2)];

  it("holds when the page starts with the last row shown", () => {
    expect(pageLinesUp(loaded, [meeting("b", 2), meeting("c", 3)])).toBe(true);
    expect(pageLinesUp([], [meeting("a", 1)])).toBe(true);
  });

  it("fails when a meeting was added or removed above", () => {
    expect(pageLinesUp(loaded, [meeting("a", 1), meeting("b", 2)])).toBe(false);
    expect(pageLinesUp(loaded, [meeting("c", 3)])).toBe(false);
    expect(pageLinesUp(loaded, [])).toBe(false);
  });
});

describe("refreshLimit", () => {
  it("reads the loaded range in whole pages, at least one", () => {
    expect(refreshLimit(0)).toBe(RECORDINGS_PAGE_SIZE);
    expect(refreshLimit(30)).toBe(RECORDINGS_PAGE_SIZE);
    expect(refreshLimit(120)).toBe(3 * RECORDINGS_PAGE_SIZE);
    expect(refreshLimit(1000)).toBe(1000);
  });

  it("stops the poll at its cap", () => {
    expect(refreshLimit(1000, POLL_REFRESH_ROWS)).toBe(POLL_REFRESH_ROWS);
    expect(refreshLimit(30, POLL_REFRESH_ROWS)).toBe(RECORDINGS_PAGE_SIZE);
  });
});
