import { useEffect, useState } from "react";

import { Switch } from "@/components/ui/Switch";
import { getThinkingSupport } from "@/lib/api";
import SettingsBlock from "./SettingsBlock";
import SettingsCallout from "./SettingsCallout";
import SettingsRow from "./SettingsRow";
import { SETTINGS_CONTROL_END } from "./settingsControls";

interface OllamaThinkingRowProps {
  /** Prefix for the row's element ids; unique per card. */
  idPrefix: string;
  /** The selected Ollama model. Empty until one is chosen. */
  model: string;
  /** Ask the fallback provider's server instead of the default one. */
  secondary?: boolean;
  checked: boolean;
  onCheckedChange: (checked: boolean) => void;
}

type Support = "none" | "checking" | "supported" | "unsupported" | "unknown";

const STATUS_TEXT: Record<Support, string> = {
  none: "Choose a model to see whether it supports thinking.",
  checking: "Checking whether this model supports thinking…",
  supported: "This model supports thinking.",
  unsupported:
    "This model doesn't support thinking, so Nojoin leaves the setting out of its requests. Your choice is kept for other models.",
  unknown: "Nojoin couldn't check whether this model supports thinking.",
};

/**
 * Whether the provider's Ollama model reasons before it answers, with what its
 * server says about the selected model beside the switch.
 *
 * The lookup runs again whenever the model changes, saved or not, so the status
 * follows the picker. It lives in this row's own state: nothing else on the page
 * reads it, and it changes only when a model is picked. A model known not to
 * think disables the switch but keeps its value, and an unknown answer never
 * touches the switch, because the server sends the setting either way.
 */
export default function OllamaThinkingRow({
  idPrefix,
  model,
  secondary = false,
  checked,
  onCheckedChange,
}: OllamaThinkingRowProps) {
  // The answer is keyed by what it was asked, so a stale one reads as checking.
  const asked = `${secondary ? "secondary" : "primary"}:${model}`;
  const [answer, setAnswer] = useState<{
    asked: string;
    supported: boolean | null;
  } | null>(null);

  useEffect(() => {
    if (!model) return;
    let current = true;
    getThinkingSupport(model, secondary)
      .then((result) => result.supported)
      .catch(() => null)
      .then((supported) => {
        if (current) setAnswer({ asked, supported });
      });
    return () => {
      current = false;
    };
  }, [asked, model, secondary]);

  let support: Support = "none";
  if (model) {
    if (answer?.asked !== asked) support = "checking";
    else if (answer.supported === true) support = "supported";
    else if (answer.supported === false) support = "unsupported";
    else support = "unknown";
  }

  const labelId = `${idPrefix}-think-label`;
  const helpId = `${idPrefix}-think-help`;

  return (
    <>
      <SettingsRow
        label="Thinking"
        labelId={labelId}
        descriptionId={helpId}
        description={
          <>
            Lets a model that supports it reason before it answers. Off is
            faster: a model that thinks can take minutes to its first word, more
            than Nojoin waits for notes. It applies to the Meeting Edge model
            too.{" "}
            <span aria-live="polite" className="font-medium text-foreground">
              {STATUS_TEXT[support]}
            </span>
          </>
        }
        controlClassName={SETTINGS_CONTROL_END}
      >
        <Switch
          aria-labelledby={labelId}
          aria-describedby={helpId}
          checked={checked}
          disabled={support === "unsupported"}
          onCheckedChange={onCheckedChange}
        />
      </SettingsRow>

      {support === "unknown" && checked && (
        <SettingsBlock>
          <SettingsCallout
            tone="warning"
            title="Thinking support couldn't be confirmed"
          >
            If this model doesn&apos;t support thinking, Ollama may refuse requests
            with an error (HTTP 400). Turn thinking off or choose another model
            if notes or chat start failing.
          </SettingsCallout>
        </SettingsBlock>
      )}
    </>
  );
}
