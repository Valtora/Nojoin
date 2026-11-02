import { act, renderHook, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { RecordingFilters } from "@/lib/api";
import type { Recording } from "@/types";

import { POLL_REFRESH_ROWS, RECORDINGS_PAGE_SIZE } from "../recordingListPages";
import { useRecordingListPages } from "./useRecordingListPages";

const getRecordings = vi.hoisted(() => vi.fn());
vi.mock("@/lib/api", () => ({ getRecordings }));

/** The n-th newest meeting of a made-up library, a minute apart. */
const meeting = (n: number, name = `Meeting ${n}`): Recording =>
  ({
    id: `rec-${n}`,
    name,
    created_at: new Date(Date.UTC(2026, 10, 2, 12) - n * 60_000).toISOString().replace("Z", ""),
  }) as Recording;

const library = (size: number) => Array.from({ length: size }, (_, n) => meeting(n));

/** Answers like the list route: a window of `rows` at skip and limit. */
const serve = (rows: () => Recording[]) =>
  getRecordings.mockImplementation(async (filters: RecordingFilters = {}) => {
    const skip = filters.skip ?? 0;
    return rows().slice(skip, skip + (filters.limit ?? 100));
  });

/** A request that answers only when told to, for ordering tests. */
const deferred = () => {
  let resolve!: (rows: Recording[]) => void;
  const promise = new Promise<Recording[]>((done) => {
    resolve = done;
  });
  return { promise, resolve };
};

const ids = (rows: Recording[]) => rows.map((row) => row.id);
const lastCall = () => getRecordings.mock.calls.at(-1)?.[0] as RecordingFilters;

const mount = (filters: RecordingFilters = {}) =>
  renderHook(({ query }) => useRecordingListPages(query), {
    initialProps: { query: filters },
  });

describe("useRecordingListPages", () => {
  beforeEach(() => {
    getRecordings.mockReset();
  });

  it("reads the first page, then each further page from one row early, each meeting once", async () => {
    serve(() => library(120));
    const { result } = mount();

    await waitFor(() => expect(result.current.recordings).toHaveLength(RECORDINGS_PAGE_SIZE));
    expect(getRecordings).toHaveBeenCalledWith({ skip: 0, limit: RECORDINGS_PAGE_SIZE });
    expect(result.current.hasMore).toBe(true);

    act(() => result.current.loadMore());
    expect(result.current.isLoadingMore).toBe(true);
    await waitFor(() => expect(result.current.recordings).toHaveLength(100));
    expect(lastCall()).toEqual({ skip: 49, limit: 51 });
    expect(result.current.lastAdded).toBe(50);

    act(() => result.current.loadMore());
    await waitFor(() => expect(result.current.recordings).toHaveLength(120));
    expect(result.current.hasMore).toBe(false);
    expect(new Set(ids(result.current.recordings)).size).toBe(120);

    // The end: nothing more is asked for.
    const calls = getRecordings.mock.calls.length;
    act(() => result.current.loadMore());
    expect(getRecordings).toHaveBeenCalledTimes(calls);
  });

  it("reads the whole range again when a meeting arrived above mid-scroll", async () => {
    let rows = library(120);
    serve(() => rows);
    const { result } = mount();
    await waitFor(() => expect(result.current.recordings).toHaveLength(50));

    rows = [meeting(-1, "Arrived mid-scroll"), ...rows];
    act(() => result.current.loadMore());

    await waitFor(() => expect(result.current.recordings[0].name).toBe("Arrived mid-scroll"));
    expect(ids(result.current.recordings)).toEqual(ids(rows.slice(0, 100)));
    expect(lastCall()).toEqual({ skip: 0, limit: 100 });
  });

  it("lets a later search's answer stand over a slower earlier one", async () => {
    const first = deferred();
    const second = deferred();
    getRecordings.mockResolvedValueOnce(library(3));
    const { result, rerender } = mount();
    await waitFor(() => expect(result.current.recordings).toHaveLength(3));

    getRecordings.mockReturnValueOnce(first.promise).mockReturnValueOnce(second.promise);
    rerender({ query: { q: "bud" } });
    rerender({ query: { q: "budget" } });
    expect(result.current.isLoadingQuery).toBe(true);

    await act(async () => second.resolve([meeting(7, "Budget review")]));
    await act(async () => first.resolve([meeting(8, "Buddy check")]));

    expect(ids(result.current.recordings)).toEqual(["rec-7"]);
    expect(result.current.isLoadingQuery).toBe(false);
    expect(getRecordings).toHaveBeenLastCalledWith({ q: "budget", skip: 0, limit: 50 });
  });

  it("drops an answer for an earlier search even when it is the only one back", async () => {
    const stale = deferred();
    getRecordings.mockResolvedValueOnce(library(3));
    const { result, rerender } = mount();
    await waitFor(() => expect(result.current.recordings).toHaveLength(3));

    getRecordings.mockReturnValueOnce(stale.promise).mockReturnValueOnce(new Promise(() => {}));
    rerender({ query: { q: "a" } });
    rerender({ query: { q: "al" } });
    await act(async () => stale.resolve([meeting(9)]));

    expect(ids(result.current.recordings)).toEqual(["rec-0", "rec-1", "rec-2"]);
    expect(result.current.isLoadingQuery).toBe(true);
  });

  it("polls at most the first rows it caps at, and keeps the rows below", async () => {
    let rows = library(200);
    serve(() => rows);
    const { result } = mount();
    await waitFor(() => expect(result.current.recordings).toHaveLength(50));
    for (const length of [100, 150]) {
      act(() => result.current.loadMore());
      await waitFor(() => expect(result.current.recordings).toHaveLength(length));
    }

    rows = [meeting(-1, "New on top"), ...rows];
    act(() => result.current.refresh("poll"));

    await waitFor(() => expect(result.current.recordings).toHaveLength(151));
    expect(lastCall()).toEqual({ skip: 0, limit: POLL_REFRESH_ROWS });
    expect(result.current.recordings[0].name).toBe("New on top");
    expect(result.current.recordings.at(-1)?.id).toBe("rec-149");

    act(() => result.current.refresh("all"));
    await waitFor(() => expect(lastCall()).toEqual({ skip: 0, limit: 200 }));
  });

  it("keeps the same rows through a poll that finds nothing new", async () => {
    serve(() => library(10));
    const { result } = mount();
    await waitFor(() => expect(result.current.recordings).toHaveLength(10));
    const before = result.current.recordings;
    const calls = getRecordings.mock.calls.length;

    act(() => result.current.refresh("poll"));
    await waitFor(() => expect(getRecordings).toHaveBeenCalledTimes(calls + 1));
    await act(async () => {});

    expect(result.current.recordings).toBe(before);
  });

  it("drops a row deleted elsewhere from the stretch a refresh covers", async () => {
    let rows = library(10);
    serve(() => rows);
    const { result } = mount();
    await waitFor(() => expect(result.current.recordings).toHaveLength(10));

    rows = rows.filter((row) => row.id !== "rec-4");
    act(() => result.current.refresh("poll"));

    await waitFor(() => expect(result.current.recordings).toHaveLength(9));
    expect(ids(result.current.recordings)).not.toContain("rec-4");
  });

  it("keeps a rename and a removal made while a refresh was on its way", async () => {
    getRecordings.mockResolvedValueOnce(library(5));
    const { result } = mount();
    await waitFor(() => expect(result.current.recordings).toHaveLength(5));

    const poll = deferred();
    getRecordings.mockReturnValueOnce(poll.promise);
    act(() => result.current.refresh("poll"));
    act(() =>
      result.current.update((rows) =>
        rows.map((row) => (row.id === "rec-1" ? { ...row, name: "Renamed here" } : row)),
      ),
    );
    act(() => result.current.update((rows) => rows.filter((row) => row.id !== "rec-3")));

    // The poll was answered before either change reached the server.
    await act(async () => poll.resolve(library(5)));

    expect(result.current.recordings.find((row) => row.id === "rec-1")?.name).toBe("Renamed here");
    expect(ids(result.current.recordings)).toEqual(["rec-0", "rec-1", "rec-2", "rec-4"]);
  });

  it("applies only the latest of two refreshes", async () => {
    getRecordings.mockResolvedValueOnce(library(3));
    const { result } = mount();
    await waitFor(() => expect(result.current.recordings).toHaveLength(3));

    const older = deferred();
    const newer = deferred();
    getRecordings.mockReturnValueOnce(older.promise).mockReturnValueOnce(newer.promise);
    act(() => result.current.refresh("poll"));
    act(() => result.current.refresh("all"));
    await act(async () => newer.resolve([meeting(0, "Fresh"), meeting(1), meeting(2)]));
    await act(async () => older.resolve([meeting(0, "Stale"), meeting(1), meeting(2)]));

    expect(result.current.recordings[0].name).toBe("Fresh");
  });

  it("offers a retry when a further page fails, and keeps the rows it has", async () => {
    vi.spyOn(console, "error").mockImplementation(() => {});
    serve(() => library(80));
    const { result } = mount();
    await waitFor(() => expect(result.current.recordings).toHaveLength(50));

    getRecordings.mockRejectedValueOnce(new Error("offline"));
    act(() => result.current.loadMore());
    await waitFor(() => expect(result.current.loadMoreFailed).toBe(true));
    expect(result.current.recordings).toHaveLength(50);
    expect(result.current.hasMore).toBe(true);

    act(() => result.current.loadMore());
    await waitFor(() => expect(result.current.recordings).toHaveLength(80));
    expect(result.current.loadMoreFailed).toBe(false);
  });
});
