"use client";

import { useState, useEffect } from "react";
import { X, FileText, StickyNote, Files, Download, Music } from "lucide-react";
import { ExportContentType, ExportFormat } from "@/lib/api";

interface ExportModalProps {
  isOpen: boolean;
  onClose: () => void;
  onExport: (contentType: ExportContentType, format: ExportFormat) => void;
  hasNotes: boolean;
}

export default function ExportModal({
  isOpen,
  onClose,
  onExport,
  hasNotes,
}: ExportModalProps) {
  const [selected, setSelected] = useState<ExportContentType>("transcript");
  const [format, setFormat] = useState<ExportFormat>("txt");

  // Reset selection when modal opens
  useEffect(() => {
    if (isOpen) {
      setSelected("transcript");
    }
  }, [isOpen]);

  if (!isOpen) return null;

  const handleExport = () => {
    onExport(selected, format);
    onClose();
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center">
      {/* Backdrop */}
      <div
        className="absolute inset-0 bg-scrim"
        onClick={onClose}
      />

      {/* Modal */}
      <div className="relative bg-surface-card rounded-xl shadow-2xl w-full max-w-md mx-4 overflow-hidden">
        {/* Header */}
        <div className="flex items-center justify-between px-6 py-4 border-b border-surface-border">
          <h2 className="text-lg font-semibold text-foreground">
            Export Content
          </h2>
          <button
            onClick={onClose}
            className="p-1 text-contrast-icon-muted hover:text-contrast-helper hover:text-contrast-icon-muted rounded-md hover:bg-surface-inset transition-colors"
          >
            <X className="w-5 h-5" />
          </button>
        </div>

        {/* Content */}
        <div className="p-6 space-y-4">
          <p className="text-sm text-contrast-helper mb-4">
            Choose what you want to export:
          </p>

          {/* Options */}
          <div className="space-y-3">
            {/* Transcript Option */}
            <label
              className={`flex items-center gap-4 p-4 rounded-lg border-2 cursor-pointer transition-colors ${
                selected === "transcript"
                  ? "border-action bg-action-tint"
                  : "border-surface-border hover:border-control-border"
              }`}
            >
              <input
                type="radio"
                name="exportType"
                value="transcript"
                checked={selected === "transcript"}
                onChange={() => setSelected("transcript")}
                className="sr-only"
              />
              <div
                className={`w-10 h-10 rounded-lg flex items-center justify-center ${
                  selected === "transcript"
                    ? "bg-action-tint text-action-text"
                    : "bg-surface-inset text-contrast-helper"
                }`}
              >
                <FileText className="w-5 h-5" />
              </div>
              <div className="flex-1">
                <div className="font-medium text-foreground">
                  Transcript Only
                </div>
                <div className="text-sm text-contrast-helper">
                  Export the diarized transcript with timestamps
                </div>
              </div>
              <div
                className={`w-5 h-5 rounded-full border-2 flex items-center justify-center ${
                  selected === "transcript"
                    ? "border-action bg-action"
                    : "border-control-border"
                }`}
              >
                {selected === "transcript" && (
                  <div className="w-2 h-2 rounded-full bg-surface-card" />
                )}
              </div>
            </label>

            {/* Notes Option */}
            <label
              className={`flex items-center gap-4 p-4 rounded-lg border-2 transition-colors ${
                !hasNotes
                  ? "opacity-50 cursor-not-allowed border-surface-border"
                  : selected === "notes"
                    ? "border-action bg-action-tint cursor-pointer"
                    : "border-surface-border hover:border-control-border cursor-pointer"
              }`}
            >
              <input
                type="radio"
                name="exportType"
                value="notes"
                checked={selected === "notes"}
                onChange={() => hasNotes && setSelected("notes")}
                disabled={!hasNotes}
                className="sr-only"
              />
              <div
                className={`w-10 h-10 rounded-lg flex items-center justify-center ${
                  selected === "notes"
                    ? "bg-action-tint text-action-text"
                    : "bg-surface-inset text-contrast-helper"
                }`}
              >
                <StickyNote className="w-5 h-5" />
              </div>
              <div className="flex-1">
                <div className="font-medium text-foreground">
                  Notes Only
                </div>
                <div className="text-sm text-contrast-helper">
                  {hasNotes
                    ? "Export the AI-generated meeting notes"
                    : "No notes available - generate notes first"}
                </div>
              </div>
              <div
                className={`w-5 h-5 rounded-full border-2 flex items-center justify-center ${
                  selected === "notes"
                    ? "border-action bg-action"
                    : "border-control-border"
                }`}
              >
                {selected === "notes" && (
                  <div className="w-2 h-2 rounded-full bg-surface-card" />
                )}
              </div>
            </label>

            {/* Both Option */}
            <label
              className={`flex items-center gap-4 p-4 rounded-lg border-2 transition-colors ${
                !hasNotes
                  ? "opacity-50 cursor-not-allowed border-surface-border"
                  : selected === "both"
                    ? "border-action bg-action-tint cursor-pointer"
                    : "border-surface-border hover:border-control-border cursor-pointer"
              }`}
            >
              <input
                type="radio"
                name="exportType"
                value="both"
                checked={selected === "both"}
                onChange={() => hasNotes && setSelected("both")}
                disabled={!hasNotes}
                className="sr-only"
              />
              <div
                className={`w-10 h-10 rounded-lg flex items-center justify-center ${
                  selected === "both"
                    ? "bg-action-tint text-action-text"
                    : "bg-surface-inset text-contrast-helper"
                }`}
              >
                <Files className="w-5 h-5" />
              </div>
              <div className="flex-1">
                <div className="font-medium text-foreground">
                  Both
                </div>
                <div className="text-sm text-contrast-helper">
                  {hasNotes
                    ? "Export transcript and notes in a single file"
                    : "No notes available - generate notes first"}
                </div>
              </div>
              <div
                className={`w-5 h-5 rounded-full border-2 flex items-center justify-center ${
                  selected === "both"
                    ? "border-action bg-action"
                    : "border-control-border"
                }`}
              >
                {selected === "both" && (
                  <div className="w-2 h-2 rounded-full bg-surface-card" />
                )}
              </div>
            </label>

            {/* Audio Option */}
            <label
              className={`flex items-center gap-4 p-4 rounded-lg border-2 cursor-pointer transition-colors ${
                selected === "audio"
                  ? "border-action bg-action-tint"
                  : "border-surface-border hover:border-control-border"
              }`}
            >
              <input
                type="radio"
                name="exportType"
                value="audio"
                checked={selected === "audio"}
                onChange={() => setSelected("audio")}
                className="sr-only"
              />
              <div
                className={`w-10 h-10 rounded-lg flex items-center justify-center ${
                  selected === "audio"
                    ? "bg-action-tint text-action-text"
                    : "bg-surface-inset text-contrast-helper"
                }`}
              >
                <Music className="w-5 h-5" />
              </div>
              <div className="flex-1">
                <div className="font-medium text-foreground">
                  Audio File (.mp3)
                </div>
                <div className="text-sm text-contrast-helper">
                  Export the proxy audio file of the recording
                </div>
              </div>
              <div
                className={`w-5 h-5 rounded-full border-2 flex items-center justify-center ${
                  selected === "audio"
                    ? "border-action bg-action"
                    : "border-control-border"
                }`}
              >
                {selected === "audio" && (
                  <div className="w-2 h-2 rounded-full bg-surface-card" />
                )}
              </div>
            </label>
          </div>

          {selected !== "audio" && (
            <>
              <div className="border-t border-surface-border my-4" />

              <p className="text-sm text-contrast-helper mb-2">
                Choose format:
              </p>

              <div className="space-y-3">
            <label
              className={`flex items-center gap-4 p-4 rounded-lg border-2 cursor-pointer transition-colors ${
                format === "txt"
                  ? "border-action bg-action-tint"
                  : "border-surface-border hover:border-control-border"
              }`}
            >
              <input
                type="radio"
                name="exportFormat"
                value="txt"
                checked={format === "txt"}
                onChange={() => setFormat("txt")}
                className="sr-only"
              />
              <div
                className={`w-10 h-10 rounded-lg flex items-center justify-center ${
                  format === "txt"
                    ? "bg-action-tint text-action-text"
                    : "bg-surface-inset text-contrast-helper"
                }`}
              >
                <FileText className="w-5 h-5" />
              </div>
              <div className="flex-1">
                <div className="font-medium text-foreground">
                  Text File (.txt)
                </div>
                <div className="text-sm text-contrast-helper">
                  Simple text format
                </div>
              </div>
              <div
                className={`w-5 h-5 rounded-full border-2 flex items-center justify-center ${
                  format === "txt"
                    ? "border-action bg-action"
                    : "border-control-border"
                }`}
              >
                {format === "txt" && (
                  <div className="w-2 h-2 rounded-full bg-surface-card" />
                )}
              </div>
            </label>

            <label
              className={`flex items-center gap-4 p-4 rounded-lg border-2 cursor-pointer transition-colors ${
                format === "pdf"
                  ? "border-action bg-action-tint"
                  : "border-surface-border hover:border-control-border"
              }`}
            >
              <input
                type="radio"
                name="exportFormat"
                value="pdf"
                checked={format === "pdf"}
                onChange={() => setFormat("pdf")}
                className="sr-only"
              />
              <div
                className={`w-10 h-10 rounded-lg flex items-center justify-center ${
                  format === "pdf"
                    ? "bg-action-tint text-action-text"
                    : "bg-surface-inset text-contrast-helper"
                }`}
              >
                <FileText className="w-5 h-5" />
              </div>
              <div className="flex-1">
                <div className="font-medium text-foreground">
                  PDF Document (.pdf)
                </div>
                <div className="text-sm text-contrast-helper">
                  Formatted document with header
                </div>
              </div>
              <div
                className={`w-5 h-5 rounded-full border-2 flex items-center justify-center ${
                  format === "pdf"
                    ? "border-action bg-action"
                    : "border-control-border"
                }`}
              >
                {format === "pdf" && (
                  <div className="w-2 h-2 rounded-full bg-surface-card" />
                )}
              </div>
            </label>

            <label
              className={`flex items-center gap-4 p-4 rounded-lg border-2 cursor-pointer transition-colors ${
                format === "docx"
                  ? "border-action bg-action-tint"
                  : "border-surface-border hover:border-control-border"
              }`}
            >
              <input
                type="radio"
                name="exportFormat"
                value="docx"
                checked={format === "docx"}
                onChange={() => setFormat("docx")}
                className="sr-only"
              />
              <div
                className={`w-10 h-10 rounded-lg flex items-center justify-center ${
                  format === "docx"
                    ? "bg-action-tint text-action-text"
                    : "bg-surface-inset text-contrast-helper"
                }`}
              >
                <FileText className="w-5 h-5" />
              </div>
              <div className="flex-1">
                <div className="font-medium text-foreground">
                  Microsoft Word (.docx)
                </div>
                <div className="text-sm text-contrast-helper">
                  Editable document format
                </div>
              </div>
              <div
                className={`w-5 h-5 rounded-full border-2 flex items-center justify-center ${
                  format === "docx"
                    ? "border-action bg-action"
                    : "border-control-border"
                }`}
              >
                {format === "docx" && (
                  <div className="w-2 h-2 rounded-full bg-surface-card" />
                )}
              </div>
            </label>
          </div>
            </>
          )}
        </div>

        {/* Footer */}
        <div className="flex justify-end gap-3 px-6 py-4 border-t border-surface-border bg-surface-inset">
          <button
            onClick={onClose}
            className="px-4 py-2 text-contrast-helper hover:text-foreground transition-colors"
          >
            Cancel
          </button>
          <button
            onClick={handleExport}
            className="flex items-center gap-2 px-4 py-2 bg-action text-foreground rounded-md hover:bg-action-hover transition-colors"
          >
            <Download className="w-4 h-4" />
            Export
          </button>
        </div>
      </div>
    </div>
  );
}
