import { Mic, ArrowLeft, ArrowRight } from 'lucide-react';

export const dynamic = 'force-dynamic';

export default function Home() {
  return (
    <div className="flex flex-col items-center justify-center h-full p-8 text-center">
      <div className="bg-orange-100 dark:bg-orange-900/20 p-6 rounded-full mb-6">
        <Mic className="w-12 h-12 text-orange-600 dark:text-orange-500" />
      </div>
      <h1 className="text-2xl font-bold text-gray-900 dark:text-white mb-2">
        Welcome to Nojoin
      </h1>
      <p className="text-gray-500 dark:text-gray-400 max-w-md mb-8">
        Your distributed meeting intelligence platform.
      </p>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-6 max-w-2xl w-full text-left">
        <div className="p-6 bg-white dark:bg-gray-800 rounded-xl border border-gray-200 dark:border-gray-700 shadow-sm">
            <div className="flex items-center gap-3 mb-3 text-orange-600 dark:text-orange-500">
                <ArrowLeft className="w-5 h-5" />
                <h3 className="font-semibold">Record a Meeting</h3>
            </div>
            <p className="text-sm text-gray-500 dark:text-gray-400">
                Use the controls in the sidebar to start recording system audio and microphone input via the Companion App.
            </p>
        </div>

        <div className="p-6 bg-white dark:bg-gray-800 rounded-xl border border-gray-200 dark:border-gray-700 shadow-sm">
            <div className="flex items-center justify-end gap-3 mb-3 text-orange-600 dark:text-orange-500">
                <h3 className="font-semibold">Import & Settings</h3>
                <ArrowRight className="w-5 h-5" />
            </div>
            <p className="text-sm text-gray-500 dark:text-gray-400 text-right">
                Use the panel on the right to import existing audio files, manage global speakers, or configure application settings.
            </p>
        </div>
      </div>
    </div>
  );
}
