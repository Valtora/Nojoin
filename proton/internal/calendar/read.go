package calendar

import (
	"context"
	"net/url"
	"strconv"
	"strings"
	"time"

	"github.com/Valtora/Nojoin/proton/internal/ical"
	"github.com/Valtora/Nojoin/proton/internal/papi"
	"github.com/Valtora/Nojoin/proton/internal/protocol"
)

const (
	pageSize        = 100
	maxWindow       = 93 * 24 * time.Hour
	windowPad       = 24 * time.Hour
	maxChangesPages = 10
)

// The four windowed query buckets (CalendarEventsQueryType in the web client).
var queryTypes = []int{0, 1, 2, 3}

type eventsPage struct {
	Events []*RawEvent `json:"Events"`
	More   int         `json:"More"`
}

// FetchWindow reads every row overlapping the window: each 93-day chunk of the
// padded window, all four Type buckets, paged while More is 1, one request at a
// time, deduplicated by ID.
func FetchWindow(ctx context.Context, c *papi.Client, calendarID string, w Window) ([]*RawEvent, error) {
	start := w.Start.Add(-windowPad)
	end := w.End.Add(windowPad)
	seen := map[string]bool{}
	var out []*RawEvent
	for s := start; s.Before(end); s = s.Add(maxWindow) {
		e := s.Add(maxWindow)
		if e.After(end) {
			e = end
		}
		for _, typ := range queryTypes {
			for page := 0; ; page++ {
				q := url.Values{}
				q.Set("Start", strconv.FormatInt(s.Unix(), 10))
				q.Set("End", strconv.FormatInt(e.Unix(), 10))
				q.Set("Timezone", "UTC")
				q.Set("Type", strconv.Itoa(typ))
				q.Set("Page", strconv.Itoa(page))
				q.Set("PageSize", strconv.Itoa(pageSize))
				var resp eventsPage
				if err := c.Get(ctx, "/calendar/v1/"+calendarID+"/events", q, &resp); err != nil {
					return nil, err
				}
				for _, ev := range resp.Events {
					if ev != nil && !seen[ev.ID] {
						seen[ev.ID] = true
						out = append(out, ev)
					}
				}
				if resp.More != 1 || len(resp.Events) == 0 {
					break
				}
			}
		}
	}
	return out, nil
}

// FetchUID reads every row sharing a UID (a series' master and exceptions).
func FetchUID(ctx context.Context, c *papi.Client, calendarID, uid string) ([]*RawEvent, error) {
	var out []*RawEvent
	for page := 0; ; page++ {
		q := url.Values{}
		q.Set("UID", uid)
		q.Set("Page", strconv.Itoa(page))
		q.Set("PageSize", strconv.Itoa(pageSize))
		var resp eventsPage
		if err := c.Get(ctx, "/calendar/v1/"+calendarID+"/events", q, &resp); err != nil {
			return nil, err
		}
		for _, ev := range resp.Events {
			if ev != nil && ev.UID == uid {
				out = append(out, ev)
			}
		}
		if resp.More != 1 || len(resp.Events) == 0 {
			return out, nil
		}
	}
}

// FetchEvent reads one row by ID.
func FetchEvent(ctx context.Context, c *papi.Client, calendarID, eventID string) (*RawEvent, error) {
	var resp struct {
		Event *RawEvent `json:"Event"`
	}
	if err := c.Get(ctx, "/calendar/v1/"+calendarID+"/events/"+eventID, nil, &resp); err != nil {
		return nil, err
	}
	if resp.Event == nil {
		return nil, &papi.APIError{Status: 404, Code: papi.CodeEventNotFound}
	}
	return resp.Event, nil
}

// decodeAll decrypts rows, skipping the unreadable ones and counting them.
func (a *Access) decodeAll(rows []*RawEvent) ([]*Decoded, int) {
	var out []*Decoded
	bad := 0
	for _, r := range rows {
		d, err := a.Decode(r)
		if err != nil {
			bad++
			continue
		}
		out = append(out, d)
	}
	return out, bad
}

// Records turns occurrences into records.
func (a *Access) Records(occ []Occurrence) []Record {
	out := make([]Record, 0, len(occ))
	for _, o := range occ {
		out = append(out, a.ToRecord(o))
	}
	return out
}

// WindowArg is a window as the protocol carries it.
type WindowArg struct {
	Start string `json:"start"`
	End   string `json:"end"`
}

// ResolveWindow parses a window, or returns the default sync range.
func ResolveWindow(w *WindowArg, now time.Time, field string, limit time.Duration) (Window, error) {
	if w == nil {
		return Window{Start: now.AddDate(0, -1, 0), End: now.AddDate(0, 24, 0)}, nil
	}
	s, err1 := time.Parse(time.RFC3339, w.Start)
	e, err2 := time.Parse(time.RFC3339, w.End)
	if err1 != nil || err2 != nil {
		return Window{}, protocol.FieldError(protocol.CodeInvalidArgument, field, "%s needs RFC 3339 start and end times.", field)
	}
	if !e.After(s) {
		return Window{}, protocol.FieldError(protocol.CodeInvalidArgument, field, "%s ends before it starts.", field)
	}
	if limit > 0 && e.Sub(s) > limit {
		return Window{}, protocol.FieldError(protocol.CodeInvalidArgument, field, "%s is longer than %d days.", field, int(limit.Hours()/24))
	}
	return Window{Start: s.UTC(), End: e.UTC()}, nil
}

