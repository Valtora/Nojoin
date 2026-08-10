"use client";

import {
  Bar,
  BarChart,
  Cell,
  LabelList,
  ResponsiveContainer,
  XAxis,
  YAxis,
} from "recharts";

import type { AnalyticsMetrics, AnalyticsSpeaker } from "@/types";

import { chartColor } from "./chartPalette";
import { formatDuration, formatShare } from "./formatDuration";

interface TalkShareChartProps {
  speakers: AnalyticsSpeaker[];
  metrics: AnalyticsMetrics;
}

const ROW_HEIGHT = 34;

export default function TalkShareChart({
  speakers,
  metrics,
}: TalkShareChartProps) {
  const data = speakers.map((speaker, index) => {
    const figures = metrics.talk_time[speaker.speaker_key];
    return {
      name: speaker.name,
      share: figures?.share_of_speech ?? 0,
      speechMs: figures?.speech_ms ?? 0,
      color: chartColor(index),
    };
  });

  if (!data.length) return null;

  return (
    <div>
      <ResponsiveContainer
        width="100%"
        height={Math.max(data.length * ROW_HEIGHT, ROW_HEIGHT)}
      >
        <BarChart
          data={data}
          layout="vertical"
          margin={{ top: 0, right: 96, bottom: 0, left: 0 }}
          barCategoryGap="26%"
        >
          <XAxis type="number" domain={[0, 1]} hide />
          <YAxis
            type="category"
            dataKey="name"
            width={112}
            tickLine={false}
            axisLine={false}
            tick={{ fill: "var(--contrast-muted)", fontSize: 12 }}
          />
          {/* Rounded data-end anchored to the baseline; the square end stays
              square so the bar reads as growing from zero. */}
          <Bar dataKey="share" radius={[0, 4, 4, 0]} isAnimationActive={false}>
            {data.map((entry) => (
              <Cell key={entry.name} fill={entry.color} />
            ))}
            {/* Direct labels rather than a tooltip alone. Three of the light
                theme's series steps sit under 3:1 on the card, which is only
                permissible while the value is legible as text beside the bar. */}
            <LabelList
              dataKey="share"
              position="right"
              formatter={(value) =>
                typeof value === "number" ? formatShare(value) : ""
              }
              style={{ fill: "var(--foreground)", fontSize: 12, fontWeight: 600 }}
            />
          </Bar>
        </BarChart>
      </ResponsiveContainer>

      {/* The table view the palette's contrast relief depends on, and the only
          place the absolute figure appears. */}
      <ul className="mt-1 space-y-0.5 text-xs text-contrast-helper">
        {data.map((entry) => (
          <li key={entry.name} className="flex items-center gap-2">
            <span
              className="h-2 w-2 shrink-0 rounded-full"
              style={{ backgroundColor: entry.color }}
              aria-hidden="true"
            />
            <span className="truncate">{entry.name}</span>
            <span className="ml-auto tabular-nums">
              {formatDuration(entry.speechMs)}
            </span>
          </li>
        ))}
      </ul>
    </div>
  );
}
