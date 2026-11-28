package calendar

import (
	"context"
	"errors"
	"sort"
	"strconv"
	"strings"
	"time"

	"github.com/ProtonMail/gopenpgp/v2/crypto"

	"github.com/Valtora/Nojoin/proton/internal/ical"
	"github.com/Valtora/Nojoin/proton/internal/papi"
	"github.com/Valtora/Nojoin/proton/internal/protocol"
)

// WriteResult is the write result of PROTOCOL.md.
type WriteResult struct {
	Written             bool           `json:"written"`
	Verified            bool           `json:"verified"`
	Events              []Record       `json:"events"`
	DeletedKeys         []string       `json:"deleted_keys"`
	Before              []Record       `json:"before"`
	Revision            *string        `json:"revision"`
	Warnings            []string       `json:"warnings"`
	Stats               map[string]int `json:"stats"`
	ITIP                *ITIP          `json:"itip"`
	OrganiserSeesAnswer *bool          `json:"organiser_sees_answer,omitempty"`

	// sent is the series as written, for when the read-back fails.
	sent []*Decoded
}

// afterWrite is the series as a write leaves it: rows replaced by what was
// sent, deleted rows gone, created rows added.
func afterWrite(a *Access, rows []*Decoded, changed map[string]*ical.Component, deleted map[string]bool, created ...*ical.Component) []*Decoded {
	var out []*Decoded
	for _, d := range rows {
		if deleted[d.Row.ID] {
			continue
		}
		if ev, ok := changed[d.Row.ID]; ok {
			out = append(out, &Decoded{Row: d.Row, Merged: ev, Warnings: d.Warnings})
			continue
		}
		out = append(out, d)
	}
	for _, ev := range created {
		out = append(out, &Decoded{Row: &RawEvent{UID: propText(ev, "UID"), CalendarID: a.CalendarID, IsOrganizer: true}, Merged: ev})
	}
	return out
}

func newResult() *WriteResult {
	return &WriteResult{Events: []Record{}, DeletedKeys: []string{}, Before: []Record{}, Warnings: []string{}, Stats: map[string]int{"occurrences": 0, "exceptions_removed": 0}}
}

func (r *WriteResult) warn(w string) { r.Warnings = append(r.Warnings, w) }

// series is every row of one UID, decrypted.
type series struct {
	uid        string
	rows       []*Decoded
	master     *Decoded
	exceptions map[int64]*Decoded
}

func (s *series) recurring() bool { return s.master != nil && rruleOf(s.master.Merged) != "" }

func (a *Access) loadSeries(ctx context.Context, c *papi.Client, uid string) (*series, error) {
	if uid == "" || len(uid) > 512 {
		return nil, protocol.FieldError(protocol.CodeInvalidArgument, "target.uid", "target.uid is required.")
	}
	rows, err := FetchUID(ctx, c, a.CalendarID, uid)
	if err != nil {
		return nil, papi.ToProtocol(err, false)
	}
	if len(rows) == 0 {
		return nil, protocol.Errorf(protocol.CodeNotFound, "Proton has no event with that UID in this calendar.")
	}
	s := &series{uid: uid, exceptions: map[int64]*Decoded{}}
	for _, r := range rows {
		d, err := a.Decode(r)
		if err != nil {
			return nil, protocol.Errorf(protocol.CodeProviderRejected, "One of the event's rows cannot be decrypted, so Nojoin will not change it.")
		}
		s.rows = append(s.rows, d)
		if rid := recurrenceIDOf(d); rid != nil {
			s.exceptions[rid.Instant().Unix()] = d
		} else if s.master == nil {
			s.master = d
		}
	}
	return s, nil
}

// isOwn reports whether the user may change or cancel the event: they
// organise it, or it has no attendees.
func isOwn(d *Decoded) bool {
	return bool(d.Row.IsOrganizer) || len(d.Merged.All("ATTENDEE")) == 0
}

func checkRevision(expected *string, d *Decoded) *protocol.Error {
	if expected == nil || *expected == "" {
		return nil
	}
	current := Revision(d.Row, d.Sequence())
	if current != *expected {
		return protocol.Errorf(protocol.CodeConflict, "The event changed since revision %s.", *expected).WithDetail("current_revision", current)
	}
	return nil
}

func (a *Access) requireWritable() *protocol.Error {
	if !a.Writable() {
		return protocol.Errorf(protocol.CodeNotWritable, "The user can only view this calendar.").WithDetail("reason", "calendar_read_only")
	}
	return nil
}

func sanitiseParam(s string) string {
	s = strings.NewReplacer(`"`, "'", "\r", " ", "\n", " ").Replace(s)
	return strings.TrimSpace(s)
}

func (a *Access) displayName() string {
	for _, ad := range a.Keys.Addresses {
		if strings.EqualFold(ad.Email, a.Email) {
			return ad.DisplayName
		}
	}
	return ""
}

func (a *Access) organiserProperty() ical.Property {
	p := ical.Property{Name: "ORGANIZER", Value: "mailto:" + a.Email}
	if n := sanitiseParam(a.displayName()); n != "" {
		p.Params = []ical.Param{{Name: "CN", Values: []string{n}}}
	}
	return p
}

func attendeeProperty(uid string, at AttendeeArg) ical.Property {
	email := strings.TrimSpace(at.Email)
	cn := email
	if at.Name != nil && sanitiseParam(*at.Name) != "" {
		cn = sanitiseParam(*at.Name)
	}
	return ical.Property{Name: "ATTENDEE", Value: "mailto:" + email, Params: []ical.Param{
		{Name: "CUTYPE", Values: []string{"INDIVIDUAL"}},
		{Name: "ROLE", Values: []string{RoleParam(at.Role)}},
		{Name: "RSVP", Values: []string{"TRUE"}},
		{Name: "CN", Values: []string{cn}},
		{Name: "X-PM-TOKEN", Values: []string{AttendeeToken(uid, email)}},
	}}
}

