"use client";

import { useState, useEffect, useCallback, useRef } from "react";
import {
  FileText,
  Upload,
  Trash2,
  AlertCircle,
  Loader2,
  RefreshCw,
  Sparkles,
} from "lucide-react";
import type { RecordingId } from "@/types";
import { Document, getDocuments, deleteDocument } from "@/lib/api";
import { reparseDocument } from "@/lib/api/documents";
import DocumentUploadModal from "./DocumentUploadModal";
import { useNotificationStore } from "@/lib/notificationStore";
import { getErrorMessage } from "@/lib/errors";
import ConfirmationModal from "./ConfirmationModal";

interface DocumentsViewProps {
  recordingId: RecordingId;
}

const POLL_INTERVAL_MS = 2000;

const formatBytes = (bytes?: number | null): string => {
  if (bytes === null || bytes === undefined) return "—";
  if (bytes === 0) return "0 KB";
  const units = ["B", "KB", "MB", "GB"];
  const index = Math.min(
    units.length - 1,
    Math.floor(Math.log(bytes) / Math.log(1024)),
  );
  const value = bytes / Math.pow(1024, index);
  return `${value >= 10 || index === 0 ? Math.round(value) : value.toFixed(1)} ${units[index]}`;
};

/** Extension from the filename; the stored MIME type is client-supplied and unreliable. */
const formatOf = (title: string): string => {
  const dot = title.lastIndexOf(".");
  return dot !== -1 ? title.slice(dot + 1).toUpperCase() : "—";
};

const isBusy = (doc: Document) =>
  doc.status === "PENDING" || doc.status === "PROCESSING";

/**
 * A parse writes to the row on every page batch and stage change, so a long
 * silence means the worker died rather than that the work is slow. Showing this
 * matters: without it a killed parse looks identical to a running one forever.
 * Matches STALLED_PARSE_AFTER on the server, which is what actually permits the
 * re-parse this state invites.
 */
const STALLED_AFTER_MS = 10 * 60 * 1000;

const looksStalled = (doc: Document) =>
  isBusy(doc) && Date.now() - new Date(doc.updated_at).getTime() > STALLED_AFTER_MS;

function StatusCell({ doc }: { doc: Document }) {
  if (doc.status === "ERROR") {
    return (
      <span
        className="inline-flex items-center gap-1.5 text-xs font-medium text-red-600 dark:text-red-400"
        title={doc.error_message ?? undefined}
      >
        <AlertCircle className="h-3.5 w-3.5 flex-shrink-0" />
        Failed
      </span>
    );
  }

  if (looksStalled(doc)) {
    return (
      <span
        className="inline-flex items-center gap-1.5 text-xs font-medium text-amber-700 dark:text-amber-400"
        title="No progress recorded for over ten minutes. Use Parse again to restart it."
      >
        <AlertCircle className="h-3.5 w-3.5 flex-shrink-0" />
        Stalled
      </span>
    );
  }

  if (isBusy(doc)) {
    // Percentage comes from pages, but the label comes from the stage: page
    // counters reach "7 of 7" while indexing is still running, and without the
    // stage that reads as a hang rather than as progress.
    const total = doc.page_count ?? 0;
    const done = Math.min(doc.pages_parsed, total || doc.pages_parsed);
    const pct = total > 0 ? Math.round((done / total) * 100) : null;

    return (
      <div className="min-w-[9rem] space-y-1">
        <div className="h-1.5 overflow-hidden rounded-full bg-gray-200 dark:bg-gray-700">
          <div
            className={`h-full rounded-full bg-orange-500 ${pct === null ? "w-1/3 animate-pulse" : "transition-all duration-500"}`}
            style={pct === null ? undefined : { width: `${pct}%` }}
          />
        </div>
        <p className="text-xs text-gray-500 dark:text-gray-400">
          {doc.parse_stage ?? "Starting"}
          {total > 0 ? ` · ${done}/${total}` : null}
        </p>
      </div>
    );
  }

  if (doc.parse_warning) {
    return (
      <span
        className="inline-flex items-center gap-1.5 text-xs font-medium text-amber-700 dark:text-amber-400"
        title={doc.parse_warning}
      >
        <Sparkles className="h-3.5 w-3.5 flex-shrink-0" />
        Text only
      </span>
    );
  }

  return (
    <span className="inline-flex items-center gap-1.5 text-xs font-medium text-green-700 dark:text-green-400">
      <span className="h-1.5 w-1.5 flex-shrink-0 rounded-full bg-green-500" />
      Ready
    </span>
  );
}

