'use client';

import { useState } from 'react';
import { Settings, FileInput, MessageSquare, Users, Upload } from 'lucide-react';
import Link from 'next/link';
import { useParams } from 'next/navigation';
import GlobalSpeakersModal from './GlobalSpeakersModal';
import SettingsModal from './SettingsModal';

export default function ChatPanel() {
  const [isSpeakersModalOpen, setIsSpeakersModalOpen] = useState(false);
  const [isSettingsModalOpen, setIsSettingsModalOpen] = useState(false);
  const params = useParams();
  const recordingId = params?.id;

  return (
    <aside className="w-80 flex-shrink-0 border-l border-gray-200 dark:border-gray-800 bg-gray-50 dark:bg-gray-950 h-screen sticky top-0 flex flex-col">
      <div className="p-4 border-b border-gray-200 dark:border-gray-800 flex justify-end space-x-2">
        <button
            onClick={() => setIsSpeakersModalOpen(true)}
            className="p-2 bg-orange-600 text-white rounded-full hover:bg-orange-700 shadow-lg transition-colors"
            title="Global Speakers"
        >
            <Users className="w-5 h-5" />
        </button>
        <button
            onClick={() => alert("Import Audio coming soon!")}
            className="p-2 bg-orange-600 text-white rounded-full hover:bg-orange-700 shadow-lg transition-colors"
            title="Import Audio"
        >
            <Upload className="w-5 h-5" />
        </button>
        <button
            onClick={() => setIsSettingsModalOpen(true)}
            className="p-2 bg-orange-600 text-white rounded-full hover:bg-orange-700 shadow-lg transition-colors"
            title="Settings"
        >
            <Settings className="w-5 h-5" />
        </button>
      </div>
      
      <div className="flex-1 p-4 flex flex-col items-center justify-center text-center text-gray-500 dark:text-gray-400">
        <MessageSquare className="w-12 h-12 mb-4 opacity-20" />
        <p className="text-sm">
            {recordingId ? "Chat functionality coming soon." : "Select a meeting to start chatting with it."}
        </p>
      </div>

      <div className="p-4 border-t border-gray-200 dark:border-gray-800">
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

      <GlobalSpeakersModal 
        isOpen={isSpeakersModalOpen} 
        onClose={() => setIsSpeakersModalOpen(false)} 
      />
      
      <SettingsModal
        isOpen={isSettingsModalOpen}
        onClose={() => setIsSettingsModalOpen(false)}
      />
    </aside>
  );
}
