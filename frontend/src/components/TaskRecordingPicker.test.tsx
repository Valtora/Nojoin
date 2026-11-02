import { useState } from "react";
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { RecordingFilters } from "@/lib/api";
import { pressWhileComposing } from "@/test/composition";
import type { Recording } from "@/types";

import TaskRecordingPicker, { type TaskRecordingChoice } from "./TaskRecordingPicker";

const getRecordings = vi.hoisted(() => vi.fn());
vi.mock("@/lib/api", () => ({ getRecordings }));

/** The n-th newest recording of a made-up library, a day apart. */
const meeting = (n: number, name = `Meeting ${n}`, extra: Partial<Recording> = {}): Recording =>
  ({
    id: `rec-${n}`,
    name,
    created_at: new Date(Date.UTC(2026, 10, 2, 12) - n * 86_400_000).toISOString().replace("Z", ""),
    is_archived: false,
    is_deleted: false,
    ...extra,
  }) as Recording;

const library = (size: number) => Array.from({ length: size }, (_, n) => meeting(n));

/** Answers like the list route: matches for `q`, newest first, a window at a time. */
const serve = (rows: Recording[]) =>
  getRecordings.mockImplementation(async (filters: RecordingFilters = {}) => {
    const q = filters.q?.toLowerCase();
    const skip = filters.skip ?? 0;
    return rows
      .filter((row) => !q || row.name.toLowerCase().includes(q))
      .slice(skip, skip + (filters.limit ?? 100));
  });

const deferred = () => {
  let resolve!: (rows: Recording[]) => void;
  const promise = new Promise<Recording[]>((done) => {
    resolve = done;
  });
  return { promise, resolve };
};

const choice = (recording: Recording): TaskRecordingChoice => ({
  id: recording.id,
  name: recording.name,
  created_at: recording.created_at,
  is_archived: recording.is_archived,
  is_deleted: recording.is_deleted,
});

const changes = vi.fn();

function Picker({ initial = [] }: { initial?: TaskRecordingChoice[] }) {
  const [selected, setSelected] = useState(initial);
  return (
    <TaskRecordingPicker
      selected={selected}
      timeZone="UTC"
      onChange={(next) => {
        changes(next);
        setSelected(next);
      }}
    />
  );
}

const searchBox = () => screen.getByRole("combobox", { name: "Search recordings to link" });
const optionNames = () =>
  within(screen.getByRole("listbox"))
    .queryAllByRole("option")
    .map((option) => option.textContent);
const searches = () =>
  getRecordings.mock.calls
    .map(([filters]) => (filters as RecordingFilters).q)
    .filter((q) => q !== undefined);

