import { X, Check, Trash2, Edit2, Palette } from "lucide-react";
import { useState } from "react";

// Since I don't know if shadcn/ui is installed, I'll build a custom modal using Tailwind.
// The user has `ConfirmationModal.tsx`, I can check that for style.

interface SpeakerManagementModalProps {
  isOpen: boolean;
  onClose: () => void;
  speakers: string[]; // List of speaker names
  speakerMap: Record<string, string>; // label -> name
  colorMap: Record<string, string>; // name -> color class
  onRename: (oldName: string, newName: string) => void;
  onColorChange: (speakerName: string, colorClass: string) => void;
  availableColors: string[];
}

export default function SpeakerManagementModal({
  isOpen,
  onClose,
  speakers,
  speakerMap,
  colorMap,
  onRename,
  onColorChange,
  availableColors
}: SpeakerManagementModalProps) {
  const [editingSpeaker, setEditingSpeaker] = useState<string | null>(null);
  const [editValue, setEditValue] = useState("");
  const [openColorPicker, setOpenColorPicker] = useState<string | null>(null);

  if (!isOpen) return null;

  // Filter speakers to only those present in the transcript (passed via speakers prop)
  const uniqueSpeakerNames = Array.from(new Set(speakers.map(label => speakerMap[label] || label))).sort();

  const handleStartEdit = (name: string) => {
    setEditingSpeaker(name);
    setEditValue(name);
  };

  const handleSaveEdit = () => {
    if (editingSpeaker && editValue.trim() && editValue !== editingSpeaker) {
      onRename(editingSpeaker, editValue.trim());
    }
    setEditingSpeaker(null);
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 backdrop-blur-sm">
      <div className="bg-white dark:bg-gray-800 rounded-xl shadow-xl w-full max-w-2xl max-h-[80vh] flex flex-col">
        <div className="flex items-center justify-between p-6 border-b border-gray-200 dark:border-gray-700">
          <h2 className="text-xl font-bold text-gray-900 dark:text-white">Speaker Management</h2>
          <button onClick={onClose} className="text-gray-500 hover:text-gray-700 dark:hover:text-gray-300">
            <X className="w-5 h-5" />
          </button>
        </div>
        
        <div className="flex-1 overflow-y-auto p-6">
          <div className="space-y-4">
            {uniqueSpeakerNames.map((name) => (
              <div key={name} className="flex items-center justify-between p-3 bg-gray-50 dark:bg-gray-700/50 rounded-lg border border-gray-100 dark:border-gray-700">
                <div className="flex items-center gap-3 flex-1">
                  {/* Color Picker Trigger */}
                  <div className="relative">
                    <button 
                        className={`w-6 h-6 rounded-full border ${colorMap[name] || 'bg-gray-200 border-gray-300'} hover:scale-110 transition-transform`}
                        onClick={() => setOpenColorPicker(openColorPicker === name ? null : name)}
                        title="Change color"
                    />
                    {openColorPicker === name && (
                        <div className="absolute left-0 top-full mt-2 p-2 bg-white dark:bg-gray-800 rounded-lg shadow-xl border border-gray-200 dark:border-gray-700 grid grid-cols-4 gap-1 z-10 w-32">
                            {availableColors.map((color, i) => (
                                <button
                                    key={i}
                                    className={`w-6 h-6 rounded-full border ${color} hover:scale-110 transition-transform`}
                                    onClick={() => {
                                        onColorChange(name, color);
                                        setOpenColorPicker(null);
                                    }}
                                />
                            ))}
                        </div>
                    )}
                  </div>

                  {editingSpeaker === name ? (
                    <div className="flex items-center gap-2 flex-1">
                        <input
                            autoFocus
                            value={editValue}
                            onChange={(e) => setEditValue(e.target.value)}
                            className="flex-1 px-2 py-1 text-sm border rounded dark:bg-gray-800 dark:border-gray-600"
                            onKeyDown={(e) => {
                                if (e.key === 'Enter') handleSaveEdit();
                                if (e.key === 'Escape') setEditingSpeaker(null);
                            }}
                        />
                        <button onClick={handleSaveEdit} className="text-green-600 hover:text-green-700">
                            <Check className="w-4 h-4" />
                        </button>
                        <button onClick={() => setEditingSpeaker(null)} className="text-red-500 hover:text-red-600">
                            <X className="w-4 h-4" />
                        </button>
                    </div>
                  ) : (
                    <span 
                        className="font-medium text-gray-900 dark:text-white cursor-pointer hover:underline"
                        onClick={() => handleStartEdit(name)}
                    >
                        {name}
                    </span>
                  )}
                </div>

                <div className="flex items-center gap-2">
                    {/* Placeholder for Merge/Delete actions */}
                    <button 
                        className="p-2 text-gray-400 hover:text-blue-500 hover:bg-blue-50 dark:hover:bg-blue-900/20 rounded-full transition-colors"
                        title="Rename"
                        onClick={() => handleStartEdit(name)}
                    >
                        <Edit2 className="w-4 h-4" />
                    </button>
                </div>
              </div>
            ))}
            {uniqueSpeakerNames.length === 0 && (
                <p className="text-center text-gray-500 py-4">No speakers found.</p>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
