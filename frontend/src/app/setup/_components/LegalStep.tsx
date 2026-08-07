import { ArrowRight } from "lucide-react";

import Button from "@/components/ui/Button";

const TELEMETRY_DOC_URL =
  "https://github.com/Valtora/Nojoin/blob/main/docs/TELEMETRY.md";

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
        <h2 className="text-xl font-semibold text-foreground">Legal Disclaimer</h2>
        <p className="text-sm text-contrast-helper">
          Please review and accept the terms of use
        </p>
      </div>

      {/* Deliberately not its own scroll region. A nested scroller inside a page
          that also scrolls traps the wheel, and these terms are short enough to
          flow. */}
      <div className="prose prose-sm dark:prose-invert max-w-none bg-surface-inset p-4 rounded-lg border border-surface-border">
        <h3 className="text-base font-semibold mt-0">1. Compliance with Laws</h3>
        <p>
          You acknowledge that many legal jurisdictions require the consent of all
          parties before a conversation can be recorded. It is your sole
          responsibility to ensure compliance with all applicable laws and
          regulations regarding audio recording and transcription in your
          jurisdiction.
        </p>

        <h3 className="text-base font-semibold">
          2. Data Privacy &amp; Local Processing
        </h3>
        <p>Nojoin is designed with a privacy-first architecture.</p>
        <ul className="list-disc pl-4 space-y-1">
          <li>
            Your meeting data — audio, transcripts, notes, chat, documents,
            voiceprints, and calendar content — is never stored or transmitted to
            third parties without your explicit consent.
          </li>
          <li>
            All audio processing (transcription, diarization, etc.) is performed
            locally on your machine or your self-hosted server, unless you
            explicitly configure an external provider.
          </li>
        </ul>

        <p className="font-medium mt-4">
          By proceeding, you agree to these terms and accept full responsibility
          for the lawful use of this software.
        </p>
      </div>

      {/* A separate decision from accepting the terms, and labelled as one, so
          the continue button is not silently carrying two consents. The full
          disclosure lives in the docs rather than here: spelling it out inline
          pushed the accept button past the fold. */}
      <div className="rounded-lg border border-surface-border p-4">
        <label
          htmlFor="setup-enable-telemetry"
          className="flex items-start gap-3 cursor-pointer"
        >
          <input
            id="setup-enable-telemetry"
            name="setup-enable-telemetry"
            type="checkbox"
            checked={enableTelemetry}
            onChange={(e) => onEnableTelemetryChange(e.target.checked)}
            className="mt-0.5 h-4 w-4 rounded border-control-border text-action accent-action focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-focus-ring"
          />
          <span className="text-sm">
            <span className="font-medium text-foreground">
              Share anonymous usage data
            </span>
            <span className="block text-xs text-contrast-helper mt-0.5">
              Counts and settings only. Never your recordings, transcripts,
              notes, names, or keys. Changeable in Settings.{" "}
              <a
                href={TELEMETRY_DOC_URL}
                target="_blank"
                rel="noopener noreferrer"
                className="text-action-text hover:text-action-text-hover"
                onClick={(e) => e.stopPropagation()}
              >
                What is collected
              </a>
            </span>
          </span>
        </label>
      </div>

      <Button
        variant="primary"
        fullWidth
        onClick={onAccept}
        iconRight={<ArrowRight className="w-4 h-4" />}
      >
        I Accept &amp; Continue
      </Button>
    </div>
  );
}
