import { Settings } from "@/types";
import SettingsPanel from "./SettingsPanel";
import SettingsSection from "./SettingsSection";

interface AiHuggingFaceSectionProps {
  settings: Settings;
}

/** Admin-only "Hugging Face access" section. Extracted verbatim from
 * {@link AISettings} so behaviour is unchanged. */
export default function AiHuggingFaceSection({
  settings,
}: AiHuggingFaceSectionProps) {
  return (
    <SettingsSection
      eyebrow="Administration"
      title="Hugging Face access"
      description="View status of the installation token required for diarization and related model downloads."
      width="regular"
    >
      <SettingsPanel className="mx-auto max-w-3xl space-y-4">
        <div className="flex items-center justify-between p-4 bg-gray-50 dark:bg-gray-900 border border-gray-200 dark:border-gray-700 rounded-xl">
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
              <span className="inline-flex items-center px-3 py-1 rounded-full text-xs font-semibold bg-green-100 text-green-800 dark:bg-green-950/40 dark:text-green-400">
                Configured via Server (.env)
              </span>
            ) : (
              <span className="inline-flex items-center px-3 py-1 rounded-full text-xs font-semibold bg-yellow-100 text-yellow-800 dark:bg-yellow-950/40 dark:text-yellow-400">
                Missing Config
              </span>
            )}
          </div>
        </div>
        <p className="text-xs contrast-helper">
          Required for Pyannote speaker diarization. Ensure you have accepted the
          user agreement for{" "}
          <code>pyannote/speaker-diarization-community-1</code> on Hugging Face.
        </p>
      </SettingsPanel>
    </SettingsSection>
  );
}
