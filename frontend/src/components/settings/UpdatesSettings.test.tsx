import { beforeEach, describe, expect, it, vi } from "vitest";
import { act, fireEvent, render, screen } from "@testing-library/react";

import type { ReleaseInfo, VersionInfo } from "@/types";

const context = { isAdmin: false };
vi.mock("./SettingsProvider", () => ({
  useSettingsContext: () => context,
}));

const getVersion = vi.fn();
vi.mock("@/lib/api", () => ({
  getVersion: (...args: unknown[]) => getVersion(...args),
}));

import UpdatesSettings from "./UpdatesSettings";

const body = (version: string) =>
  [
    `## Nojoin ${version}`,
    "",
    "Container images for this release.",
    "",
    `- \`ghcr.io/example/api@sha256:${version}\``,
    "",
    "### Highlights",
    "",
    "<!-- Maintainer: one bullet per item. -->",
    "",
    `- **Headline ${version}.** What changed.`,
  ].join("\n");

const release = (version: string): ReleaseInfo => ({
  version,
  tag_name: `v${version}`,
  name: null,
  html_url: `https://example.test/v${version}`,
  published_at: "2026-10-15T00:00:00Z",
  body: body(version),
  draft: false,
  prerelease: false,
  assets: [],
});

const versionInfo: VersionInfo = {
  current_version: "9.2.0",
  latest_version: "9.2.0",
  is_update_available: false,
  update_status: "current",
  release_url: null,
  latest_published_at: "2026-10-15T00:00:00Z",
  releases: [release("9.2.0"), release("9.1.0")],
};

beforeEach(() => {
  context.isAdmin = false;
  getVersion.mockResolvedValue(versionInfo);
});

describe("UpdatesSettings", () => {
  // Chrome moves focus from a disabled button to the page body, so the button
  // stays enabled while it checks and ignores clicks instead (#614).
  it("keeps focus on Check for updates while it checks", async () => {
    render(<UpdatesSettings />);
    const button = await screen.findByRole("button", { name: "Check for updates" });
    let answer: (value: VersionInfo) => void = () => undefined;
    getVersion.mockImplementationOnce(
      () => new Promise<VersionInfo>((resolve) => (answer = resolve)),
    );
    button.focus();

    fireEvent.click(button);

    const checking = await screen.findByRole("button", { name: "Checking..." });
    expect(checking).toBe(button);
    expect(button).toBeEnabled();
    expect(button).toHaveAttribute("aria-disabled", "true");
    expect(button).toHaveFocus();
    fireEvent.click(button);
    expect(getVersion).toHaveBeenCalledTimes(2);

    await act(async () => answer(versionInfo));
    expect(button).toHaveAccessibleName("Check for updates");
    expect(button).toHaveFocus();
  });

  it("renders release notes as formatted text, without the maintainer's comments", async () => {
    render(<UpdatesSettings />);

    const headline = await screen.findByText("Headline 9.2.0.");
    expect(headline.tagName).toBe("STRONG");
    expect(screen.queryByText(/Maintainer/)).toBeNull();
    expect(screen.queryByText(/\*\*/)).toBeNull();
  });

  it("keeps older releases collapsed until opened", async () => {
    render(<UpdatesSettings />);

    const older = await screen.findByRole("button", { name: /v9\.1\.0/ });
    expect(older.getAttribute("aria-expanded")).toBe("false");
    expect(screen.queryByText("Headline 9.1.0.")).toBeNull();

    fireEvent.click(older);

    expect(await screen.findByText("Headline 9.1.0.")).toBeTruthy();
  });

  it("folds the image digests away from the notes", async () => {
    render(<UpdatesSettings />);

    await screen.findByText("Headline 9.2.0.");
    expect(screen.queryByText(/sha256:9\.2\.0/)).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: "Container images" }));

    expect(await screen.findByText(/sha256:9\.2\.0/)).toBeTruthy();
  });

  it("shows the upgrade commands to administrators only", async () => {
    const { unmount } = render(<UpdatesSettings />);
    await screen.findByText("Headline 9.2.0.");
    expect(screen.queryByText("How to upgrade")).toBeNull();
    unmount();

    context.isAdmin = true;
    render(<UpdatesSettings />);

    expect(await screen.findByText("How to upgrade")).toBeTruthy();
  });
});
