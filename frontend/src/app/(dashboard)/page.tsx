import { Radio, PlayCircle, FileText } from 'lucide-react';
import Image from 'next/image';

export const dynamic = 'force-dynamic';

export default function Home() {
  return (
    <div className="flex flex-col items-center justify-center h-full p-8 text-center">
      <div className="mb-8">
        <Image 
          src="/assets/NojoinLogo.png" 
          alt="Nojoin Logo" 
          width={180} 
          height={180}
          priority
          className="h-auto w-auto"
        />
      </div>
      <h1 className="text-3xl font-bold text-gray-900 dark:text-white mb-3">
        Welcome to Nojoin
      </h1>
      <p className="text-gray-500 dark:text-gray-400 max-w-md mb-10 text-lg">
        Your self-hosted meeting intelligence platform.
      </p>

      <div className="max-w-4xl w-full bg-white dark:bg-gray-800 rounded-2xl border border-gray-200 dark:border-gray-700 shadow-sm overflow-hidden">
        <div className="p-6 border-b border-gray-100 dark:border-gray-700 bg-gray-50/50 dark:bg-gray-800/50 text-center">
          <h3 className="font-semibold text-gray-900 dark:text-white text-lg">Getting Started</h3>
        </div>
        
        <div className="p-8 grid gap-8 md:grid-cols-3">
          <div className="flex flex-col items-center text-center gap-4 p-4 rounded-xl hover:bg-gray-50 dark:hover:bg-gray-700/30 transition-colors duration-200">
            <div className="w-12 h-12 rounded-xl bg-blue-100 dark:bg-blue-900/30 flex items-center justify-center text-blue-600 dark:text-blue-400 shadow-sm">
              <Radio className="w-6 h-6" />
            </div>
            <div>
              <h4 className="font-medium text-gray-900 dark:text-white mb-2">1. Connect</h4>
              <p className="text-sm text-gray-500 dark:text-gray-400 leading-relaxed">
                Ensure the Companion App is running in your system tray to capture audio.
              </p>
            </div>
          </div>

          <div className="flex flex-col items-center text-center gap-4 p-4 rounded-xl hover:bg-gray-50 dark:hover:bg-gray-700/30 transition-colors duration-200">
            <div className="w-12 h-12 rounded-xl bg-orange-100 dark:bg-orange-900/30 flex items-center justify-center text-orange-600 dark:text-orange-500 shadow-sm">
              <PlayCircle className="w-6 h-6" />
            </div>
            <div>
              <h4 className="font-medium text-gray-900 dark:text-white mb-2">2. Record</h4>
              <p className="text-sm text-gray-500 dark:text-gray-400 leading-relaxed">
                Use the controls in the sidebar to start recording a meeting or call.
              </p>
            </div>
          </div>

          <div className="flex flex-col items-center text-center gap-4 p-4 rounded-xl hover:bg-gray-50 dark:hover:bg-gray-700/30 transition-colors duration-200">
            <div className="w-12 h-12 rounded-xl bg-green-100 dark:bg-green-900/30 flex items-center justify-center text-green-600 dark:text-green-400 shadow-sm">
              <FileText className="w-6 h-6" />
            </div>
            <div>
              <h4 className="font-medium text-gray-900 dark:text-white mb-2">3. Review</h4>
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
