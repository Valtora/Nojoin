import { useEffect, useMemo, useState } from "react";
import {
  addMonths,
  eachDayOfInterval,
  endOfMonth,
  endOfWeek,
  format,
  isSameDay,
  isSameMonth,
  startOfDay,
  startOfMonth,
  startOfWeek,
} from "date-fns";

import { getCalendarDashboardSummary } from "@/lib/api";
import {
  DEFAULT_TIME_ZONE,
  getUserTimeZone,
  toTimeZoneDate,
} from "@/lib/timezone";
import { CalendarDashboardSummary } from "@/types";

import {
  CalendarViewMode,
  DayTimelineDayState,
  buildDayTimeline,
  buildFooterText,
  buildMonthAgendaItems,
  buildNextEventHelper,
  eventOccursOnDay,
  getDayMarkerColours,
  recordingOccursOnDay,
} from "./calendarUtils";

export function useCalendarDashboard() {
  const [now, setNow] = useState<Date>(() => new Date());
  const [timeZone, setTimeZone] = useState(DEFAULT_TIME_ZONE);
  const [timeZoneReady, setTimeZoneReady] = useState(false);
  const [viewedMonth, setViewedMonth] = useState<Date>(() =>
    startOfMonth(toTimeZoneDate(new Date(), DEFAULT_TIME_ZONE)),
  );
  const [viewMode, setViewMode] = useState<CalendarViewMode>("month");
  const [selectedDay, setSelectedDay] = useState<Date | null>(() =>
    startOfDay(toTimeZoneDate(new Date(), DEFAULT_TIME_ZONE)),
  );
  const [summary, setSummary] = useState<CalendarDashboardSummary | null>(null);
  const [calendarLoading, setCalendarLoading] = useState(true);
  const [calendarRefreshing, setCalendarRefreshing] = useState(false);
  const [calendarError, setCalendarError] = useState<string | null>(null);
  const [initialisedView, setInitialisedView] = useState(false);

  useEffect(() => {
    let cancelled = false;

    void getUserTimeZone().then((resolvedTimeZone) => {
      if (!cancelled) {
        setTimeZone(resolvedTimeZone);
        setTimeZoneReady(true);
      }
    });

    return () => {
      cancelled = true;
    };
  }, []);

  const activeTimeZone = summary?.timezone || timeZone;
  const zonedNow = useMemo(() => toTimeZoneDate(now, activeTimeZone), [now, activeTimeZone]);
  const currentDay = useMemo(() => startOfDay(zonedNow), [zonedNow]);

  useEffect(() => {
    const updateNow = () => {
      setNow(new Date());
    };

    updateNow();
    const interval = window.setInterval(updateNow, 30000);

    return () => {
      window.clearInterval(interval);
    };
  }, []);

  useEffect(() => {
    if (!timeZoneReady || initialisedView) {
      return;
    }

    setViewedMonth(startOfMonth(currentDay));
    setSelectedDay(currentDay);
    setInitialisedView(true);
  }, [currentDay, initialisedView, timeZoneReady]);

  const viewedMonthKey = format(viewedMonth, "yyyy-MM");

  useEffect(() => {
    if (!timeZoneReady) {
      return;
    }

    setSelectedDay((currentSelectedDay) => {
      if (currentSelectedDay && isSameMonth(currentSelectedDay, viewedMonth)) {
        return currentSelectedDay;
      }
      if (isSameMonth(viewedMonth, zonedNow)) {
        return currentDay;
      }
      return startOfMonth(viewedMonth);
    });
  }, [currentDay, timeZoneReady, viewedMonth, zonedNow]);

  useEffect(() => {
    if (!timeZoneReady) {
      return;
    }

    let active = true;

    const loadSummary = async (preserveContent = false) => {
      if (active) {
        if (preserveContent) {
          setCalendarRefreshing(true);
        } else {
          setCalendarLoading(true);
          setCalendarError(null);
        }
      }

      try {
        const response = await getCalendarDashboardSummary(viewedMonthKey, activeTimeZone);
        if (!active) {
          return;
        }
        setSummary(response);
      } catch {
        if (!active) {
          return;
        }
        if (!preserveContent) {
          setCalendarError("Unable to load calendar data.");
        }
      } finally {
        if (active) {
          if (preserveContent) {
            setCalendarRefreshing(false);
          } else {
            setCalendarLoading(false);
          }
        }
      }
    };

    void loadSummary();
    const interval = window.setInterval(() => {
      void loadSummary(true);
    }, 60000);

    return () => {
      active = false;
      window.clearInterval(interval);
    };
  }, [activeTimeZone, timeZoneReady, viewedMonthKey]);

  const monthDays = useMemo(
    () =>
      eachDayOfInterval({
        start: startOfWeek(startOfMonth(viewedMonth), { weekStartsOn: 1 }),
        end: endOfWeek(endOfMonth(viewedMonth), { weekStartsOn: 1 }),
      }),
    [viewedMonth],
  );
  const isViewingCurrentMonth = isSameMonth(viewedMonth, zonedNow);
  const viewedMonthLabel = format(viewedMonth, "MMMM yyyy");
  const monthEvents = useMemo(() => summary?.agenda_items ?? [], [summary]);
  const monthRecordings = useMemo(() => summary?.recording_items ?? [], [summary]);
  const dayMarkerColours = useMemo(() => {
    const coloursByDay = new Map<string, string[]>();

    monthDays.forEach((day) => {
      const dayKey = format(day, "yyyy-MM-dd");
      const dayColours = getDayMarkerColours(monthEvents, monthRecordings, day, activeTimeZone);

      if (dayColours.length) {
        coloursByDay.set(dayKey, dayColours);
      }
    });

    return coloursByDay;
  }, [activeTimeZone, monthDays, monthEvents, monthRecordings]);
  const nextEventHelper = useMemo(
    () => buildNextEventHelper(summary, now, activeTimeZone),
    [activeTimeZone, now, summary],
  );
  const footerText = useMemo(
    () => buildFooterText(summary, viewedMonthLabel, calendarError),
    [summary, viewedMonthLabel, calendarError],
  );
  const monthAgendaItems = useMemo(
    () => buildMonthAgendaItems(monthEvents, monthRecordings),
    [monthEvents, monthRecordings],
  );
  const monthHasContent = monthEvents.length > 0 || monthRecordings.length > 0;
  const selectedDayEvents = useMemo(() => {
    if (!selectedDay) {
      return [];
    }
    return monthEvents.filter((event) => eventOccursOnDay(event, selectedDay, activeTimeZone));
  }, [activeTimeZone, monthEvents, selectedDay]);
  const selectedDayRecordings = useMemo(() => {
    if (!selectedDay) {
      return [];
    }
    return monthRecordings.filter((recording) => recordingOccursOnDay(recording, selectedDay, activeTimeZone));
  }, [activeTimeZone, monthRecordings, selectedDay]);
  const selectedDayHasContent = selectedDayEvents.length > 0 || selectedDayRecordings.length > 0;
  const selectedDayLabel = selectedDay ? format(selectedDay, "EEEE d MMMM") : null;
  const selectedDayState = useMemo<DayTimelineDayState | null>(() => {
    if (!selectedDay) {
      return null;
    }

    if (isSameDay(selectedDay, currentDay)) {
      return "today";
    }

    return selectedDay.getTime() < currentDay.getTime() ? "past" : "future";
  }, [currentDay, selectedDay]);
  const selectedDayTimeline = useMemo(
    () => buildDayTimeline(selectedDayEvents, selectedDay, now, activeTimeZone, selectedDayState),
    [activeTimeZone, now, selectedDay, selectedDayEvents, selectedDayState],
  );
  const mobileNowDividerIndex = useMemo(() => {
    if (selectedDayState !== "today" || !selectedDayTimeline?.timedEvents.length) {
      return null;
    }

    const firstLiveEventIndex = selectedDayTimeline.timedEvents.findIndex((event) => event.status === "live");
    if (firstLiveEventIndex >= 0) {
      return firstLiveEventIndex;
    }

    const firstUpcomingEventIndex = selectedDayTimeline.timedEvents.findIndex((event) => event.status === "upcoming");
    return firstUpcomingEventIndex >= 0
      ? firstUpcomingEventIndex
      : selectedDayTimeline.timedEvents.length;
  }, [selectedDayState, selectedDayTimeline]);
  const isViewingToday = Boolean(
    selectedDay && isViewingCurrentMonth && isSameDay(selectedDay, currentDay),
  );

  const handleJumpToToday = () => {
    const currentDate = new Date();
    const currentZonedDate = startOfDay(toTimeZoneDate(currentDate, activeTimeZone));
    setNow(currentDate);
    setViewedMonth(startOfMonth(currentZonedDate));
    setSelectedDay(currentZonedDate);
  };

  const handlePreviousMonth = () =>
    setViewedMonth((currentMonth) => addMonths(currentMonth, -1));
  const handleNextMonth = () =>
    setViewedMonth((currentMonth) => addMonths(currentMonth, 1));

  return {
    now,
    activeTimeZone,
    viewedMonth,
    viewMode,
    setViewMode,
    selectedDay,
    setSelectedDay,
    calendarLoading,
    calendarRefreshing,
    currentDay,
    monthDays,
    isViewingCurrentMonth,
    viewedMonthLabel,
    dayMarkerColours,
    nextEventHelper,
    footerText,
    monthAgendaItems,
    monthHasContent,
    selectedDayEvents,
    selectedDayRecordings,
    selectedDayHasContent,
    selectedDayLabel,
    selectedDayState,
    selectedDayTimeline,
    mobileNowDividerIndex,
    isViewingToday,
    handleJumpToToday,
    handlePreviousMonth,
    handleNextMonth,
  };
}