func (a *Access) selfSet() map[string]bool {
	self := map[string]bool{}
	for _, e := range a.Keys.Emails() {
		self[CanonicalEmail(e)] = true
	}
	return self
}

func setTimes(ev *ical.Component, start, end ical.Moment) {
	ev.Set(ical.MomentProperty("DTSTART", start))
	ev.Remove("DURATION")
	ev.Set(ical.MomentProperty("DTEND", end))
}

func setSequence(ev *ical.Component, n int) {
	ev.Set(ical.Property{Name: "SEQUENCE", Value: strconv.Itoa(n)})
}

func setHold(ev *ical.Component, hold bool) {
	ev.Set(ical.Property{Name: "STATUS", Value: "CONFIRMED"})
	if hold {
		ev.Set(ical.Property{Name: ical.HoldProperty, Value: "TRUE"})
	} else {
		ev.Remove(ical.HoldProperty)
	}
}

// holdOf reports whether a VEVENT is a hold.
func holdOf(ev *ical.Component) bool {
	if p := ev.Get(ical.HoldProperty); p != nil && strings.EqualFold(strings.TrimSpace(p.Value), "TRUE") {
		return true
	}
	return strings.EqualFold(propText(ev, "STATUS"), "TENTATIVE")
}

func setShowAs(ev *ical.Component, showAs string) {
	v := "OPAQUE"
	if showAs == "free" {
		v = "TRANSPARENT"
	}
	ev.Set(ical.Property{Name: "TRANSP", Value: v})
}

func setOptText(ev *ical.Component, name string, o OptString) {
	if !o.Set {
		return
	}
	if o.Null || o.Value == "" {
		ev.Remove(name)
		return
	}
	ev.SetText(name, o.Value)
}

// newEvent builds the VEVENT for a create.
func (a *Access) newEvent(uid string, f EventFields) (*ical.Component, *protocol.Error) {
	if err := firstProblem(ValidateFields(f, "event")); err != nil {
		return nil, err
	}
	start, _ := ParseTime(&f.Start, "event.start", f.AllDay)
	end := ical.Moment{AllDay: true, Date: start.Date.AddDate(0, 0, 1)}
	if f.End != nil {
		end, _ = ParseTime(f.End, "event.end", f.AllDay)
	}
	ev := &ical.Component{Name: "VEVENT"}
	ev.Add(ical.Property{Name: "UID", Value: uid})
	ev.Add(ical.Property{Name: "DTSTAMP", Value: Now().Format("20060102T150405Z")})
	setTimes(ev, start, end)
	if f.RecurrenceRule != nil {
		rule, perr := CanonicalRule(*f.RecurrenceRule, start)
		if perr != nil {
			perr.Field = strp("event.recurrence_rule")
			return nil, perr
		}
		ev.Add(ical.Property{Name: "RRULE", Value: rule})
	}
	setSequence(ev, 0)
	ev.SetText("SUMMARY", f.Title)
	if f.Description != nil && *f.Description != "" {
		ev.SetText("DESCRIPTION", *f.Description)
	}
	if f.Location != nil && *f.Location != "" {
		ev.SetText("LOCATION", *f.Location)
	}
	if f.URL != nil && *f.URL != "" {
		ev.Add(ical.Property{Name: "URL", Value: strings.TrimSpace(*f.URL)})
	}
	setHold(ev, f.Hold)
	setShowAs(ev, f.ShowAs)
	self := a.selfSet()
	seen := map[string]bool{}
	for _, at := range f.Attendees {
		canon := CanonicalEmail(at.Email)
		if self[canon] || seen[canon] {
			continue
		}
		seen[canon] = true
		ev.Add(attendeeProperty(uid, at))
	}
	if len(ev.All("ATTENDEE")) > 0 {
		ev.Set(a.organiserProperty())
	}
	return ev, nil
}

// itipSource is the written event with each attendee's PARTSTAT, for mail.
func itipSource(ev *ical.Component, clear []ClearAttendeeIn) *ITIP {
	out := ev.Clone()
	status := map[string]int{}
	for _, c := range clear {
		status[c.Token] = c.Status
	}
	for i, p := range out.Props {
		if p.Name != "ATTENDEE" {
			continue
		}
		code := status[p.Param("X-PM-TOKEN")]
		out.Props[i] = p.WithParam("PARTSTAT", Partstat(statusName(code)))
	}
	return &ITIP{ICS: ical.Serialize(out), UID: propText(ev, "UID"), Sequence: sequenceOf(ev)}
}

// readBack reads the series after a write. If that fails, the records are
// built from what was sent and the result is marked unverified.
func (a *Access) readBack(ctx context.Context, c *papi.Client, uid string, w Window, res *WriteResult, fallback []*Decoded) {
	recs, rows, err := a.SeriesRecords(ctx, c, uid, w)
	if err == nil && (len(rows) > 0 || fallback == nil) {
		res.Events, res.Verified = recs, true
	} else {
		res.Verified = false
		res.warn("read_back_failed")
		res.Events = a.Records(Expand(fallback, w, true))
		rows = fallback
	}
	for _, d := range rows {
		if recurrenceIDOf(d) == nil {
			rev := Revision(d.Row, d.Sequence())
			res.Revision = &rev
		}
	}
	res.Stats["occurrences"] = len(res.Events)
}

