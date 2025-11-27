import { Mic, Radio, PlayCircle, FileText } from 'lucide-react';

export const dynamic = 'force-dynamic';

export default function Home() {
  return (
    <div className="flex flex-col items-center justify-center h-full p-8 text-center">
      <div className="bg-orange-100 dark:bg-orange-900/20 p-6 rounded-full mb-6">
        <Mic className="w-12 h-12 text-orange-600 dark:text-orange-500" />
      </div>
      <h1 className="text-3xl font-bold text-gray-900 dark:text-white mb-3">
        Welcome to Nojoin
      </h1>
      <p className="text-gray-500 dark:text-gray-400 max-w-md mb-10 text-lg">
        Your distributed meeting intelligence platform.
      </p>

      <div className="max-w-3xl w-full text-left bg-white dark:bg-gray-800 rounded-2xl border border-gray-200 dark:border-gray-700 shadow-sm overflow-hidden">
        <div className="p-6 border-b border-gray-100 dark:border-gray-700 bg-gray-50/50 dark:bg-gray-800/50 text-center">
          <h3 className="font-semibold text-gray-900 dark:text-white text-lg">Getting Started</h3>
        </div>
        
        <div className="p-6 grid gap-8 md:grid-cols-3">
          <div className="flex flex-col gap-3">
            <div className="w-10 h-10 rounded-lg bg-blue-100 dark:bg-blue-900/30 flex items-center justify-center text-blue-600 dark:text-blue-400">
              <Radio className="w-5 h-5" />
            </div>
            <div>
              <h4 className="font-medium text-gray-900 dark:text-white mb-1">1. Connect</h4>
              <p className="text-sm text-gray-500 dark:text-gray-400 leading-relaxed">
                Ensure the Companion App is running in your system tray to capture audio.
              </p>
            </div>
          </div>

          <div className="flex flex-col gap-3">
            <div className="w-10 h-10 rounded-lg bg-orange-100 dark:bg-orange-900/30 flex items-center justify-center text-orange-600 dark:text-orange-500">
              <PlayCircle className="w-5 h-5" />
            </div>
            <div>
              <h4 className="font-medium text-gray-900 dark:text-white mb-1">2. Record</h4>
              <p className="text-sm text-gray-500 dark:text-gray-400 leading-relaxed">
                Use the controls in the sidebar to start recording a meeting or call.
              </p>
            </div>
          </div>

          <div className="flex flex-col gap-3">
            <div className="w-10 h-10 rounded-lg bg-green-100 dark:bg-green-900/30 flex items-center justify-center text-green-600 dark:text-green-400">
              <FileText className="w-5 h-5" />
            </div>
            <div>
              <h4 className="font-medium text-gray-900 dark:text-white mb-1">3. Review</h4>
              <p className="text-sm text-gray-500 dark:text-gray-400 leading-relaxed">
                Access processed transcripts, speaker insights, and summaries from the dashboard.
              </p>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
