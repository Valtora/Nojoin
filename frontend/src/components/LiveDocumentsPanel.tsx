"use client";

import { FileText, Loader2, AlertCircle, CheckCircle2 } from "lucide-react";

import { Document } from "@/lib/api";

interface LiveDocumentsPanelProps {
  documents: Document[];
}

/**
 * What is attached to a meeting that is still being recorded or processed.
 *
 * The point of attaching now rather than later is timing, not convenience.
 * Notes are generated at the end of processing, and a document attached during
 * the meeting is normally parsed well before then, so it lands in the notes on
 * the first pass rather than marking them stale afterwards.
 *
 * Deliberately read-only: no delete and no re-parse here. Both belong on the
 * full Documents tab, and a destructive control next to a live recording is the
 * wrong thing to offer mid-meeting. Uploading lives on the capture toolbar,
 * where it is found; the caller hides this panel when there is nothing to list,
 * which is why there is no empty state.
 */
export default function LiveDocumentsPanel({
  documents,
}: LiveDocumentsPanelProps) {
  return (
    <section className="density-surface flex h-full min-h-0 flex-col border border-surface-border bg-surface-card shadow-card">
      <div className="mb-4 flex flex-wrap items-center gap-x-3 gap-y-2">
        <FileText className="h-5 w-5 shrink-0 text-action-text" />
        <h3 className="text-base font-semibold text-foreground">Documents</h3>
        <span className="ml-auto text-xs font-semibold uppercase tracking-[0.2em] text-contrast-helper">
          {documents.length === 1 ? "1 attached" : `${documents.length} attached`}
        </span>
      </div>

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
    </section>
  );
}
