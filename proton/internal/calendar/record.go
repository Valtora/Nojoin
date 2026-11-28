package calendar

import (
	"crypto/sha1" //nolint:gosec // Proton's attendee token is SHA-1 by definition.
	"encoding/hex"
	"fmt"
	"regexp"
	"strings"
	"time"

	"github.com/Valtora/Nojoin/proton/internal/ical"
)

// Person is an organiser in a record.
type Person struct {
	Name   string `json:"name"`
	Email  string `json:"email"`
	IsSelf bool   `json:"is_self"`
}

// AttendeeOut is an attendee in a record.
type AttendeeOut struct {
	Name     string `json:"name"`
	Email    string `json:"email"`
	Response string `json:"response"`
	Role     string `json:"role"`
	IsSelf   bool   `json:"is_self"`
	Token    string `json:"token"`
}

// ProviderData is what Python stores and hands back unchanged.
type ProviderData struct {
	UID                  string            `json:"uid"`
	CalendarID           string            `json:"calendar_id"`
	EventID              string            `json:"event_id"`
	MasterEventID        string            `json:"master_event_id"`
	ExceptionEventID     *string           `json:"exception_event_id"`
	SharedEventID        string            `json:"shared_event_id"`
	AttendeeIDs          map[string]string `json:"attendee_ids"`
	IsProtonProtonInvite bool              `json:"is_proton_proton_invite"`
	IsPersonalSingleEdit bool              `json:"is_personal_single_edit"`
}

// Record is the event record of PROTOCOL.md.
type Record struct {
	ProviderEventID   string        `json:"provider_event_id"`
	UID               string        `json:"uid"`
	Title             string        `json:"title"`
	Description       *string       `json:"description"`
	Location          *string       `json:"location"`
	MeetingURL        *string       `json:"meeting_url"`
	URL               *string       `json:"url"`
	IsAllDay          bool          `json:"is_all_day"`
	StartsAt          *string       `json:"starts_at"`
	EndsAt            *string       `json:"ends_at"`
	StartDate         *string       `json:"start_date"`
	EndDate           *string       `json:"end_date"`
	StartTimeZone     *string       `json:"start_time_zone"`
	EndTimeZone       *string       `json:"end_time_zone"`
	Status            string        `json:"status"`
	IsHold            bool          `json:"is_hold"`
	ShowAs            string        `json:"show_as"`
	Organiser         *Person       `json:"organiser"`
	IsOrganiser       bool          `json:"is_organiser"`
	MyResponse        *string       `json:"my_response"`
	Attendees         []AttendeeOut `json:"attendees"`
	AttendeeCount     int           `json:"attendee_count"`
	SeriesKey         *string       `json:"series_key"`
	RecurrenceRule    *string       `json:"recurrence_rule"`
	OccurrenceType    string        `json:"occurrence_type"`
	OriginalStart     *string       `json:"original_start"`
	OriginalStartDate *string       `json:"original_start_date"`
	Sequence          int           `json:"sequence"`
	Revision          string        `json:"revision"`
	CreatedAt         *string       `json:"created_at"`
	UpdatedAt         *string       `json:"updated_at"`
	DecryptWarnings   []string      `json:"decrypt_warnings"`
	ProviderData      ProviderData  `json:"provider_data"`
}

// conferenceSeparator brackets the join block Proton writes into a
// description (SEPARATOR_PROTON_EVENTS in the web client).
const conferenceSeparator = "~-~-~-~-~-~-~%~!~%~!~%~!~%~!~%~!~%~!~%~!~%~!~%~!~%~!~%~!~%~!~%~!~-~-~-~-~-~-~"

var conferenceBlock = regexp.MustCompile(`\n?` + regexp.QuoteMeta(conferenceSeparator) + `[\s\S]*?` + regexp.QuoteMeta(conferenceSeparator))

var protonDomains = map[string]bool{"protonmail.com": true, "protonmail.ch": true, "pm.me": true, "proton.me": true}

// CanonicalEmail canonicalises an address as Proton's web client guesses it
// (canonicalizeEmailByGuess): the Proton scheme for Proton's domains, the
// Gmail scheme for Google's, plus-alias removal for a few others, and lower
// case for everything.
func CanonicalEmail(email string) string {
	email = strings.TrimSpace(email)
	at := strings.LastIndex(email, "@")
	if at < 0 {
		return strings.ToLower(email)
	}
	local, domain := email[:at], strings.ToLower(email[at+1:])
	plus := func(l string) string {
		if i := strings.Index(l, "+"); i >= 0 {
			return l[:i]
		}
		return l
	}
	switch {
	case protonDomains[domain]:
		local = strings.NewReplacer(".", "", "_", "", "-", "").Replace(plus(local))
	case domain == "gmail.com" || domain == "googlemail.com" || domain == "google.com":
		local = strings.ReplaceAll(plus(local), ".", "")
	case domain == "hotmail.com" || domain == "hotmail.co.uk" || domain == "hotmail.fr" || domain == "outlook.com" || domain == "yandex.ru" || domain == "mail.ru":
		local = plus(local)
	}
	return strings.ToLower(local) + "@" + domain
}

