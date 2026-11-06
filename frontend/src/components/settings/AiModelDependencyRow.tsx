import { Download, Loader2, Trash2 } from "lucide-react";

import type { ModelPreparationTarget, ModelSource, ModelStatusInfo } from "@/types";
import { ButtonBase } from "@/components/ui/nativeBases";
import { loadingButtonProps } from "@/components/ui/loadingButton";
import { cn } from "@/lib/cn";
import SettingsRow from "./SettingsRow";
import SettingsStatusBadge from "./SettingsStatusBadge";
import { SETTINGS_BUTTON_SECONDARY } from "./settingsControls";

export interface DependencyModel {
  id: string;
  label: string;
  desc: string;
  target: ModelPreparationTarget;
}

interface Props {
  model: DependencyModel;
  status?: ModelStatusInfo;
  deleting: string | null;
  handleDeleteModel: (modelName: string) => void;
  isAdmin: boolean;
  preparationRunning: boolean;
  startPreparation: (target: ModelPreparationTarget) => Promise<boolean>;
}

const UNDELETABLE_REASON: Partial<Record<ModelSource, string>> = {
  bundled: "Bundled with Nojoin, so it cannot be deleted here.",
  external: "Kept in a cache outside Nojoin's, so it is used but not deleted from here.",
};
const ICON_BUTTON = cn(SETTINGS_BUTTON_SECONDARY, "h-9 w-9 px-0");
const VERIFICATION_ERRORS: Record<string, string> = {
  receipt_or_assets_changed: "Model files changed or verification is invalid. Retry to repair.",
  receipt_missing: "Model verification is missing. Retry to repair.",
};

function ModelBadge({ status }: { status?: ModelStatusInfo }) {
  const state = status?.state ?? (status?.downloaded ? "ready" : "absent");
  if (state === "downloading") {
    return <SettingsStatusBadge tone="info">Downloading</SettingsStatusBadge>;
  }
  if (state === "failed") {
    return <SettingsStatusBadge tone="error">Failed</SettingsStatusBadge>;
  }
  if (state === "retry_wait") {
    return <SettingsStatusBadge tone="info">Waiting to retry</SettingsStatusBadge>;
  }
  if (state === "ready") {
    const label = status?.source === "bundled" ? "Bundled"
      : status?.source === "external" ? "External" : "Ready";
    return <SettingsStatusBadge tone="success">{label}</SettingsStatusBadge>;
  }
  return <SettingsStatusBadge tone="error">Missing</SettingsStatusBadge>;
}

/** Preserve cache deletion and preparation actions for every model in the list. */
export default function AiModelDependencyRow({
  model, status, deleting, handleDeleteModel, isAdmin,
  preparationRunning, startPreparation,
}: Props) {
  const reason = status?.source ? UNDELETABLE_REASON[status.source] : undefined;
  const busy = deleting === model.id;
  const ready = status?.state ? status.state === "ready" : status?.downloaded;
  const downloading = status?.state === "downloading";
  const retryWaiting = status?.state === "retry_wait";
  const failed = status?.state === "failed" || retryWaiting;
  const preparationBusy = preparationRunning || downloading;

  return (
    <SettingsRow
      label={model.label}
      description={
        <>
          {model.desc}
          {status?.size_bytes != null && (
            <span className="block">Download size: {Math.round(status.size_bytes / 1_000_000)} MB.</span>
          )}
          {status?.unused_reason && <span className="block">{status.unused_reason}</span>}
          {retryWaiting && (
            <span className="block">
              Preparation failed. Automatic requests wait ten minutes before trying again; Retry starts now.
            </span>
          )}
          {ready && reason && <span className="block">{reason}</span>}
          {failed && status?.verification_error && (
            <span className="block">
              {VERIFICATION_ERRORS[status.verification_error] || "Model verification failed. Retry to repair."}
            </span>
          )}
          {!ready && (status?.checked_paths?.length ?? 0) > 0 && (
            <details className="mt-1">
              <summary className="cursor-pointer text-action-text">Paths checked</summary>
              <ul className="mt-1 space-y-0.5 font-mono break-all">
                {status?.checked_paths.map((path) => <li key={path}>{path}</li>)}
              </ul>
            </details>
          )}
        </>
      }
      badge={<ModelBadge status={status} />}
      controlClassName="@min-[26rem]:min-w-0"
    >
      <div className="flex flex-wrap items-center gap-2 @min-[26rem]:justify-end">
        {ready ? (
          reason === undefined && (
            <ButtonBase
              data-ui-fill="card"
              data-ui-hover="inset"
              data-ui-active="inset"
              data-ui-border="full"
              chrome="action"
              type="button"
              {...loadingButtonProps(busy, !isAdmin, () => handleDeleteModel(model.id))}
              title="Delete this model"
              aria-label={`Delete ${model.label}`}
              className={ICON_BUTTON}
            >
              {busy ? <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
                : <Trash2 className="h-4 w-4" aria-hidden="true" />}
            </ButtonBase>
          )
        ) : (
          <>
            {status?.partial && (
              <ButtonBase
                data-ui-fill="card"
                data-ui-hover="inset"
                data-ui-active="inset"
                data-ui-border="full"
                chrome="action"
                type="button"
                {...loadingButtonProps(busy, !isAdmin || preparationBusy, () => handleDeleteModel(model.id))}
                title={preparationBusy
                  ? "A model preparation is running and may still be writing this download"
                  : "Clear the partial download left in the model cache"}
                aria-label={`Clear partial download of ${model.label}`}
                className={ICON_BUTTON}
              >
                {busy ? <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
                  : <Trash2 className="h-4 w-4" aria-hidden="true" />}
              </ButtonBase>
            )}
            <ButtonBase
              data-ui-fill="card"
              data-ui-hover="inset"
              data-ui-active="inset"
              data-ui-border="full"
              chrome="action"
              type="button"
              onClick={() => void startPreparation(model.target)}
              disabled={!isAdmin || preparationBusy}
              className={SETTINGS_BUTTON_SECONDARY}
            >
              <Download className="h-4 w-4" aria-hidden="true" />
              {failed ? "Retry" : "Download"}
            </ButtonBase>
          </>
        )}
      </div>
    </SettingsRow>
  );
}
