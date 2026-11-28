package calendar

import (
	"encoding/json"
	"fmt"
	"net/mail"
	"slices"
	"strconv"
	"strings"
	"time"
	"unicode/utf8"

	"github.com/teambition/rrule-go"

	"github.com/Valtora/Nojoin/proton/internal/ical"
	"github.com/Valtora/Nojoin/proton/internal/protocol"
)

// Proton's field limits (MAX_CHARS_API and MAX_ATTENDEES in the web client).
const (
	MaxTitle       = 255
	MaxDescription = 3000
	MaxLocation    = 255
	MaxUID         = 191
	MaxAttendees   = 100
	MaxURL         = 2048
)

// TimeArg is a protocol time.
type TimeArg struct {
	DateTime *string `json:"date_time,omitempty"`
	TimeZone *string `json:"time_zone,omitempty"`
	Date     *string `json:"date,omitempty"`
}

// AttendeeArg is an attendee to write.
type AttendeeArg struct {
	Email string  `json:"email"`
	Name  *string `json:"name"`
	Role  string  `json:"role,omitempty"`
}

// EventFields are a new event's fields.
type EventFields struct {
	Title          string        `json:"title"`
	Description    *string       `json:"description"`
	Location       *string       `json:"location"`
	URL            *string       `json:"url"`
	AllDay         bool          `json:"all_day"`
	Start          TimeArg       `json:"start"`
	End            *TimeArg      `json:"end"`
	Hold           bool          `json:"hold"`
	ShowAs         string        `json:"show_as,omitempty"`
	RecurrenceRule *string       `json:"recurrence_rule"`
	Attendees      []AttendeeArg `json:"attendees"`
}

// OptString tells an absent key from null from a value.
type OptString struct {
	Set   bool
	Null  bool
	Value string
}

// UnmarshalJSON records that the key was present.
func (o *OptString) UnmarshalJSON(b []byte) error {
	o.Set = true
	if strings.TrimSpace(string(b)) == "null" {
		o.Null = true
		return nil
	}
	return json.Unmarshal(b, &o.Value)
}

// Patch is update_event's patch.
type Patch struct {
	Title           OptString     `json:"title"`
	Description     OptString     `json:"description"`
	Location        OptString     `json:"location"`
	URL             OptString     `json:"url"`
	Start           *TimeArg      `json:"start"`
	End             *TimeArg      `json:"end"`
	AllDay          *bool         `json:"all_day"`
	Hold            *bool         `json:"hold"`
	ShowAs          *string       `json:"show_as"`
	RecurrenceRule  OptString     `json:"recurrence_rule"`
	AddAttendees    []AttendeeArg `json:"add_attendees"`
	RemoveAttendees []string      `json:"remove_attendees"`
}

// Empty reports whether the patch asks for nothing.
func (p Patch) Empty() bool {
	return !p.Title.Set && !p.Description.Set && !p.Location.Set && !p.URL.Set && p.Start == nil && p.End == nil &&
		p.AllDay == nil && p.Hold == nil && p.ShowAs == nil && !p.RecurrenceRule.Set && len(p.AddAttendees) == 0 && len(p.RemoveAttendees) == 0
}

// Problem is one validation failure.
type Problem struct {
	Field   string `json:"field"`
	Code    string `json:"code"`
	Message string `json:"message"`
}

// Err turns the first problem into a protocol error.
func firstProblem(ps []Problem) *protocol.Error {
	if len(ps) == 0 {
		return nil
	}
	return protocol.FieldError(ps[0].Code, ps[0].Field, "%s", ps[0].Message)
}

type checker struct{ problems []Problem }

func (c *checker) add(field, code, format string, args ...any) {
	c.problems = append(c.problems, Problem{Field: field, Code: code, Message: fmt.Sprintf(format, args...)})
}

func (c *checker) text(field, value string, max int, single bool) {
	if utf8.RuneCountInString(value) > max {
		c.add(field, protocol.CodeInvalidArgument, "%s is longer than Proton allows (%d characters).", field, max)
	}
	if single && strings.ContainsAny(value, "\r\n") {
		c.add(field, protocol.CodeInvalidArgument, "%s must be one line.", field)
	}
	if !utf8.ValidString(value) {
		c.add(field, protocol.CodeInvalidArgument, "%s is not valid UTF-8.", field)
	}
}

func (c *checker) url(field, value string) {
	c.text(field, value, MaxURL, true)
	if !strings.HasPrefix(strings.ToLower(value), "https://") {
		c.add(field, protocol.CodeInvalidArgument, "%s must be an https:// address.", field)
	}
}

