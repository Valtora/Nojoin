"use client";

/** A person's meeting history, independent of the editable contact form. */
import { useEffect, useState } from "react";
import Link from "next/link";
import { getPersonMeetings } from "@/lib/api";
import { parseServerTimestamp } from "@/lib/serverTimestamp";
import { formatTimeZoneDate } from "@/lib/timezone";
import { useUserTimeZone } from "@/lib/useUserTimeZone";
import { formatDuration } from "@/components/analytics/formatDuration";
import { ButtonBase } from "@/components/ui/nativeBases";
import type { PersonMeetingsRead, PersonTalkMonthRead } from "@/types";

type Result = { personId: number; data: PersonMeetingsRead } | { personId: number; error: true };

export function PersonMeetings({ personId }: { personId: number }) {
  const [result, setResult] = useState<Result | null>(null);
  const [attempt, setAttempt] = useState(0);
  const timeZone = useUserTimeZone();
  useEffect(() => {
    let active = true;
    void getPersonMeetings(personId).then(
      (data) => { if (active) setResult({ personId, data }); },
      () => { if (active) setResult({ personId, error: true }); },
    );
    return () => { active = false; };
  }, [personId, attempt]);

  const shown = result?.personId === personId ? result : null;
  return (
    <section aria-label="Meeting history" className="mb-6 space-y-4 border-b border-surface-border pb-6">
      <h3 className="text-base font-semibold text-foreground">Meetings</h3>
      {!shown ? <p role="status" className="text-sm text-contrast-helper">Loading meetings…</p>
        : "error" in shown ? <div className="space-y-2">
          <p role="alert" className="text-sm text-contrast-helper">Could not load meeting history.</p>
          <ButtonBase type="button" className="text-sm text-action-text hover:underline" onClick={() => {
            setResult(null);
            setAttempt((value) => value + 1);
          }}>Retry</ButtonBase>
        </div> : <MeetingHistory data={shown.data} timeZone={timeZone} />}
    </section>
  );
}

function displayDate(value: string | null, timeZone: string): string {
  const date = parseServerTimestamp(value);
  return date ? formatTimeZoneDate(date, timeZone, "d MMM yyyy") : "Unknown date";
}

function MeetingHistory({ data, timeZone }: { data: PersonMeetingsRead; timeZone: string }) {
  if (data.meeting_count === 0) return <p className="text-sm text-contrast-helper">
    No linked meetings yet. Meetings appear here when this person is linked to a speaker in one of your meetings.
  </p>;

  return <>
    <p className="text-xs text-contrast-helper">Figures cover all linked meetings you own, excluding deleted meetings.</p>
    <dl className="grid grid-cols-2 gap-3 text-sm">
      <Figure label="Meetings" value={String(data.meeting_count)} />
      <Figure label="Total talk time" value={formatDuration(data.total_talk_time_seconds * 1000)} />
      <Figure label="First seen" value={displayDate(data.first_seen, timeZone)} />
      <Figure label="Last seen" value={displayDate(data.last_seen, timeZone)} />
    </dl>
    <details className="space-y-4">
      <summary className="cursor-pointer rounded-sm text-sm font-medium text-action-text focus-visible:outline-2 focus-visible:outline-focus-ring">
        View meeting history
      </summary>
      {data.talk_time_by_month.length > 0 && <MonthlyTalkTime months={data.talk_time_by_month} />}
      <div>
      <h4 className="text-sm font-medium text-foreground">
        {data.meeting_count > data.meetings.length ? `Latest ${data.meetings.length} meetings` : "Linked meetings"}
      </h4>
      <ul className="mt-2 divide-y divide-surface-border">
        {data.meetings.map((meeting) => <li key={meeting.recording_id}>
          <Link href={`/recordings/${meeting.recording_id}`}
            className="block rounded-md py-2 focus-visible:outline-2 focus-visible:outline-focus-ring">
            <span className="block truncate text-sm font-medium text-action-text hover:underline">{meeting.title}</span>
            <span className="block text-xs text-contrast-helper">
              {displayDate(meeting.date, timeZone)} · Duration: {meeting.duration_seconds === null
                ? "Unknown" : formatDuration(meeting.duration_seconds * 1000)}
              {" · Talk time: "}{formatDuration(meeting.talk_time_seconds * 1000)}
            </span>
          </Link>
        </li>)}
      </ul>
      </div>
    </details>
  </>;
}

function Figure({ label, value }: { label: string; value: string }) {
  return <div>
    <dt className="text-xs text-contrast-helper">{label}</dt>
    <dd className="font-medium tabular-nums text-foreground">{value}</dd>
  </div>;
}

function MonthlyTalkTime({ months }: { months: PersonTalkMonthRead[] }) {
  const maximum = Math.max(...months.map((month) => month.talk_time_seconds), 1);
  return <div>
    <h4 className="text-sm font-medium text-foreground">Talk time by month (UTC)</h4>
    <ul className="mt-2 space-y-2" aria-label="Monthly talk time">
      {months.map((month) => <li key={month.month}>
        <div className="flex justify-between gap-3 text-xs text-contrast-helper">
          <span>{formatTimeZoneDate(`${month.month}-01T00:00:00Z`, "UTC", "MMM yyyy")}</span>
          <span className="tabular-nums">{formatDuration(month.talk_time_seconds * 1000)}</span>
        </div>
        <div aria-hidden="true" className="mt-1 h-1.5 overflow-hidden rounded-full bg-surface-inset">
          <div className="h-full rounded-full bg-action"
            style={{ width: `${month.talk_time_seconds / maximum * 100}%` }} />
        </div>
      </li>)}
    </ul>
  </div>;
}
