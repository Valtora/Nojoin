"use client";

import { useNavigationStore } from "@/lib/store";
import { Archive, Trash2, RotateCcw, Tag, X } from "lucide-react";
import { useState } from "react";
import ConfirmationModal from "./ConfirmationModal";
import BatchTagModal from "./BatchTagModal";
import {
  batchArchiveRecordings,
  batchRestoreRecordings,
  batchSoftDeleteRecordings,
  batchPermanentlyDeleteRecordings,
  batchAddTagToRecordings,
  batchRemoveTagFromRecordings,
} from "@/lib/api";
import { useNotificationStore } from "@/lib/notificationStore";

interface BatchActionBarProps {
  onActionComplete: () => void;
}

export default function BatchActionBar({
  onActionComplete,
}: BatchActionBarProps) {
  const { selectedRecordingIds, clearSelection, currentView } =
    useNavigationStore();
  const { addNotification } = useNotificationStore();

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

  const [tagModal, setTagModal] = useState<{
    isOpen: boolean;
    mode: "add" | "remove";
  }>({
    isOpen: false,
    mode: "add",
  });

  if (selectedRecordingIds.length === 0) return null;

  const handleArchive = async () => {
    try {
      await batchArchiveRecordings(selectedRecordingIds);
      addNotification({
        type: "success",
        message: `Archived ${selectedRecordingIds.length} recordings`,
      });
      onActionComplete();
      clearSelection();

        } catch (error: unknown) {
      console.error("Batch archive failed:", error);
      addNotification({
        type: "error",
        message: "Failed to archive recordings",
      });
    }
  };

  const handleRestore = async () => {
    try {
      await batchRestoreRecordings(selectedRecordingIds);
      addNotification({
        type: "success",
        message: `Restored ${selectedRecordingIds.length} recordings`,
      });
      onActionComplete();
      clearSelection();

        } catch (error: unknown) {
      console.error("Batch restore failed:", error);
      addNotification({
        type: "error",
        message: "Failed to restore recordings",
      });
    }
  };

  const handleSoftDelete = async () => {
    try {
      await batchSoftDeleteRecordings(selectedRecordingIds);
      addNotification({
        type: "success",
        message: `Moved ${selectedRecordingIds.length} recordings to trash`,
      });
      onActionComplete();
      clearSelection();

        } catch (error: unknown) {
      console.error("Batch delete failed:", error);
      addNotification({
        type: "error",
        message: "Failed to delete recordings",
      });
    }
  };

  const handlePermanentDelete = () => {
    setConfirmModal({
      isOpen: true,
      title: "Permanently Delete Recordings",
      message: `Are you sure you want to permanently delete ${selectedRecordingIds.length} recordings? This action cannot be undone.`,
      isDangerous: true,
      onConfirm: async () => {
        try {
          await batchPermanentlyDeleteRecordings(selectedRecordingIds);
          addNotification({
            type: "success",
            message: `Permanently deleted ${selectedRecordingIds.length} recordings`,
          });
          onActionComplete();
          clearSelection();

                } catch (error: unknown) {
          console.error("Batch permanent delete failed:", error);
          addNotification({
            type: "error",
            message: "Failed to permanently delete recordings",
          });
        }
      },
    });
  };

  const handleTagAction = async (tagName: string) => {
    try {
      if (tagModal.mode === "add") {
        await batchAddTagToRecordings(selectedRecordingIds, tagName);
        addNotification({
          type: "success",
          message: `Added tag "${tagName}" to ${selectedRecordingIds.length} recordings`,
        });
      } else {
        await batchRemoveTagFromRecordings(selectedRecordingIds, tagName);
        addNotification({
          type: "success",
          message: `Removed tag "${tagName}" from ${selectedRecordingIds.length} recordings`,
        });
      }
      window.dispatchEvent(new CustomEvent("tags-updated"));
      onActionComplete();
      clearSelection();

        } catch (error: unknown) {
      console.error("Batch tag action failed:", error);
      addNotification({ type: "error", message: "Failed to update tags" });
    }
  };

  return (
    <>
      <div className="absolute bottom-4 left-4 right-4 bg-surface-card rounded-lg shadow-lg border border-surface-border p-3 flex items-center justify-between z-20 animate-in slide-in-from-bottom-4 duration-200">
        <div className="flex items-center gap-3">
          <span className="bg-action-tint text-action-text px-2 py-1 rounded-md text-xs font-medium">
            {selectedRecordingIds.length} selected
          </span>
          <button
            onClick={clearSelection}
            className="text-contrast-helper hover:text-contrast-muted"
          >
            <X className="w-4 h-4" />
          </button>
        </div>

        <div className="flex items-center gap-2">
          {/* Tag Actions */}
          <div className="flex gap-1 border-r border-surface-border pr-2 mr-1">
            <button
              onClick={() => setTagModal({ isOpen: true, mode: "add" })}
              className="p-2 text-contrast-helper hover:bg-surface-inset rounded-md"
              title="Add Tag"
            >
              <Tag className="w-4 h-4" />
            </button>
          </div>

          {/* View Specific Actions */}
          {currentView === "recordings" && (
            <>
              <button
                onClick={handleArchive}
                className="p-2 text-contrast-helper hover:bg-surface-inset rounded-md"
                title="Archive"
              >
                <Archive className="w-4 h-4" />
              </button>
              <button
                onClick={handleSoftDelete}
                className="p-2 text-status-danger-fg hover:bg-status-danger-bg rounded-md"
                title="Delete"
              >
                <Trash2 className="w-4 h-4" />
              </button>
            </>
          )}

          {currentView === "archived" && (
            <>
              <button
                onClick={handleRestore}
                className="p-2 text-contrast-helper hover:bg-surface-inset rounded-md"
                title="Restore"
              >
                <RotateCcw className="w-4 h-4" />
              </button>
              <button
                onClick={handleSoftDelete}
                className="p-2 text-status-danger-fg hover:bg-status-danger-bg rounded-md"
                title="Delete"
              >
                <Trash2 className="w-4 h-4" />
              </button>
            </>
          )}

          {currentView === "deleted" && (
            <>
              <button
                onClick={handleRestore}
                className="p-2 text-contrast-helper hover:bg-surface-inset rounded-md"
                title="Restore"
              >
                <RotateCcw className="w-4 h-4" />
              </button>
              <button
                onClick={handlePermanentDelete}
                className="p-2 text-status-danger-fg hover:bg-status-danger-bg rounded-md"
                title="Permanently Delete"
              >
                <Trash2 className="w-4 h-4" />
              </button>
            </>
          )}
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

      <BatchTagModal
        isOpen={tagModal.isOpen}
        onClose={() => setTagModal({ ...tagModal, isOpen: false })}
        onApply={handleTagAction}
        count={selectedRecordingIds.length}
        mode={tagModal.mode}
      />
    </>
  );
}
