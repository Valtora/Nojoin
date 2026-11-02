import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
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

/** Opens the speaker card's ⋯ menu and returns one of its items. */
const menuItem = (speakerName: string, item: string) => {
  fireEvent.click(screen.getByRole("button", { name: `Actions for ${speakerName}` }));
  return within(screen.getByRole("menu", { name: `Actions for ${speakerName}` }))
    .getByRole("menuitem", { name: item });
};

describe("live speaker rows", () => {
  beforeEach(() => { mocks.rename.mockReset().mockResolvedValue(true); mocks.promote.mockReset(); });

  it("changes identity once a line locks and never offers fabricated pending suggestions", async () => {
    render(<SpeakerPanel {...props()} globalSpeakers={[person(9, "Robin")]} />);
    expect(screen.queryByText("No speakers detected.")).toBeNull();
    expect(screen.queryByRole("button", { name: "Confirm Alex" })).toBeNull();
    fireEvent.click(menuItem("Alex", "Change identity"));
    // People are found by typing, not listed.
    expect(screen.queryByRole("button", { name: "Robin" })).toBeNull();
    fireEvent.change(screen.getByPlaceholderText("Search or add..."), { target: { value: "rob" } });
    fireEvent.click(screen.getByRole("button", { name: "Robin" }));
    await waitFor(() => expect(mocks.rename).toHaveBeenCalledWith("LIVE_PROVISIONAL_1", "Robin", false));
    expect(mocks.promote).not.toHaveBeenCalled();
  });

  it("adds a live name to People in one request", async () => {
    render(<SpeakerPanel {...props()} />);
    const add = menuItem("Alex", "Add to People");
    expect(screen.queryByText(/Choose a name|can be named here/)).toBeNull();
    expect(add).toHaveAttribute("aria-disabled", "false");
    fireEvent.click(add);
    await waitFor(() => expect(mocks.rename).toHaveBeenCalledWith("LIVE_PROVISIONAL_1", "Alex", true));
    expect(mocks.promote).not.toHaveBeenCalled();
  });

  it.each(["closed", "read-only"])("keeps %s live labels visible with mutations unavailable", (kind) => {
    const options = props();
    render(<SpeakerPanel {...options} onRenameLiveSpeaker={kind === "closed" ? undefined : mocks.rename}
      disableEditing={kind === "read-only"} />);
    expect(screen.getByRole("button", { name: "Alex" })).toBeInTheDocument();
    // The card is one row: the reason is said in the menu, not under the name.
    const reason = "Speakers can be named here while recording or paused, and again once processing finishes.";
    expect(screen.queryByText(reason)).toBeNull();
    const add = menuItem("Alex", "Add to People");
    const change = screen.getByRole("menuitem", { name: "Change identity" });
    // Naming closes at Stop and opens again on the final speakers.
    expect(screen.getAllByText(reason)).toHaveLength(1);
    for (const item of [add, change]) {
      expect(item).toHaveAttribute("aria-disabled", "true");
      expect(item).toHaveAccessibleDescription(reason);
    }
    fireEvent.click(add);
    fireEvent.click(change);
    expect(mocks.rename).not.toHaveBeenCalled();
    expect(screen.queryByPlaceholderText("Search or add...")).toBeNull();
  });

  it("names ordinary live labels and adds them to People without enabling word settling", async () => {
    const ordinary = { ...anchor, settling_required: false };
    render(<SpeakerPanel {...props()} segments={[ordinary]} globalSpeakers={[person(9, "Robin")]} />);
    fireEvent.click(menuItem("Alex", "Change identity"));
    fireEvent.change(screen.getByPlaceholderText("Search or add..."), { target: { value: "Robin" } });
    fireEvent.click(screen.getByRole("button", { name: "Robin" }));
    await waitFor(() => expect(mocks.rename).toHaveBeenCalledWith("LIVE_PROVISIONAL_1", "Robin", false));
    fireEvent.click(menuItem("Alex", "Add to People"));
    await waitFor(() => expect(mocks.rename).toHaveBeenLastCalledWith("LIVE_PROVISIONAL_1", "Alex", true));
  });

  it("keeps the identity draft after refusal and feed changes", async () => {
    mocks.rename.mockResolvedValue(false);
    const { rerender } = render(<SpeakerPanel {...props()} />);
    fireEvent.click(menuItem("Alex", "Change identity"));
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
    const reason = "Choose a name with Change identity before adding to People.";
    expect(screen.queryByText(reason)).toBeNull();
    const add = menuItem("Speaker 1", "Add to People");
    expect(screen.getByRole("menuitem", { name: "Change identity" })).toHaveAttribute("aria-disabled", "false");
    expect(add).toHaveAttribute("aria-disabled", "true");
    // Written in the menu, not only in a tooltip that touch and keyboard users never see.
    expect(screen.getByText(reason)).toBeVisible();
    expect(add).toHaveAccessibleDescription(reason);
    fireEvent.click(add);
    expect(mocks.rename).not.toHaveBeenCalled();
  });

  it("keeps a live card to one row, with its actions behind an always-shown menu button", () => {
    render(<SpeakerPanel {...props()} />);
    const trigger = screen.getByRole("button", { name: "Actions for Alex" });
    expect(trigger).toHaveAttribute("aria-haspopup", "menu");
    expect(trigger).toHaveClass("opacity-100");
    expect(screen.queryByRole("button", { name: /Change identity|Add to People/ })).toBeNull();
    expect(trigger.closest("[data-ui-fill='card']")?.children).toHaveLength(2);
  });

  it("does not duplicate live labels with actual speaker records", () => {
    render(<SpeakerPanel {...props()} speakers={[speaker("LIVE_PROVISIONAL_1", "Alex")]} />);
    expect(screen.getAllByRole("button", { name: "Actions for Alex" })).toHaveLength(1);
  });
});
