import "fake-indexeddb/auto";
import { act, fireEvent, render, renderHook, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AxiosError, type AxiosAdapter, type AxiosResponse, type InternalAxiosRequestConfig } from "axios";
import { useEffect, type ReactNode } from "react";

import api from "@/lib/api";
import { CaptureProvider } from "@/lib/capture/CaptureProvider";
import { ViewportDensityProvider } from "@/components/ViewportDensityProvider";
import NotesSection from "../_components/NotesSection";
import { renderCounter } from "@/test/renderCounts";
import { ClientStatus, RecordingStatus, type NotesLifecycle, type Recording, type Transcript } from "@/types";
import { useRecordingDetail, IDLE_RECORDING_POLL_INTERVAL_MS } from "./useRecordingDetail";

// Next's browser routing boundary; all Nojoin hooks/components/API clients run normally.
const router = vi.hoisted(() => ({ push: () => undefined }));
vi.mock("next/navigation", () => ({
  useRouter: () => router,
  usePathname: () => "/recordings",
  useSearchParams: () => new URLSearchParams(),
}));

const RUN_A = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa";
const RUN_B = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb";
const NOTES = "## Summary\n\nEarlier notes";
const params = Promise.resolve({ id: "rec-1" });
const noop = () => undefined;
const lifecycle = (token: string | null): NotesLifecycle => ({
  notes_status: token ? "generating" : "completed", notes_run_id: token,
  notes_progress: token ? "Drafting notes" : null, notes: NOTES, error_message: null,
});
const recording = (token: string | null = RUN_A): Recording => ({
  id: "rec-1", created_at: "2026-10-27T10:00:00Z", updated_at: "2026-10-27T10:00:00Z",
  name: "Meeting", meeting_uid: "meeting-1", audio_path: "/audio.wav", status: RecordingStatus.PROCESSED,
  client_status: ClientStatus.IDLE, has_audio: true, has_proxy: true, speakers: [],
  upload_progress: 100, processing_progress: 100, is_archived: false, is_deleted: false,
  transcript: { ...lifecycle(token), id: 1, recording_id: "rec-1", segments: [],
    transcript_status: "completed", created_at: "2026-10-27", updated_at: "2026-10-27" } as Transcript,
});
function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason: Error) => void;
  const promise = new Promise<T>((done, fail) => { resolve = done; reject = fail; });
  return { promise, resolve, reject };
}
const response = (config: InternalAxiosRequestConfig, data: unknown, status = 200): AxiosResponse => ({
  data, status, statusText: "OK", headers: {}, config,
});
let server: Recording;
let read: (config: InternalAxiosRequestConfig) => Promise<AxiosResponse>;
let cancel: (config: InternalAxiosRequestConfig) => Promise<AxiosResponse>;
let generate: (config: InternalAxiosRequestConfig) => Promise<AxiosResponse>;
let transcript: (config: InternalAxiosRequestConfig) => Promise<AxiosResponse>;
const originalAdapter = api.defaults.adapter;
const adapter: AxiosAdapter = async (config) => {
  if (config.url === "/recordings/rec-1") return read(config);
  if (config.url?.endsWith("/notes/cancel")) return cancel(config);
  if (config.url?.endsWith("/notes/generate")) return generate(config);
  if (config.url?.includes("/utterances")) return transcript(config);
  if (config.url?.includes("/settings")) return response(config, {});
  if (config.url?.includes("/users/me")) return response(config, { id: 1, username: "Mike" });
  return response(config, []);
};
function Providers({ children }: { children: ReactNode }) {
  return <ViewportDensityProvider><CaptureProvider>{children}</CaptureProvider></ViewportDensityProvider>;
}
const flush = () => act(async () => { await vi.advanceTimersByTimeAsync(0); });
let shown: ReturnType<typeof useRecordingDetail>;
function NotesPage() {
  const detail = useRecordingDetail({ params });
  useEffect(() => { shown = detail; }, [detail]);
  if (!detail.recording) return null;
  return <NotesSection active recording={detail.recording} isGenerating={detail.isGeneratingNotes ||
    detail.recording.transcript?.notes_status === "generating"}
    inFlight={detail.recording.status === RecordingStatus.PROCESSING} hasRetainedPublication
    canUndo={false} canRedo={false} onNotesChange={detail.handleNotesChange}
    onGenerateNotes={detail.handleGenerateNotes} onCancelNotes={detail.handleCancelNotes}
    onFindAndReplace={noop} onUndo={noop} onRedo={noop} onExport={noop} />;
}

