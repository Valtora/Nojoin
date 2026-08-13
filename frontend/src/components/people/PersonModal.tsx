"use client";

import React, { useState, useEffect, useMemo, useRef } from "react";
import { X, Plus, Users, Fingerprint, ArrowRight } from "lucide-react";
import Button from "@/components/ui/Button";
import Modal from "@/components/ui/Modal";
import { GlobalSpeaker, PeopleTag } from "@/types";
import ColorPicker from "@/components/ColorPicker";
import { getPeopleTags, createPeopleTag } from "@/lib/api";
import { getColorByKey } from "@/lib/constants";
import {
  getGlobalSpeakers,
  mergeSpeakers,
  deleteGlobalSpeakerEmbedding,
} from "@/lib/api";
import ConfirmationModal from "@/components/ConfirmationModal";
import { useAnchoredPanel } from "@/components/ui/useAnchoredPanel";
import { useNotificationStore } from "@/lib/notificationStore";

interface PersonModalProps {
  person: GlobalSpeaker | null; // If null, creating new
  isOpen: boolean;
  onClose: () => void;
  onSave: (
    data: Partial<GlobalSpeaker> & { tag_ids: number[] },
  ) => Promise<void>;
  onDelete?: (id: number) => void;
  onVoiceprintDeleted?: (id: number) => void;
}

