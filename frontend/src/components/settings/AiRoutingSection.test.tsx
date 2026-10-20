import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";

import type { Settings } from "@/types";

vi.mock("./CliOAuthPanel", () => ({ default: () => null }));
vi.mock("@/lib/api/cliOauth", () => ({
  getClaudeCliModels: vi.fn(async () => ({ models: [] })),
  getCodexModels: vi.fn(async () => ({ models: [] })),
  refreshCodexModels: vi.fn(),
}));

import AiRoutingSection from "./AiRoutingSection";

const noModel = { llm_provider: "ollama", ollama_api_url: "http://ollama.test" };

const renderFor = (isAdmin: boolean, settings: Partial<Settings> = noModel) =>
  render(
    <AiRoutingSection
      settings={settings as Settings}
      onPersist={vi.fn()}
      isAdmin={isAdmin}
    />,
  );

describe("AiRoutingSection server provider summary", () => {
  it("links an administrator to the AI providers page", () => {
    renderFor(true);

    const link = screen.getByRole("link", { name: "Change it under AI providers" });
    expect(link.getAttribute("href")).toBe("/settings/ai-providers");
    expect(screen.queryByText(/section below/)).toBeNull();
  });

  it("tells a user that no model has been chosen yet", () => {
    renderFor(false);

    expect(screen.getByText(/has not chosen a model yet/)).toBeTruthy();
    expect(screen.queryByRole("link", { name: /AI providers/ })).toBeNull();
  });
});
