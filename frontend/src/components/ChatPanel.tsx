'use client';

import { useState } from 'react';
import { MessageSquare } from 'lucide-react';
import { useParams } from 'next/navigation';

export default function ChatPanel() {
  const params = useParams();
  const recordingId = params?.id;

  return (
    <aside className="w-80 flex-shrink-0 border-l border-gray-400 dark:border-gray-800 bg-gray-300 dark:bg-gray-950 h-screen sticky top-0 flex flex-col">
      <div className="p-4 border-b border-gray-400 dark:border-gray-800">
        <h2 className="text-sm font-semibold text-gray-900 dark:text-gray-100">
          Chat with Meeting
        </h2>
      </div>
      
      <div className="flex-1 p-4 flex flex-col items-center justify-center text-center text-gray-500 dark:text-gray-400">
        <MessageSquare className="w-12 h-12 mb-4 opacity-20" />
        <p className="text-sm">
            {recordingId ? "Chat functionality coming soon." : "Select a meeting to start chatting with it."}
        </p>
      </div>

      <div className="p-4 border-t border-gray-400 dark:border-gray-800">
        <div className="flex gap-2">
            <input 
            type="text" 
            placeholder={recordingId ? "Ask a question..." : "Select a meeting first..."}
            className="flex-1 bg-white dark:bg-gray-900 border border-gray-300 dark:border-gray-700 rounded-md px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-orange-500"
            disabled={!recordingId}
            />
            <button 
                disabled={!recordingId}
                className={`bg-orange-500 text-white px-4 py-2 rounded-md text-sm font-medium ${!recordingId ? 'opacity-50 cursor-not-allowed' : 'hover:bg-orange-600'}`}
            >
                Send
            </button>
        </div>
      </div>
    </aside>
  );
}
