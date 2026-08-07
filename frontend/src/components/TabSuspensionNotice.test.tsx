import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it } from "vitest";

import TabSuspensionNotice, {
  readTabSuspensionNoticeDismissed,
} from "./TabSuspensionNotice";

describe("tab suspension notice", () => {
  beforeEach(() => {
    window.localStorage.clear();
  });

  it("tells the reader where the Chrome setting is", async () => {
    render(<TabSuspensionNotice />);

    await waitFor(() => {
      expect(screen.getByRole("note")).toBeTruthy();
    });
    expect(screen.getByRole("note").textContent).toContain(
      "Always keep these sites active",
    );
  });

  it("shows the address to add, so it does not have to be guessed", async () => {
    render(<TabSuspensionNotice />);

    await waitFor(() => {
      expect(screen.getByRole("note").textContent).toContain(
        window.location.host,
      );
    });
  });

  it("stays hidden once dismissed", async () => {
    const { unmount } = render(<TabSuspensionNotice />);
    await waitFor(() => screen.getByRole("note"));

    fireEvent.click(
      screen.getByRole("button", { name: /dismiss the tab suspension notice/i }),
    );
    expect(screen.queryByRole("note")).toBeNull();

    unmount();
    render(<TabSuspensionNotice />);
    await waitFor(() => {
      expect(screen.queryByRole("note")).toBeNull();
    });
  });

  it("records the dismissal per browser rather than per user", async () => {
    // What it asks for is a per-browser Chrome setting, so someone who has done
    // it on their desktop has not done it on their laptop.
    render(<TabSuspensionNotice />);
    await waitFor(() => screen.getByRole("note"));

    fireEvent.click(
      screen.getByRole("button", { name: /dismiss the tab suspension notice/i }),
    );

    expect(readTabSuspensionNoticeDismissed()).toBe(true);
    expect(
      window.localStorage.getItem("nojoin.capture.tabSuspensionNoticeDismissed"),
    ).toBe("true");
  });

  it("renders nothing before mount, so it cannot flash at someone who dismissed it", () => {
    window.localStorage.setItem(
      "nojoin.capture.tabSuspensionNoticeDismissed",
      "true",
    );
    const { container } = render(<TabSuspensionNotice />);

    expect(container.querySelector('[role="note"]')).toBeNull();
  });
});
