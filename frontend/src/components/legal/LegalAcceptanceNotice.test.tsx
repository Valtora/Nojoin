import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { LEGAL_TERMS as legalTerms } from "@/components/legal/LegalTerms";
import {
  requireLegalAcceptance,
  resetLegalAcceptanceForTests,
  useLegalAcceptanceStore,
} from "@/lib/legalAcceptance";
import { fireEvent, renderWithProviders, waitFor } from "@/test/renderWithProviders";

import LegalAcceptanceNotice from "./LegalAcceptanceNotice";

const acceptLegalTerms = vi.fn();
const logout = vi.fn();

vi.mock("@/lib/api", () => ({
  acceptLegalTerms: (...args: unknown[]) => acceptLegalTerms(...args),
  logout: (...args: unknown[]) => logout(...args),
}));

beforeEach(() => {
  resetLegalAcceptanceForTests();
  requireLegalAcceptance();
});

afterEach(() => {
  vi.clearAllMocks();
  resetLegalAcceptanceForTests();
});

describe("LegalAcceptanceNotice (#1004)", () => {
  it("shows every term the setup step shows, and not the owner's sentence", () => {
    const { container, getByText, queryByText } = renderWithProviders(
      <LegalAcceptanceNotice />,
    );
    const text = container.textContent ?? "";

    legalTerms.sections.forEach((section, index) => {
      expect(
        getByText(`${index + 1}. ${section.heading}`).tagName,
      ).toBe("H2");
      section.blocks.forEach((block) => {
        (block.list ?? [block.paragraph]).forEach((line) =>
          expect(text).toContain(line),
        );
      });
    });
    expect(text).toContain(legalTerms.closing);
    expect(queryByText(legalTerms.ownerResponsibility)).not.toBeInTheDocument();
  });

  it("starts the keyboard and a screen reader at its title", () => {
    const { getByRole } = renderWithProviders(<LegalAcceptanceNotice />);

    const title = getByRole("heading", { level: 1, name: "Legal disclaimer" });
    expect(document.activeElement).toBe(title);
    expect(getByRole("main")).toHaveAccessibleName("Legal disclaimer");
  });

  it("offers accepting or signing out, and nothing that closes it", () => {
    const { getAllByRole, getByRole } = renderWithProviders(<LegalAcceptanceNotice />);

    expect(getAllByRole("button").map((button) => button.textContent)).toEqual([
      "I accept",
      "Sign out",
    ]);
    fireEvent.keyDown(getByRole("main"), { key: "Escape" });
    expect(getByRole("heading", { level: 1 })).toBeInTheDocument();
    expect(useLegalAcceptanceStore.getState().required).toBe(true);
  });

  it("lets the app through once the server records the acceptance", async () => {
    acceptLegalTerms.mockResolvedValue({
      username: "alice",
      force_password_change: false,
      legal_acceptance_required: false,
      legal_accepted_at: "2026-11-11T10:00:00Z",
    });
    const { getByRole } = renderWithProviders(<LegalAcceptanceNotice />);

    fireEvent.click(getByRole("button", { name: "I accept" }));

    await waitFor(() =>
      expect(useLegalAcceptanceStore.getState().required).toBe(false),
    );
    expect(acceptLegalTerms).toHaveBeenCalledTimes(1);
  });

  it("stays, and says so, when the acceptance is not recorded", async () => {
    acceptLegalTerms.mockRejectedValue(new Error("Network Error"));
    const { getByRole, findByRole } = renderWithProviders(<LegalAcceptanceNotice />);

    fireEvent.click(getByRole("button", { name: "I accept" }));

    expect(await findByRole("alert")).toHaveTextContent("Network Error");
    expect(useLegalAcceptanceStore.getState().required).toBe(true);
    expect(getByRole("button", { name: "I accept" })).not.toHaveAttribute(
      "aria-busy",
      "true",
    );
  });

  it("signs out", () => {
    logout.mockReturnValue(new Promise(() => {}));
    const { getByRole } = renderWithProviders(<LegalAcceptanceNotice />);

    fireEvent.click(getByRole("button", { name: "Sign out" }));

    expect(logout).toHaveBeenCalledTimes(1);
    expect(acceptLegalTerms).not.toHaveBeenCalled();
  });
});