// CreateArgs are create_event's arguments.
type CreateArgs struct {
	CalendarID         string      `json:"calendar_id"`
	UID                string      `json:"uid"`
	Event              EventFields `json:"event"`
	ShareWithAttendees bool        `json:"share_with_attendees"`
	Window             *WindowArg  `json:"window"`
}

// Create writes a new event.
func (a *Access) Create(ctx context.Context, c *papi.Client, args CreateArgs) (*WriteResult, error) {
	if args.UID == "" || len(args.UID) > MaxUID || strings.ContainsAny(args.UID, "\r\n;:,") {
		return nil, protocol.FieldError(protocol.CodeInvalidArgument, "uid", "uid must be 1 to %d bytes without separators.", MaxUID)
	}
	w, werr := ResolveWindow(args.Window, Now(), "window", 0)
	if werr != nil {
		return nil, werr
	}
	if perr := a.requireWritable(); perr != nil {
		return nil, perr
	}
	ev, perr := a.newEvent(args.UID, args.Event)
	if perr != nil {
		return nil, perr
	}
	sharedSK, err := crypto.GenerateSessionKey()
	if err != nil {
		return nil, err
	}
	calSK, err := crypto.GenerateSessionKey()
	if err != nil {
		return nil, err
	}
	body, err := a.buildBody(ev, sharedSK, calSK, nil, true)
	if err != nil {
		return nil, err
	}
	res := newResult()
	if err := a.shareCreate(ctx, c, body, sharedSK, ev, args.ShareWithAttendees, res); err != nil {
		return nil, err
	}
	zero := 0
	resp, err := putSync(ctx, c, a.CalendarID, SyncRequest{MemberID: a.Member.ID, IsImport: &zero,
		Events: []SyncEntry{{Overwrite: &zero, Event: body}}})
	if err != nil {
		return nil, err
	}
	res.Written = true
	fallback := &Decoded{Row: &RawEvent{UID: args.UID, IsOrganizer: true, CalendarID: a.CalendarID}, Merged: ev}
	if resp != nil && len(resp.Responses) > 0 && resp.Responses[0].Response.Event != nil {
		if d, err := a.Decode(resp.Responses[0].Response.Event); err == nil {
			fallback = d
		}
	}
	a.readBack(ctx, c, args.UID, w, res, []*Decoded{fallback})
	res.ITIP = itipSource(ev, body.Attendees)
	return res, nil
}

// UpdateArgs are update_event's arguments.
type UpdateArgs struct {
	CalendarID         string     `json:"calendar_id"`
	Target             Target     `json:"target"`
	Scope              string     `json:"scope"`
	Patch              Patch      `json:"patch"`
	ShareWithAttendees bool       `json:"share_with_attendees"`
	ShareWith          []string   `json:"share_with"`
	ExpectedRevision   *string    `json:"expected_revision"`
	Window             *WindowArg `json:"window"`
}

// resolveTarget finds the row a write applies to and the occurrence it names.
func resolveTarget(s *series, t Target, scope string) (row *Decoded, original *ical.Moment, exception *Decoded, err *protocol.Error) {
	var origTime time.Time
	hasOrig := false
	if t.RecurrenceID != nil && *t.RecurrenceID != "" {
		tm, allDay, perr := ical.ParseRecurrenceKey(*t.RecurrenceID)
		if perr != nil {
			return nil, nil, nil, protocol.FieldError(protocol.CodeInvalidArgument, "target.recurrence_id", "target.recurrence_id is not a recurrence key.")
		}
		origTime, hasOrig = tm, true
		_ = allDay
	}
	switch scope {
	case "single":
		if hasOrig {
			return nil, nil, nil, protocol.FieldError(protocol.CodeInvalidArgument, "target.recurrence_id", "A single event has no recurrence_id.")
		}
		if s.master == nil {
			return nil, nil, nil, protocol.Errorf(protocol.CodeNotFound, "Proton has no such event.")
		}
		if s.recurring() {
			return nil, nil, nil, protocol.FieldError(protocol.CodeInvalidArgument, "scope", "This event is a recurring series: scope must be occurrence or series.")
		}
		return s.master, nil, nil, nil
	case "occurrence", "series":
		if scope == "occurrence" && !hasOrig {
			return nil, nil, nil, protocol.FieldError(protocol.CodeInvalidArgument, "target.recurrence_id", "scope occurrence needs target.recurrence_id.")
		}
		if !hasOrig {
			if !s.recurring() {
				return nil, nil, nil, protocol.FieldError(protocol.CodeInvalidArgument, "scope", "This event does not recur: use scope single.")
			}
			return s.master, nil, nil, nil
		}
		if ex := s.exceptions[origTime.Unix()]; ex != nil {
			return ex, recurrenceIDOf(ex), ex, nil
		}
		if !s.recurring() {
			return nil, nil, nil, protocol.Errorf(protocol.CodeNotFound, "Proton has no such occurrence.")
		}
		if !seriesHasOccurrence(s.master, origTime) {
			return nil, nil, nil, protocol.Errorf(protocol.CodeNotFound, "The series has no occurrence at that time.")
		}
		ms, _, _ := eventTimes(s.master.Merged)
		m := ical.Moment{AllDay: ms.AllDay}
		if ms.AllDay {
			m.Date = origTime
		} else {
			loc, _ := ical.LoadZone(ms.Zone)
			m.Time, m.Zone = origTime.In(loc), ms.Zone
		}
		return s.master, &m, nil, nil
	default:
		return nil, nil, nil, protocol.FieldError(protocol.CodeInvalidArgument, "scope", "scope must be single, occurrence or series.")
	}
}

