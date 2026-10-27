import { afterEach, describe, expect, it, vi } from "vitest";
import { act, fireEvent, render, screen } from "@testing-library/react";

vi.mock("./SettingsProvider", () => ({
  useSettingsContext: () => ({
    settings: {},
    isAdmin: true,
    viewer: { isAdmin: true, isSuperuser: true },
  }),
}));

import SettingsAdvancedSection from "./SettingsAdvancedSection";

const followLink = (hash: string) =>
  act(() => {
    window.history.replaceState(null, "", hash);
    window.dispatchEvent(new HashChangeEvent("hashchange"));
  });

afterEach(() => {
  window.history.replaceState(null, "", "#");
});

describe("SettingsAdvancedSection", () => {
  it.each([
    "transcription-live-settling",
    "transcription-live-settling-k",
    "transcription-live-settling-window",
  ])("opens the live settling controls from a search link to %s", async (id) => {
    render(
      <SettingsAdvancedSection categoryId="transcription">
        <p>Live settling controls</p>
      </SettingsAdvancedSection>,
    );
    expect(screen.queryByText("Live settling controls")).toBeNull();
    followLink(`#${id}`);
    expect(await screen.findByText("Live settling controls")).toBeTruthy();
  });

  it("opens when an in-page link points inside, and again after being closed", async () => {
    render(
      <SettingsAdvancedSection categoryId="integrations">
        <p>Credentials</p>
      </SettingsAdvancedSection>,
    );
    expect(screen.queryByText("Credentials")).toBeNull();

    followLink("#integrations-calendar-providers");
    expect(await screen.findByText("Credentials")).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: /Advanced/ }));
    expect(screen.queryByText("Credentials")).toBeNull();

    followLink("#integrations-calendar-providers");
    expect(await screen.findByText("Credentials")).toBeTruthy();
  });

  it("ignores a link to a setting outside the block", () => {
    render(
      <SettingsAdvancedSection categoryId="integrations">
        <p>Credentials</p>
      </SettingsAdvancedSection>,
    );

    followLink("#integrations-calendars");

    expect(screen.queryByText("Credentials")).toBeNull();
  });
});
