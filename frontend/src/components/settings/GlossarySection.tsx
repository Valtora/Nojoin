"use client";

import { TextareaBase } from "@/components/ui/nativeBases";
import { Settings } from "@/types";
import { cn } from "@/lib/cn";
import SettingsBlock from "./SettingsBlock";
import SettingsCard from "./SettingsCard";
import { SETTINGS_TEXTAREA_CLASS } from "./settingsControls";

interface GlossarySectionProps {
  settings: Settings;
  /** Debounced apply (free text). */
  onUpdate: (newSettings: Settings) => void;
  isAdmin?: boolean;
}

const PLACEHOLDER = `Nojoin: our meeting transcription product
ARR: annual recurring revenue
Kubernetes, heard as "cubanetties"`;

const GLOSSARY_CLASS = cn(SETTINGS_TEXTAREA_CLASS, "min-h-36 font-mono text-xs");

/**
 * "Glossary" (issue #137).
 *
 * Two tiers that merge rather than replace: an admin maintains the
 * organisation's vocabulary, each user adds their own, and a personal entry wins
 * where both define the same term. Feeds the notes prompt and Meeting Edge. It
 * cannot correct the transcript itself, which is produced before any of this is
 * consulted, so the wording below says so rather than implying otherwise.
 *
 * Everyone sees the shared list, read-only unless they are an admin, because
 * their own list is described in terms of it.
 */
export default function GlossarySection({
  settings,
  onUpdate,
  isAdmin = false,
}: GlossarySectionProps) {
  const shared = settings.install_glossary_terms || "";

  return (
    <SettingsCard
      id="transcription-glossary"
      title="Glossary"
      description="Names, acronyms and products the AI should spell correctly, and words it often mishears. Notes and Meeting Edge use it; the transcript is written before it is read, so it does not change."
    >
      <SettingsBlock
        id="transcription-shared-glossary"
        label={<label htmlFor="glossary-shared">Shared glossary</label>}
        description={
          isAdmin
            ? "Applies to everyone on this installation. One term per line, as Term: meaning."
            : "Applies to everyone on this installation. Your administrator maintains it."
        }
      >
        {isAdmin ? (
          <TextareaBase
            id="glossary-shared"
            value={shared}
            onChange={(event) =>
              onUpdate({ ...settings, install_glossary_terms: event.target.value })
            }
            rows={6}
            spellCheck={false}
            placeholder={PLACEHOLDER}
            className={GLOSSARY_CLASS}
          />
        ) : shared.trim() ? (
          <TextareaBase
            id="glossary-shared"
            value={shared}
            readOnly
            rows={Math.min(8, Math.max(3, shared.split("\n").length))}
            className={cn(GLOSSARY_CLASS, "min-h-0 bg-control-disabled-bg")}
          />
        ) : (
          <p id="glossary-shared" className="text-sm contrast-helper">
            No shared terms yet.
          </p>
        )}
      </SettingsBlock>

      <SettingsBlock
        label={<label htmlFor="glossary-personal">Your glossary</label>}
        description="Added to the shared glossary. Where both define a term, yours is used. One term per line, as Term: meaning."
      >
        <TextareaBase
          id="glossary-personal"
          value={settings.glossary_terms || ""}
          onChange={(event) =>
            onUpdate({ ...settings, glossary_terms: event.target.value })
          }
          rows={6}
          spellCheck={false}
          placeholder={PLACEHOLDER}
          className={GLOSSARY_CLASS}
        />
      </SettingsBlock>
    </SettingsCard>
  );
}