// AttendeeToken is Proton's token for an attendee: hex SHA-1 of the UID and
// the canonical address (generateAttendeeToken in the web client).
func AttendeeToken(uid, email string) string {
	sum := sha1.Sum([]byte(uid + CanonicalEmail(email))) //nolint:gosec
	return hex.EncodeToString(sum[:])
}

func mailtoAddress(v string) string {
	v = strings.TrimSpace(v)
	if len(v) >= 7 && strings.EqualFold(v[:7], "mailto:") {
		v = v[7:]
	}
	return strings.TrimSpace(v)
}

func roleName(r string) string {
	switch strings.ToUpper(r) {
	case "OPT-PARTICIPANT":
		return "optional"
	case "NON-PARTICIPANT":
		return "information"
	default:
		return "required"
	}
}

// RoleParam maps a protocol role onto ROLE.
func RoleParam(r string) string {
	switch r {
	case "optional":
		return "OPT-PARTICIPANT"
	case "information":
		return "NON-PARTICIPANT"
	default:
		return "REQ-PARTICIPANT"
	}
}

func statusName(s int) string {
	switch s {
	case StatusAccepted:
		return "accepted"
	case StatusDeclined:
		return "declined"
	case StatusTentative:
		return "tentative"
	default:
		return "needs_action"
	}
}

func partstatName(p string) string {
	switch strings.ToUpper(p) {
	case "ACCEPTED":
		return "accepted"
	case "DECLINED":
		return "declined"
	case "TENTATIVE":
		return "tentative"
	default:
		return "needs_action"
	}
}

// StatusCode maps a protocol response onto Proton's clear status.
func StatusCode(r string) int {
	switch r {
	case "accepted":
		return StatusAccepted
	case "declined":
		return StatusDeclined
	case "tentative":
		return StatusTentative
	default:
		return StatusNeedsAction
	}
}

// Partstat maps a protocol response onto PARTSTAT.
func Partstat(r string) string {
	switch r {
	case "accepted":
		return "ACCEPTED"
	case "declined":
		return "DECLINED"
	case "tentative":
		return "TENTATIVE"
	default:
		return "NEEDS-ACTION"
	}
}

func strp(s string) *string { return &s }

func optText(c *ical.Component, name string) *string {
	if p := c.Get(name); p != nil {
		v := p.Text()
		if v != "" {
			return &v
		}
	}
	return nil
}

func rfc3339(t time.Time) string { return t.UTC().Format("2006-01-02T15:04:05Z") }

func unixString(sec int64) *string {
	if sec <= 0 {
		return nil
	}
	return strp(rfc3339(time.Unix(sec, 0)))
}

// Revision is the opaque revision of a row.
func Revision(r *RawEvent, seq int) string {
	return fmt.Sprintf("p1:%d:%d", r.ModifyTime, seq)
}

func isSelf(email string, self map[string]bool) bool {
	return email != "" && self[CanonicalEmail(email)]
}

