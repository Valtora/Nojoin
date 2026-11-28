package calendar

import (
	"sort"
	"strings"
	"time"

	"github.com/teambition/rrule-go"

	"github.com/Valtora/Nojoin/proton/internal/ical"
)

// Window is a half-open time range.
type Window struct {
	Start time.Time
	End   time.Time
}

func (w Window) overlaps(start, end time.Time) bool {
	if end.Equal(start) {
		end = start.Add(time.Second)
	}
	return start.Before(w.End) && end.After(w.Start)
}

// maxOccurrences caps the occurrences generated for one series in one window.
const maxOccurrences = 2000

// Occurrence is one row Nojoin stores: a single event, an occurrence expanded
// from a series, or an exception row.
type Occurrence struct {
	Row    *Decoded // the row whose properties describe it
	Master *Decoded // the series master, nil for a single event
	Start  ical.Moment
	End    ical.Moment
	// Original is the occurrence's start before any edit (RECURRENCE-ID).
	Original *ical.Moment
	Kind     string // single, occurrence or exception
}

// eventTimes reads DTSTART and DTEND (or DURATION) from a VEVENT. A missing
// end is one day for a date and zero length for a time.
func eventTimes(ev *ical.Component) (ical.Moment, ical.Moment, error) {
	start, err := ical.ParseMoment(ev.Get("DTSTART"))
	if err != nil {
		return start, start, err
	}
	end := start
	if p := ev.Get("DTEND"); p != nil {
		if e, err := ical.ParseMoment(p); err == nil {
			end = e
		}
	} else if start.AllDay {
		end = ical.Moment{AllDay: true, Date: start.Date.AddDate(0, 0, 1)}
	}
	if start.AllDay != end.AllDay {
		if start.AllDay {
			end = ical.Moment{AllDay: true, Date: start.Date.AddDate(0, 0, 1)}
		} else {
			end = start
		}
	}
	return start, end, nil
}

// rruleOf returns the RRULE value, or "".
func rruleOf(ev *ical.Component) string {
	if p := ev.Get("RRULE"); p != nil {
		v := strings.TrimSpace(p.Value)
		if len(v) > 6 && strings.EqualFold(v[:6], "RRULE:") {
			v = v[6:]
		}
		return v
	}
	return ""
}

// recurrenceIDOf returns a row's RECURRENCE-ID, from the card or the row.
func recurrenceIDOf(d *Decoded) *ical.Moment {
	if p := d.Merged.Get("RECURRENCE-ID"); p != nil {
		if m, err := ical.ParseMoment(p); err == nil {
			return &m
		}
	}
	if d.Row.RecurrenceID != nil {
		t := time.Unix(*d.Row.RecurrenceID, 0).UTC()
		m := ical.Moment{Time: t, Zone: "UTC"}
		if d.Row.FullDay == 1 {
			m = ical.Moment{AllDay: true, Date: time.Date(t.Year(), t.Month(), t.Day(), 0, 0, 0, 0, time.UTC)}
		}
		return &m
	}
	return nil
}

// seriesStarts lists the original starts of a master's occurrences that
// overlap the window, EXDATEs removed.
func seriesStarts(master *Decoded, w Window) ([]ical.Moment, time.Duration, int, error) {
	start, end, err := eventTimes(master.Merged)
	if err != nil {
		return nil, 0, 0, err
	}
	rule := rruleOf(master.Merged)
	loc := time.UTC
	anchor := start.Instant()
	if !start.AllDay {
		if l, err := ical.LoadZone(start.Zone); err == nil {
			loc = l
		}
		// Iterating in the event's own zone keeps each occurrence at the same
		// wall-clock time across daylight-saving changes.
		anchor = start.Time.In(loc)
	}
	opt, err := rrule.StrToROptionInLocation(untilForExpansion(rule, start.AllDay, loc), loc)
	if err != nil {
		return nil, 0, 0, err
	}
	opt.Dtstart = anchor
	r, err := rrule.NewRRule(*opt)
	if err != nil {
		return nil, 0, 0, err
	}
	var length time.Duration
	days := 0
	if start.AllDay {
		days = int(end.Date.Sub(start.Date).Hours()/24 + 0.5)
		if days < 1 {
			days = 1
		}
		length = time.Duration(days) * 24 * time.Hour
	} else {
		length = end.Time.Sub(start.Time)
	}
	excluded := map[int64]bool{}
	for _, t := range ical.ParseExdates(master.Merged.All("EXDATE")) {
		excluded[t.Unix()] = true
	}
	for _, x := range master.Row.Exdates {
		excluded[x] = true
	}
	from := w.Start.Add(-length - time.Second)
	var out []ical.Moment
	for _, t := range r.Between(from, w.End, true) {
		var m ical.Moment
		if start.AllDay {
			m = ical.Moment{AllDay: true, Date: time.Date(t.Year(), t.Month(), t.Day(), 0, 0, 0, 0, time.UTC)}
		} else {
			m = ical.Moment{Time: t.In(loc), Zone: start.Zone}
		}
		if excluded[m.Instant().Unix()] {
			continue
		}
		out = append(out, m)
		if len(out) >= maxOccurrences {
			break
		}
	}
	return out, length, days, nil
}