// newTimes works out an event's times after a patch, keeping the length when
// only one end is given.
func newTimes(ev *ical.Component, p Patch) (ical.Moment, ical.Moment, bool, *protocol.Error) {
	curStart, curEnd, err := eventTimes(ev)
	if err != nil {
		return curStart, curEnd, false, protocol.Errorf(protocol.CodeProviderRejected, "The event's stored times cannot be read.")
	}
	allDay := curStart.AllDay
	if p.AllDay != nil {
		allDay = *p.AllDay
	}
	if p.Start == nil && p.End == nil {
		if allDay != curStart.AllDay {
			return curStart, curEnd, false, protocol.FieldError(protocol.CodeInvalidTime, "patch.start", "Changing all_day needs start and end.")
		}
		return curStart, curEnd, false, nil
	}
	start, end := curStart, curEnd
	if p.Start != nil {
		s, perr := ParseTime(p.Start, "patch.start", allDay)
		if perr != nil {
			return start, end, false, perr
		}
		start = s
	}
	if p.End != nil {
		e, perr := ParseTime(p.End, "patch.end", allDay)
		if perr != nil {
			return start, end, false, perr
		}
		end = e
	} else {
		if allDay != curStart.AllDay {
			return start, end, false, protocol.FieldError(protocol.CodeInvalidTime, "patch.end", "Changing all_day needs start and end.")
		}
		if allDay {
			end = ical.Moment{AllDay: true, Date: start.Date.Add(curEnd.Date.Sub(curStart.Date))}
		} else {
			end = ical.Moment{Time: start.Time.Add(curEnd.Time.Sub(curStart.Time)), Zone: start.Zone}
		}
	}
	if p.Start == nil && allDay != curStart.AllDay {
		return start, end, false, protocol.FieldError(protocol.CodeInvalidTime, "patch.start", "Changing all_day needs start and end.")
	}
	if !end.Instant().After(start.Instant()) {
		return start, end, false, protocol.FieldError(protocol.CodeInvalidTime, "patch.end", "end must be after start.")
	}
	changed := ical.FormatLine(ical.MomentProperty("DTSTART", start)) != ical.FormatLine(ical.MomentProperty("DTSTART", curStart)) ||
		ical.FormatLine(ical.MomentProperty("DTEND", end)) != ical.FormatLine(ical.MomentProperty("DTEND", curEnd))
	return start, end, changed, nil
}

// fieldEdits applies the patch's content fields (everything but times and the
// rule) and reports whether a significant property changed.
func (a *Access) fieldEdits(ev *ical.Component, p Patch, uid string, hasCalendarKey bool) (bool, *protocol.Error) {
	significant := false
	if p.Title.Set {
		ev.SetText("SUMMARY", p.Title.Value)
	}
	setOptText(ev, "DESCRIPTION", p.Description)
	setOptText(ev, "LOCATION", p.Location)
	if p.URL.Set {
		if p.URL.Null || p.URL.Value == "" {
			ev.Remove("URL")
		} else {
			ev.Set(ical.Property{Name: "URL", Value: strings.TrimSpace(p.URL.Value)})
		}
	}
	if p.ShowAs != nil {
		setShowAs(ev, *p.ShowAs)
	}
	if p.Hold != nil && *p.Hold != holdOf(ev) {
		if *p.Hold && !hasCalendarKey {
			return false, protocol.Errorf(protocol.CodeUnsupported,
				"This event was made in Proton's own app without a private part, so Nojoin cannot mark it as a hold.").
				WithDetail("hint", "Create a new hold instead, or leave this event as it is.")
		}
		setHold(ev, *p.Hold)
		significant = true
	}
	if len(p.AddAttendees) > 0 || len(p.RemoveAttendees) > 0 {
		before := len(ev.All("ATTENDEE"))
		remove := map[string]bool{}
		for _, e := range p.RemoveAttendees {
			remove[CanonicalEmail(e)] = true
		}
		kept := ev.Props[:0]
		removed := 0
		for _, q := range ev.Props {
			if q.Name == "ATTENDEE" && remove[CanonicalEmail(mailtoAddress(q.Value))] {
				removed++
				continue
			}
			kept = append(kept, q)
		}
		ev.Props = kept
		have := map[string]bool{}
		for _, q := range ev.All("ATTENDEE") {
			have[CanonicalEmail(mailtoAddress(q.Value))] = true
		}
		self := a.selfSet()
		added := 0
		for _, at := range p.AddAttendees {
			canon := CanonicalEmail(at.Email)
			if self[canon] || have[canon] {
				continue
			}
			have[canon] = true
			ev.Add(attendeeProperty(uid, at))
			added++
		}
		if len(ev.All("ATTENDEE")) > MaxAttendees {
			return false, protocol.FieldError(protocol.CodeInvalidArgument, "patch.add_attendees", "Proton allows at most %d attendees.", MaxAttendees)
		}
		if added > 0 && ev.Get("ORGANIZER") == nil {
			ev.Set(a.organiserProperty())
		}
		significant = significant || added > 0 || removed > 0 || before != len(ev.All("ATTENDEE"))
	}
	return significant, nil
}

func attendeeEmails(ev *ical.Component) map[string]string {
	out := map[string]string{}
	for _, p := range ev.All("ATTENDEE") {
		e := mailtoAddress(p.Value)
		out[CanonicalEmail(e)] = e
	}
	return out
}

// sameEvent compares two VEVENTs and their clear attendee lists.
func sameEvent(a, b *ical.Component) bool { return ical.Serialize(a) == ical.Serialize(b) }