export default function DocumentsView({ recordingId }: DocumentsViewProps) {
  const [documents, setDocuments] = useState<Document[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadFailed, setLoadFailed] = useState(false);
  const [isUploadModalOpen, setIsUploadModalOpen] = useState(false);
  const [documentToDelete, setDocumentToDelete] = useState<Document | null>(
    null,
  );
  const [reparsingId, setReparsingId] = useState<number | null>(null);
  const { addNotification } = useNotificationStore();
  const notifiedDocumentErrorsRef = useRef<Set<string>>(new Set());

  const fetchDocuments = useCallback(async () => {
    try {
      setLoading(true);
      const docs = await getDocuments(recordingId);
      setDocuments(docs);
      setLoadFailed(false);
    } catch (e: unknown) {
      console.error("Failed to load documents", e);
      setLoadFailed(true);
      addNotification({ type: "error", message: "Failed to load documents." });
    } finally {
      setLoading(false);
    }
  }, [addNotification, recordingId]);

  useEffect(() => {
    if (recordingId) {
      fetchDocuments();
    }
  }, [recordingId, fetchDocuments]);

  // Poll while anything is in flight. Faster than the old 3s tick because the
  // progress bar is now the primary signal that work is still moving.
  useEffect(() => {
    if (!documents.some(isBusy)) return;
    const interval = setInterval(() => {
      getDocuments(recordingId).then(setDocuments).catch(console.error);
    }, POLL_INTERVAL_MS);
    return () => clearInterval(interval);
  }, [documents, recordingId]);

  useEffect(() => {
    documents.forEach((document) => {
      if (!document.error_message) {
        return;
      }

      const signature = `${document.id}:${document.error_message}`;
      if (notifiedDocumentErrorsRef.current.has(signature)) {
        return;
      }

      notifiedDocumentErrorsRef.current.add(signature);
      addNotification({
        type: "error",
        message: `${document.title}: ${document.error_message}`,
      });
    });
  }, [addNotification, documents]);

  const handleReparse = async (target: Document) => {
    setReparsingId(target.id);
    try {
      const updated = await reparseDocument(target.id, { deepParse: true });
      setDocuments((prev) =>
        prev.map((d) => (d.id === updated.id ? updated : d)),
      );
      // Clear the recorded error so a repeat failure notifies again rather
      // than being swallowed as already-seen.
      notifiedDocumentErrorsRef.current.forEach((signature) => {
        if (signature.startsWith(`${target.id}:`)) {
          notifiedDocumentErrorsRef.current.delete(signature);
        }
      });
      addNotification({
        type: "success",
        message: `Parsing "${target.title}" again with visual analysis.`,
      });
    } catch (e: unknown) {
      console.error("Failed to re-parse document", e);
      addNotification({
        type: "error",
        message: getErrorMessage(e, "Failed to start parsing again."),
      });
    } finally {
      setReparsingId(null);
    }
  };

  const handleDelete = async () => {
    if (!documentToDelete) return;

    try {
      await deleteDocument(documentToDelete.id);
      addNotification({ type: "success", message: "Document deleted" });
      setDocuments((prev) => prev.filter((d) => d.id !== documentToDelete.id));
    } catch (e: unknown) {
      console.error("Failed to delete document", e);
      addNotification({ type: "error", message: "Failed to delete document" });
    } finally {
      setDocumentToDelete(null);
    }
  };

  return (
    <div className="flex flex-col h-full bg-white dark:bg-gray-900 relative">
      <div className="p-4 border-b border-gray-200 dark:border-gray-800 flex justify-between items-center">
        <h3 className="font-semibold text-gray-900 dark:text-white flex items-center gap-2">
          <FileText className="w-4 h-4 text-orange-500" />
          Attached Documents
        </h3>
        <button
          onClick={() => setIsUploadModalOpen(true)}
          className="flex items-center gap-2 px-3 py-1.5 bg-orange-600 text-white text-sm rounded-lg hover:bg-orange-700 transition-colors"
        >
          <Upload className="w-4 h-4" />
          Upload
        </button>
      </div>

      <div className="flex-1 overflow-y-auto">
        {loading ? (
          <div className="flex items-center justify-center h-full text-gray-500">
            <Loader2 className="w-6 h-6 animate-spin mr-2" />
            Loading documents...
          </div>
        ) : loadFailed ? (
          <div className="flex flex-col items-center justify-center h-full text-center text-gray-500">
            <AlertCircle className="w-10 h-10 mb-3 opacity-40" />
            <p className="text-base font-medium text-gray-600 dark:text-gray-300">
              Documents are temporarily unavailable
            </p>
            <button
              onClick={() => void fetchDocuments()}
              className="mt-4 rounded-lg border border-gray-300 px-4 py-2 text-sm font-medium text-gray-700 transition-colors hover:border-orange-300 hover:text-orange-700 dark:border-gray-700 dark:text-gray-200 dark:hover:border-orange-500/30 dark:hover:text-orange-300"
            >
              Try again
            </button>
          </div>
        ) : documents.length === 0 ? (
          <div className="flex flex-col items-center justify-center h-full text-gray-400">
            <FileText className="w-16 h-16 mb-4 opacity-20" />
            <p className="text-lg font-medium text-gray-500 dark:text-gray-400">
              No documents yet
            </p>
            <p className="text-sm mt-1 mb-6">
              Upload a PDF, deck, document, spreadsheet, or image for context.
            </p>
            <button
              onClick={() => setIsUploadModalOpen(true)}
              className="flex items-center gap-2 px-4 py-2 bg-orange-600 text-white font-medium rounded-lg hover:bg-orange-700 transition-colors shadow-sm"
            >
              <Upload className="w-4 h-4" />
              Upload Document
            </button>
          </div>
        ) : (
          // Wide content scrolls inside its own container so the panel never
          // scrolls horizontally as a whole.
          <div className="overflow-x-auto">
            <table className="w-full min-w-[42rem] border-collapse text-left">
              <thead>
                <tr className="border-b border-gray-200 text-xs font-semibold uppercase tracking-wider text-gray-500 dark:border-gray-800 dark:text-gray-400">
                  <th scope="col" className="px-4 py-2.5 font-semibold">
                    Name
                  </th>
                  <th scope="col" className="w-20 px-3 py-2.5 font-semibold">
                    Format
                  </th>
                  <th scope="col" className="w-24 px-3 py-2.5 font-semibold">
                    Size
                  </th>
                  <th scope="col" className="w-24 px-3 py-2.5 font-semibold">
                    Pages
                  </th>
                  <th scope="col" className="w-48 px-3 py-2.5 font-semibold">
                    Status
                  </th>
                  <th scope="col" className="w-24 px-3 py-2.5">
                    <span className="sr-only">Actions</span>
                  </th>
                </tr>
              </thead>
              <tbody>
                {documents.map((doc) => (
                  <tr
                    key={doc.id}
                    className="group border-b border-gray-100 transition-colors last:border-0 hover:bg-gray-50 dark:border-gray-800/60 dark:hover:bg-gray-800/40"
                  >
                    <td className="max-w-0 px-4 py-3">
                      <p
                        className="truncate text-sm font-medium text-gray-900 dark:text-white"
                        title={doc.title}
                      >
                        {doc.title}
                      </p>
                      <p className="mt-0.5 text-xs text-gray-500 dark:text-gray-400">
                        {new Date(doc.created_at).toLocaleDateString()}
                      </p>
                    </td>
                    <td className="px-3 py-3">
                      <span className="inline-block rounded bg-gray-100 px-1.5 py-0.5 font-mono text-[11px] font-medium text-gray-600 dark:bg-gray-800 dark:text-gray-300">
                        {formatOf(doc.title)}
                      </span>
                    </td>
                    <td className="px-3 py-3 text-sm tabular-nums text-gray-600 dark:text-gray-300">
                      {formatBytes(doc.file_size_bytes)}
                    </td>
                    <td className="px-3 py-3 text-sm tabular-nums text-gray-600 dark:text-gray-300">
                      {doc.page_count ?? "—"}
                    </td>
                    <td className="px-3 py-3">
                      <StatusCell doc={doc} />
                    </td>
                    <td className="px-3 py-3">
                      <div className="flex justify-end gap-1 opacity-100 transition-opacity lg:opacity-0 lg:group-hover:opacity-100 lg:focus-within:opacity-100">
                        <button
                          onClick={() => handleReparse(doc)}
                          disabled={reparsingId === doc.id || (isBusy(doc) && !looksStalled(doc))}
                          className="rounded-lg p-1.5 text-gray-400 transition-colors hover:bg-orange-50 hover:text-orange-600 disabled:opacity-40 disabled:hover:bg-transparent disabled:hover:text-gray-400 dark:hover:bg-orange-900/20"
                          title="Parse again with visual analysis"
                        >
                          {reparsingId === doc.id ? (
                            <Loader2 className="h-4 w-4 animate-spin" />
                          ) : (
                            <RefreshCw className="h-4 w-4" />
                          )}
                        </button>
                        <button
                          onClick={() => setDocumentToDelete(doc)}
                          className="rounded-lg p-1.5 text-gray-400 transition-colors hover:bg-red-50 hover:text-red-500 dark:hover:bg-red-900/20"
                          title="Delete document"
                        >
                          <Trash2 className="h-4 w-4" />
                        </button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>

            {/* Warnings sit below the table rather than inside a cell: they are
                sentences, and a row that grows to fit one breaks the scan. */}
            {documents.some((doc) => doc.parse_warning) && (
              <div className="space-y-2 p-4">
                {documents
                  .filter((doc) => doc.parse_warning)
                  .map((doc) => (
                    <div
                      key={doc.id}
                      className="flex items-start gap-2 rounded-lg bg-amber-50 p-3 text-amber-800 dark:bg-amber-900/20 dark:text-amber-300"
                    >
                      <Sparkles className="mt-0.5 h-4 w-4 flex-shrink-0" />
                      <p className="text-xs">
                        <span className="font-medium">{doc.title}:</span>{" "}
                        {doc.parse_warning}
                      </p>
                    </div>
                  ))}
              </div>
            )}
          </div>
        )}
      </div>

      <DocumentUploadModal
        isOpen={isUploadModalOpen}
        onClose={() => setIsUploadModalOpen(false)}
        recordingId={recordingId}
        onSuccess={fetchDocuments}
      />

      <ConfirmationModal
        isOpen={!!documentToDelete}
        onClose={() => setDocumentToDelete(null)}
        onConfirm={handleDelete}
        title="Delete Document"
        message={`Are you sure you want to delete "${documentToDelete?.title}"? This will remove it from the chat context.`}
        confirmText="Delete"
        isDangerous={true}
      />
    </div>
  );
}