describe("TaskRecordingPicker", () => {
  beforeEach(() => {
    getRecordings.mockReset();
    changes.mockReset();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("offers the newest recordings, archived ones included, before anything is typed", async () => {
    serve([meeting(0, "Weekly sync"), meeting(1, "Old plan", { is_archived: true })]);
    render(<Picker />);

    expect(await screen.findByRole("option", { name: /Weekly sync/ })).toBeInTheDocument();
    expect(getRecordings).toHaveBeenCalledTimes(1);
    expect(getRecordings).toHaveBeenCalledWith({ include_archived: true, skip: 0, limit: 50 });
    expect(screen.getByRole("listbox", { name: "Recent recordings" })).toBeInTheDocument();
    expect(
      screen.getByRole("option", { name: "Old plan, 1 Nov 2026 · Archived" }),
    ).toBeInTheDocument();
  });

  it("searches the server once typing pauses, not on every letter", async () => {
    vi.useFakeTimers();
    serve(library(10));
    render(<Picker />);
    await act(async () => {});

    for (const value of ["b", "bu", "budget"]) {
      fireEvent.change(searchBox(), { target: { value } });
      await act(async () => {
        await vi.advanceTimersByTimeAsync(100);
      });
    }
    expect(searches()).toEqual([]);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(199);
    });
    expect(searches()).toEqual([]);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(1);
    });
    expect(getRecordings).toHaveBeenLastCalledWith({
      include_archived: true,
      q: "budget",
      skip: 0,
      limit: 50,
    });
    expect(searches()).toEqual(["budget"]);
    await act(async () => {});
    expect(screen.getByRole("listbox", { name: "Matching recordings" })).toBeInTheDocument();
    expect(screen.getByRole("listbox").parentElement).toHaveTextContent("No recordings match.");
  });

  it("keeps the latest search's answer when an earlier one answers late", async () => {
    const first = deferred();
    const second = deferred();
    getRecordings.mockImplementation(async (filters: RecordingFilters = {}) => {
      if (filters.q === "alpha") return first.promise;
      if (filters.q === "bravo") return second.promise;
      return [meeting(0, "Newest")];
    });
    render(<Picker />);
    await screen.findByRole("option", { name: /Newest/ });

    fireEvent.change(searchBox(), { target: { value: "alpha" } });
    await waitFor(() => expect(searches()).toEqual(["alpha"]));
    fireEvent.change(searchBox(), { target: { value: "bravo" } });
    await waitFor(() => expect(searches()).toEqual(["alpha", "bravo"]));

    await act(async () => second.resolve([meeting(4, "Bravo review")]));
    expect(await screen.findByRole("option", { name: /Bravo review/ })).toBeInTheDocument();

    await act(async () => first.resolve([meeting(2, "Alpha kickoff")]));
    expect(optionNames()).toEqual([expect.stringContaining("Bravo review")]);
    expect(screen.queryByRole("option", { name: /Alpha kickoff/ })).not.toBeInTheDocument();
  });

  it("shows linked recordings the results leave out, and unlinks them", async () => {
    const old = meeting(400, "Budget review with Alice Example");
    const older = meeting(500, "Hiring panel with Bob Example");
    serve([...library(60), old, older]);
    render(<Picker initial={[choice(old), choice(older)]} />);
    await screen.findByRole("option", { name: /Meeting 0/ });

    const linked = screen.getByRole("list", { name: "Linked recordings" });
    expect(linked).toHaveTextContent("Budget review with Alice Example");
    expect(linked).toHaveTextContent("Hiring panel with Bob Example");
    expect(screen.queryByRole("option", { name: /Budget review/ })).not.toBeInTheDocument();

    // A click with no pointer, as Enter or Space on the button gives, moves
    // focus on rather than losing it with the button.
    const first = screen.getByRole("button", { name: "Unlink Budget review with Alice Example" });
    first.focus();
    fireEvent.click(first, { detail: 0 });
    expect(changes).toHaveBeenLastCalledWith([choice(older)]);
    const second = screen.getByRole("button", { name: "Unlink Hiring panel with Bob Example" });
    expect(second).toHaveFocus();

    fireEvent.click(second, { detail: 0 });
    expect(changes).toHaveBeenLastCalledWith([]);
    expect(screen.queryByRole("list", { name: "Linked recordings" })).not.toBeInTheDocument();
    expect(searchBox()).toHaveFocus();
  });

  it("keeps a recording linked from one search when the next search leaves it out", async () => {
    serve([meeting(0, "Planning"), meeting(1, "Retro")]);
    render(<Picker />);

    await screen.findByRole("option", { name: /Planning/ });
    fireEvent.change(searchBox(), { target: { value: "retro" } });
    await waitFor(() => expect(optionNames()).toEqual([expect.stringContaining("Retro")]));
    fireEvent.click(screen.getByRole("option", { name: /Retro/ }));
    expect(screen.getByRole("option", { name: /Retro/ })).toHaveAttribute("aria-selected", "true");

    fireEvent.change(searchBox(), { target: { value: "planning" } });
    await waitFor(() => expect(optionNames()).toEqual([expect.stringContaining("Planning")]));
    expect(screen.getByRole("list", { name: "Linked recordings" })).toHaveTextContent("Retro");
    expect(changes).toHaveBeenLastCalledWith([choice(meeting(1, "Retro"))]);
  });

  it("moves through the results with the arrow keys and links with Enter, never submitting the form", async () => {
    serve(library(3));
    const submit = vi.fn((event: { preventDefault: () => void }) => event.preventDefault());
    render(
      <form onSubmit={submit}>
        <Picker />
      </form>,
    );
    await screen.findByRole("option", { name: /Meeting 2/ });
    const input = searchBox();
    input.focus();

    // Enter's default, submitting the form, is always prevented here.
    expect(fireEvent.keyDown(input, { key: "Enter" })).toBe(false);
    expect(changes).not.toHaveBeenCalled();

    fireEvent.keyDown(input, { key: "ArrowDown" });
    fireEvent.keyDown(input, { key: "ArrowDown" });
    const second = screen.getByRole("option", { name: /Meeting 1/ });
    expect(input).toHaveAttribute("aria-activedescendant", second.id);

    fireEvent.keyDown(input, { key: "Enter" });
    expect(second).toHaveAttribute("aria-selected", "true");
    expect(changes).toHaveBeenLastCalledWith([choice(meeting(1))]);

    fireEvent.keyDown(input, { key: "Enter" });
    expect(second).toHaveAttribute("aria-selected", "false");
    expect(changes).toHaveBeenLastCalledWith([]);

    // Past the last row the highlight stays on it.
    fireEvent.keyDown(input, { key: "ArrowDown" });
    fireEvent.keyDown(input, { key: "ArrowDown" });
    expect(input).toHaveAttribute(
      "aria-activedescendant",
      screen.getByRole("option", { name: /Meeting 2/ }).id,
    );
    expect(input).toHaveFocus();
    expect(submit).not.toHaveBeenCalled();

    fireEvent.change(input, { target: { value: "meeting" } });
    fireEvent.keyDown(input, { key: "Escape" });
    expect(input).toHaveValue("");
  });

  it("leaves the arrow keys, Enter and Escape to an input method while it composes", async () => {
    serve(library(3));
    render(<Picker />);
    await screen.findByRole("option", { name: /Meeting 2/ });
    const input = searchBox();
    input.focus();
    fireEvent.keyDown(input, { key: "ArrowDown" });
    fireEvent.keyDown(input, { key: "ArrowDown" });
    const highlighted = screen.getByRole("option", { name: /Meeting 1/ }).id;
    fireEvent.change(input, { target: { value: "かいぎ" } });

    for (const key of ["ArrowDown", "ArrowUp", "Enter", "Escape"]) pressWhileComposing(input, key);

    expect(changes).not.toHaveBeenCalled();
    expect(input).toHaveAttribute("aria-activedescendant", highlighted);
    expect(input).toHaveValue("かいぎ");

    // Once the composition has ended, Enter links the highlighted recording.
    fireEvent.keyDown(input, { key: "Enter" });
    expect(changes).toHaveBeenLastCalledWith([choice(meeting(1))]);
  });

  it("loads older recordings a page at a time, and returns focus to the search box at the end", async () => {
    serve(library(60));
    render(<Picker />);
    await screen.findByRole("option", { name: /Meeting 49/ });
    expect(optionNames()).toHaveLength(50);

    const button = screen.getByRole("button", { name: "Load more recordings" });
    button.focus();
    fireEvent.click(button);

    expect(await screen.findByRole("option", { name: /Meeting 59/ })).toBeInTheDocument();
    expect(getRecordings).toHaveBeenLastCalledWith({ include_archived: true, skip: 49, limit: 51 });
    expect(optionNames()).toHaveLength(60);
    expect(screen.queryByRole("button", { name: "Load more recordings" })).not.toBeInTheDocument();
    await waitFor(() => expect(searchBox()).toHaveFocus());
  });

  it("says when the recordings could not be read, and reads them again on request", async () => {
    getRecordings.mockRejectedValueOnce(new Error("offline"));
    const logged = vi.spyOn(console, "error").mockImplementation(() => {});
    render(<Picker />);

    expect(await screen.findByRole("alert")).toHaveTextContent("Could not load recordings.");
    serve([meeting(0, "Back again")]);
    fireEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(await screen.findByRole("option", { name: /Back again/ })).toBeInTheDocument();
    logged.mockRestore();
  });
});
