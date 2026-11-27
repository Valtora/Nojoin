import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import SettingsShell from "./SettingsShell";

const replace = vi.fn();
let pathname = "/settings/backup";

vi.mock("next/navigation", () => ({
  usePathname: () => pathname,
  useRouter: () => ({ replace, push: vi.fn() }),
}));

vi.mock("./VersionTag", () => ({ default: () => null }));

const context = {
  viewer: { isAdmin: true, isSuperuser: false },
  loading: false,
  forcePasswordChange: false,
  autosave: { status: "idle" },
};

vi.mock("./SettingsProvider", () => ({
  useSettingsContext: () => context,
  SettingsAutosaveFooter: () => null,
}));

const renderAt = (path: string, viewer: typeof context.viewer) => {
  pathname = path;
  context.viewer = viewer;
  render(
    <SettingsShell>
      <p>Category page</p>
    </SettingsShell>,
  );
};

describe("SettingsShell", () => {
  beforeEach(() => {
    replace.mockClear();
  });

  it("sends an administrator away from Backup and restore, which only the superuser can use", () => {
    renderAt("/settings/backup", { isAdmin: true, isSuperuser: false });

    expect(replace).toHaveBeenCalledWith("/settings");
    // Not rendered on the way out, so it sends no request the API refuses.
    expect(screen.queryByText("Category page")).not.toBeInTheDocument();
  });

  it("sends a user away from an administrator category", () => {
    renderAt("/settings/users", { isAdmin: false, isSuperuser: false });

    expect(replace).toHaveBeenCalledWith("/settings");
  });

  it("opens Backup and restore for the superuser", () => {
    renderAt("/settings/backup", { isAdmin: true, isSuperuser: true });

    expect(replace).not.toHaveBeenCalled();
    expect(screen.getByText("Category page")).toBeInTheDocument();
  });

  it("contains the category's positioned content in the column that scrolls it", () => {
    renderAt("/settings/appearance", { isAdmin: true, isSuperuser: false });

    // An absolute element, such as screen-reader-only text, resolves against
    // its nearest positioned ancestor. If that is <main> rather than this
    // column, the column does not scroll it and <main> grows past the last card.
    const column = screen.getByText("Category page").parentElement;
    expect(column).toHaveClass("overflow-y-auto");
    expect(column).toHaveClass("relative");
  });

  it("leaves categories an administrator can see alone", () => {
    renderAt("/settings/system", { isAdmin: true, isSuperuser: false });

    expect(replace).not.toHaveBeenCalled();
    expect(screen.getByText("Category page")).toBeInTheDocument();
  });
});
