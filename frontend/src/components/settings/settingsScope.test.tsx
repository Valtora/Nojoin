import { readFileSync, readdirSync } from "node:fs";
import path from "node:path";

import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import SettingsBlock from "./SettingsBlock";
import SettingsCard from "./SettingsCard";
import SettingsRow from "./SettingsRow";
import { SETTINGS_CATEGORIES } from "./settingsCategories";
import { SETTINGS_REGISTRY } from "./settingsRegistry";

/**
 * Scope badges come from the registry: a card, row or block shows the scope of
 * the entry its id names. These tests keep the declarations honest and check
 * that each one reaches the page.
 */
const scoped = SETTINGS_REGISTRY.filter((entry) => entry.scope);

function settingsSource(): string {
  return readdirSync(__dirname)
    .filter((name) => name.endsWith(".tsx") && !/\.test\.tsx?$/.test(name))
    .map((name) => readFileSync(path.join(__dirname, name), "utf8"))
    .join("\n");
}

describe("settings scope declarations", () => {
  it("marks some settings, so the badges are in use", () => {
    expect(scoped.length).toBeGreaterThan(5);
  });

  it("leaves admin-only pages unmarked, since everything there is install-wide", () => {
    const onAdminPages = scoped
      .filter((entry) => SETTINGS_CATEGORIES[entry.category].access !== "all")
      .map((entry) => entry.id);

    expect(onAdminPages).toEqual([]);
  });

  it("keeps browser-only settings off the account's Settings object", () => {
    const stored = scoped
      .filter((entry) => entry.scope === "browser" && entry.settingsKeys?.length)
      .map((entry) => entry.id);

    expect(stored).toEqual([]);
  });

  it("renders every scoped entry on a card, row or block", () => {
    const source = settingsSource();
    const unrendered = scoped
      .filter(
        (entry) =>
          !source.includes(`id="${entry.id}"`) && !source.includes(`scopeId="${entry.id}"`),
      )
      .map((entry) => entry.id);

    expect(unrendered).toEqual([]);
  });
});

describe("scope badges", () => {
  it("puts a shared scope on the card title and not on its rows", () => {
    render(
      <SettingsCard id="recording-input" title="Input">
        <SettingsRow id="recording-microphone" label="Microphone" />
        <SettingsRow id="recording-microphone-gain" label="Microphone gain" />
      </SettingsCard>,
    );

    expect(screen.getAllByText("This browser")).toHaveLength(1);
  });

  it("marks a scoped block inside a card with no scope of its own", () => {
    render(
      <SettingsCard id="transcription-glossary" title="Glossary">
        <SettingsBlock id="transcription-shared-glossary" label="Shared glossary">
          <p>No shared terms yet.</p>
        </SettingsBlock>
        <SettingsBlock label="Your glossary">
          <p>Terms</p>
        </SettingsBlock>
      </SettingsCard>,
    );

    expect(screen.getAllByText("Everyone")).toHaveLength(1);
  });

  it("leaves a per-account setting unmarked", () => {
    render(
      <SettingsCard id="profile-username" title="Profile">
        <SettingsRow label="Username" />
      </SettingsCard>,
    );

    expect(screen.queryByText("Everyone")).toBeNull();
    expect(screen.queryByText("This browser")).toBeNull();
  });

  it("lets a card show the scope of an entry it renders under another id", () => {
    render(
      <SettingsCard
        id="integrations-calendar-google"
        scopeId="integrations-calendar-providers"
        title="Google calendar sign-in"
      >
        <SettingsRow label="Client ID" />
      </SettingsCard>,
    );

    expect(screen.getAllByText("Everyone")).toHaveLength(1);
  });
});
