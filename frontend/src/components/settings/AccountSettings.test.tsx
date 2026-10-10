import { afterEach, describe, expect, it, vi } from "vitest";

import { fireEvent, renderWithProviders, waitFor } from "@/test/renderWithProviders";

import AccountSettings from "./AccountSettings";

const routerPush = vi.fn();
const updatePasswordMe = vi.fn();

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: routerPush }),
}));

vi.mock("@/lib/api", () => ({
  updatePasswordMe: (...args: unknown[]) => updatePasswordMe(...args),
  updateUserMe: vi.fn(),
}));

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
