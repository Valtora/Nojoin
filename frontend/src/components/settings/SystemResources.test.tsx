import { act, render, screen } from "@testing-library/react";
import { cloneElement, type ReactElement } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import api from "@/lib/api/client";
import { setCachedUserTimeZone } from "@/lib/timezone";
import type { SystemResources as Sample } from "@/types";

import SystemResources from "./SystemResources";

// Layout is a browser edge: give the real charts a measured size in jsdom.
vi.mock("recharts", async (original) => ({
  ...await original<typeof import("recharts")>(),
  ResponsiveContainer: ({ children }: { children: ReactElement }) =>
    cloneElement(children as ReactElement<{ width: number; height: number }>, { width: 600, height: 150 }),
}));

const sample: Sample = {
  sampled_at: "2026-10-29T12:00:00Z",
  host: { cpu_percent: 60, memory: { used_bytes: 5 * 1024 ** 3, total_bytes: 8 * 1024 ** 3 } },
  nojoin: { cpu_percent: 40, memory_bytes: 2 * 1024 ** 3 },
  gpus: [{ uuid: "GPU-example", name: "Example GPU", utilisation_percent: 75, memory: { used_bytes: 2 * 1024 ** 3, total_bytes: 8 * 1024 ** 3 } }],
  workers: [{ identity: "gpu@synthetic", lanes: ["gpu"], sampled_at: "2026-10-29T12:00:00Z",
    running: [{ name: "celery.backend_cleanup", started_at: "2026-10-29T11:59:50Z", elapsed_seconds: 10 }],
    reserved_count: 1, models: [
      { name: "Whisper base", device: "cuda:0", estimated_bytes: 36, method: "CUDA tensor storage" },
      { name: "parakeet: parakeet", device: "cuda", estimated_bytes: null, method: "estimate unavailable" },
    ], cuda_reserved_bytes: { "cuda:0": 4096 } }],
  queues: { gpu: 6, cpu: 0, io: 0, parse: 0 },
  capabilities: { gpu: true, docker: true, workers: true },
};

describe("System resources", () => {
  let intersect: (entries: Array<{ isIntersecting: boolean }>) => void;
  let response: Sample;
  let requests: number;
  const adapter = api.defaults.adapter;

  beforeEach(() => {
    vi.useFakeTimers();
    requests = 0;
    response = structuredClone(sample);
    setCachedUserTimeZone("Asia/Tokyo");
    vi.spyOn(document, "hidden", "get").mockReturnValue(false);
    vi.stubGlobal("IntersectionObserver", class {
      constructor(callback: typeof intersect) { intersect = callback; }
      observe() { intersect([{ isIntersecting: true }]); }
      disconnect() {}
    });
    api.defaults.adapter = async (config) => {
      requests++;
      const data = { ...response, sampled_at: new Date(Date.parse(response.sampled_at) + requests * 2000).toISOString() };
      return { data, status: 200, statusText: "OK", headers: {}, config };
    };
  });

  afterEach(() => {
    api.defaults.adapter = adapter;
    vi.useRealTimers();
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  it("plots host/share/GPU data from the endpoint and shows lane activity", async () => {
    const { container } = render(<SystemResources />);
    await act(async () => { await vi.advanceTimersByTimeAsync(2000); });
    expect(screen.getByRole("img", { name: "Host CPU and Nojoin share" })).toBeInTheDocument();
    expect(screen.getByRole("img", { name: "Host RAM and Nojoin share" })).toBeInTheDocument();
    expect(screen.getByRole("img", { name: "Example GPU utilisation and VRAM" })).toBeInTheDocument();
    expect(container.querySelectorAll(".recharts-line-curve")).toHaveLength(6);
    expect(screen.getByText(/Host CPU 60.0% · RAM 5 GB \/ 8 GB/)).toBeInTheDocument();
    expect(screen.getByText(/Nojoin CPU 40.0% · RAM 2 GB/)).toBeInTheDocument();
    expect(screen.getByText(/gpu: 6 queued/)).toBeInTheDocument();
    expect(screen.getByText(/celery.backend_cleanup · Started .* · 10s elapsed/)).toBeInTheDocument();
    const started = new Date("2026-10-29T11:59:50Z").toLocaleTimeString(undefined, { timeZone: "Asia/Tokyo" });
    expect(screen.getByText(`celery.backend_cleanup · Started ${started} · 10s elapsed`)).toBeInTheDocument();
    expect(screen.getByText(/Whisper base · cuda:0 · Estimate: .*CUDA tensor storage/)).toBeInTheDocument();
    expect(screen.getByText(/parakeet: parakeet · cuda · Estimate: unavailable/)).toBeInTheDocument();
    expect(screen.getByText(/cuda:0 · PyTorch process reserved estimate:/)).toBeInTheDocument();
  });

  it("hides GPU graphs without capability and labels a missing Docker share", async () => {
    response.capabilities.gpu = false;
    response.nojoin = null;
    response.capabilities.docker = false;
    render(<SystemResources />);
    await act(async () => { await vi.advanceTimersByTimeAsync(0); });
    expect(screen.getAllByRole("img")).toHaveLength(2);
    expect(screen.getByText(/Nojoin CPU unavailable · RAM unavailable/)).toBeInTheDocument();
  });

  it("pauses for hidden tabs and offscreen sections, resumes, and stops on unmount", async () => {
    const { unmount } = render(<SystemResources />);
    await act(async () => { await vi.advanceTimersByTimeAsync(4000); });
    expect(requests).toBe(3);
    vi.spyOn(document, "hidden", "get").mockReturnValue(true);
    act(() => document.dispatchEvent(new Event("visibilitychange")));
    await act(async () => { await vi.advanceTimersByTimeAsync(6000); });
    expect(requests).toBe(3);
    vi.spyOn(document, "hidden", "get").mockReturnValue(false);
    act(() => document.dispatchEvent(new Event("visibilitychange")));
    await act(async () => { await vi.advanceTimersByTimeAsync(0); });
    expect(requests).toBe(4);
    act(() => intersect([{ isIntersecting: false }]));
    await act(async () => { await vi.advanceTimersByTimeAsync(6000); });
    expect(requests).toBe(4);
    act(() => intersect([{ isIntersecting: true }]));
    await act(async () => { await vi.advanceTimersByTimeAsync(0); });
    expect(requests).toBe(5);
    unmount();
    await act(async () => { await vi.advanceTimersByTimeAsync(6000); });
    expect(requests).toBe(5);
  });
});
