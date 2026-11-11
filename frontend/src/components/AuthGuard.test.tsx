import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  requireLegalAcceptance,
  resetLegalAcceptanceForTests,
  useLegalAcceptanceStore,
} from "@/lib/legalAcceptance";
import { holdSignInInPlace, resetSessionForTests } from "@/lib/session";
import {
  act,
  fireEvent,
  renderWithProviders,
  waitFor,
} from "@/test/renderWithProviders";

import AuthGuard from "./AuthGuard";

const routerPush = vi.fn();
const getCurrentUser = vi.fn();
const acceptLegalTerms = vi.fn();
let pathname = "/";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: routerPush }),
  usePathname: () => pathname,
}));

vi.mock("@/lib/api", () => ({
  getCurrentUser: (...args: unknown[]) => getCurrentUser(...args),
  acceptLegalTerms: (...args: unknown[]) => acceptLegalTerms(...args),
  logout: vi.fn(),
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

describe("AuthGuard before the terms of use are accepted (#1004)", () => {
  const notAccepted = {
    username: "alice",
    force_password_change: false,
    legal_acceptance_required: true,
  };

  beforeEach(() => {
    resetSessionForTests();
    resetLegalAcceptanceForTests();
    getCurrentUser.mockResolvedValue(notAccepted);
  });

  afterEach(() => {
    resetSessionForTests();
    resetLegalAcceptanceForTests();
  });

  it.each(["/", "/recordings/rec-1", "/settings/users", "/tasks?view=week"])(
    "shows the notice in place of %s",
    async (path) => {
      visit(path);

      const { findByRole, queryByText } = renderWithProviders(
        <AuthGuard>content</AuthGuard>,
      );

      expect(
        await findByRole("heading", { level: 1, name: "Legal disclaimer" }),
      ).toBeInTheDocument();
      expect(queryByText("content")).not.toBeInTheDocument();
      expect(routerPush).not.toHaveBeenCalled();
    },
  );

  it("keeps the consent page's URL and shows it once the terms are accepted", async () => {
    const consent = "/oauth/authorize?client_id=abc&redirect_uri=https%3A%2F%2Fclaude.ai%2Fcb";
    visit(consent);
    acceptLegalTerms.mockResolvedValue({ ...notAccepted, legal_acceptance_required: false });

    const { findByRole, findByText } = renderWithProviders(
      <AuthGuard>consent</AuthGuard>,
    );
    fireEvent.click(await findByRole("button", { name: "I accept" }));

    expect(await findByText("consent")).toBeInTheDocument();
    expect(acceptLegalTerms).toHaveBeenCalledTimes(1);
    expect(window.location.pathname + window.location.search).toBe(consent);
  });

  it("asks the setup account of an older install nothing", async () => {
    getCurrentUser.mockResolvedValue({
      username: "owner",
      force_password_change: false,
      is_superuser: true,
      legal_acceptance_required: false,
    });
    visit("/");

    const { findByText } = renderWithProviders(<AuthGuard>content</AuthGuard>);

    expect(await findByText("content")).toBeInTheDocument();
  });

  it("puts a pending password change first", async () => {
    getCurrentUser.mockResolvedValue({ ...notAccepted, force_password_change: true });
    visit("/settings/profile");

    const { findByText } = renderWithProviders(<AuthGuard>password</AuthGuard>);

    expect(await findByText("password")).toBeInTheDocument();
    expect(useLegalAcceptanceStore.getState().required).toBe(false);
  });

  it("lets a running capture keep its page, then shows the notice", async () => {
    visit("/recordings/rec-1");
    holdSignInInPlace(true);

    const { findByText, findByRole } = renderWithProviders(
      <AuthGuard>capture</AuthGuard>,
    );

    expect(await findByText("capture")).toBeInTheDocument();
    act(() => holdSignInInPlace(false));
    expect(
      await findByRole("heading", { level: 1, name: "Legal disclaimer" }),
    ).toBeInTheDocument();
  });

  it("answers the API's refusal of a capture with the notice", async () => {
    getCurrentUser.mockResolvedValue({
      ...notAccepted,
      legal_acceptance_required: false,
    });
    visit("/");
    const { findByText, findByRole } = renderWithProviders(
      <AuthGuard>content</AuthGuard>,
    );
    expect(await findByText("content")).toBeInTheDocument();
    // Every check of the account has answered before the upgrade lands, and
    // from then on the server asks, as it does for the refused start.
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
    getCurrentUser.mockResolvedValue(notAccepted);

    act(() => requireLegalAcceptance());

    expect(
      await findByRole("heading", { level: 1, name: "Legal disclaimer" }),
    ).toBeInTheDocument();
  });

  it("never asks on the sign-in pages", async () => {
    getCurrentUser.mockRejectedValue(
      Object.assign(new Error("401"), { response: { status: 401, data: {} } }),
    );
    requireLegalAcceptance();
    visit("/login");

    const { findByText } = renderWithProviders(<AuthGuard>sign in</AuthGuard>);

    expect(await findByText("sign in")).toBeInTheDocument();
  });
});
