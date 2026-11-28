package ops

import (
	"fmt"
	"net/url"
	"strings"
	"testing"

	"github.com/Valtora/Nojoin/proton/internal/calendar"
	"github.com/Valtora/Nojoin/proton/internal/fakeproton"
	"github.com/Valtora/Nojoin/proton/internal/ical"
	"github.com/Valtora/Nojoin/proton/internal/protocol"
)

func seedLines(h *harness, opt fakeproton.SeedOptions, lines ...string) string {
	ev := &ical.Component{Name: "VEVENT"}
	for _, line := range lines {
		p, err := ical.ParseLine(line)
		if err != nil {
			h.t.Fatal(err)
		}
		ev.Add(p)
	}
	return h.s.Seed(h.cal, ev, opt)
}

type listResult struct {
	Events []calendar.Record `json:"events"`
	Cursor *string           `json:"cursor"`
	Stats  map[string]int    `json:"stats"`
}

func window(start, end string) map[string]string {
	return map[string]string{"start": start, "end": end}
}

func TestListCalendars(t *testing.T) {
	h := newHarness(t)
	h.s.AddCalendar("Holidays", 2, 96)
	h.s.AddCalendar("Team (view only)", 0, 96)
	var out struct {
		Settings  calendar.SettingsOut   `json:"settings"`
		Calendars []calendar.CalendarOut `json:"calendars"`
	}
	h.ok("list_calendars", map[string]any{}, &out)
	if len(out.Calendars) != 3 || out.Settings.PrimaryTimeZone != "Europe/London" {
		t.Fatalf("%+v", out)
	}
	personal, holidays, team := out.Calendars[0], out.Calendars[1], out.Calendars[2]
	if personal.Name != "Personal" || !personal.Writable || personal.ReadOnlyReason != nil || !personal.IsDefault || personal.Type != "personal" {
		t.Fatalf("personal %+v", personal)
	}
	if holidays.Writable || holidays.Type != "holidays" || *holidays.ReadOnlyReason != "calendar_read_only" {
		t.Fatalf("holidays %+v", holidays)
	}
	if team.Writable || team.Name != "Team (view only)" {
		t.Fatalf("a member without the write bit cannot write: %+v", team)
	}
	h.noCalls("/calendar/v2/", "/core/v4/keys/salts")
}

func TestListEventsReadsEveryBucketChunkAndPage(t *testing.T) {
	h := newHarness(t)
	for i := 0; i < 105; i++ {
		seedLines(h, fakeproton.SeedOptions{Organiser: true}, fmt.Sprintf("UID:bulk-%03d", i), "DTSTAMP:20261101T090000Z",
			fmt.Sprintf("DTSTART:202612%02dT%02d0000Z", 1+i%28, 8+i%10), fmt.Sprintf("DTEND:202612%02dT%02d3000Z", 1+i%28, 8+i%10), "SUMMARY:Bulk")
	}
	seedLines(h, fakeproton.SeedOptions{Organiser: true}, "UID:allday", "DTSTAMP:20261101T090000Z",
		"DTSTART;VALUE=DATE:20261224", "DTEND;VALUE=DATE:20261226", "SUMMARY:Holiday")
	// A yearly series that began years before the window comes back in the
	// "before window" bucket and is expanded here.
	seedLines(h, fakeproton.SeedOptions{Organiser: true}, "UID:birthday", "DTSTAMP:20201101T090000Z",
		"DTSTART;VALUE=DATE:20201215", "DTEND;VALUE=DATE:20201216", "RRULE:FREQ=YEARLY", "SUMMARY:Birthday")
	h.s.ResetCalls()
	var res listResult
	h.ok("list_events", map[string]any{"calendar_id": h.cal, "window": window("2026-11-01T00:00:00Z", "2027-02-02T00:00:00Z"), "include_cursor": true}, &res)
	if len(res.Events) != 107 || res.Stats["undecryptable"] != 0 {
		t.Fatalf("listed %d events, stats %v", len(res.Events), res.Stats)
	}
	if res.Cursor == nil || *res.Cursor == "" {
		t.Fatal("include_cursor returns the change cursor")
	}
	type key struct {
		typ, start, page string
	}
	seen := map[key]bool{}
	for _, c := range h.s.Calls() {
		if !strings.HasSuffix(c.Path, "/events") {
			continue
		}
		q, _ := url.ParseQuery(c.Query)
		if q.Get("PageSize") != "100" {
			t.Fatalf("page size %s", q.Get("PageSize"))
		}
		seen[key{q.Get("Type"), q.Get("Start"), q.Get("Page")}] = true
	}
	chunks := map[string]bool{}
	for k := range seen {
		chunks[k.start] = true
	}
	// 93 days padded by a day each side is two chunks of at most 93 days.
	if len(chunks) != 2 {
		t.Fatalf("expected two chunks, got %v", chunks)
	}
	for start := range chunks {
		for _, typ := range []string{"0", "1", "2", "3"} {
			if !seen[key{typ, start, "0"}] {
				t.Fatalf("bucket %s was not read for chunk %s", typ, start)
			}
		}
	}
	paged := false
	for k := range seen {
		if k.page == "1" && k.typ == "0" {
			paged = true
		}
	}
	if !paged {
		t.Fatal("105 timed events need a second page")
	}
	var birthday, holiday calendar.Record
	for _, r := range res.Events {
		switch r.UID {
		case "birthday":
			birthday = r
		case "allday":
			holiday = r
		}
	}
	if birthday.ProviderEventID != "birthday#20261215" || *birthday.OriginalStartDate != "2026-12-15" || *birthday.SeriesKey != "birthday" {
		t.Fatalf("birthday %+v", birthday)
	}
	if !holiday.IsAllDay || *holiday.StartDate != "2026-12-24" || *holiday.EndDate != "2026-12-26" || holiday.StartsAt != nil {
		t.Fatalf("holiday %+v", holiday)
	}
	if e := h.fail("list_events", map[string]any{"calendar_id": h.cal, "window": window("2026-01-01T00:00:00Z", "2026-06-01T00:00:00Z")}); e.Code != protocol.CodeInvalidArgument {
		t.Fatalf("a window over 93 days gave %s", e.Code)
	}
}

