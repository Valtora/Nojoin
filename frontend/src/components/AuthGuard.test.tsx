import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { renderWithProviders, waitFor } from "@/test/renderWithProviders";

import AuthGuard from "./AuthGuard";

const routerPush = vi.fn();
const getCurrentUser = vi.fn();
let pathname = "/";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: routerPush }),
  usePathname: () => pathname,
}));

vi.mock("@/lib/api", () => ({
  getCurrentUser: (...args: unknown[]) => getCurrentUser(...args),
}));

function visit(path: string) {
  const url = new URL(path, window.location.origin);
  pathname = url.pathname;
  window.history.pushState({}, "", path);
}

beforeEach(() => {
  getCurrentUser.mockResolvedValue({
    username: "priya",
    force_password_change: true,
  });
});

afterEach(() => {
  vi.clearAllMocks();
  window.history.pushState({}, "", "/");
});

describe("AuthGuard with a pending password change", () => {
  it("keeps the consent URL so the change can return to it", async () => {
    const consent =
      "/oauth/authorize?client_id=abc&redirect_uri=https%3A%2F%2Fclaude.ai%2Fcb&state=s1";
    visit(consent);

    renderWithProviders(<AuthGuard>content</AuthGuard>);

    await waitFor(() =>
      expect(routerPush).toHaveBeenCalledWith(
        `/settings/profile?next=${encodeURIComponent(consent)}`,
      ),
    );
  });

  it("sends any other page to the plain password change", async () => {
    visit("/recordings?view=list");

    renderWithProviders(<AuthGuard>content</AuthGuard>);

    await waitFor(() =>
      expect(routerPush).toHaveBeenCalledWith("/settings/profile"),
    );
  });
});
