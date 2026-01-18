"use client";

import React, { useState, useEffect, useMemo } from "react";
import { X, Plus, Users } from "lucide-react";
import { GlobalSpeaker, PeopleTag } from "@/types";
import ColorPicker from "@/components/ColorPicker";
import { getPeopleTags, createPeopleTag } from "@/lib/api";
import { getColorByKey } from "@/lib/constants";
import { Fingerprint, ArrowRight } from "lucide-react";
import {
  getGlobalSpeakers,
  mergeSpeakers,
  deleteGlobalSpeakerEmbedding,
} from "@/lib/api";
import ConfirmationModal from "@/components/ConfirmationModal";

interface PersonModalProps {
  person: GlobalSpeaker | null; // If null, creating new
  isOpen: boolean;
  onClose: () => void;
  onSave: (
    data: Partial<GlobalSpeaker> & { tag_ids: number[] },
  ) => Promise<void>;
  onDelete?: (id: number) => void;
}

export function PersonModal({
  person,
  isOpen,
  onClose,
  onSave,
  onDelete,
}: PersonModalProps) {
  const [formData, setFormData] = useState<
    Partial<GlobalSpeaker> & { tag_ids: number[] }
  >({
    name: "",
    color: "#3B82F6",
    title: "",
    company: "",
    email: "",
    phone_number: "",
    notes: "",
    tag_ids: [],
  });

  const [isSubmitting, setIsSubmitting] = useState(false);
  const [newTagName, setNewTagName] = useState("");
  const [showTagInput, setShowTagInput] = useState(false);
  const [allTags, setAllTags] = useState<PeopleTag[]>([]);

  // Merge & Voiceprint State
  const [showMerge, setShowMerge] = useState(false);
  const [mergeTarget, setMergeTarget] = useState<GlobalSpeaker | null>(null);
  const [speakerSearch, setSpeakerSearch] = useState("");
  const [availableSpeakers, setAvailableSpeakers] = useState<GlobalSpeaker[]>(
    [],
  );
  const [isDeletingVoiceprint, setIsDeletingVoiceprint] = useState(false);
  const [confirmMerge, setConfirmMerge] = useState<{
    target: GlobalSpeaker;
  } | null>(null);

  const [confirmVoiceprintDelete, setConfirmVoiceprintDelete] = useState(false);

  // Load available tags
  useEffect(() => {
    if (isOpen) {
      getPeopleTags().then(setAllTags).catch(console.error);
    }
  }, [isOpen]);

  const handleCreateTag = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!newTagName.trim()) return;

    try {
      const newTag = await createPeopleTag(newTagName.trim());
      setAllTags((prev) => [...prev, newTag]);
      setFormData((prev) => ({
        ...prev,
        tag_ids: [...prev.tag_ids, newTag.id],
      }));
      setNewTagName("");
      setShowTagInput(false);
    } catch (error) {
      console.error("Failed to create tag:", error);
    }
  };

  useEffect(() => {
    if (isOpen) {
      if (person) {
        setFormData({
          name: person.name,
          color: person.color || "#3B82F6",
          title: person.title || "",
          company: person.company || "",
          email: person.email || "",
          phone_number: person.phone_number || "",
          notes: person.notes || "",
          tag_ids: person.tags?.map((t) => t.id) || [],
        });

        // Load speakers for merge (excluding current person)
        getGlobalSpeakers()
          .then((speakers) => {
            setAvailableSpeakers(speakers.filter((s) => s.id !== person.id));
          })
          .catch(console.error);
      } else {
        setFormData({
          name: "",
          color: "#3B82F6",
          title: "",
          company: "",
          email: "",
          phone_number: "",
          notes: "",
          tag_ids: [],
        });
      }
      setShowMerge(false);
      setMergeTarget(null);
    }
  }, [isOpen, person]);

  const tagTree = useMemo(() => {
    const tagMap = new Map<number, PeopleTag & { children: any[] }>();
    const roots: any[] = [];
    allTags.forEach((tag) => tagMap.set(tag.id, { ...tag, children: [] }));
    allTags.forEach((tag) => {
      const node = tagMap.get(tag.id)!;
      if (tag.parent_id && tagMap.has(tag.parent_id)) {
        tagMap.get(tag.parent_id)!.children.push(node);
      } else {
        roots.push(node);
      }
    });
    return roots;
  }, [allTags]);

  const renderTagSelection = (nodes: any[], level = 0): React.ReactNode => {
    return nodes.map((tag) => (
      <React.Fragment key={tag.id}>
        <button
          type="button"
          onClick={() => toggleTag(tag.id)}
          className={`inline-flex items-center px-2.5 py-1 rounded-full text-xs font-medium border transition-all ${
            formData.tag_ids.includes(tag.id)
              ? "bg-orange-100 text-orange-800 dark:bg-orange-900/40 dark:text-orange-300 border-orange-500 shadow-sm ring-1 ring-orange-500"
              : "bg-white text-gray-600 dark:bg-gray-800 dark:text-gray-400 border-gray-200 dark:border-gray-700 hover:border-orange-400"
          }`}
          style={{ marginLeft: level > 0 ? `${level * 12}px` : "0" }}
        >
          <span
            className={`w-1.5 h-1.5 rounded-full mr-1.5 ${getColorByKey(tag.color || "gray").dot}`}
          />
          {tag.name}
        </button>
        {tag.children.length > 0 && renderTagSelection(tag.children, level + 1)}
      </React.Fragment>
    ));
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!formData.name) return;

    console.log("[PersonModal] Saving data:", formData);

    setIsSubmitting(true);
    try {
      await onSave(formData);
      onClose();
    } catch (error) {
      console.error("Failed to save person:", error);
    } finally {
      setIsSubmitting(false);
    }
  };

  const toggleTag = (tagId: number) => {
    setFormData((prev) => {
      const current = prev.tag_ids;
      if (current.includes(tagId)) {
        return { ...prev, tag_ids: current.filter((id) => id !== tagId) };
      } else {
        return { ...prev, tag_ids: [...current, tagId] };
      }
    });
  };

  const handleMergeClick = () => {
    if (!person || !mergeTarget) return;
    setConfirmMerge({ target: mergeTarget });
  };

  const executeMerge = async () => {
    if (!person || !confirmMerge) return;

    setIsSubmitting(true);
    try {
      await mergeSpeakers(person.id, confirmMerge.target.id);
      onClose();
      // Optimistic update via callback
      if (onDelete) {
        onDelete(person.id);
      } else {
        window.location.reload();
      }
    } catch (error) {
      console.error("Merge failed:", error);
      alert("Failed to merge speakers.");
    } finally {
      setIsSubmitting(false);
      setConfirmMerge(null);
    }
  };

  const handleDeleteVoiceprint = () => {
    if (!person) return;
    setConfirmVoiceprintDelete(true);
  };

  const executeDeleteVoiceprint = async () => {
    if (!person) return;

    setIsDeletingVoiceprint(true);
    try {
      await deleteGlobalSpeakerEmbedding(person.id);
      setIsDeletingVoiceprint(false);
      setConfirmVoiceprintDelete(false);
      alert("Voiceprint deleted.");
      onClose();
    } catch (error) {
      console.error("Failed to delete voiceprint:", error);
      alert("Failed to delete voiceprint.");
      setIsDeletingVoiceprint(false);
    }
  };

  const filteredSpeakers =
    speakerSearch === ""
      ? availableSpeakers
      : availableSpeakers.filter((s) =>
          s.name.toLowerCase().includes(speakerSearch.toLowerCase()),
        );

  if (!isOpen) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 backdrop-blur-sm p-4">
      <div className="bg-white dark:bg-gray-800 rounded-xl shadow-xl w-full max-w-2xl max-h-[90vh] overflow-hidden flex flex-col">
        {/* Header */}
        <div className="px-6 py-4 border-b border-gray-200 dark:border-gray-700 flex justify-between items-center bg-gray-50 dark:bg-gray-800/50">
          <h2 className="text-xl font-semibold text-gray-900 dark:text-gray-100">
            {person ? "Edit Person" : "Add Person"}
          </h2>
          <button
            onClick={onClose}
            className="p-2 hover:bg-gray-200 dark:hover:bg-gray-700 rounded-full transition-colors"
          >
            <X className="w-5 h-5 text-gray-500" />
          </button>
        </div>

        {/* Scrollable Content */}
        <div className="p-6 overflow-y-auto flex-1">
          <form id="person-form" onSubmit={handleSubmit} className="space-y-6">
            {/* Basic Info */}
            <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
              <div className="space-y-2">
                <label className="text-sm font-medium text-gray-700 dark:text-gray-300">
                  Full Name *
                </label>
                <input
                  type="text"
                  required
                  value={formData.name || ""}
                  onChange={(e) =>
                    setFormData({ ...formData, name: e.target.value })
                  }
                  className="w-full px-3 py-2 rounded-lg border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-700 focus:ring-2 focus:ring-orange-500 outline-none"
                  placeholder="e.g. John Doe"
                />
              </div>

              <div className="space-y-2">
                <label className="text-sm font-medium text-gray-700 dark:text-gray-300">
                  Avatar Color
                </label>
                <ColorPicker
                  selectedColor={formData.color || "#3B82F6"}
                  onColorSelect={(color) => setFormData({ ...formData, color })}
                />
              </div>
            </div>

            {/* Contact Info */}
            <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
              <div className="space-y-2">
                <label className="text-sm font-medium text-gray-700 dark:text-gray-300">
                  Title
                </label>
                <input
                  type="text"
                  value={formData.title || ""}
                  onChange={(e) =>
                    setFormData({ ...formData, title: e.target.value })
                  }
                  className="w-full px-3 py-2 rounded-lg border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-700 focus:ring-2 focus:ring-orange-500 outline-none"
                  placeholder="e.g. CEO"
                />
              </div>
              <div className="space-y-2">
                <label className="text-sm font-medium text-gray-700 dark:text-gray-300">
                  Company
                </label>
                <input
                  type="text"
                  value={formData.company || ""}
                  onChange={(e) =>
                    setFormData({ ...formData, company: e.target.value })
                  }
                  className="w-full px-3 py-2 rounded-lg border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-700 focus:ring-2 focus:ring-orange-500 outline-none"
                  placeholder="e.g. Acme Corp"
                />
              </div>
              <div className="space-y-2">
                <label className="text-sm font-medium text-gray-700 dark:text-gray-300">
                  Email
                </label>
                <input
                  type="email"
                  value={formData.email || ""}
                  onChange={(e) =>
                    setFormData({ ...formData, email: e.target.value })
                  }
                  className="w-full px-3 py-2 rounded-lg border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-700 focus:ring-2 focus:ring-orange-500 outline-none"
                  placeholder="john@example.com"
                />
              </div>
              <div className="space-y-2">
                <label className="text-sm font-medium text-gray-700 dark:text-gray-300">
                  Phone
                </label>
                <input
                  type="tel"
                  value={formData.phone_number || ""}
                  onChange={(e) =>
                    setFormData({ ...formData, phone_number: e.target.value })
                  }
                  className="w-full px-3 py-2 rounded-lg border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-700 focus:ring-2 focus:ring-orange-500 outline-none"
                  placeholder="+1 (555) 000-0000"
                />
              </div>
            </div>

            {/* Voiceprint & Merge Section (Only for existing users) */}
            {person && (
              <div className="space-y-6 pt-6 border-t border-gray-200 dark:border-gray-700">
                <h3 className="text-md font-medium text-gray-900 dark:text-gray-100">
                  Voiceprint & Actions
                </h3>

                <div className="flex flex-col gap-4">
                  {/* Voiceprint Status */}
                  <div className="flex items-center justify-between p-3 bg-gray-50 dark:bg-gray-700/50 rounded-lg border border-gray-200 dark:border-gray-600">
                    <div className="flex items-center gap-3">
                      <div
                        className={`p-2 rounded-full ${person.has_voiceprint ? "bg-green-100 text-green-600 dark:bg-green-900/30 dark:text-green-400" : "bg-gray-100 text-gray-400 dark:bg-gray-800 dark:text-gray-500"}`}
                      >
                        <Fingerprint className="w-5 h-5" />
                      </div>
                      <div>
                        <div className="font-medium text-sm text-gray-900 dark:text-gray-100">
                          {person.has_voiceprint
                            ? "Voiceprint Active"
                            : "No Voiceprint"}
                        </div>
                        <div className="text-xs text-gray-500 dark:text-gray-400">
                          {person.has_voiceprint
                            ? "Speaker identification is enabled for this person."
                            : "This person cannot be automatically identified in recordings."}
                        </div>
                      </div>
                    </div>
                    {person.has_voiceprint && (
                      <button
                        type="button"
                        onClick={handleDeleteVoiceprint}
                        disabled={isDeletingVoiceprint}
                        className="text-red-500 hover:text-red-600 text-sm font-medium px-3 py-1.5 hover:bg-red-50 dark:hover:bg-red-900/20 rounded-md transition-colors"
                      >
                        {isDeletingVoiceprint ? "Deleting..." : "Delete"}
                      </button>
                    )}
                  </div>

                  {/* Merge Action */}
                  {!showMerge ? (
                    <button
                      type="button"
                      onClick={() => setShowMerge(true)}
                      className="flex items-center justify-center gap-2 w-full p-2 text-sm text-gray-600 dark:text-gray-400 hover:text-gray-900 dark:hover:text-gray-200 hover:bg-gray-100 dark:hover:bg-gray-700 rounded-lg border border-dashed border-gray-300 dark:border-gray-600 transition-colors"
                    >
                      <Users className="w-4 h-4" />
                      Merge into another person...
                    </button>
                  ) : (
                    <div className="p-4 bg-orange-50 dark:bg-orange-900/10 border border-orange-200 dark:border-orange-800 rounded-lg space-y-3">
                      <div className="flex justify-between items-start">
                        <div>
                          <h4 className="text-sm font-medium text-orange-900 dark:text-orange-100">
                            Merge Person
                          </h4>
                          <p className="text-xs text-orange-700 dark:text-orange-300 mt-1">
                            Merge <strong>{person.name}</strong> into another
                            person. <br />
                            <span className="font-bold text-red-600 dark:text-red-400">
                              Warning:
                            </span>{" "}
                            {person.name} will be deleted.
                          </p>
                        </div>
                        <button
                          onClick={() => {
                            setShowMerge(false);
                            setMergeTarget(null);
                          }}
                          className="text-gray-400 hover:text-gray-600"
                        >
                          <X className="w-4 h-4" />
                        </button>
                      </div>

                      <div className="space-y-2">
                        <label className="text-xs font-medium text-gray-700 dark:text-gray-300">
                          Target Person (Recipient)
                        </label>
                        {!mergeTarget ? (
                          <div className="relative">
                            <input
                              type="text"
                              value={speakerSearch}
                              onChange={(e) => setSpeakerSearch(e.target.value)}
                              className="w-full px-3 py-2 rounded-lg border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-700 focus:ring-2 focus:ring-orange-500 outline-none text-sm"
                              placeholder="Search person..."
                            />
                            {speakerSearch && (
                              <div className="absolute z-10 mt-1 w-full bg-white dark:bg-gray-700 rounded-md shadow-lg border border-gray-200 dark:border-gray-600 max-h-48 overflow-y-auto">
                                {filteredSpeakers.length === 0 ? (
                                  <div className="px-3 py-2 text-sm text-gray-500 dark:text-gray-400">
                                    No people found
                                  </div>
                                ) : (
                                  filteredSpeakers.map((p) => (
                                    <button
                                      key={p.id}
                                      type="button"
                                      onClick={() => {
                                        setMergeTarget(p);
                                        setSpeakerSearch("");
                                      }}
                                      className="w-full text-left px-3 py-2 text-sm hover:bg-orange-50 dark:hover:bg-gray-600 flex items-center justify-between group text-gray-900 dark:text-gray-100"
                                    >
                                      <span>{p.name}</span>
                                      {p.company && (
                                        <span className="text-xs text-gray-500">
                                          {p.company}
                                        </span>
                                      )}
                                    </button>
                                  ))
                                )}
                              </div>
                            )}
                          </div>
                        ) : (
                          <div className="flex items-center justify-between p-2 bg-orange-100 dark:bg-orange-900/30 rounded-lg border border-orange-200 dark:border-orange-800">
                            <div className="flex items-center gap-2">
                              <div className="w-6 h-6 rounded-full bg-orange-200 flex items-center justify-center text-xs font-bold text-orange-800">
                                {mergeTarget.name.charAt(0)}
                              </div>
                              <span className="text-sm font-medium text-gray-900 dark:text-gray-100">
                                {mergeTarget.name}
                              </span>
                            </div>
                            <button
                              type="button"
                              onClick={() => setMergeTarget(null)}
                              className="text-gray-500 hover:text-red-500"
                            >
                              <X className="w-4 h-4" />
                            </button>
                          </div>
                        )}
                      </div>

                      <button
                        type="button"
                        onClick={handleMergeClick}
                        disabled={!mergeTarget || isSubmitting}
                        className="w-full flex items-center justify-center gap-2 px-4 py-2 bg-orange-600 text-white rounded-lg hover:bg-orange-700 transition-colors shadow-sm font-medium disabled:opacity-50 disabled:cursor-not-allowed"
                      >
                        {isSubmitting ? "Merging..." : "Confirm Merge"}
                        <ArrowRight className="w-4 h-4" />
                      </button>
                    </div>
                  )}
                </div>
              </div>
            )}

            {/* Merge Confirmation Modal */}
            <ConfirmationModal
              isOpen={!!confirmMerge}
              onClose={() => setConfirmMerge(null)}
              onConfirm={executeMerge}
              title="Merge Speakers"
              message={`Are you sure you want to merge "${person?.name}" into "${confirmMerge?.target.name}"? This will delete "${person?.name}" and move all data to "${confirmMerge?.target.name}". This action cannot be undone.`}
              confirmText="Confirm Merge"
              isDangerous
            />

            {/* Voiceprint Delete Confirmation Modal */}
            <ConfirmationModal
              isOpen={confirmVoiceprintDelete}
              onClose={() => setConfirmVoiceprintDelete(false)}
              onConfirm={executeDeleteVoiceprint}
              title="Delete Voiceprint"
              message="Are you sure you want to delete this voiceprint? Speaker recognition for this person will stop working until a new voiceprint is created."
              confirmText="Delete Voiceprint"
              isDangerous
            />

            {/* Tags */}
            <div className="space-y-2">
              <div className="flex justify-between items-center">
                <label className="text-sm font-medium text-gray-700 dark:text-gray-300">
                  Tags
                </label>
                <button
                  type="button"
                  onClick={() => setShowTagInput(!showTagInput)}
                  className="text-xs text-orange-600 hover:text-orange-700 flex items-center gap-1"
                >
                  <Plus className="w-3 h-3" /> New Tag
                </button>
              </div>

              {showTagInput && (
                <div className="flex gap-2 mb-2">
                  <input
                    type="text"
                    value={newTagName}
                    onChange={(e) => setNewTagName(e.target.value)}
                    placeholder="New tag name..."
                    className="flex-1 px-3 py-1.5 text-sm rounded-md border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-700 outline-none"
                    onKeyDown={(e) => {
                      if (e.key === "Enter") {
                        e.preventDefault();
                        handleCreateTag(e as any);
                      }
                    }}
                  />
                  <button
                    type="button"
                    onClick={handleCreateTag}
                    className="px-3 py-1.5 text-sm bg-orange-600 text-white rounded-md hover:bg-orange-700"
                  >
                    Add
                  </button>
                </div>
              )}

              <div className="flex flex-wrap gap-2 p-3 border border-gray-200 dark:border-gray-700 rounded-lg bg-gray-50 dark:bg-gray-900/50 min-h-[60px]">
                {allTags.length > 0 ? (
                  renderTagSelection(tagTree)
                ) : (
                  <p className="text-xs text-gray-400 italic w-full text-center">
                    No tags created yet.
                  </p>
                )}
              </div>
            </div>

            {/* Notes */}
            <div className="space-y-2">
              <label className="text-sm font-medium text-gray-700 dark:text-gray-300">
                Notes
              </label>
              <textarea
                value={formData.notes || ""}
                onChange={(e) =>
                  setFormData({ ...formData, notes: e.target.value })
                }
                rows={4}
                className="w-full px-3 py-2 rounded-lg border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-700 focus:ring-2 focus:ring-orange-500 outline-none resize-none"
                placeholder="Additional notes about this person..."
              />
            </div>
          </form>
        </div>

        {/* Footer */}
        <div className="px-6 py-4 border-t border-gray-200 dark:border-gray-700 flex justify-end gap-3 bg-gray-50 dark:bg-gray-800/50">
          <button
            type="button"
            onClick={onClose}
            className="px-4 py-2 text-sm font-medium text-gray-700 dark:text-gray-300 hover:bg-gray-200 dark:hover:bg-gray-700 rounded-lg transition-colors"
          >
            Cancel
          </button>
          <button
            type="submit"
            form="person-form"
            disabled={isSubmitting}
            className="px-4 py-2 text-sm font-medium text-white bg-orange-600 hover:bg-orange-700 rounded-lg transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
          >
            {isSubmitting ? "Saving..." : "Save Person"}
          </button>
        </div>
      </div>
    </div>
  );
}
