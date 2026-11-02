import type { ComponentPropsWithoutRef, ReactElement } from "react";
import { act, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { CaptureProvider, useCapture } from "@/lib/capture/CaptureProvider";
import {
  ClientStatus,
  RecordingStatus,
  type CalendarDashboardRecording,
  type Recording,
} from "@/types";

import RecordingInfoModal from "../RecordingInfoModal";
import { DashboardRecordingCard } from "../upcomingMeetings/CalendarCards";
import ProcessingCard from "./ProcessingCard";
import RecentRecordingsCard from "./RecentRecordingsCard";

const getUnfinishedCaptures = vi.hoisted(() => vi.fn());
const getRecordingInfo = vi.hoisted(() => vi.fn());

vi.mock("@/lib/api", async (importOriginal) => ({
  ...await importOriginal<typeof import("@/lib/api")>(),
  getUnfinishedCaptures,
  getRecordingInfo,
}));
vi.mock("next/navigation", () => ({
  usePathname: () => "/",
  useSearchParams: () => new URLSearchParams(),
}));
vi.mock("next/link", () => ({
  default: ({ children, ...props }: ComponentPropsWithoutRef<"a">) => <a {...props}>{children}</a>,
}));

const FIREFOX = "Mozilla/5.0 (X11; Linux x86_64; rv:140.0) Gecko/20100101 Firefox/140.0";

// A capture another browser is running, as the recordings list returns it.
const live = (clientStatus: ClientStatus): Recording => ({
  id: "rec-live",
  name: "Weekly sync",
  status: RecordingStatus.UPLOADING,
  client_status: clientStatus,
  created_at: "2026-10-29T09:00:00Z",
  updated_at: "2026-10-29T09:00:00Z",
  meeting_uid: "weekly",
  audio_path: "/audio.wav",
  is_archived: false,
  is_deleted: false,
});

function Support() {
  const { support } = useCapture();
  return <output data-testid="support">{support.supported ? "supported" : "unsupported"}</output>;
}

const surfaces: [string, (recording: Recording) => ReactElement][] = [
  ["recent recordings", (recording) => <RecentRecordingsCard recordings={[recording]} timeZone="UTC" />],
  ["processing", (recording) => <ProcessingCard recordings={[recording]} />],
];

beforeEach(() => {
  vi.stubGlobal("navigator", {
    userAgent: FIREFOX,
    mediaDevices: { getUserMedia: vi.fn(), getDisplayMedia: vi.fn() },
    locks: { query: async () => ({ held: [], pending: [] }) },
  });
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe.each(surfaces)("a live capture on the %s surface, in a browser that cannot capture", (_name, surface) => {
  it.each([
    // Nothing records in this browser, so it is "Live" here.
    [ClientStatus.RECORDING, "Live"],
    [ClientStatus.UPLOADING, "Uploading"],
  ])("labels client status %s as %s", async (clientStatus, label) => {
    const recording = live(clientStatus);
    getUnfinishedCaptures.mockResolvedValue([recording]);
    render(<CaptureProvider><Support />{surface(recording)}</CaptureProvider>);
    await act(async () => {});

    expect(screen.getByTestId("support")).toHaveTextContent("unsupported");
    expect(screen.getByText(label)).toBeInTheDocument();
    expect(screen.queryByText(label === "Live" ? "Uploading" : "Live")).not.toBeInTheDocument();
    expect(screen.queryByText("Recording")).not.toBeInTheDocument();
  });
});

describe("a live capture in the recording info, in a browser that cannot capture", () => {
  it("names its state Live rather than the raw UPLOADING", async () => {
    const recording = live(ClientStatus.RECORDING);
    getUnfinishedCaptures.mockResolvedValue([recording]);
    getRecordingInfo.mockResolvedValue({});
    render(<CaptureProvider><Support />
      <RecordingInfoModal isOpen onClose={() => {}} recording={recording} />
    </CaptureProvider>);
    await act(async () => {});

    expect(screen.getByTestId("support")).toHaveTextContent("unsupported");
    expect(screen.getByText("Live")).toBeInTheDocument();
    expect(screen.queryByText("UPLOADING")).not.toBeInTheDocument();
  });
});

describe("a live capture on the agenda, in a browser that cannot capture", () => {
  it("reads Live rather than the raw UPLOADING", async () => {
    // As the calendar dashboard endpoint returns it.
    const recording: CalendarDashboardRecording = {
      id: "rec-live",
      name: "Weekly sync",
      starts_at: "2026-10-29T09:00:00Z",
      status: RecordingStatus.UPLOADING,
      client_status: ClientStatus.RECORDING,
      speaker_names: [],
      tags: [],
    };
    getUnfinishedCaptures.mockResolvedValue([]);
    render(<CaptureProvider><Support />
      <DashboardRecordingCard recording={recording} timeZone="UTC" showDate />
    </CaptureProvider>);
    await act(async () => {});

    expect(screen.getByTestId("support")).toHaveTextContent("unsupported");
    expect(screen.getByText("Live")).toBeInTheDocument();
    expect(screen.queryByText("UPLOADING")).not.toBeInTheDocument();
  });
});
