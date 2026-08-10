"use client";

import {
  Area,
  AreaChart,
  CartesianGrid,
  Legend,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import type { AnalyticsMetrics, AnalyticsSpeaker } from "@/types";

import { chartColor } from "./chartPalette";
import { formatDuration, formatTimestamp } from "./formatDuration";

interface TalkShareTimelineProps {
  speakers: AnalyticsSpeaker[];
  metrics: AnalyticsMetrics;
}

interface TooltipEntry {
  name: string;
  value: number;
  color: string;
}

const ChartTooltip = ({
  active,
  payload,
  label,
}: {
  active?: boolean;
  payload?: TooltipEntry[];
  label?: number;
}) => {
  if (!active || !payload?.length) return null;
  const speaking = payload.filter((entry) => entry.value > 0);
  if (!speaking.length) return null;
  return (
    <div className="rounded-lg border border-surface-float-border bg-surface-float p-2 text-xs shadow-float">
      <p className="mb-1 font-medium text-foreground">
        From {formatTimestamp(label ?? 0)}
      </p>
      <ul className="space-y-0.5">
        {speaking.map((entry) => (
          <li key={entry.name} className="flex items-center gap-2">
            <span
              className="h-2 w-2 shrink-0 rounded-full"
              style={{ backgroundColor: entry.color }}
              aria-hidden="true"
            />
            <span className="text-contrast-muted">{entry.name}</span>
            <span className="ml-auto tabular-nums text-foreground">
              {formatDuration(entry.value)}
            </span>
          </li>
        ))}
      </ul>
    </div>
  );
};

export default function TalkShareTimeline({
  speakers,
  metrics,
}: TalkShareTimelineProps) {
  const { buckets } = metrics.timeline;
  if (buckets.length < 2) return null;

  const data = buckets.map((bucket) => {
    const point: Record<string, number> = { start: bucket.start_ms };
    speakers.forEach((speaker) => {
      point[speaker.name] = bucket.speech_ms[speaker.speaker_key] ?? 0;
    });
    return point;
  });

  return (
    <ResponsiveContainer width="100%" height={200}>
      <AreaChart data={data} margin={{ top: 4, right: 8, bottom: 0, left: 0 }}>
        <CartesianGrid
          stroke="var(--chart-grid)"
          strokeDasharray="3 3"
          vertical={false}
        />
        <XAxis
          dataKey="start"
          tickFormatter={(value: number) => formatTimestamp(value)}
          tickLine={false}
          axisLine={false}
          tick={{ fill: "var(--contrast-icon-muted)", fontSize: 11 }}
          minTickGap={32}
        />
        <YAxis
          tickFormatter={(value: number) => `${Math.round(value / 1000)}s`}
          tickLine={false}
          axisLine={false}
          width={44}
          tick={{ fill: "var(--contrast-icon-muted)", fontSize: 11 }}
        />
        <Tooltip content={<ChartTooltip />} cursor={{ stroke: "var(--chart-grid)" }} />
        {/* A stacked area cannot carry a direct label per series, so identity
            comes from the legend plus the tooltip, and the talk-share list
            beside this chart repeats the same colours against names. */}
        <Legend
          iconType="circle"
          iconSize={8}
          wrapperStyle={{ fontSize: 12, color: "var(--contrast-helper)" }}
        />
        {speakers.map((speaker, index) => (
          <Area
            key={speaker.speaker_key}
            type="monotone"
            dataKey={speaker.name}
            stackId="speech"
            // Stroked in the surface colour, not the series colour: the 2px
            // seam is what stops two adjacent bands reading as one shape.
            // Stroking each band in its own colour would merge them instead.
            stroke="var(--surface-card)"
            strokeWidth={2}
            fill={chartColor(index)}
            fillOpacity={0.9}
            isAnimationActive={false}
          />
        ))}
      </AreaChart>
    </ResponsiveContainer>
  );
}
