import { Settings } from "@/types";
import SettingsCard from "./SettingsCard";
import SettingsRow from "./SettingsRow";
import SettingsStatusBadge from "./SettingsStatusBadge";
import { SETTINGS_VALUE_CLASS } from "./settingsControls";

const DIARISATION_MODEL_URL =
  "https://huggingface.co/pyannote/speaker-diarization-community-1";

interface AiHuggingFaceSectionProps {
  settings: Settings;
}

/** Whether the install's Hugging Face token is set. Admins only. */
export default function AiHuggingFaceSection({
  settings,
}: AiHuggingFaceSectionProps) {
  const configured = Boolean(settings.hf_token);

  return (
    <SettingsCard
      id="ai-providers-hf-token"
      title="Hugging Face"
      description="Speaker separation downloads its models from Hugging Face."
    >
      <SettingsRow
        label="Access token"
        description={
          <>
            Set by HF_TOKEN in .env. The account must also have accepted the
            terms of{" "}
            <a
              href={DIARISATION_MODEL_URL}
              target="_blank"
              rel="noopener noreferrer"
              className="font-medium text-action-text underline underline-offset-2 hover:text-action-text-hover"
            >
              pyannote/speaker-diarization-community-1
            </a>
            .
          </>
        }
        badge={
          <SettingsStatusBadge tone={configured ? "success" : "warning"}>
            {configured ? "Ready" : "Not set up"}
          </SettingsStatusBadge>
        }
      >
        <p className={SETTINGS_VALUE_CLASS}>{configured ? "Set" : "Missing"}</p>
      </SettingsRow>
    </SettingsCard>
  );
}
