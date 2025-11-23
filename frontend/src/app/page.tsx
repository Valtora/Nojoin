import { Mic } from 'lucide-react';

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
      <p className="text-gray-500 dark:text-gray-400 max-w-md">
        Select a recording from the sidebar to view transcripts, notes, and insights.
      </p>
    </div>
  );
}