// Update changes an event, one occurrence or a whole series.
func (a *Access) Update(ctx context.Context, c *papi.Client, args UpdateArgs) (*WriteResult, error) {
	if err := firstProblem(ValidatePatch(args.Patch, "patch")); err != nil {
		return nil, err
	}
	w, werr := ResolveWindow(args.Window, Now(), "window", 0)
	if werr != nil {
		return nil, werr
	}
	if perr := a.requireWritable(); perr != nil {
		return nil, perr
	}
	s, err := a.loadSeries(ctx, c, args.Target.UID)
	if err != nil {
		return nil, err
	}
	row, original, exception, perr := resolveTarget(s, args.Target, args.Scope)
	if perr != nil {
		return nil, perr
	}
	owner := row
	if s.master != nil {
		owner = s.master
	}
	if !isOwn(owner) || !isOwn(row) {
		return nil, protocol.Errorf(protocol.CodeNotOrganiser, "Someone else organises this event: answer it with respond instead.")
	}
	if perr := checkRevision(args.ExpectedRevision, row); perr != nil {
		return nil, perr
	}
	if args.Patch.RecurrenceRule.Set && args.Scope == "occurrence" {
		return nil, protocol.FieldError(protocol.CodeInvalidArgument, "patch.recurrence_rule", "A recurrence rule belongs to the whole series: use scope series.")
	}
	res := newResult()
	res.Before = a.Records(Expand(s.rows, w, true))
	switch {
	case args.Scope == "occurrence":
		err = a.updateOccurrence(ctx, c, s, original, exception, args, res)
	case args.Scope == "series" && s.recurring():
		err = a.updateSeries(ctx, c, s, original, args, res)
	default:
		err = a.updateSingle(ctx, c, row, args, res)
	}
	if err != nil {
		return nil, err
	}
	if res.Written {
		a.readBack(ctx, c, s.uid, w, res, res.sent)
		res.DeletedKeys = deletedKeys(res.Before, res.Events)
	} else {
		res.Events = res.Before
		res.Verified = true
		rev := Revision(row.Row, row.Sequence())
		res.Revision = &rev
	}
	return res, nil
}

func deletedKeys(before, after []Record) []string {
	have := map[string]bool{}
	for _, r := range after {
		have[r.ProviderEventID] = true
	}
	out := []string{}
	seen := map[string]bool{}
	for _, r := range before {
		if !have[r.ProviderEventID] && !seen[r.ProviderEventID] {
			seen[r.ProviderEventID] = true
			out = append(out, r.ProviderEventID)
		}
	}
	return out
}

func (a *Access) updateSingle(ctx context.Context, c *papi.Client, row *Decoded, args UpdateArgs, res *WriteResult) error {
	ev := row.Merged.Clone()
	start, end, timeChanged, perr := newTimes(ev, args.Patch)
	if perr != nil {
		return perr
	}
	if timeChanged {
		setTimes(ev, start, end)
	}
	significant, perr := a.fieldEdits(ev, args.Patch, propText(ev, "UID"), row.CalendarSK != nil)
	if perr != nil {
		return perr
	}
	if args.Patch.RecurrenceRule.Set {
		before := rruleOf(ev)
		if args.Patch.RecurrenceRule.Null || args.Patch.RecurrenceRule.Value == "" {
			ev.Remove("RRULE")
			ev.Remove("EXDATE")
		} else {
			rule, perr := CanonicalRule(args.Patch.RecurrenceRule.Value, start)
			if perr != nil {
				perr.Field = strp("patch.recurrence_rule")
				return perr
			}
			ev.Set(ical.Property{Name: "RRULE", Value: rule})
		}
		significant = significant || before != rruleOf(ev)
	} else if timeChanged && rruleOf(ev) != "" {
		if rule, perr := CanonicalRule(rruleOf(ev), start); perr == nil {
			ev.Set(ical.Property{Name: "RRULE", Value: rule})
		}
	}
	if timeChanged || significant {
		setSequence(ev, row.Sequence()+1)
	}
	if sameEvent(ev, row.Merged) {
		return nil
	}
	body, err := a.buildBody(ev, row.SharedSK, row.CalendarSK, row.Row, false)
	if err != nil {
		return a.sealError(err)
	}
	if err := a.share(ctx, c, body, row.SharedSK, row.Merged, ev, args, res); err != nil {
		return err
	}
	if _, err := putSync(ctx, c, a.CalendarID, SyncRequest{MemberID: a.Member.ID, Events: []SyncEntry{{ID: row.Row.ID, Event: body}}}); err != nil {
		return err
	}
	res.Written = true
	res.sent = afterWrite(a, []*Decoded{row}, map[string]*ical.Component{row.Row.ID: ev}, nil)
	res.ITIP = itipSource(ev, body.Attendees)
	return nil
}

func (a *Access) sealError(err error) error {
	if errors.Is(err, errNoCalendarKey) {
		return protocol.Errorf(protocol.CodeUnsupported, "This event has no private calendar part, so Nojoin cannot store that change on it.").
			WithDetail("hint", "Edit this event in Proton's own app.")
	}
	var pe *protocol.Error
	if errors.As(err, &pe) {
		return pe
	}
	return protocol.Errorf(protocol.CodeProviderRejected, "The event could not be encrypted.")
}

