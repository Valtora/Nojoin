import { ArrowRight, CheckCircle, Loader2 } from "lucide-react";

interface CompleteStepProps {
  modelPreparationComplete: boolean;
  modelPreparationMessage: string;
  modelPreparationStage: string | null;
  modelPreparationProgress: number;
  error: string;
  onComplete: () => void;
}

export default function CompleteStep({
  modelPreparationComplete,
  modelPreparationMessage,
  modelPreparationStage,
  modelPreparationProgress,
  error,
  onComplete,
}: CompleteStepProps) {
  return (
    <div className="space-y-6">
      {modelPreparationComplete ? (
        <>
          <div className="text-center mb-6">
            <CheckCircle className="mx-auto mb-4 h-12 w-12 text-status-success-fg" />
            <h2 className="text-xl font-semibold text-foreground">
              Setup Complete
            </h2>
            <p className="text-contrast-helper text-sm">
              Transcription, diarization, and voice embedding models are ready for your first recording.
            </p>
          </div>

          <button
            onClick={onComplete}
            className="w-full bg-action hover:bg-action-hover text-action-on font-medium py-3 rounded-lg flex items-center justify-center gap-2 shadow-lg hover:shadow-xl transform hover:-translate-y-0.5 transition-all"
          >
            Complete Setup <ArrowRight className="w-5 h-5" />
          </button>
        </>
      ) : (
        <div className="space-y-5">
          <div className="text-center">
            <Loader2 className="mx-auto mb-4 h-12 w-12 animate-spin text-action-text" />
            <h2 className="text-xl font-semibold text-foreground">
              Preparing Models
            </h2>
            <p className="text-contrast-helper text-sm">
              {modelPreparationMessage}
            </p>
          </div>

          <div className="space-y-2">
            <div className="h-2 w-full overflow-hidden rounded-full bg-surface-inset">
              <div
                className="h-full rounded-full bg-action transition-all"
                style={{ width: `${Math.max(0, Math.min(modelPreparationProgress, 100))}%` }}
              />
            </div>
            <div className="flex justify-between text-xs text-contrast-helper">
              <span>{modelPreparationStage || "queued"}</span>
              <span>{modelPreparationProgress}%</span>
            </div>
          </div>

          <p className="text-xs text-center text-contrast-helper">
            Your owner account is already created. Model preparation continues
            on the server even if you close this page — you can sign in at any
            time while it finishes.
          </p>

          {error && (
            <button
              onClick={onComplete}
              className="w-full bg-action hover:bg-action-hover text-action-on font-medium py-3 rounded-lg flex items-center justify-center gap-2 shadow-lg hover:shadow-xl transform hover:-translate-y-0.5 transition-all"
            >
              Continue to Dashboard <ArrowRight className="w-5 h-5" />
            </button>
          )}
        </div>
      )}
    </div>
  );
}