func TestInvitationRecordShowsAnswersAndOrganiser(t *testing.T) {
	h := newHarness(t)
	h.seedInvitation("invite-r@example.org", false)
	var res listResult
	h.ok("list_events", map[string]any{"calendar_id": h.cal, "window": window("2026-12-01T00:00:00Z", "2026-12-31T00:00:00Z")}, &res)
	if len(res.Events) != 1 {
		t.Fatalf("%d events", len(res.Events))
	}
	ev := res.Events[0]
	if ev.IsOrganiser || ev.Organiser.Email != "bob@proton.me" || ev.Organiser.Name != "Bob Example" || ev.Organiser.IsSelf {
		t.Fatalf("organiser %+v", ev.Organiser)
	}
	if *ev.MyResponse != "needs_action" || ev.Revision == "" || ev.ProviderData.AttendeeIDs[calendar.AttendeeToken("invite-r@example.org", "alice@example.com")] == "" {
		t.Fatalf("record %+v", ev)
	}
	for _, a := range ev.Attendees {
		switch a.Email {
		case "carol@example.org":
			if a.Response != "accepted" || a.Role != "optional" || a.IsSelf {
				t.Fatalf("carol %+v", a)
			}
		case "alice@example.com":
			if !a.IsSelf || a.Role != "required" {
				t.Fatalf("alice %+v", a)
			}
		default:
			t.Fatalf("unexpected attendee %+v", a)
		}
	}
}

func TestGetEventAndChanges(t *testing.T) {
	h := newHarness(t)
	var first listResult
	h.ok("list_events", map[string]any{"calendar_id": h.cal, "window": window("2026-12-01T00:00:00Z", "2026-12-31T00:00:00Z"), "include_cursor": true}, &first)
	seedLines(h, fakeproton.SeedOptions{Organiser: true}, "UID:daily", "DTSTAMP:20261101T090000Z",
		"DTSTART;TZID=Europe/London:20261201T090000", "DTEND;TZID=Europe/London:20261201T093000", "RRULE:FREQ=DAILY;COUNT=4", "SUMMARY:Daily")
	seedLines(h, fakeproton.SeedOptions{Organiser: true}, "UID:once", "DTSTAMP:20261101T090000Z",
		"DTSTART:20261210T090000Z", "DTEND:20261210T100000Z", "SUMMARY:Once")
	var got struct {
		Events []calendar.Record `json:"events"`
	}
	h.ok("get_event", map[string]any{"calendar_id": h.cal, "target": map[string]any{"uid": "daily", "recurrence_id": "20261203T090000Z"}}, &got)
	if len(got.Events) != 1 || got.Events[0].ProviderEventID != "daily#20261203T090000Z" {
		t.Fatalf("%+v", got.Events)
	}
	h.ok("get_event", map[string]any{"calendar_id": h.cal, "target": map[string]any{"uid": "daily"}}, &got)
	if len(got.Events) != 4 {
		t.Fatalf("the whole series: %d", len(got.Events))
	}
	if e := h.fail("get_event", map[string]any{"calendar_id": h.cal, "target": map[string]any{"uid": "missing"}}); e.Code != protocol.CodeNotFound {
		t.Fatalf("missing event gave %s", e.Code)
	}
	if e := h.fail("get_event", map[string]any{"calendar_id": h.cal, "target": map[string]any{"uid": "daily", "recurrence_id": "20261209T090000Z"}}); e.Code != protocol.CodeNotFound {
		t.Fatalf("an occurrence past COUNT gave %s", e.Code)
	}
	var ch calendar.ChangesResult
	h.ok("changes", map[string]any{"calendar_id": h.cal, "cursor": *first.Cursor}, &ch)
	if ch.RefreshRequired || len(ch.Series) != 2 || ch.Series[0].UID != "daily" || len(ch.Series[0].Events) != 4 || ch.Cursor == *first.Cursor {
		t.Fatalf("changes %+v", ch)
	}
	h.ok("changes", map[string]any{"calendar_id": h.cal, "cursor": "m999"}, &ch)
	if !ch.RefreshRequired {
		t.Fatal("a cursor Proton does not know means a full fetch")
	}
}