export function PersonModal({
  person,
  isOpen,
  onClose,
  onSave,
  onDelete,
  onVoiceprintDeleted,
}: PersonModalProps) {
  const { addNotification } = useNotificationStore();
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
  // The merge section is the last thing in this modal, so its results list opens
  // into the region the modal's panel clips. Anchor it to the window instead.
  const speakerSearchRef = useRef<HTMLDivElement>(null);
  const { panelRef: speakerResultsRef, panelStyle: speakerResultsStyle } =
    useAnchoredPanel<HTMLDivElement>(Boolean(speakerSearch), speakerSearchRef, {
      matchAnchorWidth: true,
    });
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

  const handleCreateTag = async (e?: React.SyntheticEvent) => {
    e?.preventDefault();
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

        } catch (error: unknown) {
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

interface TagNode extends PeopleTag {
  children: TagNode[];
}

  const tagTree = useMemo(() => {
    const tagMap = new Map<number, TagNode>();
    const roots: TagNode[] = [];
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

  const renderTagSelection = (nodes: TagNode[], level = 0): React.ReactNode => {
    return nodes.map((tag) => (
      <React.Fragment key={tag.id}>
        <button
          type="button"
          onClick={() => toggleTag(tag.id)}
          className={`inline-flex items-center px-2.5 py-1 rounded-full text-xs font-medium border transition-all ${
            formData.tag_ids.includes(tag.id)
              ? "bg-action-tint text-action-text border-action shadow-card ring-1 ring-action"
              : "bg-surface-card text-contrast-helper text-contrast-icon-muted border-surface-border hover:border-action-border"
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


    setIsSubmitting(true);
    try {
      await onSave(formData);
      onClose();

        } catch (error: unknown) {
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

        } catch (error: unknown) {
      console.error("Merge failed:", error);
      addNotification({
        type: "error",
        message: "Failed to merge speakers.",
      });
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
      onVoiceprintDeleted?.(person.id);
      addNotification({
        type: "success",
        message: "Voiceprint deleted.",
      });
      onClose();

        } catch (error: unknown) {
      console.error("Failed to delete voiceprint:", error);
      addNotification({
        type: "error",
        message: "Failed to delete voiceprint.",
      });
    } finally {
      setIsDeletingVoiceprint(false);
    }
  };

  const filteredSpeakers =
    speakerSearch === ""
      ? availableSpeakers
      : availableSpeakers.filter((s) =>
          s.name.toLowerCase().includes(speakerSearch.toLowerCase()),
        );

  return (
    <Modal
      open={isOpen}
      onClose={onClose}
      size="lg"
      className="max-h-[90dvh]"
      title={person ? "Edit Person" : "Add Person"}
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button
            type="submit"
            form="person-form"
            variant="primary"
            disabled={isSubmitting}
            loading={isSubmitting}
          >
            {isSubmitting ? "Saving..." : "Save Person"}
          </Button>
        </>
      }
    >
          <form id="person-form" onSubmit={handleSubmit} className="space-y-6">
            {/* Basic Info */}
            <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
              <div className="space-y-2">
                <label className="text-sm font-medium text-contrast-muted">
                  Full Name *
                </label>
                <input
                  type="text"
                  required
                  value={formData.name || ""}
                  onChange={(e) =>
                    setFormData({ ...formData, name: e.target.value })
                  }
                  className="w-full px-3 py-2 rounded-lg border border-control-border bg-control-bg focus:ring-2 focus-visible:outline-focus-ring outline-none"
                  placeholder="e.g. John Doe"
                />
              </div>

              <div className="space-y-2">
                <label className="text-sm font-medium text-contrast-muted">
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
                <label className="text-sm font-medium text-contrast-muted">
                  Title
                </label>
                <input
                  type="text"
                  value={formData.title || ""}
                  onChange={(e) =>
                    setFormData({ ...formData, title: e.target.value })
                  }
                  className="w-full px-3 py-2 rounded-lg border border-control-border bg-control-bg focus:ring-2 focus-visible:outline-focus-ring outline-none"
                  placeholder="e.g. CEO"
                />
              </div>
              <div className="space-y-2">
                <label className="text-sm font-medium text-contrast-muted">
                  Company
                </label>
                <input
                  type="text"
                  value={formData.company || ""}
                  onChange={(e) =>
                    setFormData({ ...formData, company: e.target.value })
                  }
                  className="w-full px-3 py-2 rounded-lg border border-control-border bg-control-bg focus:ring-2 focus-visible:outline-focus-ring outline-none"
                  placeholder="e.g. Acme Corp"
                />
              </div>
              <div className="space-y-2">
                <label className="text-sm font-medium text-contrast-muted">
                  Email
                </label>
                <input
                  type="email"
                  value={formData.email || ""}
                  onChange={(e) =>
                    setFormData({ ...formData, email: e.target.value })
                  }
                  className="w-full px-3 py-2 rounded-lg border border-control-border bg-control-bg focus:ring-2 focus-visible:outline-focus-ring outline-none"
                  placeholder="john@example.com"
                />
              </div>
              <div className="space-y-2">
                <label className="text-sm font-medium text-contrast-muted">
                  Phone
                </label>
                <input
                  type="tel"
                  value={formData.phone_number || ""}
                  onChange={(e) =>
                    setFormData({ ...formData, phone_number: e.target.value })
                  }
                  className="w-full px-3 py-2 rounded-lg border border-control-border bg-control-bg focus:ring-2 focus-visible:outline-focus-ring outline-none"
                  placeholder="+1 (555) 000-0000"
                />
              </div>
            </div>

            {/* Voiceprint & Merge Section (Only for existing users) */}
            {person && (
              <div className="space-y-6 pt-6 border-t border-surface-border">
                <h3 className="text-md font-medium text-foreground">
                  Voiceprint & Actions
                </h3>

                <div className="flex flex-col gap-4">
                  {/* Voiceprint Status */}
                  <div className="flex items-center justify-between p-3 bg-surface-inset rounded-lg border border-surface-border">
                    <div className="flex items-center gap-3">
                      <div
                        className={`p-2 rounded-full ${person.has_voiceprint ? "bg-status-success-bg text-status-success-fg" : "bg-surface-inset text-contrast-icon-muted text-contrast-helper"}`}
                      >
                        <Fingerprint className="w-5 h-5" />
                      </div>
                      <div>
                        <div className="font-medium text-sm text-foreground">
                          {person.has_voiceprint
                            ? "Voiceprint Active"
                            : "No Voiceprint"}
                        </div>
                        <div className="text-xs text-contrast-helper">
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
                        className="text-danger-text hover:text-danger-text text-sm font-medium px-3 py-1.5 hover:bg-status-danger-bg rounded-md transition-colors"
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
                      className="flex items-center justify-center gap-2 w-full p-2 text-sm text-contrast-helper hover:text-foreground hover:text-contrast-icon-muted hover:bg-surface-inset rounded-lg border border-dashed border-control-border transition-colors"
                    >
                      <Users className="w-4 h-4" />
                      Merge into another person...
                    </button>
                  ) : (
                    <div className="p-4 bg-action-tint border border-action-border rounded-lg space-y-3">
                      <div className="flex justify-between items-start">
                        <div>
                          <h4 className="text-sm font-medium text-action-text">
                            Merge Person
                          </h4>
                          <p className="text-xs text-action-text mt-1">
                            Merge <strong>{person.name}</strong> into another
                            person. <br />
                            <span className="font-bold text-status-danger-fg">
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
                          className="text-contrast-icon-muted hover:text-contrast-helper"
                        >
                          <X className="w-4 h-4" />
                        </button>
                      </div>

                      <div className="space-y-2">
                        <label className="text-xs font-medium text-contrast-muted">
                          Target Person (Recipient)
                        </label>
                        {!mergeTarget ? (
                          <div className="relative" ref={speakerSearchRef}>
                            <input
                              type="text"
                              value={speakerSearch}
                              onChange={(e) => setSpeakerSearch(e.target.value)}
                              className="w-full px-3 py-2 rounded-lg border border-control-border bg-control-bg focus:ring-2 focus-visible:outline-focus-ring outline-none text-sm"
                              placeholder="Search person..."
                            />
                            {speakerSearch && (
                              <div
                                ref={speakerResultsRef}
                                style={speakerResultsStyle}
                                className="z-10 bg-control-bg rounded-md shadow-float border border-surface-border overflow-y-auto"
                              >
                                {filteredSpeakers.length === 0 ? (
                                  <div className="px-3 py-2 text-sm text-contrast-helper">
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
                                      className="w-full text-left px-3 py-2 text-sm hover:bg-action-tint flex items-center justify-between group text-foreground"
                                    >
                                      <span>{p.name}</span>
                                      {p.company && (
                                        <span className="text-xs text-contrast-helper">
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
                          <div className="flex items-center justify-between p-2 bg-action-tint rounded-lg border border-action-border">
                            <div className="flex items-center gap-2">
                              <div className="w-6 h-6 rounded-full bg-action-tint flex items-center justify-center text-xs font-bold text-action-text">
                                {mergeTarget.name.charAt(0)}
                              </div>
                              <span className="text-sm font-medium text-foreground">
                                {mergeTarget.name}
                              </span>
                            </div>
                            <button
                              type="button"
                              onClick={() => setMergeTarget(null)}
                              className="text-contrast-helper hover:text-danger-text"
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
                        className="w-full flex items-center justify-center gap-2 px-4 py-2 bg-action text-action-on rounded-lg hover:bg-action-hover transition-colors shadow-card font-medium disabled:opacity-50 disabled:cursor-not-allowed"
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
                <label className="text-sm font-medium text-contrast-muted">
                  Tags
                </label>
                <button
                  type="button"
                  onClick={() => setShowTagInput(!showTagInput)}
                  className="text-xs text-action-text hover:text-action-text-hover flex items-center gap-1"
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
                    className="flex-1 px-3 py-1.5 text-sm rounded-md border border-control-border bg-control-bg outline-none"
                    onKeyDown={(e) => {
                      if (e.key === "Enter") {
                        e.preventDefault();
                        handleCreateTag(e);
                      }
                    }}
                  />
                  <button
                    type="button"
                    onClick={handleCreateTag}
                    className="px-3 py-1.5 text-sm bg-action text-action-on rounded-md hover:bg-action-hover"
                  >
                    Add
                  </button>
                </div>
              )}

              <div className="flex flex-wrap gap-2 p-3 border border-surface-border rounded-lg bg-surface-inset min-h-[60px]">
                {allTags.length > 0 ? (
                  renderTagSelection(tagTree)
                ) : (
                  <p className="text-xs text-contrast-icon-muted italic w-full text-center">
                    No tags created yet.
                  </p>
                )}
              </div>
            </div>

            {/* Notes */}
            <div className="space-y-2">
              <label className="text-sm font-medium text-contrast-muted">
                Notes
              </label>
              <textarea
                value={formData.notes || ""}
                onChange={(e) =>
                  setFormData({ ...formData, notes: e.target.value })
                }
                rows={4}
                className="w-full px-3 py-2 rounded-lg border border-control-border bg-control-bg focus:ring-2 focus-visible:outline-focus-ring outline-none resize-none"
                placeholder="Additional notes about this person..."
              />
            </div>
          </form>
    </Modal>
  );
}