// occurrenceBase builds a new exception row from the master: the master's
// properties at the occurrence's time, with RECURRENCE-ID in the master's form
// and the master's SEQUENCE.
func occurrenceBase(master *Decoded, original ical.Moment) *ical.Component {
	ev := master.Merged.Clone()
	ev.Remove("RRULE")
	ev.Remove("EXDATE")
	ms, me, _ := eventTimes(master.Merged)
	var start, end ical.Moment
	if ms.AllDay {
		start = ical.Moment{AllDay: true, Date: original.Date}
		end = ical.Moment{AllDay: true, Date: original.Date.Add(me.Date.Sub(ms.Date))}
	} else {
		start = original
		end = ical.Moment{Time: original.Time.Add(me.Time.Sub(ms.Time)), Zone: me.Zone}
		if loc, err := ical.LoadZone(me.Zone); err == nil {
			end.Time = end.Time.In(loc)
		}
	}
	setTimes(ev, start, end)
	rid := ical.MomentProperty("RECURRENCE-ID", original)
	ev.Set(rid)
	setSequence(ev, master.Sequence())
	return ev
}

func (a *Access) updateOccurrence(ctx context.Context, c *papi.Client, s *series, original *ical.Moment, exception *Decoded, args UpdateArgs, res *WriteResult) error {
	if exception != nil {
		ev := exception.Merged.Clone()
		start, end, timeChanged, perr := newTimes(ev, args.Patch)
		if perr != nil {
			return perr
		}
		if timeChanged {
			setTimes(ev, start, end)
		}
		significant, perr := a.fieldEdits(ev, args.Patch, s.uid, exception.CalendarSK != nil)
		if perr != nil {
			return perr
		}
		seq := exception.Sequence()
		if s.master != nil && s.master.Sequence() > seq {
			seq = s.master.Sequence()
		}
		if timeChanged || significant {
			seq++
		}
		setSequence(ev, seq)
		if sameEvent(ev, exception.Merged) {
			return nil
		}
		body, err := a.buildBody(ev, exception.SharedSK, exception.CalendarSK, exception.Row, false)
		if err != nil {
			return a.sealError(err)
		}
		if err := a.share(ctx, c, body, exception.SharedSK, exception.Merged, ev, args, res); err != nil {
			return err
		}
		if _, err := putSync(ctx, c, a.CalendarID, SyncRequest{MemberID: a.Member.ID, Events: []SyncEntry{{ID: exception.Row.ID, Event: body}}}); err != nil {
			return err
		}
		res.Written = true
		res.sent = afterWrite(a, s.rows, map[string]*ical.Component{exception.Row.ID: ev}, nil)
		res.ITIP = itipSource(ev, body.Attendees)
		return nil
	}
	master := s.master
	base := occurrenceBase(master, *original)
	ev := base.Clone()
	start, end, timeChanged, perr := newTimes(ev, args.Patch)
	if perr != nil {
		return perr
	}
	if timeChanged {
		setTimes(ev, start, end)
	}
	if _, perr := a.fieldEdits(ev, args.Patch, s.uid, true); perr != nil {
		return perr
	}
	if sameEvent(ev, base) {
		return nil
	}
	if master.SharedSK == nil {
		return protocol.Errorf(protocol.CodeProviderRejected, "The series' shared key cannot be opened.")
	}
	// The exception reuses the master's shared session key, wrapped again for
	// the calendar key, so guests who hold that key can read the edit.
	calSK := master.CalendarSK
	if calSK == nil {
		var err error
		if calSK, err = crypto.GenerateSessionKey(); err != nil {
			return err
		}
	}
	body, err := a.buildBody(ev, master.SharedSK, calSK, master.Row, true)
	if err != nil {
		return a.sealError(err)
	}
	if err := a.share(ctx, c, body, master.SharedSK, base, ev, args, res); err != nil {
		return err
	}
	zero := 0
	if _, err := putSync(ctx, c, a.CalendarID, SyncRequest{MemberID: a.Member.ID, IsImport: &zero,
		Events: []SyncEntry{{Overwrite: &zero, Event: body}}}); err != nil {
		return err
	}
	res.Written = true
	res.sent = afterWrite(a, s.rows, nil, nil, ev)
	res.ITIP = itipSource(ev, body.Attendees)
	return nil
}

// seriesShift moves a master's times so the named occurrence gets the new
// start and end: same date, new time of day and length, in the event's zone.
func seriesShift(master *Decoded, original *ical.Moment, newStart, newEnd ical.Moment) (ical.Moment, ical.Moment, *protocol.Error) {
	ms, _, _ := eventTimes(master.Merged)
	orig := ms
	if original != nil {
		orig = *original
	}
	unsupported := protocol.Errorf(protocol.CodeUnsupported, "A series cannot be moved to another date this way.").
		WithDetail("hint", "Cancel and recreate the series, or edit occurrences one by one.")
	localDate := func(m ical.Moment) time.Time {
		if m.AllDay {
			return m.Date
		}
		t := m.Time
		return time.Date(t.Year(), t.Month(), t.Day(), 0, 0, 0, 0, time.UTC)
	}
	if !localDate(orig).Equal(localDate(newStart)) {
		return ms, ms, unsupported
	}
	masterDate := localDate(ms)
	var start, end ical.Moment
	if newStart.AllDay {
		start = ical.Moment{AllDay: true, Date: masterDate}
		end = ical.Moment{AllDay: true, Date: masterDate.Add(newEnd.Date.Sub(newStart.Date))}
	} else {
		loc, _ := ical.LoadZone(newStart.Zone)
		t := newStart.Time
		st := time.Date(masterDate.Year(), masterDate.Month(), masterDate.Day(), t.Hour(), t.Minute(), t.Second(), 0, loc)
		start = ical.Moment{Time: st, Zone: newStart.Zone}
		end = ical.Moment{Time: st.Add(newEnd.Time.Sub(newStart.Time)).In(loc), Zone: newEnd.Zone}
	}
	return start, end, nil
}

