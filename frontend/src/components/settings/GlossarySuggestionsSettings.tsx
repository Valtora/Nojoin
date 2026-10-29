import type { Settings } from "@/types";
import { SelectBase } from "@/components/ui/nativeBases";
import { Switch } from "@/components/ui/Switch";
import SettingsRow from "./SettingsRow";
import { SETTINGS_CONTROL_END, SETTINGS_SELECT_CLASS } from "./settingsControls";

interface Props {
  settings: Settings;
  onPersist: (settings: Settings) => void | Promise<void>;
}

/** 5 to 50, the bounds the settings API accepts. */
const LIMITS = Array.from({ length: 10 }, (_, index) => (index + 1) * 5);

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
      description="How many terms to suggest each time. You can extract more from the glossary output.">
      <SelectBase id="glossary-suggestions-limit" aria-describedby="glossary-suggestions-limit-help"
        data-ui-fill="control" data-ui-disabled-fill="control-disabled" data-ui-border="full"
        className={SETTINGS_SELECT_CLASS} value={settings.glossary_suggestions_limit ?? 15}
        onChange={(event) => void onPersist({ ...settings, glossary_suggestions_limit: Number(event.target.value) })}>
        {LIMITS.map((limit) => <option key={limit} value={limit}>{limit}</option>)}
      </SelectBase>
    </SettingsRow>
  </>;
}
