"use client";

import { useState, useEffect, useCallback, useRef } from "react";
import {
  FileText,
  Upload,
  Trash2,
  AlertCircle,
  Loader2,
  File,
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

  // Poll for status updates if any document is processing
  useEffect(() => {
    let interval: NodeJS.Timeout;
    const hasProcessing = documents.some(
      (d) => d.status === "PENDING" || d.status === "PROCESSING",
    );

    if (hasProcessing) {
      interval = setInterval(() => {
        getDocuments(recordingId).then(setDocuments).catch(console.error);
      }, 3000);
    }

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

  const getStatusColor = (status: string) => {
    switch (status) {
      case "READY":
        return "text-green-600 bg-green-50 dark:bg-green-900/20 dark:text-green-400";
      case "ERROR":
        return "text-red-600 bg-red-50 dark:bg-red-900/20 dark:text-red-400";
      case "PROCESSING":
      case "PENDING":
        return "text-blue-600 bg-blue-50 dark:bg-blue-900/20 dark:text-blue-400";
      default:
        return "text-gray-600 bg-gray-50 dark:bg-gray-800 dark:text-gray-400";
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

      <div className="flex-1 overflow-y-auto p-6">
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
              Upload PDF, text, or markdown files for context.
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
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
            {documents.map((doc) => (
              <div
                key={doc.id}
                className="bg-white dark:bg-gray-800 border border-gray-200 dark:border-gray-700 rounded-xl p-4 hover:shadow-md transition-shadow relative group"
              >
                <div className="flex items-start justify-between mb-2">
                  <div
                    className={`text-xs font-medium px-2 py-0.5 rounded-full ${getStatusColor(doc.status)}`}
                  >
                    {doc.status}
                  </div>
                  <div className="flex gap-1 opacity-100 transition-opacity lg:opacity-0 lg:group-hover:opacity-100">
                    <button
                      onClick={() => handleReparse(doc)}
                      disabled={
                        reparsingId === doc.id ||
                        doc.status === "PENDING" ||
                        doc.status === "PROCESSING"
                      }
                      className="p-1.5 text-gray-400 hover:text-orange-600 hover:bg-orange-50 dark:hover:bg-orange-900/20 rounded-lg transition-colors disabled:opacity-40 disabled:hover:bg-transparent disabled:hover:text-gray-400"
                      title="Parse again with visual analysis"
                    >
                      {reparsingId === doc.id ? (
                        <Loader2 className="w-4 h-4 animate-spin" />
                      ) : (
                        <RefreshCw className="w-4 h-4" />
                      )}
                    </button>
                    <button
                      onClick={() => setDocumentToDelete(doc)}
                      className="p-1.5 text-gray-400 hover:text-red-500 hover:bg-red-50 dark:hover:bg-red-900/20 rounded-lg transition-colors"
                      title="Delete Document"
                    >
                      <Trash2 className="w-4 h-4" />
                    </button>
                  </div>
                </div>

                <div className="flex items-center gap-3 mb-3">
                  <div className="p-2 bg-gray-100 dark:bg-gray-700 rounded-lg">
                    <File className="w-6 h-6 text-gray-500 dark:text-gray-400" />
                  </div>
                  <div className="min-w-0 flex-1">
                    <h4
                      className="font-medium text-gray-900 dark:text-white truncate"
                      title={doc.title}
                    >
                      {doc.title}
                    </h4>
                    <p className="text-xs text-gray-500 dark:text-gray-400 truncate">
                      {new Date(doc.created_at).toLocaleDateString()}
                      {doc.status === "READY" && doc.page_count
                        ? ` · ${doc.page_count} ${doc.page_count === 1 ? "page" : "pages"}`
                        : null}
                    </p>
                  </div>
                </div>

                {/* Per-page progress. A visual parse can run for minutes, so a
                    bare spinner would leave the user with no sense of movement. */}
                {(doc.status === "PROCESSING" || doc.status === "PENDING") &&
                  !!doc.page_count && (
                    <div className="mb-1">
                      <div className="h-1.5 overflow-hidden rounded-full bg-gray-100 dark:bg-gray-700">
                        <div
                          className="h-full rounded-full bg-orange-500 transition-all"
                          style={{
                            width: `${Math.min(100, Math.round((doc.pages_parsed / doc.page_count) * 100))}%`,
                          }}
                        />
                      </div>
                      <p className="mt-1 text-xs text-gray-500 dark:text-gray-400">
                        {doc.pages_parsed} of {doc.page_count} pages
                      </p>
                    </div>
                  )}

                {/* Non-fatal downgrade: the document is usable, but was parsed
                    without visual analysis and says why. */}
                {doc.status === "READY" && doc.parse_warning && (
                  <div className="mt-2 flex items-start gap-1.5 rounded-lg bg-amber-50 p-2 text-amber-800 dark:bg-amber-900/20 dark:text-amber-300">
                    <Sparkles className="mt-0.5 h-3.5 w-3.5 flex-shrink-0" />
                    <p className="text-xs">{doc.parse_warning}</p>
                  </div>
                )}
              </div>
            ))}
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
