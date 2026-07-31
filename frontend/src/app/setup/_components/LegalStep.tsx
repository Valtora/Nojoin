import { ArrowRight } from "lucide-react";

export default function LegalStep({
  onAccept,
  enableTelemetry,
  onEnableTelemetryChange,
}: {
  onAccept: () => void;
  enableTelemetry: boolean;
  onEnableTelemetryChange: (enabled: boolean) => void;
}) {
  return (
    <div className="space-y-6">
      <div className="text-center mb-6">
        <h2 className="text-xl font-semibold text-foreground">
          Legal Disclaimer
        </h2>
        <p className="text-sm text-contrast-helper">
          Please review and accept the terms of use
        </p>
      </div>

      <div className="prose prose-sm dark:prose-invert max-w-none bg-surface-inset p-4 rounded-lg border border-surface-border max-h-64 overflow-y-auto">
        <h3 className="text-base font-semibold mt-0">
          1. Compliance with Laws
        </h3>
        <p>
          You acknowledge that many legal jurisdictions require the
          consent of all parties before a conversation can be recorded.
          It is your sole responsibility to ensure compliance with all
          applicable laws and regulations regarding audio recording and
          transcription in your jurisdiction.
        </p>

        <h3 className="text-base font-semibold">
          2. Data Privacy & Local Processing
        </h3>
        <p>Nojoin is designed with a privacy-first architecture.</p>
        <ul className="list-disc pl-4 space-y-1">
          <li>
            Your meeting data — audio, transcripts, notes, chat,
            documents, voiceprints, and calendar content — is never
            stored or transmitted to third parties without your
            explicit consent.
          </li>
          <li>
            All audio processing (transcription, diarization, etc.) is
            performed locally on your machine or your self-hosted
            server, unless you explicitly configure an external
            provider.
          </li>
        </ul>

        <p className="font-medium mt-4">
          By proceeding, you agree to these terms and accept full
          responsibility for the lawful use of this software.
        </p>
      </div>

      <div className="rounded-lg border border-surface-border p-4">
        <label className="flex items-start gap-3 cursor-pointer">
          <input
            type="checkbox"
            checked={enableTelemetry}
            onChange={(e) => onEnableTelemetryChange(e.target.checked)}
            className="mt-1 h-4 w-4 rounded border-control-border text-action accent-action focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-focus-ring"
          />
          <span className="text-sm">
            <span className="font-medium text-foreground">
              Share anonymous usage data
            </span>
            <span className="block text-contrast-helper mt-1">
              Sends one anonymous ping a day with a random install ID, your
              Nojoin version, how many users and recordings this server has, and
              which features are switched on. It never includes your recordings,
              transcripts, notes, names, or API keys, and the data is never
              sold. This helps decide what to build next. You can change this
              any time in Settings.
            </span>
            <a
              href="https://www.nojoin.co.uk/docs/TELEMETRY"
              target="_blank"
              rel="noopener noreferrer"
              className="block mt-1 text-action-text hover:text-action-text-hover"
            >
              Read exactly what is collected
            </a>
          </span>
        </label>
      </div>

      <button
        onClick={onAccept}
        className="w-full bg-action hover:bg-action-hover text-white font-medium py-2.5 rounded-lg transition-colors flex items-center justify-center gap-2"
      >
        I Accept &amp; Continue <ArrowRight className="w-4 h-4" />
      </button>
    </div>
  );
}
