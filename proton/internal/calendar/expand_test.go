package calendar

import (
	"strings"
	"testing"
	"time"

	"github.com/Valtora/Nojoin/proton/internal/ical"
)

func row(t *testing.T, lines ...string) *Decoded {
	t.Helper()
	ev, err := ical.FirstEvent("BEGIN:VEVENT\r\n" + strings.Join(lines, "\r\n") + "\r\nEND:VEVENT")
	if err != nil {
		t.Fatal(err)
	}
	uid := ""
	if p := ev.Get("UID"); p != nil {
		uid = p.Value
	}
	return &Decoded{Row: &RawEvent{ID: "row-" + uid, UID: uid}, Merged: ev}
}

func utc(y int, m time.Month, d, h, min int) time.Time {
	return time.Date(y, m, d, h, min, 0, 0, time.UTC)
}

func starts(occ []Occurrence) []string {
	var out []string
	for _, o := range occ {
		if o.Start.AllDay {
			out = append(out, o.Start.Date.Format("2006-01-02"))
		} else {
			out = append(out, o.Start.Time.UTC().Format("2006-01-02T15:04Z"))
		}
	}
	return out
}

func TestWeeklySeriesKeepsWallClockAcrossLondonClockChanges(t *testing.T) {
	// 10:00 London every Tuesday from 17 March 2026: the clocks go forward on
	// 29 March and back on 25 October, and the meeting stays at 10:00 local.
	master := row(t, "UID:weekly", "DTSTART;TZID=Europe/London:20260317T100000", "DTEND;TZID=Europe/London:20260317T110000",
		"RRULE:FREQ=WEEKLY;BYDAY=TU")
	w := Window{Start: utc(2026, 3, 16, 0, 0), End: utc(2026, 4, 1, 0, 0)}
	got := starts(Expand([]*Decoded{master}, w, false))
	want := []string{"2026-03-17T10:00Z", "2026-03-24T10:00Z", "2026-03-31T09:00Z"}
	if strings.Join(got, " ") != strings.Join(want, " ") {
		t.Fatalf("spring: %v, want %v", got, want)
	}
	w = Window{Start: utc(2026, 10, 19, 0, 0), End: utc(2026, 11, 4, 0, 0)}
	got = starts(Expand([]*Decoded{master}, w, false))
	want = []string{"2026-10-20T09:00Z", "2026-10-27T10:00Z", "2026-11-03T10:00Z"}
	if strings.Join(got, " ") != strings.Join(want, " ") {
		t.Fatalf("autumn: %v, want %v", got, want)
	}
}

func TestExdatesAndExceptions(t *testing.T) {
	master := row(t, "UID:s", "DTSTART;TZID=Europe/London:20261201T100000", "DTEND;TZID=Europe/London:20261201T110000",
		"RRULE:FREQ=DAILY;COUNT=5", "EXDATE;TZID=Europe/London:20261202T100000")
	ex := row(t, "UID:s", "RECURRENCE-ID;TZID=Europe/London:20261203T100000", "DTSTART;TZID=Europe/London:20261203T150000",
		"DTEND;TZID=Europe/London:20261203T160000", "SUMMARY:Moved")
	ex.Row.ID = "row-exception"
	w := Window{Start: utc(2026, 11, 30, 0, 0), End: utc(2026, 12, 31, 0, 0)}
	occ := Expand([]*Decoded{master, ex}, w, false)
	got := starts(occ)
	want := []string{"2026-12-01T10:00Z", "2026-12-03T15:00Z", "2026-12-04T10:00Z", "2026-12-05T10:00Z"}
	if strings.Join(got, " ") != strings.Join(want, " ") {
		t.Fatalf("got %v, want %v", got, want)
	}
	if occ[1].Kind != "exception" || occ[1].Row != ex || ical.RecurrenceKey(*occ[1].Original) != "20261203T100000Z" {
		t.Fatalf("the exception should replace its occurrence: %+v", occ[1])
	}
	if occ[0].Kind != "occurrence" || ical.RecurrenceKey(*occ[0].Original) != "20261201T100000Z" {
		t.Fatalf("unexpected first occurrence %+v", occ[0])
	}
}

func TestMonthlyBydaySetposAndYearlyAllDay(t *testing.T) {
	monthly := row(t, "UID:m", "DTSTART:20261231T090000Z", "DTEND:20261231T100000Z", "RRULE:FREQ=MONTHLY;BYDAY=TH;BYSETPOS=-1;COUNT=3")
	got := starts(Expand([]*Decoded{monthly}, Window{Start: utc(2026, 12, 1, 0, 0), End: utc(2027, 6, 1, 0, 0)}, false))
	want := []string{"2026-12-31T09:00Z", "2027-01-28T09:00Z", "2027-02-25T09:00Z"}
	if strings.Join(got, " ") != strings.Join(want, " ") {
		t.Fatalf("monthly: %v, want %v", got, want)
	}
	yearly := row(t, "UID:y", "DTSTART;VALUE=DATE:20240229", "DTEND;VALUE=DATE:20240301", "RRULE:FREQ=YEARLY;BYMONTH=2;BYMONTHDAY=29")
	occ := Expand([]*Decoded{yearly}, Window{Start: utc(2025, 1, 1, 0, 0), End: utc(2029, 1, 1, 0, 0)}, false)
	got = starts(occ)
	if strings.Join(got, " ") != "2028-02-29" {
		t.Fatalf("yearly leap-day: %v", got)
	}
	if !occ[0].End.AllDay || occ[0].End.Date.Format("2006-01-02") != "2028-03-01" {
		t.Fatal("an all-day occurrence keeps its exclusive end date")
	}
}