// CalendarOut is one calendar in list_calendars.
type CalendarOut struct {
	ID             string  `json:"id"`
	Name           string  `json:"name"`
	Description    string  `json:"description"`
	Colour         string  `json:"colour"`
	Type           string  `json:"type"`
	MemberID       string  `json:"member_id"`
	AddressID      string  `json:"address_id"`
	Email          string  `json:"email"`
	Permissions    int     `json:"permissions"`
	Writable       bool    `json:"writable"`
	ReadOnlyReason *string `json:"read_only_reason"`
	IsDefault      bool    `json:"is_default"`
	Display        bool    `json:"display"`
	TimeZone       string  `json:"time_zone"`
	Flags          int     `json:"flags"`
}

// SettingsOut is the account's calendar settings.
type SettingsOut struct {
	DefaultCalendarID string `json:"default_calendar_id"`
	PrimaryTimeZone   string `json:"primary_time_zone"`
}

// ListCalendars reads the calendars and the account's calendar settings.
func ListCalendars(ctx context.Context, c *papi.Client) (SettingsOut, []CalendarOut, error) {
	var settings struct {
		CalendarUserSettings UserSettings `json:"CalendarUserSettings"`
	}
	if err := c.Get(ctx, "/settings/calendar", nil, &settings); err != nil {
		return SettingsOut{}, nil, err
	}
	var list struct {
		Calendars []CalendarRow `json:"Calendars"`
	}
	if err := c.Get(ctx, "/calendar/v1", nil, &list); err != nil {
		return SettingsOut{}, nil, err
	}
	s := SettingsOut{DefaultCalendarID: settings.CalendarUserSettings.DefaultCalendarID, PrimaryTimeZone: settings.CalendarUserSettings.PrimaryTimezone}
	if s.PrimaryTimeZone == "" {
		s.PrimaryTimeZone = "UTC"
	}
	out := []CalendarOut{}
	for _, cal := range list.Calendars {
		o := CalendarOut{ID: cal.ID, Name: cal.Name, Description: cal.Description, Colour: cal.Color, Display: cal.Display != 0, Flags: cal.Flags, TimeZone: s.PrimaryTimeZone}
		// Display metadata now lives on our member entry; the top-level fields
		// are a legacy fallback.
		if len(cal.Members) > 0 {
			m := cal.Members[0]
			if m.Name != "" {
				o.Name = m.Name
			}
			if m.Description != "" {
				o.Description = m.Description
			}
			if m.Color != "" {
				o.Colour = m.Color
			}
			o.MemberID, o.AddressID, o.Email, o.Permissions, o.Flags = m.ID, m.AddressID, m.Email, m.Permissions, m.Flags
			o.Display = m.Display != 0
		}
		switch cal.Type {
		case 0:
			o.Type = "personal"
		case 1:
			o.Type = "subscribed"
		case 2:
			o.Type = "holidays"
		default:
			o.Type = "other"
		}
		o.Writable = cal.Type == 0 && o.Permissions&PermWrite != 0
		if !o.Writable {
			o.ReadOnlyReason = strp("calendar_read_only")
		}
		o.IsDefault = cal.ID == s.DefaultCalendarID
		out = append(out, o)
	}
	return s, out, nil
}

// LatestCursor reads the calendar's change-feed cursor.
func LatestCursor(ctx context.Context, c *papi.Client, calendarID string) (string, error) {
	var resp struct {
		CalendarModelEventID string `json:"CalendarModelEventID"`
	}
	if err := c.Get(ctx, "/calendar/v1/"+calendarID+"/modelevents/latest", nil, &resp); err != nil {
		return "", err
	}
	return resp.CalendarModelEventID, nil
}

// ListEvents reads one window of a calendar.
func (a *Access) ListEvents(ctx context.Context, c *papi.Client, w Window) ([]Record, map[string]int, error) {
	rows, err := FetchWindow(ctx, c, a.CalendarID, w)
	if err != nil {
		return nil, nil, err
	}
	decoded, bad := a.decodeAll(rows)
	occ := Expand(decoded, w, false)
	return a.Records(occ), map[string]int{"rows": len(rows), "occurrences": len(occ), "undecryptable": bad}, nil
}

// SeriesRecords reads a UID and expands it inside the window. unbounded keeps
// a single event whatever its time.
func (a *Access) SeriesRecords(ctx context.Context, c *papi.Client, uid string, w Window) ([]Record, []*Decoded, error) {
	rows, err := FetchUID(ctx, c, a.CalendarID, uid)
	if err != nil {
		return nil, nil, err
	}
	decoded, _ := a.decodeAll(rows)
	return a.Records(Expand(decoded, w, true)), decoded, nil
}

