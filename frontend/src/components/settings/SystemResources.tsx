import { useEffect, useRef, useState } from "react";
import { CartesianGrid, Line, LineChart, ResponsiveContainer, XAxis, YAxis } from "recharts";

import { getSystemResources } from "@/lib/api";
import { formatFileSize as bytes } from "@/lib/backupMessages";
import { formatTimeZoneLocale } from "@/lib/timezone";
import { useUserTimeZone } from "@/lib/useUserTimeZone";
import type { SystemResources as Sample } from "@/types";

import SettingsBlock from "./SettingsBlock";
import SettingsCard from "./SettingsCard";

const percent = (value: number | null | undefined) => value == null ? "unavailable" : `${value.toFixed(1)}%`;
type Point = { time: number; [key: string]: number | null };

function Graph({ title, data, series }: {
  title: string; data: Point[]; series: string[];
}) {
  const timeZone = useUserTimeZone();
  return (
    <div role="img" aria-label={title} className="min-w-0">
      <h3 className="mb-2 text-sm font-medium text-foreground">{title}</h3>
      <ResponsiveContainer width="100%" height={150}>
        <LineChart data={data}>
          <CartesianGrid stroke="var(--chart-grid)" vertical={false} />
          <XAxis dataKey="time" type="number" domain={["dataMin", "dataMax"]}
            tickFormatter={(time: number) => formatTimeZoneLocale(time, timeZone, "time")}
            tick={{ fill: "var(--contrast-icon-muted)", fontSize: 10 }} minTickGap={50} />
          <YAxis domain={[0, 100]} unit="%" width={40}
            tick={{ fill: "var(--contrast-icon-muted)", fontSize: 10 }} />
          {series.map((key, index) => <Line key={key} dataKey={key} dot={false}
            stroke={index ? "var(--chart-2)" : "var(--chart-1)"}
            strokeDasharray={index ? "4 3" : undefined} isAnimationActive={false} />)}
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}

function point(sample: Sample): Point {
  const result: Point = {
    time: Date.parse(sample.sampled_at), cpu: sample.host.cpu_percent,
    ram: sample.host.memory.used_bytes / sample.host.memory.total_bytes * 100,
    nojoinCpu: sample.nojoin?.cpu_percent ?? null,
    nojoinRam: sample.nojoin ? sample.nojoin.memory_bytes / sample.host.memory.total_bytes * 100 : null,
  };
  for (const gpu of sample.gpus) {
    result[`${gpu.uuid}:use`] = gpu.utilisation_percent;
    result[`${gpu.uuid}:ram`] = gpu.memory.used_bytes / gpu.memory.total_bytes * 100;
  }
  return result;
}

function appendSample(previous: Point[], sample: Sample): Point[] {
  const next = point(sample);
  const recent = previous.filter((p) => p.time < next.time && p.time > next.time - 300_000);
  if (recent.length && next.time - recent[recent.length - 1].time > 6000) {
    recent.push({ time: next.time - 1 });
  }
  return [...recent, next].slice(-150);
}

function useResources() {
  const section = useRef<HTMLDivElement>(null);
  const [sample, setSample] = useState<Sample | null>(null);
  const [history, setHistory] = useState<Point[]>([]);
  const [error, setError] = useState(false);

  useEffect(() => {
    let disposed = false;
    let visible = false;
    let timer: ReturnType<typeof setInterval> | undefined;
    let pending = false;
    const refresh = async () => {
      if (disposed || pending || !visible || document.hidden) return;
      pending = true;
      try {
        const next = await getSystemResources();
        if (disposed || !visible || document.hidden) return;
        setSample(next);
        setError(false);
        setHistory((previous) => appendSample(previous, next));
      } catch {
        if (!disposed) setError(true);
      } finally {
        pending = false;
      }
    };
    const update = () => {
      clearInterval(timer);
      if (visible && !document.hidden) {
        void refresh();
        timer = setInterval(() => void refresh(), 2000);
      }
    };
    const observer = new IntersectionObserver(([entry]) => {
      visible = entry.isIntersecting;
      update();
    });
    if (section.current) observer.observe(section.current);
    document.addEventListener("visibilitychange", update);
    return () => {
      disposed = true;
      clearInterval(timer);
      observer.disconnect();
      document.removeEventListener("visibilitychange", update);
    };
  }, []);

  return { section, sample, history, error };
}

function ResourceCharts({ sample, history }: { sample: Sample; history: Point[] }) {
  return (
    <>
      <p className="text-sm contrast-helper">
        Host CPU {percent(sample.host.cpu_percent)} · RAM {bytes(sample.host.memory.used_bytes)} / {bytes(sample.host.memory.total_bytes)}
      </p>
      <p className="text-sm contrast-helper">
        Nojoin CPU {percent(sample.nojoin?.cpu_percent)} · RAM {sample.nojoin ? bytes(sample.nojoin.memory_bytes) : "unavailable"} (container charges, including cache)
      </p>
      <div className="mt-4 grid gap-4 sm:grid-cols-2">
        <Graph title="Host CPU and Nojoin share" data={history} series={["cpu", "nojoinCpu"]} />
        <Graph title="Host RAM and Nojoin share" data={history} series={["ram", "nojoinRam"]} />
        {sample.capabilities.gpu && sample.gpus.map((gpu) => (
          <div key={gpu.uuid}>
            <Graph title={`${gpu.name} utilisation and VRAM`} data={history}
              series={[`${gpu.uuid}:use`, `${gpu.uuid}:ram`]} />
            <p className="text-xs contrast-helper">
              {gpu.uuid} · Utilisation {percent(gpu.utilisation_percent)} (solid) · VRAM {bytes(gpu.memory.used_bytes)} / {bytes(gpu.memory.total_bytes)} (dashed)
            </p>
          </div>
        ))}
      </div>
    </>
  );
}

function Worker({ worker }: { worker: Sample["workers"][number] }) {
  const timeZone = useUserTimeZone();
  return (
    <div className="mt-4 space-y-1 text-sm">
      <h4 className="font-medium text-foreground">
        {worker.lanes.join(", ") || "Starting"} · {worker.identity}
      </h4>
      <p className="contrast-helper">
        {worker.reserved_count} reserved · Reported {formatTimeZoneLocale(Date.parse(worker.sampled_at), timeZone, "time")}
      </p>
      {worker.running.length === 0 && <p>Idle</p>}
      {worker.running.map((task, index) => (
        <p key={index}>
          {task.name} · Started {task.started_at ? formatTimeZoneLocale(Date.parse(task.started_at), timeZone, "time") : "unavailable"} · {task.elapsed_seconds == null ? "Elapsed unavailable" : `${Math.floor(task.elapsed_seconds)}s elapsed`}
        </p>
      ))}
      {worker.models.map((model, index) => (
        <p key={index}>
          {model.name} · {model.device} · Estimate: {model.estimated_bytes == null ? "unavailable" : bytes(model.estimated_bytes)} ({model.method})
        </p>
      ))}
      {Object.entries(worker.cuda_reserved_bytes).map(([device, size]) => (
        <p key={device}>{device} · PyTorch process reserved estimate: {bytes(size)}</p>
      ))}
    </div>
  );
}

function Workers({ sample }: { sample: Sample }) {
  const queues = sample.queues
    ? Object.entries(sample.queues).map(([lane, count]) => `${lane}: ${count} queued`).join(" · ")
    : "Queue counts unavailable";

  return (
    <SettingsBlock>
      <h3 className="font-medium text-foreground">Workers and queues</h3>
      <p className="text-sm contrast-helper">{queues}</p>
      {!sample.capabilities.workers && <p>Worker reports unavailable</p>}
      {sample.capabilities.workers && !sample.workers.length && <p>No current worker reports</p>}
      {sample.workers.map((worker) => <Worker key={worker.identity} worker={worker} />)}
      <p className="mt-3 text-xs contrast-helper">
        Model estimates exclude activations and workspaces. PyTorch reserved memory includes its allocator cache. Shared tensors are counted once.
      </p>
    </SettingsBlock>
  );
}

export default function SystemResources() {
  const { section, sample, history, error } = useResources();

  return (
    <div ref={section}>
      <SettingsCard id="system-resources" title="Resources"
        description="Last five minutes, kept in this browser. Host: solid line; Nojoin: dashed line. Updates while visible.">
        <SettingsBlock>
          {error && <p role="status">Resources unavailable. Existing figures may be stale.</p>}
          {!sample && !error && <p role="status">Waiting for a resource sample…</p>}
          {sample && <ResourceCharts sample={sample} history={history} />}
        </SettingsBlock>
        {sample && <Workers sample={sample} />}
      </SettingsCard>
    </div>
  );
}