func validEmail(s string) bool {
	if len(s) > 254 || strings.ContainsAny(s, "\r\n<>\" ") {
		return false
	}
	a, err := mail.ParseAddress(s)
	return err == nil && strings.EqualFold(a.Address, s)
}

func (c *checker) attendees(field string, list []AttendeeArg) {
	if len(list) > MaxAttendees {
		c.add(field, protocol.CodeInvalidArgument, "Proton allows at most %d attendees.", MaxAttendees)
	}
	for i, a := range list {
		f := fmt.Sprintf("%s[%d]", field, i)
		if !validEmail(strings.TrimSpace(a.Email)) {
			c.add(f+".email", protocol.CodeInvalidArgument, "%s.email is not an email address.", f)
		}
		if a.Name != nil {
			c.text(f+".name", *a.Name, 255, true)
		}
		if a.Role != "" && a.Role != "required" && a.Role != "optional" && a.Role != "information" {
			c.add(f+".role", protocol.CodeInvalidArgument, "%s.role must be required, optional or information.", f)
		}
	}
}

// ParseTime turns a protocol time into a moment.
func ParseTime(t *TimeArg, field string, allDay bool) (ical.Moment, *protocol.Error) {
	if t == nil {
		return ical.Moment{}, protocol.FieldError(protocol.CodeInvalidTime, field, "%s is required.", field)
	}
	if allDay {
		if t.Date == nil || t.DateTime != nil {
			return ical.Moment{}, protocol.FieldError(protocol.CodeInvalidTime, field, "%s must be a date for an all-day event.", field)
		}
		d, err := time.ParseInLocation("2006-01-02", *t.Date, time.UTC)
		if err != nil {
			return ical.Moment{}, protocol.FieldError(protocol.CodeInvalidTime, field, "%s is not a YYYY-MM-DD date.", field)
		}
		return ical.Moment{AllDay: true, Date: d}, nil
	}
	if t.DateTime == nil || t.Date != nil {
		return ical.Moment{}, protocol.FieldError(protocol.CodeInvalidTime, field, "%s must be a date-time for a timed event.", field)
	}
	inst, err := time.Parse(time.RFC3339, *t.DateTime)
	if err != nil {
		return ical.Moment{}, protocol.FieldError(protocol.CodeInvalidTime, field, "%s must be an RFC 3339 date-time with an offset.", field)
	}
	zone := "UTC"
	if t.TimeZone != nil && *t.TimeZone != "" {
		zone = *t.TimeZone
	}
	loc, lerr := ical.LoadZone(zone)
	if lerr != nil {
		return ical.Moment{}, protocol.FieldError(protocol.CodeInvalidTime, field, "%s names a time zone Proton does not know.", field)
	}
	if loc == time.UTC {
		zone = "UTC"
	}
	return ical.Moment{Time: inst.In(loc), Zone: zone}, nil
}

// Rule limits from the web client's getIsRruleSupported (recurrence/rrule.ts).
var (
	maxInterval = map[string]int{"DAILY": 999, "WEEKLY": 4999, "MONTHLY": 999, "YEARLY": 99}
	allowedRule = map[string][]string{
		"DAILY":   {"FREQ", "COUNT", "INTERVAL", "UNTIL", "WKST"},
		"WEEKLY":  {"FREQ", "COUNT", "INTERVAL", "UNTIL", "WKST", "BYDAY"},
		"MONTHLY": {"FREQ", "COUNT", "INTERVAL", "UNTIL", "WKST", "BYMONTHDAY", "BYDAY", "BYSETPOS"},
		"YEARLY":  {"FREQ", "COUNT", "INTERVAL", "UNTIL", "WKST", "BYMONTHDAY", "BYMONTH"},
	}
	maxUntil = time.Date(2037, 12, 31, 23, 59, 59, 0, time.UTC)
	weekdays = []string{"SU", "MO", "TU", "WE", "TH", "FR", "SA"}
)

const maxCount = 49