type modelEvents struct {
	CalendarModelEventID string `json:"CalendarModelEventID"`
	More                 int    `json:"More"`
	Refresh              int    `json:"Refresh"`
	CalendarEvents       []struct {
		ID     string    `json:"ID"`
		Action int       `json:"Action"`
		Event  *RawEvent `json:"Event"`
	} `json:"CalendarEvents"`
}

// SeriesChange is one changed UID in a changes result.
type SeriesChange struct {
	UID    string   `json:"uid"`
	Events []Record `json:"events"`
}

// ChangesResult is the changes operation's result.
type ChangesResult struct {
	Cursor          string         `json:"cursor"`
	More            bool           `json:"more"`
	RefreshRequired bool           `json:"refresh_required"`
	Series          []SeriesChange `json:"series"`
	DeletedEventIDs []string       `json:"deleted_event_ids"`
}

// Changes reads the change feed from a cursor and re-reads each changed UID.
func (a *Access) Changes(ctx context.Context, c *papi.Client, cursor string, w Window) (*ChangesResult, error) {
	res := &ChangesResult{Cursor: cursor, Series: []SeriesChange{}, DeletedEventIDs: []string{}}
	if cursor == "" || strings.ContainsAny(cursor, "/?#") {
		return nil, protocol.FieldError(protocol.CodeInvalidArgument, "cursor", "cursor is not a Proton change cursor.")
	}
	uids := []string{}
	seenUID := map[string]bool{}
	var unknownIDs []string
	for page := 0; page < maxChangesPages; page++ {
		var resp modelEvents
		err := c.Get(ctx, "/calendar/v1/"+a.CalendarID+"/modelevents/"+res.Cursor, nil, &resp)
		if err != nil {
			pe := papi.ToProtocol(err, false)
			if pe.Code == protocol.CodeNotFound || (pe.Code == protocol.CodeProviderRejected && pe.HTTPStatus != nil && *pe.HTTPStatus < 500) {
				res.RefreshRequired = true
				return res, nil
			}
			return nil, err
		}
		if resp.Refresh != 0 {
			res.RefreshRequired = true
		}
		for _, ce := range resp.CalendarEvents {
			switch {
			case ce.Action == 0:
				res.DeletedEventIDs = append(res.DeletedEventIDs, ce.ID)
			case ce.Event != nil && ce.Event.UID != "":
				if !seenUID[ce.Event.UID] {
					seenUID[ce.Event.UID] = true
					uids = append(uids, ce.Event.UID)
				}
			default:
				unknownIDs = append(unknownIDs, ce.ID)
			}
		}
		if resp.CalendarModelEventID != "" {
			res.Cursor = resp.CalendarModelEventID
		}
		res.More = resp.More == 1
		if !res.More {
			break
		}
	}
	for _, id := range unknownIDs {
		ev, err := FetchEvent(ctx, c, a.CalendarID, id)
		if err != nil {
			if papi.ToProtocol(err, false).Code == protocol.CodeNotFound {
				res.DeletedEventIDs = append(res.DeletedEventIDs, id)
				continue
			}
			return nil, err
		}
		if !seenUID[ev.UID] {
			seenUID[ev.UID] = true
			uids = append(uids, ev.UID)
		}
	}
	for _, uid := range uids {
		rows, err := FetchUID(ctx, c, a.CalendarID, uid)
		if err != nil {
			return nil, err
		}
		decoded, _ := a.decodeAll(rows)
		res.Series = append(res.Series, SeriesChange{UID: uid, Events: a.Records(Expand(decoded, w, false))})
	}
	return res, nil
}

// Get reads one event: one occurrence when the target names it, else the
// single event or every occurrence of the series inside the window.
func (a *Access) Get(ctx context.Context, c *papi.Client, t Target, w Window) (map[string]any, error) {
	if t.UID == "" {
		return nil, protocol.FieldError(protocol.CodeInvalidArgument, "target.uid", "target.uid is required.")
	}
	recs, rows, err := a.SeriesRecords(ctx, c, t.UID, w)
	if err != nil {
		return nil, papi.ToProtocol(err, false)
	}
	if len(rows) == 0 {
		return nil, protocol.Errorf(protocol.CodeNotFound, "Proton has no event with that UID in this calendar.")
	}
	if t.RecurrenceID != nil && *t.RecurrenceID != "" {
		key := t.UID + "#" + *t.RecurrenceID
		for _, r := range recs {
			if r.ProviderEventID == key {
				return map[string]any{"events": []Record{r}}, nil
			}
		}
		// The occurrence may lie outside the window: look for it directly.
		orig, _, perr := ical.ParseRecurrenceKey(*t.RecurrenceID)
		if perr == nil {
			narrow := Window{Start: orig.Add(-24 * time.Hour), End: orig.Add(48 * time.Hour)}
			for _, r := range a.Records(Expand(rows, narrow, false)) {
				if r.ProviderEventID == key {
					return map[string]any{"events": []Record{r}}, nil
				}
			}
		}
		return nil, protocol.Errorf(protocol.CodeNotFound, "The series has no such occurrence.")
	}
	return map[string]any{"events": recs}, nil
}