describe("notes lifecycle mutations through the recording page", () => {
  beforeEach(() => {
    vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout", "setInterval", "clearInterval", "Date"] });
    api.defaults.adapter = adapter;
    server = recording();
    read = async (config) => response(config, server);
    cancel = async (config) => {
      server = { ...server, transcript: { ...server.transcript!, ...lifecycle(null) } as Transcript };
      return response(config, lifecycle(null));
    };
    generate = async (config) => {
      server = { ...server, transcript: { ...server.transcript!, ...lifecycle(RUN_B) } as Transcript };
      return response(config, lifecycle(RUN_B));
    };
    transcript = async (config) => response(config, {
      recording_id: "rec-1", revision: 0, speakers: [], tombstones: [], utterances: [],
    });
    vi.stubGlobal("fetch", vi.fn(async () => new Response("", { status: 404 })));
  });
  afterEach(() => {
    api.defaults.adapter = originalAdapter;
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  it("acknowledges Cancel without a GET and redraws the control, not the notes body", async () => {
    render(<Providers><NotesPage /></Providers>);
    await flush();
    const notes = within(screen.getByRole("tabpanel"));
    expect(notes.getByRole("button", { name: "Cancel" })).toBeInTheDocument();
    const waiting = deferred<AxiosResponse>();
    cancel = () => waiting.promise;
    // Any follow-up metadata request remains unresolved.
    read = () => new Promise(() => {});
    const counter = await renderCounter();
    try {
      await counter.take();
      fireEvent.click(notes.getByRole("button", { name: "Cancel" }));
      await flush();
      expect(notes.getByRole("button", { name: "Cancelling..." })).toBeDisabled();
      const pending = await counter.take();
      expect(pending("components/NotesGenerationControl.tsx", "NotesGenerationControl")).toBeGreaterThan(0);
      expect(pending("components/RichTextEditor.tsx", "RichTextEditor")).toBe(0);
      await act(async () => waiting.resolve(response({} as InternalAxiosRequestConfig, lifecycle(null))));
      await flush();
      expect(notes.getByRole("button", { name: "Regenerate Notes" })).toBeEnabled();
      const acknowledged = await counter.take();
      expect(acknowledged("components/NotesGenerationControl.tsx", "NotesGenerationControl")).toBeGreaterThan(0);
      expect(acknowledged("components/RichTextEditor.tsx", "RichTextEditor")).toBe(0);
    } finally { await counter.stop(); }
  }, 20_000);

  it.each([NOTES, ""])("restores immediate Generate during inline processing with notes %j", async (notes) => {
    server = { ...recording(), status: RecordingStatus.PROCESSING,
      transcript: { ...recording().transcript!, notes } };
    cancel = async (config) => response(config, { ...lifecycle(null), notes,
      notes_status: notes ? "completed" : "pending" });
    render(<Providers><NotesPage /></Providers>);
    await flush();
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    await flush();
    expect(shown.recording?.status).toBe(RecordingStatus.PROCESSING);
    fireEvent.click(screen.getByRole("button", { name: notes ? "Regenerate Notes" : "Generate Notes" }));
    await flush();
    expect(shown.recording?.transcript?.notes_run_id).toBe(RUN_B);
    expect(screen.getByRole("button", { name: "Cancel" })).toBeInTheDocument();
  });

  it("observes A to B when status, notes and progress are identical", async () => {
    const { result } = renderHook(() => useRecordingDetail({ params }), { wrapper: Providers });
    await flush();
    server = recording(RUN_B);
    await act(async () => vi.advanceTimersByTimeAsync(3000));
    expect(result.current.recording?.transcript?.notes_run_id).toBe(RUN_B);
  });

  it.each(["active", "idle"])("rejects a late %s poll after Cancel and Generate B", async (mode) => {
    if (mode === "idle") server = recording(null);
    const { result } = renderHook(() => useRecordingDetail({ params }), { wrapper: Providers });
    await flush();
    const old = deferred<AxiosResponse>();
    read = () => old.promise;
    await act(async () => vi.advanceTimersByTimeAsync(mode === "idle" ? IDLE_RECORDING_POLL_INTERVAL_MS : 3000));
    await act(async () => { await result.current.handleCancelNotes(RUN_A); });
    await act(async () => { await result.current.handleGenerateNotes(); });
    await act(async () => old.resolve(response({} as InternalAxiosRequestConfig, {
      ...recording(RUN_A), transcript: { ...recording(RUN_A).transcript!, notes_progress: "Combining notes" },
    })));
    expect(result.current.recording?.transcript?.notes_run_id).toBe(RUN_B);
  });

  it("rejects a late Generate response after Cancel and replacement Generate", async () => {
    server = recording(null);
    const { result } = renderHook(() => useRecordingDetail({ params }), { wrapper: Providers });
    await flush();
    const old = deferred<AxiosResponse>();
    const next = generate;
    generate = () => old.promise;
    let request!: Promise<void>;
    await act(async () => { request = result.current.handleGenerateNotes(); });
    // A metadata read can discover the claimed run before its original POST answers.
    server = recording(RUN_A);
    await act(async () => vi.advanceTimersByTimeAsync(IDLE_RECORDING_POLL_INTERVAL_MS));
    expect(result.current.recording?.transcript?.notes_run_id).toBe(RUN_A);
    await act(async () => { await result.current.handleCancelNotes(RUN_A); });
    generate = next;
    await act(async () => { await result.current.handleGenerateNotes(); });
    await act(async () => { old.resolve(response({} as InternalAxiosRequestConfig, lifecycle(RUN_A))); await request; });
    expect(result.current.recording?.transcript?.notes_run_id).toBe(RUN_B);
  });

  it("rejects an accepted idle read delayed by transcript loading across a notes mutation", async () => {
    server = recording(null);
    const { result } = renderHook(() => useRecordingDetail({ params }), { wrapper: Providers });
    await flush();
    const oldTranscript = deferred<AxiosResponse>();
    let loadingTranscript = false;
    transcript = () => { loadingTranscript = true; return oldTranscript.promise; };
    server = { ...recording(RUN_A), transcript: { ...recording(RUN_A).transcript!, change_sequence: 1 } };
    await act(async () => vi.advanceTimersByTimeAsync(IDLE_RECORDING_POLL_INTERVAL_MS));
    expect(loadingTranscript).toBe(true);
    await act(async () => { await result.current.handleCancelNotes(RUN_A); });
    await act(async () => { await result.current.handleGenerateNotes(); });
    await act(async () => oldTranscript.resolve(response({} as InternalAxiosRequestConfig, {
      recording_id: "rec-1", revision: 1, speakers: [], tombstones: [], utterances: [],
    })));
    expect(result.current.recording?.transcript?.notes_run_id).toBe(RUN_B);
  });

  it("applies the replacement run in a stale Cancel 409 without another GET", async () => {
    const { result } = renderHook(() => useRecordingDetail({ params }), { wrapper: Providers });
    await flush();
    read = () => new Promise(() => {});
    cancel = async (config) => { throw new AxiosError("Conflict", "ERR_BAD_REQUEST", config, undefined,
      response(config, { detail: lifecycle(RUN_B) }, 409)); };
    await act(async () => { await result.current.handleCancelNotes(RUN_A); });
    expect(result.current.recording?.transcript?.notes_run_id).toBe(RUN_B);
    expect(result.current.recording?.transcript?.notes_status).toBe("generating");
  });

  it.each(["success", "failure"])("rejects a rename refresh whose transcript %s arrives after Cancel A and Generate B",
    async (outcome) => {
      const { result } = renderHook(() => useRecordingDetail({ params }), { wrapper: Providers });
      await flush();
      const oldTranscript = deferred<AxiosResponse>();
      let loadingTranscript = false;
      transcript = () => { loadingTranscript = true; return oldTranscript.promise; };
      let rename!: Promise<boolean>;
      await act(async () => { rename = result.current.handleRenameSpeaker("SPEAKER_1", "Alex"); });
      expect(loadingTranscript).toBe(true);
      await act(async () => { await result.current.handleCancelNotes(RUN_A); });
      await act(async () => { await result.current.handleGenerateNotes(); });
      await act(async () => {
        if (outcome === "success") oldTranscript.resolve(response({} as InternalAxiosRequestConfig, {
          recording_id: "rec-1", revision: 1, speakers: [], tombstones: [], utterances: [],
        }));
        else oldTranscript.reject(new Error("Transcript refresh offline"));
        await rename;
      });
      expect(result.current.recording?.transcript?.notes_run_id).toBe(RUN_B);
      expect(result.current.recording?.transcript?.notes_status).toBe("generating");
    });

  it("keeps the replacement's change counter when an older recording refresh's transcript fails", async () => {
    server = { ...recording(), transcript: { ...recording().transcript!, change_sequence: 0 } };
    const { result } = renderHook(() => useRecordingDetail({ params }), { wrapper: Providers });
    await flush();
    const oldTranscript = deferred<AxiosResponse>();
    let loadingTranscript = false;
    transcript = () => { loadingTranscript = true; return oldTranscript.promise; };
    server = { ...server, transcript: { ...server.transcript!, change_sequence: 1 } };
    let refresh!: Promise<void>;
    await act(async () => { refresh = result.current.refreshRecordingView(); });
    expect(loadingTranscript).toBe(true);
    await act(async () => { await result.current.handleCancelNotes(RUN_A); });
    await act(async () => { await result.current.handleGenerateNotes(); });
    await act(async () => { oldTranscript.reject(new Error("Transcript refresh offline")); await refresh; });
    expect(result.current.recording?.transcript?.notes_run_id).toBe(RUN_B);
    expect(result.current.recording?.transcript?.change_sequence).toBe(1);
  });
});
