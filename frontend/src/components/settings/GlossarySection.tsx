"use client";

import { Settings } from "@/types";
import SettingsBlock from "./SettingsBlock";
import SettingsCard from "./SettingsCard";

interface GlossarySectionProps {
  settings: Settings;
  /** Debounced apply (free text). */
  onUpdate: (newSettings: Settings) => void;
  isAdmin?: boolean;
}

const PLACEHOLDER = `Nojoin: our meeting transcription product
ARR: annual recurring revenue
Kubernetes, heard as "cubanetties"`;

/**
 * "Glossary" AI section (issue #137).
 *
 * Two tiers that merge rather than replace: an admin maintains the
 * organisation's vocabulary, each user adds their own, and a personal entry wins
 * where both define the same term. Feeds the notes prompt and Meeting Edge. It
 * cannot correct the transcript itself, which is produced before any of this is
 * consulted, so the wording below says so rather than implying otherwise.
 */
export default function GlossarySection({
  settings,
  onUpdate,
  isAdmin = false,
}: GlossarySectionProps) {
  return (
    <SettingsCard
      title="Glossary"
      description="Project names, acronyms and products the AI should spell correctly, plus corrections for words it commonly mishears."
    >
      <SettingsBlock className="space-y-6">
        {isAdmin && (
          <div>
            <label className="block text-sm font-medium text-contrast-muted mb-2">
              Install glossary
            </label>
            <textarea
              value={settings.install_glossary_terms || ""}
              onChange={(event) =>
                onUpdate({
                  ...settings,
                  install_glossary_terms: event.target.value,
                })
              }
              rows={6}
              spellCheck={false}
              placeholder={PLACEHOLDER}
              className="w-full p-3 font-mono text-xs rounded-lg border border-control-border bg-surface-inset text-foreground focus:ring-2 focus:ring-action outline-none transition-all"
            />
            <p className="mt-2 text-xs contrast-helper">
              Shared by everyone on this installation. One term per line, as
              <span className="font-mono"> Term: meaning</span>.
            </p>
          </div>
        )}

        <div>
          <label className="block text-sm font-medium text-contrast-muted mb-2">
            My glossary
          </label>
          <textarea
            value={settings.glossary_terms || ""}
            onChange={(event) =>
              onUpdate({ ...settings, glossary_terms: event.target.value })
            }
            rows={6}
            spellCheck={false}
            placeholder={PLACEHOLDER}
            className="w-full p-3 font-mono text-xs rounded-lg border border-control-border bg-surface-inset text-foreground focus:ring-2 focus:ring-action outline-none transition-all"
          />
          <p className="mt-2 text-xs contrast-helper">
            Added to the install glossary rather than replacing it. If both define
            the same term, yours is used. Applies to generated notes and to
            Meeting Edge; it does not change the transcript, which is produced
            before the glossary is read.
          </p>
        </div>
      </SettingsBlock>
    </SettingsCard>
  );
}
