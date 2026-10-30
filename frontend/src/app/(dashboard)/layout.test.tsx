import type { ReactNode } from "react";

import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import DashboardLayout from "./layout";

const navigation = vi.hoisted(() => ({ pathname: "/recordings/rec-2" }));

vi.mock("next/navigation", () => ({
  usePathname: () => navigation.pathname,
  useRouter: () => ({ push: vi.fn() }),
}));

// A paused capture of rec-1, so the real recording bar renders.
vi.mock("@/lib/capture/CaptureProvider", () => ({
  CaptureProvider: ({ children }: { children: ReactNode }) => <>{children}</>,
  useCapture: () => ({
    cancel: vi.fn(),
    controller: { getState: () => ({ error: null }) },
    pause: vi.fn(),
    resume: vi.fn(),
    runtimeActive: true,
    status: "paused",
    stop: vi.fn(),
    recordingId: "rec-1",
  }),
  useCaptureState: (select: (state: { elapsedSeconds: number }) => unknown) =>
    select({ elapsedSeconds: 12 }),
}));

vi.mock("@/lib/notificationStore", () => ({
  useNotificationStore: () => ({ addNotification: vi.fn() }),
}));

// The shell's other parts stand in as markers.
vi.mock("@/components/CaptureShell", () => ({
  default: ({ children }: { children: ReactNode }) => <>{children}</>,
}));
vi.mock("@/components/DemoBanner", () => ({ default: () => null, DEMO_BANNER_STYLE: undefined }));
vi.mock("@/components/TopBar", () => ({ default: () => <div data-testid="top-bar" /> }));
vi.mock("@/components/MainNav", () => ({ default: () => <div data-testid="main-nav" /> }));
vi.mock("@/components/Sidebar", () => ({ default: () => <div data-testid="recordings-rail" /> }));
vi.mock("@/components/TourGuide", () => ({ default: () => null }));
vi.mock("@/components/ServiceStatusAlerts", () => ({ default: () => null }));
vi.mock("@/components/TelemetryNotice", () => ({ default: () => null }));
vi.mock("@/components/UpdateNotice", () => ({ default: () => null }));
vi.mock("@/components/importAudio/ImportAudioHost", () => ({ default: () => null }));
vi.mock("@/components/importAudio/UnfinishedImportNotice", () => ({ default: () => null }));

const renderShell = () => render(<DashboardLayout><p>Page</p></DashboardLayout>);

describe("DashboardLayout", () => {
  beforeEach(() => {
    navigation.pathname = "/recordings/rec-2";
  });

  it("gives the recording bar a row of its own above the page, the phone header and the nav", () => {
    renderShell();
    const bar = screen.getByRole("region", { name: "Recording in progress" });
    const shell = bar.parentElement as HTMLElement;
    const topBar = screen.getByTestId("top-bar");
    const main = screen.getByRole("main");
    // A row of the shell's column, not an overlay inside the page.
    expect(shell.className).toMatch(/\bflex-col\b/);
    expect(topBar.parentElement).toBe(shell);
    expect(main.contains(bar)).toBe(false);
    for (const below of [topBar, screen.getByTestId("main-nav"), main]) {
      expect(bar.compareDocumentPosition(below) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    }
    expect(screen.getByText("Page")).toBeInTheDocument();
  });

  it("leaves no row for it on the capture's own recording", () => {
    navigation.pathname = "/recordings/rec-1";
    renderShell();
    expect(screen.queryByRole("region", { name: "Recording in progress" })).toBeNull();
    expect(document.querySelector("[data-capture-bar]")).toBeNull();
  });

  it("hands the bar's height to the rail and takes the top inset off the page below it", () => {
    renderShell();
    const shell = screen.getByRole("region", { name: "Recording in progress" }).parentElement as HTMLElement;
    // Only while the bar is a child does the shell reserve it, and the bar,
    // first in the column, is the one that clears the safe-area inset.
    expect(shell.className).toContain("has-[>[data-capture-bar]]:[--capture-bar-h:calc(44px+env(safe-area-inset-top))]");
    expect(shell.className).toContain("[--page-safe-top:env(safe-area-inset-top)]");
    expect(shell.className).toContain("has-[>[data-capture-bar]]:[--page-safe-top:0px]");
  });
});