// Expand turns rows into occurrences inside the window. Rows of one series
// share a UID: the master is expanded, EXDATEs dropped, and exception rows
// replace the occurrences they edit. With unbounded true, single events and
// exceptions are returned whatever their time (for get_event).
func Expand(rows []*Decoded, w Window, unbounded bool) []Occurrence {
	byUID := map[string][]*Decoded{}
	var order []string
	for _, d := range rows {
		uid := propText(d.Merged, "UID")
		if uid == "" {
			uid = d.Row.UID
		}
		if _, ok := byUID[uid]; !ok {
			order = append(order, uid)
		}
		byUID[uid] = append(byUID[uid], d)
	}
	var out []Occurrence
	for _, uid := range order {
		out = append(out, expandSeries(byUID[uid], w, unbounded)...)
	}
	sort.SliceStable(out, func(i, j int) bool {
		return out[i].Start.Instant().Before(out[j].Start.Instant())
	})
	return out
}

func expandSeries(group []*Decoded, w Window, unbounded bool) []Occurrence {
	var master *Decoded
	var exceptions []*Decoded
	for _, d := range group {
		if recurrenceIDOf(d) != nil {
			exceptions = append(exceptions, d)
		} else if master == nil {
			master = d
		}
	}
	var out []Occurrence
	inWindow := func(s, e ical.Moment) bool {
		return unbounded || w.overlaps(s.Instant(), e.Instant())
	}
	if master != nil && rruleOf(master.Merged) == "" {
		s, e, err := eventTimes(master.Merged)
		if err == nil && inWindow(s, e) {
			out = append(out, Occurrence{Row: master, Start: s, End: e, Kind: "single"})
		}
		master = nil
	}
	replaced := map[int64]*Decoded{}
	for _, ex := range exceptions {
		replaced[recurrenceIDOf(ex).Instant().Unix()] = ex
	}
	if master != nil {
		starts, length, days, err := seriesStarts(master, w)
		if err != nil {
			// A rule this helper cannot expand: keep the first occurrence
			// rather than lose the event, and say so.
			master.warn("recurrence_unreadable")
			if s, e, terr := eventTimes(master.Merged); terr == nil && inWindow(s, e) {
				orig := s
				out = append(out, Occurrence{Row: master, Master: master, Start: s, End: e, Original: &orig, Kind: "occurrence"})
			}
		} else {
			for _, s := range starts {
				if _, ok := replaced[s.Instant().Unix()]; ok {
					continue
				}
				var e ical.Moment
				if s.AllDay {
					e = ical.Moment{AllDay: true, Date: s.Date.AddDate(0, 0, days)}
				} else {
					e = ical.Moment{Time: s.Time.Add(length), Zone: s.Zone}
				}
				if !w.overlaps(s.Instant(), e.Instant()) {
					continue
				}
				orig := s
				out = append(out, Occurrence{Row: master, Master: master, Start: s, End: e, Original: &orig, Kind: "occurrence"})
			}
		}
	}
	for _, ex := range exceptions {
		s, e, err := eventTimes(ex.Merged)
		if err != nil {
			continue
		}
		if !inWindow(s, e) {
			continue
		}
		orig := *recurrenceIDOf(ex)
		out = append(out, Occurrence{Row: ex, Master: master, Start: s, End: e, Original: &orig, Kind: "exception"})
	}
	return out
}

// seriesHasOccurrence reports whether the master generates an occurrence at the
// original start (used to check a target before writing).
func seriesHasOccurrence(master *Decoded, original time.Time) bool {
	w := Window{Start: original.Add(-time.Second), End: original.Add(time.Second)}
	starts, _, _, err := seriesStarts(master, w)
	if err != nil {
		return false
	}
	for _, s := range starts {
		if s.Instant().Equal(original) {
			return true
		}
	}
	return false
}

// untilForExpansion reads a date UNTIL on a timed series as the end of that day
// in the event's zone, as Proton's web client does, so the last day is kept.
func untilForExpansion(rule string, allDay bool, loc *time.Location) string {
	if allDay {
		return rule
	}
	parts := strings.Split(rule, ";")
	for i, part := range parts {
		k, v, ok := strings.Cut(part, "=")
		if !ok || !strings.EqualFold(k, "UNTIL") || len(v) != 8 {
			continue
		}
		d, err := time.ParseInLocation("20060102", v, loc)
		if err != nil {
			continue
		}
		end := time.Date(d.Year(), d.Month(), d.Day(), 23, 59, 59, 0, loc)
		parts[i] = "UNTIL=" + end.UTC().Format("20060102T150405Z")
	}
	return strings.Join(parts, ";")
}
