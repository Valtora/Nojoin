import { describe, expect, it, vi } from "vitest";

import { LEGAL_TERMS as legalTerms } from "@/components/legal/LegalTerms";
import { fireEvent, renderWithProviders } from "@/test/renderWithProviders";

import LegalStep from "./LegalStep";

describe("LegalStep (#1004)", () => {
  const renderStep = (onAccept = vi.fn()) =>
    renderWithProviders(
      <LegalStep
        onAccept={onAccept}
        enableTelemetry
        onEnableTelemetryChange={vi.fn()}
      />,
    );

  it("shows the shared terms and tells the owner about the people they add", () => {
    const { container, getByText } = renderStep();
    const text = container.textContent ?? "";

    legalTerms.sections.forEach((section, index) => {
      expect(getByText(`${index + 1}. ${section.heading}`).tagName).toBe("H3");
      section.blocks.forEach((block) => {
        (block.list ?? [block.paragraph]).forEach((line) =>
          expect(text).toContain(line),
        );
      });
    });
    expect(text).toContain(legalTerms.closing);
    expect(getByText(legalTerms.ownerResponsibility)).toBeInTheDocument();
  });

  it("keeps the telemetry choice on the owner's step", () => {
    const onAccept = vi.fn();
    const { getByLabelText, getByRole } = renderStep(onAccept);

    expect(getByLabelText(/Share anonymous usage diagnostics/)).toBeChecked();
    fireEvent.click(getByRole("button", { name: /I Accept/ }));
    expect(onAccept).toHaveBeenCalledTimes(1);
  });
});
