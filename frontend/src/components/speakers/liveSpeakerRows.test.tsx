import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import SpeakerPanel from "../SpeakerPanel";
import { line, panelProps, person, speaker } from "../transcript/transcriptTestFixtures";

const mocks = vi.hoisted(() => ({ promote: vi.fn(), rename: vi.fn() }));
vi.mock("@/lib/api", () => ({
  getMergedRecordingSpeakers: vi.fn(async () => []),
  promoteToGlobalSpeaker: mocks.promote,
}));
vi.mock("../VoiceprintModal", () => ({ default: () => null }));
vi.mock("../people/SplitPersonModal", () => ({ default: () => null }));

const anchor = line({ speaker: "LIVE_PROVISIONAL_1", settling_required: true, locked: true });
const props = () => ({ ...panelProps(), speakers: [], segments: [anchor],
  liveSpeakerNames: { LIVE_PROVISIONAL_1: "Alex" }, onRenameLiveSpeaker: mocks.rename });

describe("live speaker rows", () => {
  beforeEach(() => { mocks.rename.mockReset().mockResolvedValue(true); mocks.promote.mockReset(); });

  it("changes identity once a line locks and never offers fabricated pending suggestions", async () => {
    render(<SpeakerPanel {...props()} globalSpeakers={[person(9, "Robin")]} />);
    expect(screen.queryByText("No speakers detected.")).toBeNull();
    expect(screen.queryByRole("button", { name: "Confirm Alex" })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Change identity for Alex" }));
    fireEvent.click(screen.getByRole("button", { name: "Robin" }));
    await waitFor(() => expect(mocks.rename).toHaveBeenCalledWith("LIVE_PROVISIONAL_1", "Robin", false));
    expect(mocks.promote).not.toHaveBeenCalled();
  });

  it("adds a live name to People in one request", async () => {
    render(<SpeakerPanel {...props()} />);
    fireEvent.click(screen.getByRole("button", { name: "Add to People" }));
    await waitFor(() => expect(mocks.rename).toHaveBeenCalledWith("LIVE_PROVISIONAL_1", "Alex", true));
    expect(mocks.promote).not.toHaveBeenCalled();
  });

  it.each(["closed", "read-only"])("keeps %s live labels visible with mutations unavailable", (kind) => {
    const options = props();
    render(<SpeakerPanel {...options} onRenameLiveSpeaker={kind === "closed" ? undefined : mocks.rename}
      disableEditing={kind === "read-only"} />);
    expect(screen.getByRole("button", { name: "Alex" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Change identity for Alex" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Add to People" })).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "Add to People" }));
    expect(mocks.rename).not.toHaveBeenCalled();
  });

  it("names ordinary live labels and adds them to People without enabling word settling", async () => {
    const ordinary = { ...anchor, settling_required: false };
    render(<SpeakerPanel {...props()} segments={[ordinary]} globalSpeakers={[person(9, "Robin")]} />);
    fireEvent.click(screen.getByRole("button", { name: "Change identity for Alex" }));
    fireEvent.click(screen.getByRole("button", { name: "Robin" }));
    await waitFor(() => expect(mocks.rename).toHaveBeenCalledWith("LIVE_PROVISIONAL_1", "Robin", false));
    fireEvent.click(screen.getByRole("button", { name: "Add to People" }));
    await waitFor(() => expect(mocks.rename).toHaveBeenLastCalledWith("LIVE_PROVISIONAL_1", "Alex", true));
  });

  it("keeps the identity draft after refusal and feed changes", async () => {
    mocks.rename.mockResolvedValue(false);
    const { rerender } = render(<SpeakerPanel {...props()} />);
    fireEvent.click(screen.getByRole("button", { name: "Change identity for Alex" }));
    const input = screen.getByPlaceholderText("Search or add...");
    fireEvent.change(input, { target: { value: "Robin" } });
    fireEvent.keyDown(input, { key: "Enter" });
    await waitFor(() => expect(mocks.rename).toHaveBeenCalledTimes(1));
    expect(input).toHaveValue("Robin");
    const newer = { ...anchor, revision: 8 };
    rerender(<SpeakerPanel {...props()} segments={[newer]} />);
    fireEvent.keyDown(input, { key: "Enter" });
    await waitFor(() => expect(mocks.rename).toHaveBeenLastCalledWith("LIVE_PROVISIONAL_1", "Robin", false));
  });

  it("requires a real name before adding a live label to People", () => {
    render(<SpeakerPanel {...props()} liveSpeakerNames={{}} />);
    expect(screen.getByRole("button", { name: "Change identity for Speaker 1" })).toBeEnabled();
    const add = screen.getByRole("button", { name: "Add to People" });
    expect(add).toBeDisabled();
    expect(add).toHaveAttribute("title", "Choose a name with Change identity before adding to People.");
    fireEvent.click(add);
    expect(mocks.rename).not.toHaveBeenCalled();
  });

  it("does not duplicate live labels with actual speaker records", () => {
    render(<SpeakerPanel {...props()} speakers={[speaker("LIVE_PROVISIONAL_1", "Alex")]} />);
    expect(screen.getAllByRole("button", { name: "Change identity for Alex" })).toHaveLength(1);
  });
});
