import { useState } from "react";
import type { Settings } from "@/types";
import { InputBase } from "@/components/ui/nativeBases";
import { Switch } from "@/components/ui/Switch";
import SettingsRow from "./SettingsRow";
import { SETTINGS_CONTROL_END, SETTINGS_INPUT_CLASS } from "./settingsControls";

interface Props {
  settings: Settings;
  onPersist: (settings: Settings) => void | Promise<void>;
}

function SuggestionLimit({ settings, onPersist }: Props) {
  const [draft, setDraft] = useState(String(settings.glossary_suggestions_limit ?? 15));
  const [touched, setTouched] = useState(false);
  const value = Number(draft);
  const valid = draft.trim() !== "" && Number.isInteger(value) && value >= 5 && value <= 50;
  return <>
    <InputBase id="glossary-suggestions-limit" type="number" min={5} max={50} step={1}
      value={draft} aria-invalid={touched && !valid}
      aria-describedby={`glossary-suggestions-limit-help${touched && !valid ? " glossary-suggestions-limit-error" : ""}`}
      data-ui-fill="control" data-ui-disabled-fill="control-disabled" data-ui-border="full"
      className={SETTINGS_INPUT_CLASS}
      onChange={(event) => setDraft(event.target.value)}
      onBlur={() => {
        setTouched(true);
        if (valid && value !== (settings.glossary_suggestions_limit ?? 15)) {
          void onPersist({ ...settings, glossary_suggestions_limit: value });
        }
      }} />
    {touched && !valid && <p id="glossary-suggestions-limit-error" role="alert"
      className="mt-1 text-xs text-status-danger-fg">Enter a whole number between 5 and 50.</p>}
  </>;
}

/** Per-user controls for the built-in glossary output, alongside notes structures. */
export default function GlossarySuggestionsSettings({ settings, onPersist }: Props) {
  return <>
    <SettingsRow id="notes-glossary-suggestions" label="Suggest glossary terms automatically"
      labelId="notes-glossary-suggestions-label" descriptionId="notes-glossary-suggestions-help"
      description="Create glossary suggestions after each meeting. Review them in Notes and choose which terms to keep."
      controlClassName={SETTINGS_CONTROL_END}>
      <Switch checked={settings.glossary_suggestions_enabled === true}
        aria-labelledby="notes-glossary-suggestions-label" aria-describedby="notes-glossary-suggestions-help"
        onCheckedChange={(checked) => void onPersist({ ...settings, glossary_suggestions_enabled: checked })} />
    </SettingsRow>
    <SettingsRow id="notes-glossary-suggestions-limit" label="Glossary suggestions per extraction"
      controlId="glossary-suggestions-limit" descriptionId="glossary-suggestions-limit-help"
      description="Suggest between 5 and 50 terms each time. You can extract more from the glossary output.">
      <SuggestionLimit key={settings.glossary_suggestions_limit ?? 15} settings={settings} onPersist={onPersist} />
    </SettingsRow>
  </>;
}
