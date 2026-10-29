import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, within } from "@testing-library/react";

import type { ModelStatusInfo, SystemModelStatus } from "@/types";
import AiModelDependenciesSection from "./AiModelDependenciesSection";

function renderAligner(status: Partial<ModelStatusInfo> = {}, isAdmin = true) {
  const startPreparation = vi.fn().mockResolvedValue(true);
  const handleDeleteModel = vi.fn();
  const ready: ModelStatusInfo = { downloaded: true, path: "/cache/model", checked_paths: [] };
  const modelStatus: SystemModelStatus = {
    whisper: ready, pyannote: ready, embedding: ready,
    parakeet: ready, canary: ready, segmentation: ready, nemotron: ready,
    forced_aligner: {
      downloaded: false, path: null, checked_paths: [], state: "absent",
      size_bytes: 1_839_000_000, ...status,
    },
  };
  render(
    <AiModelDependenciesSection
      modelStatus={modelStatus}
      deleting={null}
      handleDeleteModel={handleDeleteModel}
      isAdmin={isAdmin}
      downloadProgress={null}
      preparationRunning={false}
      startPreparation={startPreparation}
    />,
  );
  const row = within(screen.getByText("Word timing aligner").closest(".settings-cell") as HTMLElement);
  return { row, startPreparation, handleDeleteModel };
}

describe("Forced aligner model downloads", () => {
  it.each([
    ["absent", "Download"],
    ["failed", "Retry"],
  ] as const)("repairs only the aligner from its %s row", (state, label) => {
    const { row, startPreparation } = renderAligner({ state });
    expect(row.getByText("Download size: 1839 MB.")).toBeTruthy();
    fireEvent.click(row.getByRole("button", { name: label }));
    expect(startPreparation).toHaveBeenCalledExactlyOnceWith("forced_aligner");
  });

  it("disables download and partial clearing while its pending transfer is active", () => {
    const { row, startPreparation, handleDeleteModel } = renderAligner({
      state: "downloading", partial: true,
    });
    const download = row.getByRole("button", { name: "Download" }) as HTMLButtonElement;
    const clear = row.getByRole("button", { name: "Clear partial download of Word timing aligner" }) as HTMLButtonElement;
    expect(row.getByText("Downloading")).toBeTruthy();
    expect(download.disabled).toBe(true);
    expect(clear.disabled).toBe(true);
    fireEvent.click(download);
    fireEvent.click(clear);
    expect(startPreparation).not.toHaveBeenCalled();
    expect(handleDeleteModel).not.toHaveBeenCalled();
  });

  it("deletes the verified aligner's managed cache", () => {
    const { row, handleDeleteModel } = renderAligner({ state: "ready", downloaded: true });
    fireEvent.click(row.getByRole("button", { name: "Delete Word timing aligner" }));
    expect(handleDeleteModel).toHaveBeenCalledExactlyOnceWith("forced_aligner");
  });

  it("shows why native engines leave the aligner unused", () => {
    const reason = "Unused while the selected speech engine provides its own word timings.";
    const { row } = renderAligner({ unused_reason: reason });
    expect(row.getByText(reason)).toBeTruthy();
  });

  it("prevents a non-admin download", () => {
    const { row, startPreparation } = renderAligner({}, false);
    const download = row.getByRole("button", { name: "Download" }) as HTMLButtonElement;
    expect(download.disabled).toBe(true);
    fireEvent.click(download);
    expect(startPreparation).not.toHaveBeenCalled();
  });
});
