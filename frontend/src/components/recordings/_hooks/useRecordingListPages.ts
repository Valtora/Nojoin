"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { getRecordings, type RecordingFilters } from "@/lib/api";
import type { Recording } from "@/types";

import {
  PAGE_OVERLAP,
  POLL_REFRESH_ROWS,
  RECORDINGS_PAGE_SIZE,
  appendPage,
  mergeRefresh,
  pageLinesUp,
  refreshLimit,
} from "../recordingListPages";

type RowsChange = (rows: Recording[]) => Recording[];

interface ListState {
  rows: Recording[];
  /** The query the rows answer; null until the first answer arrives. */
  key: string | null;
  hasMore: boolean;
  loadingMore: boolean;
  loadMoreFailed: boolean;
  /** How many rows the last further page added, for its announcement. */
  lastAdded: number | null;
  /** A query whose first read failed before any answer to it arrived. */
  failedKey: string | null;
}

const EMPTY: ListState = {
  rows: [],
  key: null,
  hasMore: false,
  loadingMore: false,
  loadMoreFailed: false,
  lastAdded: null,
  failedKey: null,
};

export interface RecordingListPages {
  recordings: Recording[];
  /** Some answer has arrived since the list mounted. */
  hasLoaded: boolean;
  /** The rows answer an earlier search or filter, or none yet: a new one is on its way. */
  isLoadingQuery: boolean;
  /** The current search or filter could not be read, and nothing answers it yet. */
  loadFailed: boolean;
  hasMore: boolean;
  isLoadingMore: boolean;
  loadMoreFailed: boolean;
  lastAdded: number | null;
  /** Reads the next page, unless one is on its way or the list has ended. */
  loadMore: () => void;
  /**
   * Reads the loaded rows again. The poll reads at most the first
   * {@link POLL_REFRESH_ROWS}; anything else re-reads the whole range.
   */
  refresh: (scope?: "poll" | "all") => void;
  /**
   * Changes the rows here and now: a rename, or a row an action took away.
   * An answer to a read sent before the change arrives with the change
   * already applied, so it cannot bring the old name or the row back.
   */
  update: (change: RowsChange) => void;
}

/**
 * The recordings rail's rows, read a page at a time for a search and filter
 * (issue #900).
 *
 * Every answer is checked against the query it was sent for, so a slow
 * answer to an earlier search never replaces a newer one, and only the
 * latest re-read of the loaded range is applied.
 */
