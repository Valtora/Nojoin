import { Settings } from "@/types";
import SettingsBlock from "./SettingsBlock";
import SettingsCard from "./SettingsCard";
import SettingsStatusBadge from "./SettingsStatusBadge";

interface AiHuggingFaceSectionProps {
  settings: Settings;
}

/** Admin-only "Hugging Face access" section of {@link AISettings}. */
export default function AiHuggingFaceSection({
  settings,
}: AiHuggingFaceSectionProps) {
  return (
    <SettingsCard
      title="Hugging Face access"
      description="View status of the installation token required for diarization and related model downloads."
    >
      <SettingsBlock className="space-y-4">
        <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between p-4 bg-gray-50 dark:bg-gray-900 border border-gray-200 dark:border-gray-700 rounded-xl">
          <div>
            <div className="text-sm font-semibold text-gray-900 dark:text-white flex items-center gap-2">
              Hugging Face Integration
            </div>
            <p className="text-xs text-gray-500 mt-1">
              The access token is configured globally in the server&apos;s
              environment variable file (
              <code className="bg-gray-100 dark:bg-gray-800 px-1 py-0.5 rounded">
                .env
              </code>
              ).
            </p>
          </div>
          <div>
            {settings.hf_token ? (
              <SettingsStatusBadge tone="success">
                Configured via Server (.env)
              </SettingsStatusBadge>
            ) : (
              <SettingsStatusBadge tone="warning">
                Missing Config
              </SettingsStatusBadge>
            )}
          </div>
        </div>
        <p className="text-xs contrast-helper">
          Required for Pyannote speaker diarization. Ensure you have accepted
          the user agreement for{" "}
          <code>pyannote/speaker-diarization-community-1</code> on Hugging Face.
        </p>
      </SettingsBlock>
    </SettingsCard>
  );
}
