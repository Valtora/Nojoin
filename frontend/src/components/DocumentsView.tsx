"use client";

import { useState, useEffect, useCallback } from "react";
import {
  FileText,
  Upload,
  Trash2,
  AlertCircle,
  Loader2,
  File,
} from "lucide-react";
import { Document, getDocuments, deleteDocument } from "@/lib/api";
import DocumentUploadModal from "./DocumentUploadModal";
import { useNotificationStore } from "@/lib/notificationStore";
import ConfirmationModal from "./ConfirmationModal";

interface DocumentsViewProps {
  recordingId: number;
}

export default function DocumentsView({ recordingId }: DocumentsViewProps) {
  const [documents, setDocuments] = useState<Document[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [isUploadModalOpen, setIsUploadModalOpen] = useState(false);
  const [documentToDelete, setDocumentToDelete] = useState<Document | null>(
    null,
  );
  const { addNotification } = useNotificationStore();

  const fetchDocuments = useCallback(async () => {
    try {
      setLoading(true);
      const docs = await getDocuments(recordingId);
      setDocuments(docs);
      setError("");
    } catch (e) {
      console.error("Failed to load documents", e);
      setError("Failed to load documents.");
    } finally {
      setLoading(false);
    }
  }, [recordingId]);

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

  const handleDelete = async () => {
    if (!documentToDelete) return;

    try {
      await deleteDocument(documentToDelete.id);
      addNotification({ type: "success", message: "Document deleted" });
      setDocuments((prev) => prev.filter((d) => d.id !== documentToDelete.id));
    } catch (e) {
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
        ) : error ? (
          <div className="flex items-center justify-center h-full text-red-500">
            <AlertCircle className="w-6 h-6 mr-2" />
            {error}
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
                  <div className="flex gap-1 opacity-0 group-hover:opacity-100 transition-opacity">
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
                    </p>
                  </div>
                </div>

                {doc.error_message && (
                  <div className="mt-2 text-xs text-red-500 bg-red-50 dark:bg-red-900/10 p-2 rounded">
                    Error: {doc.error_message}
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
