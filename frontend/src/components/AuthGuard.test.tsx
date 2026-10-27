import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { holdSignInInPlace, resetSessionForTests } from "@/lib/session";
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

describe("AuthGuard when already signed in", () => {
  beforeEach(() => {
    getCurrentUser.mockResolvedValue({
      username: "alice",
      force_password_change: false,
    });
  });

  it("leaves /setup to the setup page, which can resume its wizard", async () => {
    visit("/setup");

    const { findByText } = renderWithProviders(<AuthGuard>wizard</AuthGuard>);

    expect(await findByText("wizard")).toBeInTheDocument();
    expect(routerPush).not.toHaveBeenCalled();
  });

  it("still sends the sign-in page on to the dashboard", async () => {
    visit("/login");

    renderWithProviders(<AuthGuard>sign in</AuthGuard>);

    await waitFor(() => expect(routerPush).toHaveBeenCalledWith("/"));
  });

  it("still sends /setup to a pending password change", async () => {
    getCurrentUser.mockResolvedValue({
      username: "alice",
      force_password_change: true,
    });
    visit("/setup");

    renderWithProviders(<AuthGuard>wizard</AuthGuard>);

    await waitFor(() =>
      expect(routerPush).toHaveBeenCalledWith("/settings/profile"),
    );
  });
});

describe("AuthGuard once the session has ended", () => {
  const refused = () =>
    Object.assign(new Error("Request failed with status code 401"), {
      response: { status: 401, data: { detail: "Not authenticated" } },
    });

  beforeEach(() => {
    resetSessionForTests();
    getCurrentUser.mockRejectedValue(refused());
  });

  afterEach(() => {
    resetSessionForTests();
  });

  it("sends the page to sign in", async () => {
    visit("/recordings/rec-1");

    renderWithProviders(<AuthGuard>content</AuthGuard>);

    await waitFor(() => expect(routerPush).toHaveBeenCalledWith("/login"));
  });

  it("leaves a running capture's page where it is", async () => {
    visit("/recordings/rec-1");
    holdSignInInPlace(true);

    renderWithProviders(<AuthGuard>content</AuthGuard>);

    await waitFor(() => expect(getCurrentUser).toHaveBeenCalled());
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(routerPush).not.toHaveBeenCalled();
  });
});
