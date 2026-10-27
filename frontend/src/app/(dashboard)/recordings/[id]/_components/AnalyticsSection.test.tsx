import { describe, expect, it, vi } from "vitest";
import { renderWithProviders, screen, waitFor } from "@/test/renderWithProviders";

const getRecordingAnalytics = vi.fn<(...args: unknown[]) => Promise<never>>(() => new Promise(() => {}));
vi.mock("@/lib/api", () => ({
  getRecordingAnalytics: (...args: unknown[]) => getRecordingAnalytics(...args),
  generateRecordingAnalytics: vi.fn(),
  generateRecordingAiAnalytics: vi.fn(),
}));

import AnalyticsSection from "./AnalyticsSection";

describe("AnalyticsSection in-flight guard", () => {
  it("does not request final analytics from an active tab until processing finishes", async () => {
    const view = renderWithProviders(
      <AnalyticsSection active inFlight recordingId="rec-1" speakerColors={{}} />,
    );

    expect(screen.getByText("Analytics will be available once processing finishes.")).toBeInTheDocument();
    expect(getRecordingAnalytics).not.toHaveBeenCalled();

    view.rerender(<AnalyticsSection active recordingId="rec-1" speakerColors={{}} />);
    await waitFor(() => expect(getRecordingAnalytics).toHaveBeenCalledWith("rec-1"));
  });
});