// CanonicalRule checks a rule against Proton's limits and returns the value to
// write: upper case, parts in the order given, a date UNTIL on a timed series
// turned into the end of that day in the event's zone, in UTC.
func CanonicalRule(raw string, start ical.Moment) (string, *protocol.Error) {
	const field = "recurrence_rule"
	bad := func(format string, args ...any) (string, *protocol.Error) {
		return "", protocol.FieldError(protocol.CodeInvalidArgument, field, format, args...)
	}
	v := strings.TrimSpace(raw)
	if len(v) > 6 && strings.EqualFold(v[:6], "RRULE:") {
		v = v[6:]
	}
	if v == "" || strings.ContainsAny(v, "\r\n") || len(v) > 500 {
		return bad("recurrence_rule is empty or malformed.")
	}
	parts := map[string]string{}
	var order []string
	for _, part := range strings.Split(v, ";") {
		k, val, ok := strings.Cut(part, "=")
		k, val = strings.ToUpper(strings.TrimSpace(k)), strings.ToUpper(strings.TrimSpace(val))
		if !ok || k == "" || val == "" {
			return bad("recurrence_rule has a malformed part.")
		}
		if _, dup := parts[k]; dup {
			return bad("recurrence_rule repeats %s.", k)
		}
		parts[k] = val
		order = append(order, k)
	}
	freq := parts["FREQ"]
	allowed, ok := allowedRule[freq]
	if !ok {
		return bad("Proton supports FREQ DAILY, WEEKLY, MONTHLY and YEARLY only.")
	}
	for _, k := range order {
		if !slices.Contains(allowed, k) {
			return bad("Proton does not support %s in a %s rule.", k, strings.ToLower(freq))
		}
	}
	if s, ok := parts["INTERVAL"]; ok {
		n, err := strconv.Atoi(s)
		if err != nil || n < 1 || n > maxInterval[freq] {
			return bad("INTERVAL must be between 1 and %d for a %s rule.", maxInterval[freq], strings.ToLower(freq))
		}
	}
	_, hasCount := parts["COUNT"]
	_, hasUntil := parts["UNTIL"]
	if hasCount && hasUntil {
		return bad("recurrence_rule cannot have both COUNT and UNTIL.")
	}
	if hasCount {
		n, err := strconv.Atoi(parts["COUNT"])
		if err != nil || n < 1 || n > maxCount {
			return bad("Proton allows COUNT from 1 to %d.", maxCount)
		}
	}
	loc := time.UTC
	if !start.AllDay {
		if l, err := ical.LoadZone(start.Zone); err == nil {
			loc = l
		}
	}
	if hasUntil {
		u := parts["UNTIL"]
		var untilDate time.Time
		var err error
		switch {
		case len(u) == 8:
			untilDate, err = time.ParseInLocation("20060102", u, time.UTC)
		case strings.HasSuffix(u, "Z"):
			var t time.Time
			t, err = time.Parse("20060102T150405Z", u)
			untilDate = time.Date(t.In(loc).Year(), t.In(loc).Month(), t.In(loc).Day(), 0, 0, 0, 0, time.UTC)
		default:
			var t time.Time
			t, err = time.ParseInLocation("20060102T150405", u, loc)
			untilDate = time.Date(t.Year(), t.Month(), t.Day(), 0, 0, 0, 0, time.UTC)
		}
		if err != nil {
			return bad("UNTIL is not a date or date-time.")
		}
		if untilDate.After(maxUntil) {
			return bad("Proton allows UNTIL no later than 2037-12-31.")
		}
		if start.AllDay {
			parts["UNTIL"] = untilDate.Format("20060102")
		} else {
			end := time.Date(untilDate.Year(), untilDate.Month(), untilDate.Day(), 23, 59, 59, 0, loc)
			parts["UNTIL"] = end.UTC().Format("20060102T150405Z")
		}
	}
	if byday, ok := parts["BYDAY"]; ok {
		days := strings.Split(byday, ",")
		for _, d := range days {
			bare := strings.TrimLeft(d, "+-0123456789")
			if !slices.Contains(weekdays, bare) {
				return bad("BYDAY has an unknown day %q.", d)
			}
			if freq == "WEEKLY" && bare != d {
				return bad("A weekly rule takes plain BYDAY days.")
			}
		}
		if freq == "MONTHLY" {
			if len(days) > 1 {
				return bad("A monthly rule takes one BYDAY day.")
			}
			pos, hasPos := parts["BYSETPOS"]
			day := days[0]
			if bare := strings.TrimLeft(day, "+-0123456789"); bare != day {
				if hasPos {
					return bad("A monthly rule takes BYSETPOS or a numbered BYDAY, not both.")
				}
				pos, day, hasPos = day[:len(day)-len(bare)], bare, true
			}
			n, err := strconv.Atoi(strings.TrimPrefix(pos, "+"))
			if !hasPos || err != nil || (n != -1 && (n < 1 || n > 4)) {
				return bad("A monthly BYDAY needs a BYSETPOS of -1, 1, 2, 3 or 4.")
			}
			if _, md := parts["BYMONTHDAY"]; md {
				return bad("A monthly rule takes BYDAY or BYMONTHDAY, not both.")
			}
		}
	} else if _, pos := parts["BYSETPOS"]; pos {
		return bad("BYSETPOS needs a BYDAY.")
	}
	if md, ok := parts["BYMONTHDAY"]; ok && strings.Contains(md, ",") {
		return bad("Proton takes one BYMONTHDAY.")
	}
	if freq == "YEARLY" {
		if _, md := parts["BYMONTHDAY"]; md {
			if _, m := parts["BYMONTH"]; !m {
				return bad("A yearly BYMONTHDAY needs a BYMONTH.")
			}
		}
		if m, ok := parts["BYMONTH"]; ok && strings.Contains(m, ",") {
			return bad("Proton takes one BYMONTH.")
		}
	}
	var out []string
	for _, k := range order {
		out = append(out, k+"="+parts[k])
	}
	canonical := strings.Join(out, ";")
	anchor := start.Instant()
	if !start.AllDay {
		anchor = start.Time.In(loc)
	}
	opt, err := rrule.StrToROptionInLocation(canonical, loc)
	if err != nil {
		return bad("recurrence_rule cannot be read.")
	}
	opt.Dtstart = anchor
	r, err := rrule.NewRRule(*opt)
	if err != nil {
		return bad("recurrence_rule cannot be expanded.")
	}
	first := r.After(anchor.Add(-time.Second), true)
	if first.IsZero() || !first.Equal(anchor) {
		return bad("The event's start must be the first occurrence of recurrence_rule.")
	}
	return canonical, nil
}

