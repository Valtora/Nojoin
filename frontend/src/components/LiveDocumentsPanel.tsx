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
    <section className="density-surface flex h-full min-h-0 flex-col border border-surface-border bg-surface-card shadow-card">
      {/* One row: glyph, title, action. The sentence under the title explained
          what attaching a document does, on a panel with an Upload button. */}
      <div className="mb-4 flex flex-wrap items-center gap-x-3 gap-y-2">
        <FileText className="h-5 w-5 shrink-0 text-action-text" />
        <h3 className="text-base font-semibold text-foreground">Documents</h3>
        <button
          type="button"
          onClick={() => setIsUploadOpen(true)}
          className="ml-auto inline-flex flex-shrink-0 items-center gap-2 rounded-lg bg-action px-3 py-1.5 text-sm font-medium text-action-on transition-colors hover:bg-action-hover"
        >
          <Upload className="h-4 w-4" />
          Upload
        </button>
      </div>

      {documents.length === 0 ? (
        <p className="text-sm text-contrast-helper">
          Attach an agenda or a deck and it will be used when the notes are
          generated.
        </p>
      ) : (
        <ul className="min-h-0 flex-1 space-y-2 overflow-y-auto">
          {documents.map((doc) => (
            <li
              key={doc.id}
              className="density-surface-panel flex items-center gap-3 bg-surface-inset px-3 py-2"
            >
              <FileText className="h-4 w-4 flex-shrink-0 text-action-text" />
              <span
                className="min-w-0 flex-1 truncate text-sm text-foreground"
                title={doc.title}
              >
                {doc.title}
              </span>
              <span className="flex flex-shrink-0 items-center gap-1.5 text-xs text-contrast-helper">
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
                    <AlertCircle className="h-3.5 w-3.5 text-status-danger-fg" />
                    Failed
                  </>
                ) : (
                  <>
                    <CheckCircle2 className="h-3.5 w-3.5 text-status-success-fg" />
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
