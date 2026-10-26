import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, within } from "@testing-library/react";

import type { DownloadProgress, ModelStatusInfo, SystemModelStatus } from "@/types";
import AiModelDependenciesSection from "./AiModelDependenciesSection";

const missing: ModelStatusInfo = {
  downloaded: false, path: null, checked_paths: [], state: "absent",
  size_bytes: 396_956_673,
};
const ready = { downloaded: true, path: "/cache/model", checked_paths: [] };

function renderNemotron(
  status: Partial<ModelStatusInfo> = {},
  options: { isAdmin?: boolean; preparationRunning?: boolean; downloadProgress?: DownloadProgress } = {},
) {
  const startPreparation = vi.fn().mockResolvedValue(true);
  const handleDeleteModel = vi.fn();
  const modelStatus: SystemModelStatus = {
    whisper: ready, pyannote: ready, embedding: ready,
    parakeet: ready, canary: ready, segmentation: ready,
    nemotron: { ...missing, ...status },
  };
  render(
    <AiModelDependenciesSection
      modelStatus={modelStatus}
      deleting={null}
      handleDeleteModel={handleDeleteModel}
      isAdmin={options.isAdmin ?? true}
      downloadProgress={options.downloadProgress ?? null}
      preparationRunning={options.preparationRunning ?? false}
      startPreparation={startPreparation}
    />,
  );
  const row = within(screen.getByText("Nemotron").closest(".settings-cell") as HTMLElement);
  return { row, startPreparation, handleDeleteModel };
}

describe("Nemotron model downloads", () => {
  it("shows missing assets, manifest-derived decimal size and CUDA/auto requirements", () => {
    const { row } = renderNemotron();

    expect(row.getByText("Missing")).toBeTruthy();
    expect(row.getByText("Download size: 397 MB.")).toBeTruthy();
    expect(row.getByText(/Used only on CUDA workers with the automatic speaker engine/)).toBeTruthy();
    expect(screen.getByText(/Optional Nemotron speaker models prepare during the final pass after capture ends/))
      .toBeTruthy();
  });

  it.each([
    ["absent", "Download"],
    ["failed", "Retry"],
  ] as const)("prepares only Nemotron from its %s row", (state, action) => {
    const { row, startPreparation } = renderNemotron({ state });

    fireEvent.click(row.getByRole("button", { name: action }));

    expect(startPreparation).toHaveBeenCalledExactlyOnceWith("nemotron");
  });

  it.each([
    ["receipt_or_assets_changed", "Model files changed or verification is invalid. Retry to repair."],
    ["receipt_missing", "Model verification is missing. Retry to repair."],
    ["unknown_future_code", "Model verification failed. Retry to repair."],
  ])("explains %s and allows retrying or clearing partial assets", (code, message) => {
    const { row, handleDeleteModel } = renderNemotron({
      state: "failed", verification_state: "invalid", partial: true,
      verification_error: code,
    });

    expect(row.getByText("Failed")).toBeTruthy();
    expect(row.getByText(message)).toBeTruthy();
    expect(row.queryByText(code)).toBeNull();
    expect(row.queryByText("Missing")).toBeNull();
    fireEvent.click(row.getByRole("button", { name: "Clear partial download of Nemotron" }));
    expect(handleDeleteModel).toHaveBeenCalledExactlyOnceWith("nemotron");
  });

  it("shows downloading and disables both preparation and partial clearing", () => {
    const { row, startPreparation, handleDeleteModel } = renderNemotron({
      state: "downloading", partial: true,
    });

    expect(row.getByText("Downloading")).toBeTruthy();
    const download = row.getByRole("button", { name: "Download" }) as HTMLButtonElement;
    const clear = row.getByRole("button", { name: "Clear partial download of Nemotron" }) as HTMLButtonElement;
    expect(download.disabled).toBe(true);
    expect(clear.disabled).toBe(true);
    fireEvent.click(download);
    fireEvent.click(clear);
    expect(startPreparation).not.toHaveBeenCalled();
    expect(handleDeleteModel).not.toHaveBeenCalled();
  });

  it("shows verified assets as ready and deletes the Nemotron cache", () => {
    const { row, handleDeleteModel } = renderNemotron({
      state: "ready", downloaded: true, verification_state: "verified",
    });

    expect(row.getByText("Ready")).toBeTruthy();
    expect(row.queryByRole("button", { name: "Download" })).toBeNull();
    fireEvent.click(row.getByRole("button", { name: "Delete Nemotron" }));
    expect(handleDeleteModel).toHaveBeenCalledExactlyOnceWith("nemotron");
  });

  it("keeps unused assets visible and displays the server's reason", () => {
    const { row } = renderNemotron({
      unused_reason: "The classic speaker engine does not use Nemotron.",
    });

    expect(row.getByText("The classic speaker engine does not use Nemotron.")).toBeTruthy();
    expect(row.getByRole("button", { name: "Download" })).toBeTruthy();
  });

  it("keeps Nemotron missing while a different model is downloading", () => {
    const { row } = renderNemotron({}, {
      preparationRunning: true,
      downloadProgress: {
        status: "downloading", in_progress: true, stage: "parakeet",
        progress: 20, message: "Preparing Parakeet",
      },
    });

    expect(row.getByText("Missing")).toBeTruthy();
    expect(row.queryByText("Downloading")).toBeNull();
  });

  it("accepts nullable size and usage metadata without displaying an invented size", () => {
    const { row } = renderNemotron({ size_bytes: null, unused_reason: null });

    expect(row.queryByText(/Download size:/)).toBeNull();
    expect(row.getByText("Missing")).toBeTruthy();
  });

  it.each(["Download", "Retry"])("disables %s for non-admins", (action) => {
    const { row, startPreparation } = renderNemotron({
      state: action === "Retry" ? "failed" : "absent",
    }, { isAdmin: false });
    const button = row.getByRole("button", { name: action }) as HTMLButtonElement;

    expect(button.disabled).toBe(true);
    fireEvent.click(button);
    expect(startPreparation).not.toHaveBeenCalled();
  });

  it("disables retry and clearing while another model preparation is running", () => {
    const { row, startPreparation, handleDeleteModel } = renderNemotron({
      state: "failed", partial: true,
    }, { preparationRunning: true });
    const retry = row.getByRole("button", { name: "Retry" }) as HTMLButtonElement;
    const clear = row.getByRole("button", { name: "Clear partial download of Nemotron" }) as HTMLButtonElement;

    expect(retry.disabled).toBe(true);
    expect(clear.disabled).toBe(true);
    fireEvent.click(retry);
    fireEvent.click(clear);
    expect(startPreparation).not.toHaveBeenCalled();
    expect(handleDeleteModel).not.toHaveBeenCalled();
  });
});
