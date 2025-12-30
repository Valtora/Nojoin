"use client";

import React, { useState, useEffect } from "react";
import {
  X,
  Tag as TagIcon,
  Fingerprint,
  Trash2,
  ArrowRight,
} from "lucide-react";
import { GlobalSpeaker, PeopleTag } from "@/types";
import { getPeopleTags } from "@/lib/api";
import { getColorByKey } from "@/lib/constants";

interface BatchEditModalProps {
  isOpen: boolean;
  onClose: () => void;
  selectedCount: number;
  onSave: (updates: BatchUpdates) => Promise<void>;
}

export interface BatchUpdates {
  company?: string;
  title?: string;
  email?: string;
  phone_number?: string;
  tags?: {
    action: "add" | "remove" | "set";
    tagIds: number[];
  };
  deleteVoiceprints?: boolean;
}

export function BatchEditModal({
  isOpen,
  onClose,
  selectedCount,
  onSave,
}: BatchEditModalProps) {
  const [updates, setUpdates] = useState<BatchUpdates>({});
  const [allTags, setAllTags] = useState<PeopleTag[]>([]);
  const [isSubmitting, setIsSubmitting] = useState(false);

  // Tag state
  const [tagAction, setTagAction] = useState<"add" | "remove" | "set">("add");
  const [selectedTagIds, setSelectedTagIds] = useState<number[]>([]);

  useEffect(() => {
    if (isOpen) {
      setUpdates({});
      setSelectedTagIds([]);
      setTagAction("add");
      getPeopleTags().then(setAllTags).catch(console.error);
    }
  }, [isOpen]);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setIsSubmitting(true);

    const finalUpdates = { ...updates };
    if (selectedTagIds.length > 0) {
      finalUpdates.tags = {
        action: tagAction,
        tagIds: selectedTagIds,
      };
    }

    try {
      await onSave(finalUpdates);
      onClose();
    } catch (error) {
      console.error("Batch update failed:", error);
    } finally {
      setIsSubmitting(false);
    }
  };

  const toggleTag = (id: number) => {
    setSelectedTagIds((prev) =>
      prev.includes(id) ? prev.filter((t) => t !== id) : [...prev, id],
    );
  };

  if (!isOpen) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 backdrop-blur-sm p-4">
      <div className="bg-white dark:bg-gray-800 rounded-xl shadow-xl w-full max-w-lg max-h-[90vh] overflow-hidden flex flex-col">
        {/* Header */}
        <div className="px-6 py-4 border-b border-gray-200 dark:border-gray-700 flex justify-between items-center bg-gray-50 dark:bg-gray-800/50">
          <div>
            <h2 className="text-xl font-semibold text-gray-900 dark:text-gray-100">
              Batch Edit People
            </h2>
            <p className="text-sm text-gray-500 dark:text-gray-400">
              Editing {selectedCount} selected people
            </p>
          </div>
          <button
            onClick={onClose}
            className="p-2 hover:bg-gray-200 dark:hover:bg-gray-700 rounded-full transition-colors"
          >
            <X className="w-5 h-5 text-gray-500" />
          </button>
        </div>

        {/* Scrollable Content */}
        <div className="p-6 overflow-y-auto flex-1">
          <form id="batch-form" onSubmit={handleSubmit} className="space-y-6">
            <p className="text-sm text-gray-500 italic">
              Only filled fields will be updated. Leave blank to keep existing
              values.
            </p>

            {/* Fields */}
            <div className="space-y-4">
              <div className="space-y-1">
                <label className="text-sm font-medium text-gray-700 dark:text-gray-300">
                  Set Company to
                </label>
                <input
                  type="text"
                  className="w-full px-3 py-2 rounded-lg border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-700 focus:ring-2 focus:ring-orange-500 outline-none"
                  placeholder="e.g. Acme Corp"
                  value={updates.company || ""}
                  onChange={(e) =>
                    setUpdates({ ...updates, company: e.target.value })
                  }
                />
              </div>
              <div className="space-y-1">
                <label className="text-sm font-medium text-gray-700 dark:text-gray-300">
                  Set Title to
                </label>
                <input
                  type="text"
                  className="w-full px-3 py-2 rounded-lg border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-700 focus:ring-2 focus:ring-orange-500 outline-none"
                  placeholder="e.g. Engineer"
                  value={updates.title || ""}
                  onChange={(e) =>
                    setUpdates({ ...updates, title: e.target.value })
                  }
                />
              </div>
              <div className="space-y-1">
                <label className="text-sm font-medium text-gray-700 dark:text-gray-300">
                  Set Phone Number to
                </label>
                <input
                  type="text"
                  className="w-full px-3 py-2 rounded-lg border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-700 focus:ring-2 focus:ring-orange-500 outline-none"
                  placeholder="+1 555..."
                  value={updates.phone_number || ""}
                  onChange={(e) =>
                    setUpdates({ ...updates, phone_number: e.target.value })
                  }
                />
              </div>
              <div className="space-y-1">
                <label className="text-sm font-medium text-gray-700 dark:text-gray-300">
                  Set Email to
                </label>
                <input
                  type="text"
                  className="w-full px-3 py-2 rounded-lg border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-700 focus:ring-2 focus:ring-orange-500 outline-none"
                  placeholder="email@example.com"
                  value={updates.email || ""}
                  onChange={(e) =>
                    setUpdates({ ...updates, email: e.target.value })
                  }
                />
              </div>
            </div>

            {/* Tags */}
            <div className="pt-4 border-t border-gray-200 dark:border-gray-700 space-y-3">
              <div className="flex items-center justify-between">
                <label className="text-sm font-medium text-gray-700 dark:text-gray-300 flex items-center gap-2">
                  <TagIcon className="w-4 h-4" /> Tags
                </label>
                <div className="flex bg-gray-100 dark:bg-gray-800 rounded-lg p-1">
                  <button
                    type="button"
                    onClick={() => setTagAction("add")}
                    className={`px-3 py-1 text-xs font-medium rounded-md transition-all ${tagAction === "add" ? "bg-white shadow text-orange-600" : "text-gray-500 hover:text-gray-700"}`}
                  >
                    Add
                  </button>
                  <button
                    type="button"
                    onClick={() => setTagAction("remove")}
                    className={`px-3 py-1 text-xs font-medium rounded-md transition-all ${tagAction === "remove" ? "bg-white shadow text-red-600" : "text-gray-500 hover:text-gray-700"}`}
                  >
                    Remove
                  </button>
                  <button
                    type="button"
                    onClick={() => setTagAction("set")}
                    className={`px-3 py-1 text-xs font-medium rounded-md transition-all ${tagAction === "set" ? "bg-white shadow text-blue-600" : "text-gray-500 hover:text-gray-700"}`}
                  >
                    Set (Replace)
                  </button>
                </div>
              </div>

              <div className="flex flex-wrap gap-2 p-3 border border-gray-200 dark:border-gray-700 rounded-lg bg-gray-50 dark:bg-gray-900/50 min-h-[60px]">
                {allTags.map((tag) => (
                  <button
                    key={tag.id}
                    type="button"
                    onClick={() => toggleTag(tag.id)}
                    className={`inline-flex items-center px-2.5 py-1 rounded-full text-xs font-medium border transition-all ${
                      selectedTagIds.includes(tag.id)
                        ? tagAction === "remove"
                          ? "bg-red-100 text-red-800 border-red-500"
                          : "bg-orange-100 text-orange-800 border-orange-500"
                        : "bg-white text-gray-600 border-gray-200 hover:border-gray-400"
                    }`}
                  >
                    {tag.name}
                  </button>
                ))}
                {allTags.length === 0 && (
                  <span className="text-xs text-gray-400 italic">
                    No tags available
                  </span>
                )}
              </div>
            </div>

            {/* Voiceprints */}
            <div className="pt-4 border-t border-gray-200 dark:border-gray-700">
              <label className="flex items-center gap-3 p-3 rounded-lg border border-gray-200 dark:border-gray-700 hover:bg-gray-50 dark:hover:bg-gray-800/50 cursor-pointer transition-colors">
                <input
                  type="checkbox"
                  className="rounded border-gray-300 text-red-600 focus:ring-red-500 w-4 h-4"
                  checked={updates.deleteVoiceprints || false}
                  onChange={(e) =>
                    setUpdates({
                      ...updates,
                      deleteVoiceprints: e.target.checked,
                    })
                  }
                />
                <div className="flex-1">
                  <div className="flex items-center gap-2 text-gray-900 dark:text-gray-100 font-medium text-sm">
                    <Fingerprint className="w-4 h-4 text-gray-500" />
                    Delete Voiceprints
                  </div>
                  <p className="text-xs text-gray-500">
                    Remove voiceprints for all selected people. They will no
                    longer be identified in recordings.
                  </p>
                </div>
              </label>
            </div>
          </form>
        </div>

        {/* Footer */}
        <div className="px-6 py-4 border-t border-gray-200 dark:border-gray-700 bg-gray-50 dark:bg-gray-800/50 flex justify-end gap-3 rounded-b-xl">
          <button
            type="button"
            onClick={onClose}
            className="px-4 py-2 text-sm font-medium text-gray-700 dark:text-gray-300 hover:bg-gray-200 dark:hover:bg-gray-700 rounded-lg transition-colors"
          >
            Cancel
          </button>
          <button
            type="submit"
            form="batch-form"
            disabled={isSubmitting}
            className="px-4 py-2 text-sm font-medium bg-orange-600 text-white hover:bg-orange-700 rounded-lg shadow-sm transition-colors flex items-center gap-2 disabled:opacity-50 disabled:cursor-not-allowed"
          >
            {isSubmitting ? "Saving..." : "Save Changes"}
            <ArrowRight className="w-4 h-4" />
          </button>
        </div>
      </div>
    </div>
  );
}
