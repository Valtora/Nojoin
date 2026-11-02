import { createRef } from "react";
import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import RecordingListFooter from "./RecordingListFooter";

type Props = Parameters<typeof RecordingListFooter>[0];

const props = (overrides: Partial<Props> = {}): Props => ({
  rowCount: 50,
  hasMore: true,
  isLoadingMore: false,
  loadMoreFailed: false,
  lastAdded: null,
  onLoadMore: vi.fn(),
  scrollRootRef: createRef<HTMLElement>(),
  listRef: createRef<HTMLElement>(),
  endMessage: null,
  ...overrides,
});

describe("RecordingListFooter", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("loads the next page by itself as the end of the list comes near", () => {
    let report: (entries: { isIntersecting: boolean }[]) => void = () => {};
    const observe = vi.fn();
    vi.stubGlobal(
      "IntersectionObserver",
      class {
        constructor(callback: typeof report) {
          report = callback;
        }
        observe = observe;
        disconnect = vi.fn();
      },
    );
    const onLoadMore = vi.fn();
    render(<RecordingListFooter {...props({ onLoadMore })} />);

    expect(observe).toHaveBeenCalledWith(screen.getByRole("button", { name: "Load more recordings" }));
    report([{ isIntersecting: false }]);
    expect(onLoadMore).not.toHaveBeenCalled();
    report([{ isIntersecting: true }]);
    expect(onLoadMore).toHaveBeenCalledTimes(1);
  });

  it("keeps its button, marked unavailable, while a page loads", () => {
    const onLoadMore = vi.fn();
    render(<RecordingListFooter {...props({ isLoadingMore: true, onLoadMore })} />);

    const button = screen.getByRole("button", { name: "Loading more recordings..." });
    expect(button).toHaveAttribute("aria-disabled", "true");
    fireEvent.click(button);
    expect(onLoadMore).not.toHaveBeenCalled();
    expect(screen.getByText("Loading more recordings", { selector: "[aria-live]" })).toBeInTheDocument();
  });

  it("says when a page failed and tries again on request", () => {
    const onLoadMore = vi.fn();
    render(<RecordingListFooter {...props({ loadMoreFailed: true, onLoadMore })} />);

    expect(screen.getByRole("alert")).toHaveTextContent("Could not load more recordings.");
    expect(screen.queryByRole("button", { name: "Load more recordings" })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(onLoadMore).toHaveBeenCalledTimes(1);
  });

  it("marks the end of a long list and announces the last page", () => {
    render(
      <RecordingListFooter
        {...props({ hasMore: false, lastAdded: 12, endMessage: "No older recordings." })}
      />,
    );

    expect(screen.getByText("No older recordings.")).toBeInTheDocument();
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
    expect(screen.getByText("12 more recordings loaded")).toBeInTheDocument();
  });
});
