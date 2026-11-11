"use client";

/** A person's meeting history, independent of the editable contact form. */
import { useEffect, useId, useState } from "react";
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
  const meetingsHeading = useId();
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
    {/* Each part scrolls on its own, so a long run of months never pushes the meetings out of view, and
        both stay bounded so a long history never pushes the contact form out of reach. */}
    <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
      {data.talk_time_by_month.length > 0 && <MonthlyTalkTime months={data.talk_time_by_month} />}
      <div className={data.talk_time_by_month.length > 0 ? undefined : "md:col-span-2"}>
        <h4 id={meetingsHeading} className="text-sm font-medium text-foreground">
          {data.meeting_count > data.meetings.length ? `Latest ${data.meetings.length} meetings` : "Linked meetings"}
        </h4>
        <div role="region" aria-labelledby={meetingsHeading} className="relative mt-2 max-h-72 overflow-y-auto px-1">
          <ul className="divide-y divide-surface-border">
            {data.meetings.map((meeting) => <li key={meeting.recording_id}>
              <Link href={`/recordings/${meeting.recording_id}`}
                className="block rounded-md px-1 py-2 focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-focus-ring">
                <span className="block truncate text-sm font-medium text-action-text hover:underline">
                  {meeting.title}
                </span>
                <span className="block text-xs text-contrast-helper">
                  {displayDate(meeting.date, timeZone)} · Duration: {meeting.duration_seconds === null
                    ? "Unknown" : formatDuration(meeting.duration_seconds * 1000)}
                  {" · Talk time: "}{formatDuration(meeting.talk_time_seconds * 1000)}
                </span>
              </Link>
            </li>)}
          </ul>
        </div>
      </div>
    </div>
  </>;
}

function Figure({ label, value }: { label: string; value: string }) {
  return <div>
    <dt className="text-xs text-contrast-helper">{label}</dt>
    <dd className="font-medium tabular-nums text-foreground">{value}</dd>
  </div>;
}

/** A YYYY-MM month as a count of months, so stepping back across a year boundary is plain subtraction. */
function monthIndex(month: string): number {
  const [year, number] = month.split("-").map(Number);
  return year * 12 + number - 1;
}

/**
 * Every month from the newest to the oldest given, newest first, with a month that has no meetings as null.
 * The months are already calendar months in the user's time zone, so filling the gaps needs no time zone.
 */
function withQuietMonths(months: PersonTalkMonthRead[]): { month: string; seconds: number | null }[] {
  const talk = new Map(months.map((month) => [month.month, month.talk_time_seconds]));
  const indices = months.map((month) => monthIndex(month.month));
  const slots = [];
  for (let index = Math.max(...indices); index >= Math.min(...indices); index -= 1) {
    const month = `${Math.floor(index / 12)}-${String(index % 12 + 1).padStart(2, "0")}`;
    slots.push({ month, seconds: talk.get(month) ?? null });
  }
  return slots;
}

function MonthlyTalkTime({ months }: { months: PersonTalkMonthRead[] }) {
  const heading = useId();
  const maximum = Math.max(...months.map((month) => month.talk_time_seconds), 1);
  return <div>
    <h4 id={heading} className="text-sm font-medium text-foreground">Talk time by month</h4>
    {/* Focusable so the keyboard can scroll it: nothing inside takes focus. */}
    <div role="region" aria-labelledby={heading} tabIndex={0}
      className="relative mt-2 max-h-40 overflow-y-auto rounded-md px-1 focus-visible:outline-2 focus-visible:outline-focus-ring md:max-h-72">
      <ul className="space-y-2" aria-label="Monthly talk time">
        {withQuietMonths(months).map(({ month, seconds }) => <li key={month}>
          <div className="flex justify-between gap-3 text-xs text-contrast-helper">
            <span>{formatTimeZoneDate(`${month}-01T00:00:00Z`, "UTC", "MMM yyyy")}</span>
            <span className="tabular-nums">{seconds === null ? "No meetings" : formatDuration(seconds * 1000)}</span>
          </div>
          <div aria-hidden="true" className="mt-1 h-1.5 overflow-hidden rounded-full bg-surface-inset">
            {seconds !== null && <div className="h-full rounded-full bg-action"
              style={{ width: `${seconds / maximum * 100}%` }} />}
          </div>
        </li>)}
      </ul>
    </div>
  </div>;
}
