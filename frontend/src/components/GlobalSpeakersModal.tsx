'use client';

import { useState, useEffect } from 'react';
import { GlobalSpeaker } from '@/types';
import { getGlobalSpeakers, updateGlobalSpeaker, mergeSpeakers, deleteGlobalSpeaker } from '@/lib/api';
import { X, Edit2, Merge, Save, Trash2 } from 'lucide-react';
import ConfirmationModal from './ConfirmationModal';

interface GlobalSpeakersModalProps {
  isOpen: boolean;
  onClose: () => void;
}

export default function GlobalSpeakersModal({ isOpen, onClose }: GlobalSpeakersModalProps) {
  const [speakers, setSpeakers] = useState<GlobalSpeaker[]>([]);
  const [loading, setLoading] = useState(true);
  const [editingId, setEditingId] = useState<number | null>(null);
  const [editValue, setEditValue] = useState("");
  const [mergeSource, setMergeSource] = useState<GlobalSpeaker | null>(null);
  const [searchQuery, setSearchQuery] = useState("");
  
  // Confirmation Modal State
  const [confirmModal, setConfirmModal] = useState<{
    isOpen: boolean;
    title: string;
    message: string;
    onConfirm: () => void;
    isDangerous?: boolean;
  }>({
    isOpen: false,
    title: "",
    message: "",
    onConfirm: () => {},
  });

  const fetchSpeakers = async () => {
    setLoading(true);
    try {
      const data = await getGlobalSpeakers();
      setSpeakers(data);
    } catch (e) {
      console.error("Failed to fetch speakers", e);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    if (isOpen) {
      fetchSpeakers();
    }
  }, [isOpen]);

  const handleRename = async (id: number) => {
    if (!editValue.trim()) return;
    try {
      await updateGlobalSpeaker(id, editValue.trim());
      setEditingId(null);
      fetchSpeakers();
    } catch (e) {
      console.error("Failed to rename", e);
      alert("Failed to rename speaker. Name might already exist.");
    }
  };

  const handleDelete = (speaker: GlobalSpeaker) => {
    setConfirmModal({
        isOpen: true,
        title: "Delete Speaker",
        message: `Are you sure you want to delete "${speaker.name}"? This will remove the global speaker association from all recordings.`,
        isDangerous: true,
        onConfirm: async () => {
            try {
                await deleteGlobalSpeaker(speaker.id);
                fetchSpeakers();
            } catch (e) {
                console.error("Failed to delete", e);
                alert("Failed to delete speaker.");
            }
        }
    });
  };

  const handleMergeClick = (speaker: GlobalSpeaker) => {
    setMergeSource(speaker);
  };

  const handleMergeConfirm = (targetId: number) => {
    if (!mergeSource) return;
    const targetName = speakers.find(s => s.id === targetId)?.name;
    
    setConfirmModal({
        isOpen: true,
        title: "Merge Speakers",
        message: `Are you sure you want to merge "${mergeSource.name}" into "${targetName}"? This cannot be undone.`,
        isDangerous: true,
        onConfirm: async () => {
            try {
                await mergeSpeakers(mergeSource.id, targetId);
                setMergeSource(null);
                fetchSpeakers();
            } catch (e) {
                console.error("Failed to merge", e);
                alert("Failed to merge speakers.");
            }
        }
    });
  };

  if (!isOpen) return null;

  const filteredSpeakers = speakers.filter(speaker => 
    speaker.name.toLowerCase().includes(searchQuery.toLowerCase())
  );

  return (
    <>
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 backdrop-blur-sm">
      <div className="bg-white dark:bg-gray-900 rounded-xl shadow-xl w-full max-w-2xl max-h-[80vh] flex flex-col border border-gray-200 dark:border-gray-800">
        <div className="p-6 border-b border-gray-200 dark:border-gray-800 flex justify-between items-center">
          <h2 className="text-xl font-bold text-gray-900 dark:text-white">Global Speakers</h2>
          <button onClick={onClose} className="text-gray-500 hover:text-gray-700 dark:hover:text-gray-300">
            <X className="w-6 h-6" />
          </button>
        </div>
        
        <div className="px-6 pt-4">
            <input
                type="text"
                placeholder="Search speakers..."
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
                className="w-full px-4 py-2 bg-gray-50 dark:bg-gray-800 border border-gray-200 dark:border-gray-700 rounded-lg text-sm focus:ring-2 focus:ring-orange-500 outline-none"
            />
        </div>

        <div className="flex-1 overflow-y-auto p-6">
          {loading ? (
            <div className="text-center py-8 text-gray-500">Loading...</div>
          ) : (
            <div className="space-y-2">
              {mergeSource && (
                <div className="bg-blue-50 dark:bg-blue-900/20 p-4 rounded-lg mb-4 border border-blue-200 dark:border-blue-800">
                    <p className="text-sm text-blue-800 dark:text-blue-200 mb-2">
                        Select a target speaker to merge <strong>{mergeSource.name}</strong> into:
                    </p>
                    <button 
                        onClick={() => setMergeSource(null)}
                        className="text-xs text-blue-600 dark:text-blue-400 hover:underline"
                    >
                        Cancel Merge
                    </button>
                </div>
              )}

              {filteredSpeakers.map((speaker) => (
                <div key={speaker.id} className="flex items-center justify-between p-3 bg-gray-50 dark:bg-gray-800 rounded-lg border border-gray-100 dark:border-gray-700">
                  {editingId === speaker.id ? (
                    <div className="flex items-center gap-2 flex-1">
                        <input
                            autoFocus
                            type="text"
                            value={editValue}
                            onChange={(e) => setEditValue(e.target.value)}
                            className="flex-1 p-1 text-sm border rounded dark:bg-gray-700 dark:border-gray-600"
                        />
                        <button onClick={() => handleRename(speaker.id)} className="p-1 text-green-600 hover:bg-green-50 rounded">
                            <Save className="w-4 h-4" />
                        </button>
                        <button onClick={() => setEditingId(null)} className="p-1 text-gray-500 hover:bg-gray-100 rounded">
                            <X className="w-4 h-4" />
                        </button>
                    </div>
                  ) : (
                    <>
                        <span className="font-medium text-gray-900 dark:text-gray-100">{speaker.name}</span>
                        <div className="flex items-center gap-1">
                            {mergeSource ? (
                                mergeSource.id !== speaker.id && (
                                    <button 
                                        onClick={() => handleMergeConfirm(speaker.id)}
                                        className="px-3 py-1 text-xs bg-blue-600 text-white rounded-full hover:bg-blue-700"
                                    >
                                        Merge Here
                                    </button>
                                )
                            ) : (
                                <>
                                    <button 
                                        onClick={() => { setEditingId(speaker.id); setEditValue(speaker.name); }}
                                        className="p-2 text-gray-500 hover:text-blue-600 hover:bg-blue-50 dark:hover:bg-blue-900/20 rounded-full transition-colors"
                                        title="Rename"
                                    >
                                        <Edit2 className="w-4 h-4" />
                                    </button>
                                    <button 
                                        onClick={() => handleMergeClick(speaker)}
                                        className="p-2 text-gray-500 hover:text-orange-600 hover:bg-orange-50 dark:hover:bg-orange-900/20 rounded-full transition-colors"
                                        title="Merge into another speaker"
                                    >
                                        <Merge className="w-4 h-4" />
                                    </button>
                                    <button 
                                        onClick={() => handleDelete(speaker)}
                                        className="p-2 text-gray-500 hover:text-red-600 hover:bg-red-50 dark:hover:bg-red-900/20 rounded-full transition-colors"
                                        title="Delete"
                                    >
                                        <Trash2 className="w-4 h-4" />
                                    </button>
                                </>
                            )}
                        </div>
                    </>
                  )}
                </div>
              ))}
              
              {speakers.length === 0 && (
                <div className="text-center py-8 text-gray-500">
                    No global speakers found.
                </div>
              )}
            </div>
          )}
        </div>
      </div>
    </div>

    <ConfirmationModal
        isOpen={confirmModal.isOpen}
        onClose={() => setConfirmModal({ ...confirmModal, isOpen: false })}
        onConfirm={confirmModal.onConfirm}
        title={confirmModal.title}
        message={confirmModal.message}
        isDangerous={confirmModal.isDangerous}
    />
    </>
  );
}