// ToRecord turns an occurrence into the protocol's record.
func (a *Access) ToRecord(o Occurrence) Record {
	ev := o.Row.Merged
	row := o.Row.Row
	self := map[string]bool{}
	for _, e := range a.Keys.Emails() {
		self[CanonicalEmail(e)] = true
	}
	uid := propText(ev, "UID")
	if uid == "" {
		uid = row.UID
	}
	rec := Record{
		UID:             uid,
		ProviderEventID: uid,
		Title:           propText(ev, "SUMMARY"),
		Location:        optText(ev, "LOCATION"),
		URL:             optText(ev, "URL"),
		IsAllDay:        o.Start.AllDay,
		Sequence:        o.Row.Sequence(),
		IsHold:          o.Row.IsHold(),
		ShowAs:          "busy",
		OccurrenceType:  o.Kind,
		IsOrganiser:     bool(row.IsOrganizer),
		Revision:        Revision(row, o.Row.Sequence()),
		CreatedAt:       unixString(row.CreateTime),
		UpdatedAt:       unixString(row.ModifyTime),
		DecryptWarnings: append([]string{}, o.Row.Warnings...),
		Attendees:       []AttendeeOut{},
	}
	if o.Original != nil {
		rec.ProviderEventID = uid + "#" + ical.RecurrenceKey(*o.Original)
		rec.SeriesKey = strp(uid)
		if o.Original.AllDay {
			rec.OriginalStartDate = strp(o.Original.Date.Format("2006-01-02"))
		} else {
			rec.OriginalStart = strp(rfc3339(o.Original.Time))
		}
	}
	master := o.Master
	if master != nil {
		if rule := rruleOf(master.Merged); rule != "" {
			rec.RecurrenceRule = strp(rule)
		}
	} else if rule := rruleOf(ev); rule != "" {
		rec.RecurrenceRule = strp(rule)
	}
	desc := propText(ev, "DESCRIPTION")
	conf := ev.Get("X-PM-CONFERENCE-URL")
	if conf != nil || ev.Get("X-PM-CONFERENCE-ID") != nil {
		desc = strings.TrimRight(conferenceBlock.ReplaceAllString(desc, ""), "\n")
	}
	if desc != "" {
		rec.Description = &desc
	}
	if conf != nil && strings.TrimSpace(conf.Value) != "" {
		rec.MeetingURL = strp(strings.TrimSpace(ical.UnescapeText(conf.Value)))
	} else {
		rec.MeetingURL = rec.URL
	}
	if o.Start.AllDay {
		rec.StartDate = strp(o.Start.Date.Format("2006-01-02"))
		rec.EndDate = strp(o.End.Date.Format("2006-01-02"))
	} else {
		rec.StartsAt = strp(rfc3339(o.Start.Time))
		rec.EndsAt = strp(rfc3339(o.End.Time))
		sz := o.Start.Zone
		if sz == "" {
			sz = row.StartTimezone
		}
		ez := o.End.Zone
		if ez == "" {
			ez = sz
		}
		if sz != "" {
			rec.StartTimeZone = strp(sz)
		}
		if ez != "" {
			rec.EndTimeZone = strp(ez)
		}
	}
	switch {
	case strings.EqualFold(propText(ev, "STATUS"), "CANCELLED"):
		rec.Status = "cancelled"
	case rec.IsHold:
		rec.Status = "tentative"
	default:
		rec.Status = "confirmed"
	}
	if strings.EqualFold(propText(ev, "TRANSP"), "TRANSPARENT") {
		rec.ShowAs = "free"
	}
	if p := ev.Get("ORGANIZER"); p != nil {
		email := mailtoAddress(p.Value)
		rec.Organiser = &Person{Name: p.Param("CN"), Email: email, IsSelf: isSelf(email, self)}
	}
	clear := row.ClearAttendees()
	byToken := map[string]ClearAttendee{}
	ids := map[string]string{}
	for _, c := range clear {
		byToken[c.Token] = c
		if c.ID != "" {
			ids[c.Token] = c.ID
		}
	}
	for _, p := range ev.All("ATTENDEE") {
		email := mailtoAddress(p.Value)
		token := p.Param("X-PM-TOKEN")
		if token == "" && email != "" {
			token = AttendeeToken(uid, email)
		}
		resp := partstatName(p.Param("PARTSTAT"))
		if c, ok := byToken[token]; ok {
			resp = statusName(c.Status)
		}
		at := AttendeeOut{Name: p.Param("CN"), Email: email, Response: resp, Role: roleName(p.Param("ROLE")), IsSelf: isSelf(email, self), Token: token}
		if at.Name == email {
			at.Name = ""
		}
		rec.Attendees = append(rec.Attendees, at)
		if !rec.IsOrganiser && at.IsSelf && rec.MyResponse == nil {
			rec.MyResponse = strp(resp)
		}
	}
	rec.AttendeeCount = len(rec.Attendees)
	masterID := row.ID
	if master != nil {
		masterID = master.Row.ID
	}
	var exceptionID *string
	if o.Kind == "exception" {
		exceptionID = strp(row.ID)
	}
	rec.ProviderData = ProviderData{
		UID:                  uid,
		ExceptionEventID:     exceptionID,
		CalendarID:           a.CalendarID,
		EventID:              row.ID,
		MasterEventID:        masterID,
		SharedEventID:        row.SharedEventID,
		AttendeeIDs:          ids,
		IsProtonProtonInvite: bool(row.IsProtonProtonInvite),
		IsPersonalSingleEdit: bool(row.IsPersonalSingleEdit),
	}
	return rec
}
