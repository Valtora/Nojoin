"use client";

import { useCallback, useEffect, useState } from "react";
import { FileText, Loader2, Upload, AlertCircle, CheckCircle2 } from "lucide-react";

import { Document, getDocuments } from "@/lib/api";
import type { RecordingId } from "@/types";

import DocumentUploadModal from "./DocumentUploadModal";

const POLL_INTERVAL_MS = 4000;

interface LiveDocumentsPanelProps {
  recordingId: RecordingId;
}

/**
 * Attach documents while the meeting is still being recorded or processed.
 *
 * The point is timing, not convenience. Notes are generated at the end of
 * processing, and a document attached during the meeting is normally parsed
 * well before then, so it lands in the notes on the first pass rather than
 * marking them stale afterwards.
 *
 * Deliberately read-mostly: no delete and no re-parse here. Both belong on the
 * full Documents tab, and a destructive control next to a live recording is the
 * wrong thing to offer mid-meeting.
 */
export default function LiveDocumentsPanel({
  recordingId,
}: LiveDocumentsPanelProps) {
  const [documents, setDocuments] = useState<Document[]>([]);
  const [isUploadOpen, setIsUploadOpen] = useState(false);

  const refresh = useCallback(async () => {
    try {
      setDocuments(await getDocuments(recordingId));
    } catch (error) {
      // A failed poll during a live recording is not worth a notification:
      // the capture itself is unaffected and the next tick usually recovers.
      console.error("Failed to load documents", error);
    }
  }, [recordingId]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  useEffect(() => {
    const pending = documents.some(
      (doc) => doc.status === "PENDING" || doc.status === "PROCESSING",
    );
    if (!pending) return;
    const interval = setInterval(() => void refresh(), POLL_INTERVAL_MS);
    return () => clearInterval(interval);
  }, [documents, refresh]);

  return (
    <section className="density-surface border border-white/60 bg-white/80 shadow-xl shadow-orange-950/10 backdrop-blur dark:border-white/10 dark:bg-gray-950/65 dark:shadow-black/20">
      <div className="mb-4 flex items-center justify-between gap-3">
        <div className="min-w-0">
          <h3 className="text-lg font-semibold text-gray-900 dark:text-white">
            Documents
          </h3>
          <p className="mt-1 text-sm text-gray-600 dark:text-gray-300">
            Attach an agenda or a deck now and it will be used when the meeting
            notes are generated.
          </p>
        </div>
        <button
          type="button"
          onClick={() => setIsUploadOpen(true)}
          className="inline-flex flex-shrink-0 items-center gap-2 rounded-lg bg-orange-600 px-3 py-1.5 text-sm font-medium text-white transition-colors hover:bg-orange-700"
        >
          <Upload className="h-4 w-4" />
          Upload
        </button>
      </div>

      {documents.length === 0 ? (
        <p className="text-sm text-gray-500 dark:text-gray-400">
          No documents attached yet.
        </p>
      ) : (
        <ul className="space-y-2">
          {documents.map((doc) => (
            <li
              key={doc.id}
              className="density-surface-panel flex items-center gap-3 border border-orange-200/70 bg-white px-3 py-2 dark:border-orange-500/20 dark:bg-gray-900"
            >
              <FileText className="h-4 w-4 flex-shrink-0 text-orange-500" />
              <span
                className="min-w-0 flex-1 truncate text-sm text-gray-800 dark:text-gray-100"
                title={doc.title}
              >
                {doc.title}
              </span>
              <span className="flex flex-shrink-0 items-center gap-1.5 text-xs text-gray-500 dark:text-gray-400">
                {doc.status === "PENDING" || doc.status === "PROCESSING" ? (
                  <>
                    <Loader2 className="h-3.5 w-3.5 animate-spin" />
                    {doc.parse_stage ?? "Starting"}
                    {doc.page_count
                      ? ` ${doc.pages_parsed}/${doc.page_count}`
                      : null}
                  </>
                ) : doc.status === "ERROR" ? (
                  <>
                    <AlertCircle className="h-3.5 w-3.5 text-red-500" />
                    Failed
                  </>
                ) : (
                  <>
                    <CheckCircle2 className="h-3.5 w-3.5 text-green-500" />
                    Ready
                  </>
                )}
              </span>
            </li>
          ))}
        </ul>
      )}

      <DocumentUploadModal
        isOpen={isUploadOpen}
        onClose={() => setIsUploadOpen(false)}
        recordingId={recordingId}
        onSuccess={refresh}
      />
    </section>
  );
}