// ValidateFields checks a new event against Proton's limits.
func ValidateFields(f EventFields, field string) []Problem {
	var c checker
	if strings.TrimSpace(f.Title) == "" {
		c.add(field+".title", protocol.CodeInvalidArgument, "title is required.")
	}
	c.text(field+".title", f.Title, MaxTitle, true)
	if f.Description != nil {
		c.text(field+".description", *f.Description, MaxDescription, false)
	}
	if f.Location != nil {
		c.text(field+".location", *f.Location, MaxLocation, true)
	}
	if f.URL != nil {
		c.url(field+".url", *f.URL)
	}
	if f.ShowAs != "" && f.ShowAs != "busy" && f.ShowAs != "free" {
		c.add(field+".show_as", protocol.CodeInvalidArgument, "show_as must be busy or free.")
	}
	c.attendees(field+".attendees", f.Attendees)
	start, perr := ParseTime(&f.Start, field+".start", f.AllDay)
	if perr != nil {
		c.add(*perr.Field, perr.Code, "%s", perr.Message)
		return c.problems
	}
	end := start
	if f.End != nil {
		e, perr := ParseTime(f.End, field+".end", f.AllDay)
		if perr != nil {
			c.add(*perr.Field, perr.Code, "%s", perr.Message)
			return c.problems
		}
		end = e
	} else if f.AllDay {
		end = ical.Moment{AllDay: true, Date: start.Date.AddDate(0, 0, 1)}
	} else {
		c.add(field+".end", protocol.CodeInvalidTime, "end is required for a timed event.")
	}
	if !end.Instant().After(start.Instant()) && !(f.End == nil && !f.AllDay) {
		c.add(field+".end", protocol.CodeInvalidTime, "end must be after start.")
	}
	if f.RecurrenceRule != nil {
		if _, perr := CanonicalRule(*f.RecurrenceRule, start); perr != nil {
			c.add(field+".recurrence_rule", perr.Code, "%s", perr.Message)
		}
	}
	return c.problems
}

// ValidatePatch checks an update's patch on its own (the event's state is
// checked when it is read).
func ValidatePatch(p Patch, field string) []Problem {
	var c checker
	if p.Title.Set {
		if p.Title.Null || strings.TrimSpace(p.Title.Value) == "" {
			c.add(field+".title", protocol.CodeInvalidArgument, "title cannot be empty.")
		}
		c.text(field+".title", p.Title.Value, MaxTitle, true)
	}
	if p.Description.Set && !p.Description.Null {
		c.text(field+".description", p.Description.Value, MaxDescription, false)
	}
	if p.Location.Set && !p.Location.Null {
		c.text(field+".location", p.Location.Value, MaxLocation, true)
	}
	if p.URL.Set && !p.URL.Null {
		c.url(field+".url", p.URL.Value)
	}
	if p.ShowAs != nil && *p.ShowAs != "busy" && *p.ShowAs != "free" {
		c.add(field+".show_as", protocol.CodeInvalidArgument, "show_as must be busy or free.")
	}
	c.attendees(field+".add_attendees", p.AddAttendees)
	for i, e := range p.RemoveAttendees {
		if !validEmail(strings.TrimSpace(e)) {
			c.add(fmt.Sprintf("%s.remove_attendees[%d]", field, i), protocol.CodeInvalidArgument, "remove_attendees holds something that is not an email address.")
		}
	}
	if p.Empty() {
		c.add(field, protocol.CodeInvalidArgument, "Nothing to update.")
	}
	return c.problems
}
