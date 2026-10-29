import { beforeEach, describe, expect, it } from "vitest";

import { useNavigationStore } from "./store";

describe("useNavigationStore collapse state", () => {
  beforeEach(() => {
    useNavigationStore.setState({
      isRecordingsSidebarCollapsed: false,
      isSpeakerPanelCollapsed: false,
      isChatPanelCollapsed: false,
    });
  });

  it("defaults every collapse flag to expanded", () => {
    const state = useNavigationStore.getState();
    expect(state.isRecordingsSidebarCollapsed).toBe(false);
    expect(state.isSpeakerPanelCollapsed).toBe(false);
    expect(state.isChatPanelCollapsed).toBe(false);
  });

  it("toggles the recordings sidebar collapse", () => {
    useNavigationStore.getState().toggleRecordingsSidebarCollapse();
    expect(
      useNavigationStore.getState().isRecordingsSidebarCollapsed,
    ).toBe(true);
    useNavigationStore.getState().toggleRecordingsSidebarCollapse();
    expect(
      useNavigationStore.getState().isRecordingsSidebarCollapsed,
    ).toBe(false);
  });

  it("sets the side panels independently", () => {
    useNavigationStore.getState().setSpeakerPanelCollapsed(true);
    expect(useNavigationStore.getState().isSpeakerPanelCollapsed).toBe(true);
    expect(useNavigationStore.getState().isChatPanelCollapsed).toBe(false);

    useNavigationStore.getState().setChatPanelCollapsed(true);
    expect(useNavigationStore.getState().isChatPanelCollapsed).toBe(true);

    useNavigationStore.getState().setSpeakerPanelCollapsed(false);
    expect(useNavigationStore.getState().isSpeakerPanelCollapsed).toBe(false);
    expect(useNavigationStore.getState().isChatPanelCollapsed).toBe(true);
  });

  it("persists all three collapse flags", () => {
    // The partialize whitelist is what survives a reload; a flag missing
    // from it would silently reset on every visit.
    const persisted = useNavigationStore.persist.getOptions().partialize?.(
      useNavigationStore.getState(),
    ) as Record<string, unknown>;

    expect(persisted).toHaveProperty("isRecordingsSidebarCollapsed");
    expect(persisted).toHaveProperty("isSpeakerPanelCollapsed");
    expect(persisted).toHaveProperty("isChatPanelCollapsed");
  });

  it("restores the selected Meeting Edge desktop and mobile tabs after storage hydration", async () => {
    useNavigationStore.getState().setSideTab("meeting-edge");
    useNavigationStore.getState().setActivePanel("meeting-edge");
    const saved = localStorage.getItem("navigation-storage");
    expect(saved).not.toBeNull();
    expect(JSON.parse(saved!).state).toMatchObject({ sideTab: "meeting-edge", activePanel: "meeting-edge" });
    useNavigationStore.setState({ sideTab: "speakers", activePanel: "transcript" });
    localStorage.setItem("navigation-storage", saved!);
    await useNavigationStore.persist.rehydrate();
    expect(useNavigationStore.getState()).toMatchObject({ sideTab: "meeting-edge", activePanel: "meeting-edge" });
  });
});
