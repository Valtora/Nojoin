"use client";

import React, { useState, useEffect } from "react";
import { Tag as TagIcon, Fingerprint, ArrowRight } from "lucide-react";
import Button from "@/components/ui/Button";
import Input from "@/components/ui/Input";
import Modal from "@/components/ui/Modal";
import { PeopleTag } from "@/types";
import { getPeopleTags } from "@/lib/api";

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

        } catch (error: unknown) {
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

  const tagModeClass = (active: boolean) =>
    `rounded-md px-3 py-1 text-xs font-medium transition-colors ${
      active ? "bg-surface-card text-action-text shadow-card" : "text-contrast-helper hover:text-foreground"
    }`;

  return (
    <Modal
      open={isOpen}
      onClose={onClose}
      size="md"
      className="max-h-[90dvh]"
      title={
        <span>
          <span className="block text-xl">Batch Edit People</span>
          <span className="block text-sm font-normal text-contrast-helper">
            Editing {selectedCount} selected people
          </span>
        </span>
      }
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button
            type="submit"
            form="batch-form"
            variant="primary"
            disabled={isSubmitting}
            loading={isSubmitting}
            iconRight={<ArrowRight aria-hidden="true" className="w-4 h-4" />}
          >
            {isSubmitting ? "Saving..." : "Save Changes"}
          </Button>
        </>
      }
    >
      <form id="batch-form" onSubmit={handleSubmit} className="space-y-6">
        <p className="text-sm italic text-contrast-helper">
          Only filled fields will be updated. Leave blank to keep existing
          values.
        </p>

        {/* Fields */}
        <div className="space-y-4">
          <Input
            label="Set Company to"
            type="text"
            placeholder="e.g. Acme Corp"
            value={updates.company || ""}
            onChange={(e) => setUpdates({ ...updates, company: e.target.value })}
          />
          <Input
            label="Set Title to"
            type="text"
            placeholder="e.g. Engineer"
            value={updates.title || ""}
            onChange={(e) => setUpdates({ ...updates, title: e.target.value })}
          />
          <Input
            label="Set Phone Number to"
            type="text"
            placeholder="+1 555..."
            value={updates.phone_number || ""}
            onChange={(e) => setUpdates({ ...updates, phone_number: e.target.value })}
          />
          <Input
            label="Set Email to"
            type="text"
            placeholder="email@example.com"
            value={updates.email || ""}
            onChange={(e) => setUpdates({ ...updates, email: e.target.value })}
          />
        </div>

        {/* Tags */}
        <div className="space-y-3 border-t border-surface-divider pt-4">
          <div className="flex items-center justify-between">
            <label className="flex items-center gap-2 text-sm font-medium text-contrast-muted">
              <TagIcon aria-hidden="true" className="w-4 h-4" /> Tags
            </label>
            <div className="flex rounded-lg bg-surface-inset p-1">
              <button
                type="button"
                onClick={() => setTagAction("add")}
                className={tagModeClass(tagAction === "add")}
              >
                Add
              </button>
              <button
                type="button"
                onClick={() => setTagAction("remove")}
                className={tagModeClass(tagAction === "remove")}
              >
                Remove
              </button>
              <button
                type="button"
                onClick={() => setTagAction("set")}
                className={tagModeClass(tagAction === "set")}
              >
                Set (Replace)
              </button>
            </div>
          </div>

          <div className="flex min-h-[60px] flex-wrap gap-2 rounded-lg border border-surface-border bg-surface-inset p-3">
            {allTags.map((tag) => (
              <button
                key={tag.id}
                type="button"
                onClick={() => toggleTag(tag.id)}
                className={`inline-flex items-center rounded-full border px-2.5 py-1 text-xs font-medium transition-colors ${
                  selectedTagIds.includes(tag.id)
                    ? tagAction === "remove"
                      ? "border-status-danger-border bg-status-danger-bg text-status-danger-fg"
                      : "border-action-border bg-action-tint text-action-tint-fg"
                    : "border-surface-border bg-surface-card text-contrast-helper hover:border-action-border"
                }`}
              >
                {tag.name}
              </button>
            ))}
            {allTags.length === 0 && (
              <span className="text-xs italic text-contrast-icon-muted">
                No tags available
              </span>
            )}
          </div>
        </div>

        {/* Voiceprints */}
        <div className="border-t border-surface-divider pt-4">
          <label className="flex cursor-pointer items-center gap-3 rounded-lg border border-surface-border p-3 transition-colors hover:bg-surface-inset">
            <input
              type="checkbox"
              className="h-4 w-4 rounded border-control-border accent-danger"
              checked={updates.deleteVoiceprints || false}
              onChange={(e) =>
                setUpdates({
                  ...updates,
                  deleteVoiceprints: e.target.checked,
                })
              }
            />
            <div className="flex-1">
              <div className="flex items-center gap-2 text-sm font-medium text-foreground">
                <Fingerprint aria-hidden="true" className="w-4 h-4 text-contrast-icon-muted" />
                Delete Voiceprints
              </div>
              <p className="text-xs text-contrast-helper">
                Remove voiceprints for all selected people. They will no
                longer be identified in recordings.
              </p>
            </div>
          </label>
        </div>
      </form>
    </Modal>
  );
}
