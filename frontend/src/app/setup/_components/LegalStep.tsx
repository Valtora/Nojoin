import { InputBase } from '@/components/ui/nativeBases';
import { ArrowRight } from "lucide-react";

import LegalTerms, { LEGAL_TERMS } from "@/components/legal/LegalTerms";
import Button from "@/components/ui/Button";

const TELEMETRY_DOC_URL =
  "https://github.com/Valtora/Nojoin/blob/main/docs/TELEMETRY.md";

/** What is sent. A pin decides the heartbeat as well as the diagnostics. */
function telemetryDescription(pinned: boolean | null): string {
  if (pinned === false) {
    return "Nothing is sent: no diagnostics and no daily heartbeat.";
  }
  if (pinned === true) {
    return "Counts and settings every six hours, and a daily heartbeat with a random install ID and the version, so installations can be counted. Neither includes your recordings, transcripts, notes, names, or keys.";
  }
  return "Counts and settings every six hours. Separately, Nojoin sends a daily heartbeat with a random install ID and the version, so installations can be counted. Neither includes your recordings, transcripts, notes, names, or keys. Changeable in Settings.";
}

export default function LegalStep({
  onAccept,
  enableTelemetry,
  onEnableTelemetryChange,
  telemetryPinned = null,
}: {
  onAccept: () => void;
  enableTelemetry: boolean;
  onEnableTelemetryChange: (enabled: boolean) => void;
  /** What NOJOIN_TELEMETRY_ENABLED pins telemetry to, or null when unset. */
  telemetryPinned?: boolean | null;
}) {
  const pinned = telemetryPinned !== null;
  const checked = telemetryPinned ?? enableTelemetry;

  return (
    <div className="space-y-6">
      <div className="text-center mb-6">
        <h2 className="text-xl font-semibold text-foreground">Legal Disclaimer</h2>
        <p className="text-sm text-contrast-helper">
          Please review and accept the terms of use
        </p>
      </div>

      <LegalTerms headingLevel={3} />

      {/* For the owner alone: everyone else is asked to accept the terms
          themselves the first time they sign in (#1004). */}
      <p className="text-sm text-foreground">
        {LEGAL_TERMS.ownerResponsibility}
      </p>

      {/* A separate decision from accepting the terms, and labelled as one, so
          the continue button is not silently carrying two consents. The full
          disclosure lives in the docs rather than here: spelling it out inline
          would push the accept button past the fold. A value pinned by
          NOJOIN_TELEMETRY_ENABLED is shown read-only, as Settings > Privacy
          shows it, because the box could not change what is sent. */}
      <div className="rounded-lg border border-surface-border p-4">
        <label
          htmlFor="setup-enable-telemetry"
          className={`flex items-start gap-3 ${pinned ? "cursor-default" : "cursor-pointer"}`}
        >
          <InputBase
            id="setup-enable-telemetry"
            name="setup-enable-telemetry"
            type="checkbox"
            checked={checked}
            disabled={pinned}
            onChange={(e) => onEnableTelemetryChange(e.target.checked)}
            className="mt-0.5 h-4 w-4 rounded border-control-border text-action accent-action focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-focus-ring"
          />
          <span className="text-sm">
            <span className="font-medium text-foreground">
              Share anonymous usage diagnostics
            </span>
            {pinned && (
              <span className="block text-xs text-contrast-muted mt-0.5">
                Set by NOJOIN_TELEMETRY_ENABLED in the server&apos;s
                environment, so it cannot be changed here. Update .env and
                restart Nojoin to change it.
              </span>
            )}
            <span className="block text-xs text-contrast-helper mt-0.5">
              {telemetryDescription(telemetryPinned)}{" "}
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