func (a *Access) updateSeries(ctx context.Context, c *papi.Client, s *series, original *ical.Moment, args UpdateArgs, res *WriteResult) error {
	master := s.master
	ev := master.Merged.Clone()
	timeChanged := false
	var start ical.Moment
	if args.Patch.Start != nil || args.Patch.End != nil || args.Patch.AllDay != nil {
		// Work out the named occurrence's new times, then move the master.
		occ := occurrenceBase(master, func() ical.Moment {
			if original != nil {
				return *original
			}
			ms, _, _ := eventTimes(master.Merged)
			return ms
		}())
		ns, ne, _, perr := newTimes(occ, args.Patch)
		if perr != nil {
			return perr
		}
		ms, me, perr := seriesShift(master, original, ns, ne)
		if perr != nil {
			return perr
		}
		cs, ce, _ := eventTimes(master.Merged)
		timeChanged = ical.FormatLine(ical.MomentProperty("DTSTART", ms)) != ical.FormatLine(ical.MomentProperty("DTSTART", cs)) ||
			ical.FormatLine(ical.MomentProperty("DTEND", me)) != ical.FormatLine(ical.MomentProperty("DTEND", ce))
		setTimes(ev, ms, me)
		start = ms
	} else {
		start, _, _ = eventTimes(master.Merged)
	}
	oldMaster := master.Merged
	significant, perr := a.fieldEdits(ev, args.Patch, s.uid, master.CalendarSK != nil)
	if perr != nil {
		return perr
	}
	ruleChanged := false
	if args.Patch.RecurrenceRule.Set {
		before := rruleOf(ev)
		if args.Patch.RecurrenceRule.Null || args.Patch.RecurrenceRule.Value == "" {
			ev.Remove("RRULE")
			ev.Remove("EXDATE")
		} else {
			rule, perr := CanonicalRule(args.Patch.RecurrenceRule.Value, start)
			if perr != nil {
				perr.Field = strp("patch.recurrence_rule")
				return perr
			}
			ev.Set(ical.Property{Name: "RRULE", Value: rule})
		}
		ruleChanged = before != rruleOf(ev)
	} else if timeChanged {
		if rule, perr := CanonicalRule(rruleOf(ev), start); perr == nil {
			ev.Set(ical.Property{Name: "RRULE", Value: rule})
		} else {
			return perr
		}
	}
	dropExceptions := timeChanged || ruleChanged
	newSeq := master.Sequence()
	if timeChanged || ruleChanged || significant {
		newSeq++
	}
	setSequence(ev, newSeq)
	if sameEvent(ev, oldMaster) {
		return nil
	}
	body, err := a.buildBody(ev, master.SharedSK, master.CalendarSK, master.Row, false)
	if err != nil {
		return a.sealError(err)
	}
	if err := a.share(ctx, c, body, master.SharedSK, oldMaster, ev, args, res); err != nil {
		return err
	}
	entries := []SyncEntry{{ID: master.Row.ID, Event: body}}
	changed := map[string]*ical.Component{master.Row.ID: ev}
	deleted := map[string]bool{}
	for _, ex := range s.sortedExceptions() {
		if dropExceptions {
			// Their RECURRENCE-IDs no longer name real occurrences.
			entries = append(entries, SyncEntry{ID: ex.Row.ID})
			deleted[ex.Row.ID] = true
			res.Stats["exceptions_removed"]++
			continue
		}
		exEv := ex.Merged.Clone()
		propagate(exEv, oldMaster, ev, args.Patch)
		if ex.Sequence() < newSeq {
			setSequence(exEv, newSeq)
		}
		if sameEvent(exEv, ex.Merged) {
			continue
		}
		exBody, err := a.buildBody(exEv, ex.SharedSK, ex.CalendarSK, ex.Row, false)
		if err != nil {
			return a.sealError(err)
		}
		entries = append(entries, SyncEntry{ID: ex.Row.ID, Event: exBody})
		changed[ex.Row.ID] = exEv
	}
	if _, err := putSync(ctx, c, a.CalendarID, SyncRequest{MemberID: a.Member.ID, Events: entries}); err != nil {
		return err
	}
	res.Written = true
	res.sent = afterWrite(a, s.rows, changed, deleted)
	if n := res.Stats["exceptions_removed"]; n > 0 {
		res.warn("exceptions_removed:" + strconv.Itoa(n))
	}
	res.ITIP = itipSource(ev, body.Attendees)
	return nil
}

func (s *series) sortedExceptions() []*Decoded {
	keys := make([]int64, 0, len(s.exceptions))
	for k := range s.exceptions {
		keys = append(keys, k)
	}
	sort.Slice(keys, func(i, j int) bool { return keys[i] < keys[j] })
	out := make([]*Decoded, 0, len(keys))
	for _, k := range keys {
		out = append(out, s.exceptions[k])
	}
	return out
}

