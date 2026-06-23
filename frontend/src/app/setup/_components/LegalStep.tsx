import { ArrowRight } from "lucide-react";

export default function LegalStep({ onAccept }: { onAccept: () => void }) {
  return (
    <div className="space-y-6">
      <div className="text-center mb-6">
        <h2 className="text-xl font-semibold text-gray-900 dark:text-white">
          Legal Disclaimer
        </h2>
        <p className="text-sm text-gray-500 dark:text-gray-400">
          Please review and accept the terms of use
        </p>
      </div>

      <div className="prose prose-sm dark:prose-invert max-w-none bg-gray-50 dark:bg-gray-900/50 p-4 rounded-lg border border-gray-200 dark:border-gray-700 max-h-64 overflow-y-auto">
        <h3 className="text-base font-semibold mt-0">
          1. Compliance with Laws
        </h3>
        <p>
          You acknowledge that many legal jurisdictions require the
          consent of all parties before a conversation can be recorded.
          It is your sole responsibility to ensure compliance with all
          applicable laws and regulations regarding audio recording and
          transcription in your jurisdiction.
        </p>

        <h3 className="text-base font-semibold">
          2. Data Privacy & Local Processing
        </h3>
        <p>Nojoin is designed with a privacy-first architecture.</p>
        <ul className="list-disc pl-4 space-y-1">
          <li>
            Nojoin does not store or transmit audio data to third
            parties without your explicit consent.
          </li>
          <li>
            All audio processing (transcription, diarization, etc.) is
            performed locally on your machine or your self-hosted
            server, unless you explicitly configure an external
            provider.
          </li>
        </ul>

        <p className="font-medium mt-4">
          By proceeding, you agree to these terms and accept full
          responsibility for the lawful use of this software.
        </p>
      </div>

      <button
        onClick={onAccept}
        className="w-full bg-orange-600 hover:bg-orange-700 text-white font-medium py-2.5 rounded-lg transition-colors flex items-center justify-center gap-2"
      >
        I Accept & Continue <ArrowRight className="w-4 h-4" />
      </button>
    </div>
  );
}
