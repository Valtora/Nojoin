import { afterEach, describe, expect, it, vi } from "vitest";

import { fireEvent, renderWithProviders, screen, waitFor } from "@/test/renderWithProviders";

import LoginPage from "./page";

const getLoginMethods = vi.fn();

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn() }),
}));

vi.mock("@/lib/api", () => ({
  login: vi.fn(),
  getCurrentUser: () => Promise.reject(new Error("signed out")),
  getLoginMethods: () => getLoginMethods(),
  ssoSignInUrl: (next: string) => `https://nojoin.test/api/v1/login/oidc/start?next=${next}`,
  ssoErrorMessage: (code: string) => `message for ${code}`,
}));

afterEach(() => {
  vi.clearAllMocks();
  window.history.pushState({}, "", "/");
});

function methods(policy: "enabled" | "owner_only" | "disabled", sso: boolean) {
  return {
    password: policy !== "disabled",
    password_policy: policy,
    oidc: { enabled: sso, display_name: sso ? "Authentik" : null },
  };
}

describe("login page", () => {
  it("shows only the password form without SSO", async () => {
    getLoginMethods.mockResolvedValue(methods("enabled", false));
    renderWithProviders(<LoginPage />);
    await waitFor(() => expect(getLoginMethods).toHaveBeenCalled());
    expect(screen.getByPlaceholderText("Password")).toBeTruthy();
    expect(screen.queryByText(/sign in with authentik/i)).toBeNull();
  });

  it("links to the provider and tucks the owner's password form away", async () => {
    getLoginMethods.mockResolvedValue(methods("owner_only", true));
    renderWithProviders(<LoginPage />);

    const link = await screen.findByRole("link", { name: /sign in with authentik/i });
    expect(link.getAttribute("href")).toBe("https://nojoin.test/api/v1/login/oidc/start?next=/");
    expect(screen.queryByPlaceholderText("Password")).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: /local password/i }));
    expect(screen.getByPlaceholderText("Password")).toBeTruthy();
  });

  it("offers no password form when password sign-in is off", async () => {
    getLoginMethods.mockResolvedValue(methods("disabled", true));
    renderWithProviders(<LoginPage />);
    await screen.findByRole("link", { name: /sign in with authentik/i });
    expect(screen.queryByPlaceholderText("Password")).toBeNull();
    expect(screen.queryByRole("button", { name: /local password/i })).toBeNull();
  });

  it("explains an SSO error the callback sent back", async () => {
    window.history.pushState({}, "", "/login?error=sso_not_allowed");
    getLoginMethods.mockResolvedValue(methods("disabled", true));
    renderWithProviders(<LoginPage />);
    expect(await screen.findByText("message for sso_not_allowed")).toBeTruthy();
  });
});