func TestDateUntilOnATimedSeriesKeepsItsLastDay(t *testing.T) {
	master := row(t, "UID:u", "DTSTART;TZID=America/New_York:20261201T180000", "DTEND;TZID=America/New_York:20261201T190000",
		"RRULE:FREQ=DAILY;UNTIL=20261203")
	got := starts(Expand([]*Decoded{master}, Window{Start: utc(2026, 11, 30, 0, 0), End: utc(2026, 12, 10, 0, 0)}, false))
	if len(got) != 3 {
		t.Fatalf("a date UNTIL is inclusive of its day: %v", got)
	}
}

func TestCanonicalRule(t *testing.T) {
	london, _ := time.LoadLocation("Europe/London")
	start := ical.Moment{Time: time.Date(2026, 12, 1, 10, 0, 0, 0, london), Zone: "Europe/London"}
	ok := map[string]string{
		"freq=weekly;byday=tu;count=10":    "FREQ=WEEKLY;BYDAY=TU;COUNT=10",
		"RRULE:FREQ=DAILY;UNTIL=20270131":  "FREQ=DAILY;UNTIL=20270131T235959Z",
		"FREQ=MONTHLY;BYDAY=TU;BYSETPOS=1": "FREQ=MONTHLY;BYDAY=TU;BYSETPOS=1",
		"FREQ=MONTHLY;BYMONTHDAY=1":        "FREQ=MONTHLY;BYMONTHDAY=1",
		"FREQ=YEARLY;INTERVAL=2":           "FREQ=YEARLY;INTERVAL=2",
	}
	for in, want := range ok {
		got, err := CanonicalRule(in, start)
		if err != nil || got != want {
			t.Fatalf("%s gave %q, %v; want %q", in, got, err, want)
		}
	}
	summer := ical.Moment{Time: time.Date(2027, 7, 6, 10, 0, 0, 0, london), Zone: "Europe/London"}
	if got, _ := CanonicalRule("FREQ=WEEKLY;BYDAY=TU;UNTIL=20270720", summer); got != "FREQ=WEEKLY;BYDAY=TU;UNTIL=20270720T225959Z" {
		t.Fatalf("UNTIL is the end of the day in the event's zone, in UTC: %s", got)
	}
	allDay := ical.Moment{AllDay: true, Date: time.Date(2026, 12, 1, 0, 0, 0, 0, time.UTC)}
	if got, _ := CanonicalRule("FREQ=DAILY;UNTIL=20261210T120000Z", allDay); got != "FREQ=DAILY;UNTIL=20261210" {
		t.Fatalf("an all-day UNTIL is a date: %s", got)
	}
	bad := []string{
		"FREQ=HOURLY",
		"FREQ=DAILY;COUNT=50",
		"FREQ=DAILY;UNTIL=20380101",
		"FREQ=DAILY;COUNT=3;UNTIL=20270101",
		"FREQ=DAILY;BYDAY=MO",
		"FREQ=MONTHLY;BYDAY=TU;BYSETPOS=5",
		"FREQ=MONTHLY;BYDAY=TU",
		"FREQ=MONTHLY;BYDAY=TU,WE;BYSETPOS=1",
		"FREQ=YEARLY;BYMONTHDAY=1",
		"FREQ=YEARLY;INTERVAL=100",
		"FREQ=WEEKLY;BYDAY=WE", // 1 December 2026 is a Tuesday, not the first occurrence
		"FREQ=WEEKLY;BYHOUR=10",
		"FREQ=DAILY\r\nX-INJECT:1",
	}
	for _, in := range bad {
		if _, err := CanonicalRule(in, start); err == nil {
			t.Fatalf("%q should be refused", in)
		} else if err.Field == nil || *err.Field != "recurrence_rule" || err.Code != "invalid_argument" {
			t.Fatalf("%q refused with the wrong error %+v", in, err)
		}
	}
}

func TestCanonicalEmailAndToken(t *testing.T) {
	cases := map[string]string{
		"Alice.Example+cal@Proton.me": "aliceexample@proton.me",
		"a.l-i_ce@pm.me":              "alice@pm.me",
		"Bob.Example+x@gmail.com":     "bobexample@gmail.com",
		"carol+x@outlook.com":         "carol@outlook.com",
		"Dan.Example+x@example.org":   "dan.example+x@example.org",
	}
	for in, want := range cases {
		if got := CanonicalEmail(in); got != want {
			t.Fatalf("CanonicalEmail(%q) = %q, want %q", in, got, want)
		}
	}
	// SHA-1 of "uid" + "bob@example.org", as generateAttendeeToken computes it.
	if got := AttendeeToken("uid", "Bob@Example.org"); got != "1257810d7b7cf01728e47ad1afeb06513bdfb1ad" {
		t.Fatalf("token %q", got)
	}
	if AttendeeToken("uid", "Bob@Example.org") != AttendeeToken("uid", "bob@example.org") {
		t.Fatal("tokens use the canonical address")
	}
}
