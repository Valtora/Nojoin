import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  fireEvent,
  renderWithProviders,
  screen,
} from "@/test/renderWithProviders";
import { useImportAudioStore } from "@/lib/importAudioStore";
import type { ViewType } from "@/lib/store";

let currentView: ViewType = "recordings";

vi.mock("next/link", () => ({
  default: ({ children, href, ...props }: React.ComponentPropsWithoutRef<"a">) => (
    <a href={typeof href === "string" ? href : "#"} {...props}>
      {children}
    </a>
  ),
}));

vi.mock("@/lib/store", () => ({
  useNavigationStore: (selector: (state: { currentView: ViewType }) => unknown) =>
    selector({ currentView }),
}));

vi.mock("@/lib/capture/CaptureProvider", () => ({
  useCapture: () => ({ pausedRecording: null, runtimeActive: false }),
}));

vi.mock("./ImportAudioModal", () => ({
  default: ({ isOpen }: { isOpen: boolean }) =>
    isOpen ? <div data-testid="import-modal" /> : null,
}));

import ImportAudioHost from "./importAudio/ImportAudioHost";
import RecordingsLanding from "./RecordingsLanding";

function renderLanding() {
  renderWithProviders(
    <>
      <RecordingsLanding />
      <ImportAudioHost />
    </>,
  );
}

describe("RecordingsLanding", () => {
  beforeEach(() => {
    currentView = "recordings";
    useImportAudioStore.setState({ isImportAudioOpen: false });
  });

  it("opens the import dialog from the recordings landing panel", () => {
    renderLanding();

    fireEvent.click(screen.getByRole("button", { name: "Import Audio" }));

    expect(screen.getByTestId("import-modal")).toBeInTheDocument();
  });

  it.each<ViewType>(["archived", "deleted"])(
    "offers no import on the %s landing panel",
    (view) => {
      currentView = view;
      renderLanding();

      expect(screen.getByRole("link", { name: "Help" })).toBeInTheDocument();
      expect(
        screen.queryByRole("button", { name: "Import Audio" }),
      ).not.toBeInTheDocument();
    },
  );
});
