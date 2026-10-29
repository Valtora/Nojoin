import { useMemo } from "react";
import { useHydrated } from "@/lib/useHydrated";
import {
  AlertTriangle,
  ArrowRight,
  CheckCircle,
  Loader2,
  ShieldAlert,
} from "lucide-react";

import Button from "@/components/ui/Button";
import { detectCaptureSupport } from "@/lib/capture/featureDetect";
import type { CaptureSupport } from "@/lib/capture/shared";

import type { AiRoute } from "../_hooks/useSetupWizard";

interface CompleteStepProps {
  modelPreparationComplete: boolean;
  modelPreparationMessage: string;
  modelPreparationStage: string | null;
  modelPreparationProgress: number;
  modelPreparationWarning: string;
  /** The server reported the download failed, as opposed to a lost or slow poll. */
  modelPreparationFailed: boolean;
  summary: {
    username: string;
    whisperModelLabel: string;
    diarizationReady: boolean;
    aiRoute: AiRoute;
    aiDetail: string;
    demoRecording: boolean;
  };
  onComplete: () => void;
}

export default function CompleteStep({
  modelPreparationComplete,
  modelPreparationMessage,
  modelPreparationStage,
  modelPreparationProgress,
  modelPreparationWarning,
  modelPreparationFailed,
  summary,
  onComplete,
}: CompleteStepProps) {
  // Detected only once hydrated: the server render has no navigator, and
  // guessing there would flash an unsupported-browser warning at every visitor.
  const hydrated = useHydrated();
  const capture = useMemo<CaptureSupport | null>(
    () => (hydrated ? detectCaptureSupport() : null),
    [hydrated],
  );
  const secureContext = hydrated ? window.isSecureContext : true;

  if (!modelPreparationComplete) {
    return (
      <div className="space-y-5">
        <div className="text-center">
          <Loader2 className="mx-auto mb-4 h-12 w-12 animate-spin text-action-text" />
          <h2 className="text-xl font-semibold text-foreground">
            Preparing Models
          </h2>
          <p className="text-contrast-helper text-sm">{modelPreparationMessage}</p>
        </div>

        <div className="space-y-2">
          <div
            className="h-2 w-full overflow-hidden rounded-full bg-surface-inset"
            role="progressbar"
            aria-valuenow={Math.max(0, Math.min(modelPreparationProgress, 100))}
            aria-valuemin={0}
            aria-valuemax={100}
            aria-label="Model preparation progress"
          >
            <div
              className="h-full rounded-full bg-action transition-all"
              style={{
                width: `${Math.max(0, Math.min(modelPreparationProgress, 100))}%`,
              }}
            />
          </div>
          <div className="flex justify-between text-xs text-contrast-helper">
            <span>{modelPreparationStage || "queued"}</span>
            <span>{modelPreparationProgress}%</span>
          </div>
        </div>

        <p className="text-xs text-center text-contrast-helper">
          Your owner account already exists and you are signed in. Preparation
          continues on the server even if you close this page.
        </p>

        <Button variant="secondary" fullWidth onClick={onComplete}>
          Skip ahead to the dashboard
        </Button>
      </div>
    );
  }

  // "Ready" only when preparation finished cleanly. Setup itself is done
  // either way; the warning below says what is left and where to retry it.
  const heading = !modelPreparationWarning
    ? { title: "Setup Complete", lead: "Nojoin is ready." }
    : modelPreparationFailed
      ? { title: "Setup Finished", lead: "A model still needs downloading." }
      : { title: "Setup Finished", lead: "Model downloads continue on the server." };

  return (
    <div className="space-y-5">
      <div className="text-center">
        <CheckCircle className="mx-auto mb-4 h-12 w-12 text-status-success-fg" />
        <h2 className="text-xl font-semibold text-foreground">{heading.title}</h2>
        <p className="text-contrast-helper text-sm">
          {heading.lead} Here is what this deployment is set to.
        </p>
      </div>

      {modelPreparationWarning && (
        <div className="p-4 bg-status-warning-bg border border-status-warning-border rounded-xl flex items-start gap-3">
          <AlertTriangle className="w-5 h-5 text-status-warning-fg shrink-0 mt-0.5" />
          <p className="text-xs text-status-warning-fg leading-relaxed">
            {modelPreparationWarning}
          </p>
        </div>
      )}

      <dl className="rounded-xl border border-surface-border divide-y divide-surface-divider text-sm">
        <SummaryRow label="Owner account" value={summary.username} />
        <SummaryRow label="Transcription" value={summary.whisperModelLabel} />
        <SummaryRow
          label="Speaker labels"
          value={summary.diarizationReady ? "Enabled" : "Unavailable"}
        />
        <SummaryRow label="AI" value={summary.aiDetail} />
        <SummaryRow
          label="Sample meeting"
          value={summary.demoRecording ? "Added" : "Not added"}
        />
      </dl>

      {!secureContext && (
        <div className="p-4 bg-status-danger-bg border border-status-danger-border rounded-xl flex items-start gap-3">
          <ShieldAlert className="w-5 h-5 text-status-danger-fg shrink-0 mt-0.5" />
          <div>
            <p className="text-sm font-semibold text-status-danger-fg">
              This page is not on a secure origin
            </p>
            <p className="text-xs text-status-danger-fg mt-1 leading-relaxed">
              Browsers only grant microphone and screen-capture access over HTTPS
              or localhost, so recording will fail from this address. Put a
              reverse proxy with TLS in front of Nojoin before recording from
              another device.
            </p>
          </div>
        </div>
      )}

      {secureContext && capture && !capture.supported && (
        <div className="p-4 bg-status-warning-bg border border-status-warning-border rounded-xl flex items-start gap-3">
          <AlertTriangle className="w-5 h-5 text-status-warning-fg shrink-0 mt-0.5" />
          <div>
            <p className="text-sm font-semibold text-status-warning-fg">
              This browser cannot record
            </p>
            <p className="text-xs text-status-warning-fg mt-1 leading-relaxed">
              This browser did not expose the microphone APIs Nojoin records
              through. Open Nojoin in a current version of Chrome, Edge,
              Firefox or Safari.
            </p>
          </div>
        </div>
      )}

      {secureContext && capture?.supported && (
        <div className="p-4 bg-status-success-bg border border-status-success-border rounded-xl flex items-start gap-3">
          <CheckCircle className="w-5 h-5 text-status-success-fg shrink-0 mt-0.5" />
          <p className="text-xs text-status-success-fg leading-relaxed">
            {capture.mode === "microphone_only"
              ? "This browser can record your microphone. For shared meeting audio, use Chrome on a desktop."
              : "This browser can capture shared meeting audio. Start a short test recording to confirm the whole path works."}
          </p>
        </div>
      )}

      <Button
        variant="primary"
        fullWidth
        size="lg"
        onClick={onComplete}
        iconRight={<ArrowRight className="w-5 h-5" />}
      >
        Go to the dashboard
      </Button>
    </div>
  );
}

function SummaryRow({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-baseline justify-between gap-4 px-4 py-2.5">
      <dt className="text-xs text-contrast-helper">{label}</dt>
      <dd className="text-sm text-foreground text-right">{value}</dd>
    </div>
  );
}
