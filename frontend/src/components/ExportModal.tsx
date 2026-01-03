"use client";

import { useState, useEffect } from "react";
import { X, FileText, StickyNote, Files, Download } from "lucide-react";
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
        className="absolute inset-0 bg-black/50 backdrop-blur-sm"
        onClick={onClose}
      />

      {/* Modal */}
      <div className="relative bg-white dark:bg-gray-800 rounded-xl shadow-2xl w-full max-w-md mx-4 overflow-hidden">
        {/* Header */}
        <div className="flex items-center justify-between px-6 py-4 border-b border-gray-200 dark:border-gray-700">
          <h2 className="text-lg font-semibold text-gray-900 dark:text-white">
            Export Content
          </h2>
          <button
            onClick={onClose}
            className="p-1 text-gray-400 hover:text-gray-600 dark:hover:text-gray-200 rounded-md hover:bg-gray-100 dark:hover:bg-gray-700 transition-colors"
          >
            <X className="w-5 h-5" />
          </button>
        </div>

        {/* Content */}
        <div className="p-6 space-y-4">
          <p className="text-sm text-gray-600 dark:text-gray-400 mb-4">
            Choose what you want to export:
          </p>

          {/* Options */}
          <div className="space-y-3">
            {/* Transcript Option */}
            <label
              className={`flex items-center gap-4 p-4 rounded-lg border-2 cursor-pointer transition-colors ${
                selected === "transcript"
                  ? "border-orange-500 bg-orange-100 dark:bg-orange-900/20"
                  : "border-gray-200 dark:border-gray-700 hover:border-gray-300 dark:hover:border-gray-600"
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
                    ? "bg-orange-200 dark:bg-orange-900/40 text-orange-600"
                    : "bg-gray-100 dark:bg-gray-700 text-gray-500"
                }`}
              >
                <FileText className="w-5 h-5" />
              </div>
              <div className="flex-1">
                <div className="font-medium text-gray-900 dark:text-white">
                  Transcript Only
                </div>
                <div className="text-sm text-gray-500 dark:text-gray-400">
                  Export the diarized transcript with timestamps
                </div>
              </div>
              <div
                className={`w-5 h-5 rounded-full border-2 flex items-center justify-center ${
                  selected === "transcript"
                    ? "border-orange-500 bg-orange-500"
                    : "border-gray-300 dark:border-gray-600"
                }`}
              >
                {selected === "transcript" && (
                  <div className="w-2 h-2 rounded-full bg-white" />
                )}
              </div>
            </label>

            {/* Notes Option */}
            <label
              className={`flex items-center gap-4 p-4 rounded-lg border-2 transition-colors ${
                !hasNotes
                  ? "opacity-50 cursor-not-allowed border-gray-200 dark:border-gray-700"
                  : selected === "notes"
                    ? "border-orange-500 bg-orange-100 dark:bg-orange-900/20 cursor-pointer"
                    : "border-gray-200 dark:border-gray-700 hover:border-gray-300 dark:hover:border-gray-600 cursor-pointer"
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
                    ? "bg-orange-200 dark:bg-orange-900/40 text-orange-600"
                    : "bg-gray-100 dark:bg-gray-700 text-gray-500"
                }`}
              >
                <StickyNote className="w-5 h-5" />
              </div>
              <div className="flex-1">
                <div className="font-medium text-gray-900 dark:text-white">
                  Notes Only
                </div>
                <div className="text-sm text-gray-500 dark:text-gray-400">
                  {hasNotes
                    ? "Export the AI-generated meeting notes"
                    : "No notes available - generate notes first"}
                </div>
              </div>
              <div
                className={`w-5 h-5 rounded-full border-2 flex items-center justify-center ${
                  selected === "notes"
                    ? "border-orange-500 bg-orange-500"
                    : "border-gray-300 dark:border-gray-600"
                }`}
              >
                {selected === "notes" && (
                  <div className="w-2 h-2 rounded-full bg-white" />
                )}
              </div>
            </label>

            {/* Both Option */}
            <label
              className={`flex items-center gap-4 p-4 rounded-lg border-2 transition-colors ${
                !hasNotes
                  ? "opacity-50 cursor-not-allowed border-gray-200 dark:border-gray-700"
                  : selected === "both"
                    ? "border-orange-500 bg-orange-100 dark:bg-orange-900/20 cursor-pointer"
                    : "border-gray-200 dark:border-gray-700 hover:border-gray-300 dark:hover:border-gray-600 cursor-pointer"
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
                    ? "bg-orange-200 dark:bg-orange-900/40 text-orange-600"
                    : "bg-gray-100 dark:bg-gray-700 text-gray-500"
                }`}
              >
                <Files className="w-5 h-5" />
              </div>
              <div className="flex-1">
                <div className="font-medium text-gray-900 dark:text-white">
                  Both
                </div>
                <div className="text-sm text-gray-500 dark:text-gray-400">
                  {hasNotes
                    ? "Export transcript and notes in a single file"
                    : "No notes available - generate notes first"}
                </div>
              </div>
              <div
                className={`w-5 h-5 rounded-full border-2 flex items-center justify-center ${
                  selected === "both"
                    ? "border-orange-500 bg-orange-500"
                    : "border-gray-300 dark:border-gray-600"
                }`}
              >
                {selected === "both" && (
                  <div className="w-2 h-2 rounded-full bg-white" />
                )}
              </div>
            </label>
          </div>

          <div className="border-t border-gray-200 dark:border-gray-700 my-4" />

          <p className="text-sm text-gray-600 dark:text-gray-400 mb-2">
            Choose format:
          </p>

          <div className="space-y-3">
            <label
              className={`flex items-center gap-4 p-4 rounded-lg border-2 cursor-pointer transition-colors ${
                format === "txt"
                  ? "border-orange-500 bg-orange-100 dark:bg-orange-900/20"
                  : "border-gray-200 dark:border-gray-700 hover:border-gray-300 dark:hover:border-gray-600"
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
                    ? "bg-orange-200 dark:bg-orange-900/40 text-orange-600"
                    : "bg-gray-100 dark:bg-gray-700 text-gray-500"
                }`}
              >
                <FileText className="w-5 h-5" />
              </div>
              <div className="flex-1">
                <div className="font-medium text-gray-900 dark:text-white">
                  Text File (.txt)
                </div>
                <div className="text-sm text-gray-500 dark:text-gray-400">
                  Simple text format
                </div>
              </div>
              <div
                className={`w-5 h-5 rounded-full border-2 flex items-center justify-center ${
                  format === "txt"
                    ? "border-orange-500 bg-orange-500"
                    : "border-gray-300 dark:border-gray-600"
                }`}
              >
                {format === "txt" && (
                  <div className="w-2 h-2 rounded-full bg-white" />
                )}
              </div>
            </label>

            <label
              className={`flex items-center gap-4 p-4 rounded-lg border-2 cursor-pointer transition-colors ${
                format === "pdf"
                  ? "border-orange-500 bg-orange-100 dark:bg-orange-900/20"
                  : "border-gray-200 dark:border-gray-700 hover:border-gray-300 dark:hover:border-gray-600"
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
                    ? "bg-orange-200 dark:bg-orange-900/40 text-orange-600"
                    : "bg-gray-100 dark:bg-gray-700 text-gray-500"
                }`}
              >
                <FileText className="w-5 h-5" />
              </div>
              <div className="flex-1">
                <div className="font-medium text-gray-900 dark:text-white">
                  PDF Document (.pdf)
                </div>
                <div className="text-sm text-gray-500 dark:text-gray-400">
                  Formatted document with header
                </div>
              </div>
              <div
                className={`w-5 h-5 rounded-full border-2 flex items-center justify-center ${
                  format === "pdf"
                    ? "border-orange-500 bg-orange-500"
                    : "border-gray-300 dark:border-gray-600"
                }`}
              >
                {format === "pdf" && (
                  <div className="w-2 h-2 rounded-full bg-white" />
                )}
              </div>
            </label>

            <label
              className={`flex items-center gap-4 p-4 rounded-lg border-2 cursor-pointer transition-colors ${
                format === "docx"
                  ? "border-orange-500 bg-orange-100 dark:bg-orange-900/20"
                  : "border-gray-200 dark:border-gray-700 hover:border-gray-300 dark:hover:border-gray-600"
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
                    ? "bg-orange-200 dark:bg-orange-900/40 text-orange-600"
                    : "bg-gray-100 dark:bg-gray-700 text-gray-500"
                }`}
              >
                <FileText className="w-5 h-5" />
              </div>
              <div className="flex-1">
                <div className="font-medium text-gray-900 dark:text-white">
                  Microsoft Word (.docx)
                </div>
                <div className="text-sm text-gray-500 dark:text-gray-400">
                  Editable document format
                </div>
              </div>
              <div
                className={`w-5 h-5 rounded-full border-2 flex items-center justify-center ${
                  format === "docx"
                    ? "border-orange-500 bg-orange-500"
                    : "border-gray-300 dark:border-gray-600"
                }`}
              >
                {format === "docx" && (
                  <div className="w-2 h-2 rounded-full bg-white" />
                )}
              </div>
            </label>
          </div>
        </div>

        {/* Footer */}
        <div className="flex justify-end gap-3 px-6 py-4 border-t border-gray-200 dark:border-gray-700 bg-gray-50 dark:bg-gray-900/50">
          <button
            onClick={onClose}
            className="px-4 py-2 text-gray-600 dark:text-gray-400 hover:text-gray-800 dark:hover:text-gray-200 transition-colors"
          >
            Cancel
          </button>
          <button
            onClick={handleExport}
            className="flex items-center gap-2 px-4 py-2 bg-orange-600 text-white rounded-md hover:bg-orange-700 transition-colors"
          >
            <Download className="w-4 h-4" />
            Export
          </button>
        </div>
      </div>
    </div>
  );
}