func TestReadErrorsMap(t *testing.T) {
	h := newHarness(t)
	h.s.RateLimit("GET", "/calendar/v1/"+h.cal+"/events", "7")
	e := h.fail("list_events", map[string]any{"calendar_id": h.cal, "window": window("2026-12-01T00:00:00Z", "2026-12-31T00:00:00Z")})
	if e.Code != protocol.CodeRateLimited || e.Details["retry_after_seconds"] != float64(7) || !e.Retryable {
		t.Fatalf("429 gave %s %v", e.Code, e.Details)
	}
	h.s.Fail("GET", "/calendar/v2/", 503, 0, "down")
	if e := h.fail("list_events", map[string]any{"calendar_id": h.cal, "window": window("2026-12-01T00:00:00Z", "2026-12-31T00:00:00Z")}); e.Code != protocol.CodeProviderUnavailable {
		t.Fatalf("503 gave %s", e.Code)
	}
	h.s.Fail("GET", "/calendar/v1", 400, 5003, "App version is too old")
	if e := h.fail("list_calendars", map[string]any{}); e.Code != protocol.CodeProviderRejected || e.Details["reason"] != "app_version_rejected" {
		t.Fatalf("app version refusal gave %s %v", e.Code, e.Details)
	}
	if e := h.fail("list_events", map[string]any{"calendar_id": "nope", "window": window("2026-12-01T00:00:00Z", "2026-12-31T00:00:00Z")}); e.Code != protocol.CodeNotFound {
		t.Fatalf("unknown calendar gave %s", e.Code)
	}
	bad := h.sess
	bad.KeyPassphrase = "wrong-passphrase-wrong-passphras"
	if e := h.failAs("list_events", &bad, map[string]any{"calendar_id": h.cal, "window": window("2026-12-01T00:00:00Z", "2026-12-31T00:00:00Z")}); e.Code != protocol.CodeAuthExpired || e.Details["reason"] != "key_passphrase_invalid" {
		t.Fatalf("a stale key passphrase gave %s %v", e.Code, e.Details)
	}
}

func TestValidate(t *testing.T) {
	h := newHarness(t)
	var out struct {
		Problems []calendar.Problem `json:"problems"`
	}
	h.okAs("validate", nil, map[string]any{"event": meeting("Fine", nil)}, &out)
	if len(out.Problems) != 0 {
		t.Fatalf("%+v", out.Problems)
	}
	h.okAs("validate", nil, map[string]any{"event": meeting(strings.Repeat("t", 256), map[string]any{
		"location": strings.Repeat("l", 256), "recurrence_rule": "FREQ=DAILY;COUNT=99", "attendees": []map[string]any{{"email": "not an address"}},
	})}, &out)
	fields := map[string]bool{}
	for _, p := range out.Problems {
		fields[p.Field] = true
	}
	for _, f := range []string{"event.title", "event.location", "event.recurrence_rule", "event.attendees[0].email"} {
		if !fields[f] {
			t.Fatalf("%s not reported: %+v", f, out.Problems)
		}
	}
	h.okAs("validate", nil, map[string]any{"event": map[string]any{"title": "x", "start": map[string]string{"date_time": "2026-12-01T10:00:00"}, "end": timed("2026-12-01T11:00:00Z")}}, &out)
	if len(out.Problems) == 0 || out.Problems[0].Code != protocol.CodeInvalidTime {
		t.Fatal("a time without an offset is refused")
	}
	h.okAs("validate", nil, map[string]any{"patch": map[string]any{}}, &out)
	if len(out.Problems) != 1 || out.Problems[0].Message != "Nothing to update." {
		t.Fatalf("%+v", out.Problems)
	}
	h.noCalls("/")
}
