import { readFileSync } from "node:fs";
import { join } from "node:path";

import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { CaptureResumeSupersededError } from "@/lib/capture/shared";

import RecordingFloatingBadge from "./RecordingFloatingBadge";

const addNotification = vi.fn();
const resume = vi.fn();
const cancel = vi.fn();
const navigation = vi.hoisted(() => ({ pathname: "/recordings" }));

vi.mock("next/navigation", () => ({
  usePathname: () => navigation.pathname,
  useRouter: () => ({ push: vi.fn() }),
}));

vi.mock("@/lib/capture/CaptureProvider", () => ({
  useCapture: () => ({
    cancel: (...args: unknown[]) => cancel(...args),
    controller: { getState: () => ({ error: null }) },
    pause: vi.fn(),
    resume: (...args: unknown[]) => resume(...args),
    runtimeActive: true,
    status: "paused",
    stop: vi.fn(),
    recordingId: "rec-1",
  }),
  useCaptureState: (select: (state: { elapsedSeconds: number }) => unknown) =>
    select({ elapsedSeconds: 12 }),
}));

vi.mock("@/lib/notificationStore", () => ({
  useNotificationStore: () => ({ addNotification }),
}));

describe("RecordingFloatingBadge", () => {
  beforeEach(() => {
    addNotification.mockReset();
    resume.mockReset();
    cancel.mockReset().mockResolvedValue(undefined);
    navigation.pathname = "/recordings";
  });

  it("asks in the app's own dialog before it discards (#841)", async () => {
    const nativeConfirm = vi.spyOn(window, "confirm");
    render(<RecordingFloatingBadge />);

    fireEvent.click(screen.getByRole("button", { name: "Discard recording" }));
    const dialog = await screen.findByRole("dialog", { name: "Discard Recording" });
    expect(dialog).toHaveTextContent(
      "Discard this recording? This permanently deletes the in-progress meeting and its audio, and cannot be undone.",
    );
    fireEvent.click(within(dialog).getByRole("button", { name: "Cancel" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(cancel).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole("button", { name: "Discard recording" }));
    fireEvent.click(within(await screen.findByRole("dialog")).getByRole("button", { name: "Discard" }));
    await waitFor(() => expect(cancel).toHaveBeenCalledOnce());
    expect(nativeConfirm).not.toHaveBeenCalled();
    nativeConfirm.mockRestore();
  });

  it("is a bar in the page's flow, not a card floating over it", () => {
    navigation.pathname = "/recordings/rec-2";
    render(<RecordingFloatingBadge />);
    const bar = screen.getByRole("region", { name: "Recording in progress" });
    expect(bar).toHaveAttribute("data-capture-bar");
    expect(bar.className).not.toMatch(/\b(fixed|absolute|sticky)\b/);
    // It is as tall as the shell says, so the shell can leave room for it.
    expect(bar.className).toContain("h-[var(--capture-bar-h)]");
    for (const name of ["Resume recording", "Stop recording", "Discard recording"]) {
      expect(within(bar).getByRole("button", { name })).toBeInTheDocument();
    }
    expect(within(bar).getByTitle("Go to recording")).toHaveTextContent("Paused00:12");
  });

  it("leaves the capture's own recording to its header", () => {
    navigation.pathname = "/recordings/rec-1";
    render(<RecordingFloatingBadge />);
    expect(screen.queryByRole("region", { name: "Recording in progress" })).toBeNull();
  });

  // Where the bar renders in the shell is layout.test.tsx's; these are the
  // parts of the page that make room for it.
  it("is given room by the nav rail and the phone headers, which leave the top inset to it", () => {
    const read = (path: string) => readFileSync(join(process.cwd(), "src", path), "utf8");
    expect(read("components/MainNav.tsx")).toContain("_-_var(--capture-bar-h,0px))]");
    const header = read("app/(dashboard)/recordings/[id]/_components/RecordingHeader.tsx");
    const topBar = read("components/TopBar.tsx");
    // The bar pads the safe-area inset itself, so the header under it must not
    // pad it a second time, and the header's menu sits below the bar and header.
    expect(header).toContain("pt-[calc(var(--page-safe-top,env(safe-area-inset-top))+0.75rem)]");
    expect(header).toContain(
      "top-[calc(var(--page-safe-top,env(safe-area-inset-top))+4rem+var(--capture-bar-h,0px))]");
    expect(topBar).toContain("pt-[calc(var(--page-safe-top,env(safe-area-inset-top))+0.5rem)]");
    for (const source of [header, topBar]) {
      expect(source).not.toMatch(/\[calc\(env\(safe-area-inset-top\)/);
    }
  });

  it("stays quiet about a Resume that a Stop or Discard overtook", async () => {
    resume.mockRejectedValueOnce(new CaptureResumeSupersededError());
    resume.mockRejectedValueOnce(new Error("Resume failed"));

    render(<RecordingFloatingBadge />);

    fireEvent.click(screen.getByRole("button", { name: "Resume recording" }));
    await waitFor(() => expect(resume).toHaveBeenCalledOnce());
    // A Resume that failed on its own still toasts, and only that one.
    fireEvent.click(screen.getByRole("button", { name: "Resume recording" }));

    await waitFor(() => {
      expect(addNotification).toHaveBeenCalledWith({
        type: "error",
        message: "Resume failed",
      });
    });
    expect(addNotification).toHaveBeenCalledOnce();
  });
});
