import { afterEach, describe, expect, it, vi } from "vitest";

import { fireEvent, renderWithProviders, waitFor } from "@/test/renderWithProviders";

import AccountSettings from "./AccountSettings";

const routerPush = vi.fn();
const updatePasswordMe = vi.fn();
const getCurrentUser = vi.fn();
const getLoginMethods = vi.fn();

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: routerPush }),
}));

vi.mock("@/lib/api", () => ({
  updatePasswordMe: (...args: unknown[]) => updatePasswordMe(...args),
  updateUserMe: vi.fn(),
  getCurrentUser: (...args: unknown[]) => getCurrentUser(...args),
  getLoginMethods: (...args: unknown[]) => getLoginMethods(...args),
  startSsoLink: vi.fn(),
  ssoErrorMessage: (code: string) => `error ${code}`,
}));

const LOCAL_USER = {
  id: 1,
  username: "job",
  is_active: true,
  is_superuser: true,
  role: "owner",
  force_password_change: false,
  auth_source: "local",
  oidc_linked: false,
  oidc_groups: [],
};
const SSO_METHODS = {
  password: true,
  password_policy: "owner_only",
  oidc: { enabled: true, display_name: "Authentik" },
};

const CONSENT =
  "/oauth/authorize?client_id=abc&redirect_uri=https%3A%2F%2Fclaude.ai%2Fcb&state=s1";

function changePassword(container: HTMLElement) {
  const fill = (id: string, value: string) => {
    const input = container.querySelector<HTMLInputElement>(`#${id}`);
    if (!input) {
      throw new Error(`missing #${id}`);
    }
    fireEvent.change(input, { target: { value } });
  };
  fill("account-current-password", "temporary-password");
  fill("account-new-password", "a-new-password");
  fill("account-confirm-password", "a-new-password");
  const form = container.querySelector("#account-password-form");
  if (!form) {
    throw new Error("missing password form");
  }
  fireEvent.submit(form);
}

function renderForcedChange(search: string) {
  getCurrentUser.mockResolvedValue(LOCAL_USER);
  getLoginMethods.mockResolvedValue({
    password: true,
    password_policy: "enabled",
    oidc: { enabled: false, display_name: null },
  });
  window.history.pushState({}, "", `/settings/profile${search}`);
  updatePasswordMe.mockResolvedValue(undefined);
  return renderWithProviders(
    <AccountSettings
      forcePasswordChange
      initialUsername={null}
      includeCalendarConnections={false}
    />,
  );
}

afterEach(() => {
  vi.clearAllMocks();
  window.history.pushState({}, "", "/");
});

describe("AccountSettings password card", () => {
  it("warns that a new password disconnects connected apps", () => {
    const { container } = renderForcedChange("");

    // The server revokes every connector grant when the password changes.
    expect(container.querySelector("#profile-password")?.textContent).toContain(
      "disconnects your connected apps",
    );
  });
});

describe("AccountSettings after a forced password change", () => {
  it("returns to the OAuth consent page it was sent from", async () => {
    const { container } = renderForcedChange(
      `?next=${encodeURIComponent(CONSENT)}`,
    );

    changePassword(container);

    await waitFor(() => expect(routerPush).toHaveBeenCalledWith(CONSENT));
  });

  it("goes home when no consent page is waiting", async () => {
    const { container } = renderForcedChange("");

    changePassword(container);

    await waitFor(() => expect(routerPush).toHaveBeenCalledWith("/"));
  });

  it("goes home rather than to another origin", async () => {
    const { container } = renderForcedChange(
      `?next=${encodeURIComponent("//evil.example/oauth/authorize")}`,
    );

    changePassword(container);

    await waitFor(() => expect(routerPush).toHaveBeenCalledWith("/"));
  });
});

describe("AccountSettings single sign-on", () => {
  function renderProfile(user: Record<string, unknown>, search = "") {
    getCurrentUser.mockResolvedValue(user);
    getLoginMethods.mockResolvedValue(SSO_METHODS);
    window.history.pushState({}, "", `/settings/profile${search}`);
    return renderWithProviders(
      <AccountSettings initialUsername="job" includeCalendarConnections={false} />,
    );
  }

  it("offers to link a local account", async () => {
    const { findByRole, container } = renderProfile(LOCAL_USER);
    expect(await findByRole("button", { name: /link authentik/i })).toBeTruthy();
    expect(container.querySelector("#profile-password")).not.toBeNull();
  });

  it("hides the password card for an account the provider created", async () => {
    const { findByText, container } = renderProfile({
      ...LOCAL_USER,
      auth_source: "oidc",
      oidc_linked: true,
      display_name: "Job Doesburg",
      oidc_groups: ["nojoin-users", "research"],
    });
    expect(await findByText("nojoin-users, research")).toBeTruthy();
    expect(container.querySelector("#profile-password")).toBeNull();
  });

  it("reports the result of a link that just came back", async () => {
    const { findByText } = renderProfile(LOCAL_USER, "?sso_error=sso_identity_in_use");
    expect(await findByText("error sso_identity_in_use")).toBeTruthy();
  });
});