export function useRecordingListPages(
  filters: RecordingFilters,
  onFetched?: () => void,
): RecordingListPages {
  const key = JSON.stringify(filters);
  const [state, setState] = useState<ListState>(EMPTY);

  const stateRef = useRef(state);
  const keyRef = useRef(key);
  const onFetchedRef = useRef(onFetched);
  // Bumped when the query changes; an answer from an older one is dropped.
  const generation = useRef(0);
  // One clock orders reads and local changes, so a change made after a read
  // was sent is applied again to its answer.
  const clock = useRef(0);
  const inFlight = useRef(new Set<number>());
  const changes = useRef<{ at: number; change: RowsChange }[]>([]);
  const latestRefresh = useRef(0);
  const loadingMore = useRef(false);

  useEffect(() => {
    stateRef.current = state;
  }, [state]);

  useEffect(() => {
    onFetchedRef.current = onFetched;
  }, [onFetched]);

  const send = useCallback((skip: number, limit: number) => {
    const at = ++clock.current;
    inFlight.current.add(at);
    const query = keyRef.current;
    const request = getRecordings({
      ...(JSON.parse(query) as RecordingFilters),
      skip,
      limit,
    });
    return { at, query, generation: generation.current, request };
  }, []);

  /** The local changes made since `at`, as one function over the rows. */
  const changesSince = useCallback((at: number): RowsChange => {
    const later = changes.current.filter((entry) => entry.at > at);
    return (rows) => later.reduce((acc, entry) => entry.change(acc), rows);
  }, []);

  const settle = useCallback((at: number) => {
    inFlight.current.delete(at);
    if (inFlight.current.size === 0) {
      changes.current = [];
      return;
    }
    const oldest = Math.min(...inFlight.current);
    changes.current = changes.current.filter((entry) => entry.at > oldest);
  }, []);

  const readFirst = useCallback(
    (limit: number) => {
      const { at, query, generation: sentFor, request } = send(0, limit);
      latestRefresh.current = at;
      request
        .then((fresh) => {
          if (sentFor !== generation.current || at !== latestRefresh.current) return;
          const replay = changesSince(at);
          setState((prev) => {
            const same = prev.key === query;
            const rows = replay(same ? mergeRefresh(prev.rows, fresh, limit) : fresh);
            // A short answer is the whole list; a full one leaves the end
            // where it was, as the rows past it were kept.
            const hasMore = fresh.length < limit ? false : same ? prev.hasMore : true;
            // Nothing changed: the same state, so nothing is drawn again.
            if (same && rows === prev.rows && hasMore === prev.hasMore) return prev;
            return {
              rows,
              key: query,
              hasMore,
              loadingMore: same ? prev.loadingMore : false,
              loadMoreFailed: same ? prev.loadMoreFailed : false,
              lastAdded: same ? prev.lastAdded : null,
              failedKey: null,
            };
          });
          onFetchedRef.current?.();
        })
        .catch((error: unknown) => {
          if (sentFor !== generation.current || at !== latestRefresh.current) return;
          console.error("Failed to fetch recordings:", error);
          setState((prev) => (prev.key === query ? prev : { ...prev, failedKey: query }));
        })
        .finally(() => settle(at));
    },
    [changesSince, send, settle],
  );

  const refresh = useCallback(
    (scope: "poll" | "all" = "all") => {
      const { rows, key: rowsKey } = stateRef.current;
      const loaded = rowsKey === keyRef.current ? rows.length : 0;
      readFirst(
        refreshLimit(loaded, scope === "poll" ? POLL_REFRESH_ROWS : undefined),
      );
    },
    [readFirst],
  );

  const loadMore = useCallback(() => {
    const { rows, key: rowsKey, hasMore } = stateRef.current;
    if (loadingMore.current || !hasMore || rowsKey !== keyRef.current) return;
    loadingMore.current = true;
    const skip = Math.max(0, rows.length - PAGE_OVERLAP);
    const limit = RECORDINGS_PAGE_SIZE + (rows.length - skip);
    const { at, query, generation: sentFor, request } = send(skip, limit);
    setState((prev) => ({ ...prev, loadingMore: true, loadMoreFailed: false }));
    request
      .then((page) => {
        if (sentFor !== generation.current) return;
        const replay = changesSince(at);
        setState((prev) => {
          if (prev.key !== query) return prev;
          const next = replay(appendPage(prev.rows, page));
          return {
            ...prev,
            rows: next,
            hasMore: page.length >= limit,
            loadingMore: false,
            lastAdded: next.length - prev.rows.length,
          };
        });
        // A meeting added or removed above since the rows were read moves
        // the offsets: read the whole range again so none is missed.
        if (!pageLinesUp(rows, page)) {
          readFirst(refreshLimit(rows.length + page.length - PAGE_OVERLAP));
        }
      })
      .catch((error: unknown) => {
        if (sentFor !== generation.current) return;
        console.error("Failed to load more recordings:", error);
        setState((prev) => ({ ...prev, loadingMore: false, loadMoreFailed: true }));
      })
      .finally(() => {
        if (sentFor === generation.current) loadingMore.current = false;
        settle(at);
      });
  }, [changesSince, readFirst, send, settle]);

  const update = useCallback((change: RowsChange) => {
    if (inFlight.current.size > 0) {
      changes.current.push({ at: ++clock.current, change });
    }
    setState((prev) => ({ ...prev, rows: change(prev.rows) }));
  }, []);

  useEffect(() => {
    keyRef.current = key;
    generation.current += 1;
    loadingMore.current = false;
    readFirst(RECORDINGS_PAGE_SIZE);
  }, [key, readFirst]);

  return {
    recordings: state.rows,
    hasLoaded: state.key !== null,
    isLoadingQuery: state.key !== key && state.failedKey !== key,
    loadFailed: state.key !== key && state.failedKey === key,
    hasMore: state.key === key && state.hasMore,
    isLoadingMore: state.key === key && state.loadingMore,
    loadMoreFailed: state.key === key && state.loadMoreFailed,
    lastAdded: state.key === key ? state.lastAdded : null,
    loadMore,
    refresh,
    update,
  };
}