// propagate carries a series-wide content change to an exception wherever the
// exception still had the master's old value, so an occurrence edited on its
// own keeps its own edit.
func propagate(ex, oldMaster, newMaster *ical.Component, p Patch) {
	for _, name := range []string{"SUMMARY", "DESCRIPTION", "LOCATION", "URL", "TRANSP", "STATUS", ical.HoldProperty, "ORGANIZER"} {
		oldP, newP, exP := oldMaster.Get(name), newMaster.Get(name), ex.Get(name)
		oldLine, exLine := "", ""
		if oldP != nil {
			oldLine = ical.FormatLine(*oldP)
		}
		if exP != nil {
			exLine = ical.FormatLine(*exP)
		}
		if oldLine != exLine {
			continue
		}
		if newP == nil {
			ex.Remove(name)
		} else {
			ex.Set(newP.Clone())
		}
	}
	if len(p.AddAttendees) > 0 || len(p.RemoveAttendees) > 0 {
		// Apply the same additions and removals, so an occurrence's own guests
		// stay on it.
		removed := map[string]bool{}
		for _, e := range p.RemoveAttendees {
			removed[CanonicalEmail(e)] = true
		}
		kept := ex.Props[:0]
		for _, q := range ex.Props {
			if q.Name == "ATTENDEE" && removed[CanonicalEmail(mailtoAddress(q.Value))] {
				continue
			}
			kept = append(kept, q)
		}
		ex.Props = kept
		have := attendeeEmails(ex)
		for _, q := range newMaster.All("ATTENDEE") {
			canon := CanonicalEmail(mailtoAddress(q.Value))
			if _, ok := have[canon]; ok {
				continue
			}
			if _, wasThere := attendeeEmails(oldMaster)[canon]; wasThere {
				continue
			}
			ex.Add(q.Clone())
		}
		if len(ex.All("ATTENDEE")) > 0 && ex.Get("ORGANIZER") == nil {
			if org := newMaster.Get("ORGANIZER"); org != nil {
				ex.Set(org.Clone())
			}
		}
	}
}

// CancelArgs are cancel_event's arguments.
type CancelArgs struct {
	CalendarID       string     `json:"calendar_id"`
	Target           Target     `json:"target"`
	Scope            string     `json:"scope"`
	ExpectedRevision *string    `json:"expected_revision"`
	Window           *WindowArg `json:"window"`
}

// Cancel removes an event, an occurrence or a series at Proton.
func (a *Access) Cancel(ctx context.Context, c *papi.Client, args CancelArgs) (*WriteResult, error) {
	w, werr := ResolveWindow(args.Window, Now(), "window", 0)
	if werr != nil {
		return nil, werr
	}
	if perr := a.requireWritable(); perr != nil {
		return nil, perr
	}
	s, err := a.loadSeries(ctx, c, args.Target.UID)
	if err != nil {
		return nil, err
	}
	row, original, exception, perr := resolveTarget(s, args.Target, args.Scope)
	if perr != nil {
		return nil, perr
	}
	owner := row
	if s.master != nil {
		owner = s.master
	}
	if !isOwn(owner) || !isOwn(row) {
		return nil, protocol.Errorf(protocol.CodeNotOrganiser, "Someone else organises this event: decline it with respond instead.")
	}
	if perr := checkRevision(args.ExpectedRevision, row); perr != nil {
		return nil, perr
	}
	res := newResult()
	all := a.Records(Expand(s.rows, w, true))
	res.ITIP = itipSource(row.Merged, clearAttendees(row.Merged, row.Row.ClearAttendees()))
	var entries []SyncEntry
	switch {
	case args.Scope == "occurrence" && original != nil:
		key := s.uid + "#" + ical.RecurrenceKey(*original)
		for _, r := range all {
			if r.ProviderEventID == key {
				res.Before = append(res.Before, r)
			}
		}
		master := s.master
		if master == nil {
			// An orphaned exception: delete its row.
			entries = append(entries, SyncEntry{ID: row.Row.ID})
			break
		}
		ev := master.Merged.Clone()
		ms, _, _ := eventTimes(master.Merged)
		ex := ical.MomentProperty("EXDATE", *original)
		if !ms.AllDay && ms.Zone != "" && ms.Zone != "UTC" {
			loc, _ := ical.LoadZone(ms.Zone)
			ex = ical.MomentProperty("EXDATE", ical.Moment{Time: original.Time.In(loc), Zone: ms.Zone})
		}
		ev.Add(ex)
		newSeq := master.Sequence() + 1
		setSequence(ev, newSeq)
		body, err := a.buildBody(ev, master.SharedSK, master.CalendarSK, master.Row, false)
		if err != nil {
			return nil, a.sealError(err)
		}
		entries = append(entries, SyncEntry{ID: master.Row.ID, Event: body})
		changed := map[string]*ical.Component{master.Row.ID: ev}
		deleted := map[string]bool{}
		if exception != nil {
			entries = append(entries, SyncEntry{ID: exception.Row.ID})
			deleted[exception.Row.ID] = true
		}
		for _, other := range s.sortedExceptions() {
			if other == exception || other.Sequence() >= newSeq {
				continue
			}
			exEv := other.Merged.Clone()
			setSequence(exEv, newSeq)
			exBody, err := a.buildBody(exEv, other.SharedSK, other.CalendarSK, other.Row, false)
			if err != nil {
				return nil, a.sealError(err)
			}
			entries = append(entries, SyncEntry{ID: other.Row.ID, Event: exBody})
			changed[other.Row.ID] = exEv
		}
		res.sent = afterWrite(a, s.rows, changed, deleted)
		res.ITIP = itipSource(occurrenceBase(master, *original), clearAttendees(master.Merged, master.Row.ClearAttendees()))
	default:
		// A single event or a whole series: delete every row with the UID in
		// one batch, since Proton does not cascade.
		res.Before = all
		for _, d := range s.rows {
			entries = append(entries, SyncEntry{ID: d.Row.ID})
		}
	}
	if _, err := putSync(ctx, c, a.CalendarID, SyncRequest{MemberID: a.Member.ID, Events: entries}); err != nil {
		return nil, err
	}
	res.Written = true
	if args.Scope == "occurrence" && s.master != nil {
		a.readBack(ctx, c, s.uid, w, res, res.sent)
	} else {
		res.Verified = true
	}
	res.DeletedKeys = deletedKeys(all, res.Events)
	return res, nil
}
