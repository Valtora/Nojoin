"use client";

import { createContext, useContext, useEffect, useState, type ReactNode } from "react";

import type { RecordingId, TimelineNote } from "@/types";

import { HeadNoteSync, stripNote } from "./headNoteSync";

export interface RetainedHeadNoteDraft {
  text: string;
  lastSaved: string | null;
}

/** Keeps editor text and write-token/conflict state above changing panel layouts. */
class HeadNoteOwner {
  readonly sync: HeadNoteSync;
  private draft: RetainedHeadNoteDraft | null = null;
  private readonly listeners = new Set<() => void>();

  constructor(readonly recordingId: RecordingId, notes: readonly TimelineNote[] | null) {
    this.sync = new HeadNoteSync(recordingId, notes);
  }

  getDraft = () => this.draft;
  subscribeDraft = (listener: () => void) => {
    this.listeners.add(listener);
    return () => { this.listeners.delete(listener); };
  };

  private emit() {
    this.setDirty(false);
    this.listeners.forEach((listener) => listener());
  }

  setDraftText = (text: string) => {
    this.draft = { text, lastSaved: this.draft ? this.draft.lastSaved : this.sync.getSnapshot().body };
    this.emit();
  };

  setDraftSaved = (lastSaved: string | null) => {
    this.draft = { text: this.draft?.text ?? this.sync.getSnapshot().body, lastSaved };
    this.emit();
  };

  followCleanServerText = () => {
    const draft = this.draft;
    const { body, conflict } = this.sync.getSnapshot();
    if (!draft || draft.lastSaved === null || conflict ||
      stripNote(draft.text) !== stripNote(draft.lastSaved) || stripNote(draft.text) === stripNote(body)) return;
    this.draft = { text: body, lastSaved: body };
    this.listeners.forEach((listener) => listener());
  };

  // A save from a panel just unmounted must not clear a newer editor's dirtiness.
  setDirty = (dirty: boolean) => {
    const draft = this.draft;
    this.sync.setDirty(draft
      ? draft.lastSaved === null || stripNote(draft.text) !== stripNote(draft.lastSaved)
      : dirty);
  };
}

const Context = createContext<HeadNoteOwner | null>(null);

/** Key by recording ID and mount outside the responsive/collapsible page branches. */
export function HeadNoteOwnerProvider({ recordingId, timelineNotes, children }: {
  recordingId: RecordingId;
  timelineNotes: readonly TimelineNote[] | null;
  children: ReactNode;
}) {
  const [owner] = useState(() => new HeadNoteOwner(recordingId, timelineNotes));
  useEffect(() => owner.sync.ingest(timelineNotes), [owner, timelineNotes]);
  useEffect(() => owner.sync.subscribe(owner.followCleanServerText), [owner]);
  return <Context.Provider value={owner}>{children}</Context.Provider>;
}

export const useHeadNoteOwner = () => useContext(Context);
