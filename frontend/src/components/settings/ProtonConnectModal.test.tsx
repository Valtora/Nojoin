import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";

import type { CalendarConnection } from "@/types";

vi.mock("@/lib/api", () => ({
  connectProton: vi.fn(),
  connectProtonTwoFactor: vi.fn(),
  connectProtonUnlock: vi.fn(),
}));

import { connectProton, connectProtonTwoFactor } from "@/lib/api";
import ProtonConnectModal from "./ProtonConnectModal";

const connection = {
  id: 3,
  provider: "proton",
  email: "alice@example.com",
  sync_status: "success",
  selected_calendar_count: 1,
  push_active: false,
  calendars: [],
} as unknown as CalendarConnection;

function renderModal(onConnected = vi.fn()) {
  render(<ProtonConnectModal open onClose={vi.fn()} onConnected={onConnected} />);
  return onConnected;
}

function passDisclosure() {
  const next = screen.getByRole("button", { name: "Continue" });
  expect(next).toHaveProperty("disabled", true);
  fireEvent.click(screen.getByRole("checkbox"));
  fireEvent.click(screen.getByRole("button", { name: "Continue" }));
}

function signIn() {
  fireEvent.change(screen.getByLabelText("Proton address or username"), {
    target: { value: "alice@example.com" },
  });
  fireEvent.change(screen.getByLabelText("Password"), { target: { value: "correct horse" } });
  fireEvent.click(screen.getByRole("button", { name: "Connect" }));
}

beforeEach(() => {
  vi.mocked(connectProton).mockReset();
  vi.mocked(connectProtonTwoFactor).mockReset();
});

describe("ProtonConnectModal", () => {
  it("shows the risks first, then signs in through two-factor", async () => {
    vi.mocked(connectProton).mockResolvedValue({ step: "two_factor", flow_id: "flow-1", warnings: [] });
    vi.mocked(connectProtonTwoFactor).mockResolvedValue({ step: "done", connection, warnings: [] });
    const onConnected = renderModal();

    expect(screen.getByText(/clause 2.10/)).toBeTruthy();
    expect(screen.getByText(/could also decrypt your Proton Mail/)).toBeTruthy();
    passDisclosure();
    signIn();

    const code = await screen.findByLabelText("Two-factor code");
    expect(code.getAttribute("autocomplete")).toBe("one-time-code");
    fireEvent.change(code, { target: { value: "123456" } });
    fireEvent.click(screen.getByRole("button", { name: "Connect" }));

    await waitFor(() => expect(onConnected).toHaveBeenCalledWith(connection));
    expect(connectProton).toHaveBeenCalledWith({
      username: "alice@example.com",
      password: "correct horse",
    });
    expect(connectProtonTwoFactor).toHaveBeenCalledWith(
      "flow-1",
      { username: "alice@example.com", password: "correct horse" },
      "123456",
    );
  });

  it("sends the user to Proton's check and retries with its token", async () => {
    vi.mocked(connectProton)
      .mockRejectedValueOnce({
        response: {
          data: {
            detail: {
              code: "human_verification_required",
              message: "Proton wants to check that you are a person.",
              verify_url: "https://verify.proton.me/?methods=captcha&token=hv-1",
              token: "hv-1",
            },
          },
        },
      })
      .mockResolvedValueOnce({ step: "done", connection, warnings: [] });
    const onConnected = renderModal();

    passDisclosure();
    signIn();

    const link = await screen.findByRole("link", { name: /Open Proton's check/ });
    expect(link.getAttribute("href")).toBe("https://verify.proton.me/?methods=captcha&token=hv-1");
    fireEvent.click(screen.getByRole("button", { name: "I've finished the check" }));

    await waitFor(() => expect(onConnected).toHaveBeenCalled());
    expect(vi.mocked(connectProton).mock.calls[1][1]).toEqual({ token: "hv-1", type: "captcha" });
  });

  it("shows Proton's refusal in the modal", async () => {
    vi.mocked(connectProton).mockRejectedValue({
      response: {
        data: {
          detail: {
            code: "login_failed",
            message: "Proton did not accept that username or password.",
          },
        },
      },
    });
    renderModal();

    passDisclosure();
    signIn();

    expect((await screen.findByRole("alert")).textContent).toContain(
      "Proton did not accept that username or password.",
    );
  });
});
