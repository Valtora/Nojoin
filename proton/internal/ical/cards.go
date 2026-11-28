package ical

import "strings"

// HoldProperty marks a Nojoin hold. It lives in the encrypted calendar card,
// the organiser-only part beside COMMENT, so guests never receive it.
const HoldProperty = "X-NOJOIN-HOLD"

// Card routing, as Proton's web client splits a VEVENT (getVeventParts in
// packages/shared/lib/calendar/veventHelper.ts and the *_FIELDS lists in
// constants.ts). The order of each list is the order properties are written.
var (
	SharedSignedFields    = []string{"UID", "DTSTAMP", "DTSTART", "DTEND", "RECURRENCE-ID", "RRULE", "EXDATE", "ORGANIZER", "SEQUENCE", "X-PM-CONFERENCE-ID"}
	SharedEncryptedFields = []string{"UID", "DTSTAMP", "CREATED", "DESCRIPTION", "SUMMARY", "LOCATION", "X-PM-CONFERENCE-URL"}
	CalendarSignedFields  = []string{"UID", "DTSTAMP", "EXDATE", "STATUS", "TRANSP"}
	// The web client routes unknown properties to the shared encrypted card;
	// Nojoin's hold marker is the one exception, kept out of what guests read.
	CalendarEncryptedFields  = []string{"UID", "DTSTAMP", "COMMENT", HoldProperty}
	AttendeesEncryptedFields = []string{"UID", "ATTENDEE"}
)

// takenKeys are routed to a named card; everything else is "rest" and goes to
// the shared encrypted card. COLOR is carried beside the event, not in a card.
var takenKeys = func() map[string]bool {
	m := map[string]bool{"COLOR": true}
	for _, list := range [][]string{SharedSignedFields, SharedEncryptedFields, CalendarSignedFields, CalendarEncryptedFields, AttendeesEncryptedFields} {
		for _, f := range list {
			m[f] = true
		}
	}
	return m
}()

// Cards are the iCalendar texts of one event's parts. An empty string means the
// part is absent.
type Cards struct {
	SharedSigned      string
	SharedEncrypted   string
	CalendarSigned    string
	CalendarEncrypted string
	Attendees         string
}

func pick(ev *Component, fields []string) *Component {
	out := &Component{Name: "VEVENT"}
	for _, f := range fields {
		for _, p := range ev.Props {
			if strings.EqualFold(p.Name, f) {
				out.Props = append(out.Props, p.Clone())
			}
		}
	}
	return out
}

// hasMoreThanRequired is the web client's hasMoreThan(REQUIRED_SET): a card
// holding only UID and DTSTAMP is not written.
func hasMoreThanRequired(c *Component) bool {
	for _, p := range c.Props {
		if p.Name != "UID" && p.Name != "DTSTAMP" {
			return true
		}
	}
	return false
}

// Split routes a merged VEVENT into Proton's cards. The two shared cards are
// always written; the calendar and attendee cards only when they hold more than
// UID and DTSTAMP, as the web client's toResultOptimized does. VALARM children
// are dropped: reminders travel in the row's Notifications field.
func Split(ev *Component, prodID string) Cards {
	var cards Cards
	cards.SharedSigned = Wrap(pick(ev, SharedSignedFields), prodID)

	enc := pick(ev, SharedEncryptedFields)
	for _, p := range ev.Props {
		if !takenKeys[strings.ToUpper(p.Name)] {
			enc.Props = append(enc.Props, p.Clone())
		}
	}
	cards.SharedEncrypted = Wrap(enc, prodID)

	if c := pick(ev, CalendarSignedFields); hasMoreThanRequired(c) {
		cards.CalendarSigned = Wrap(c, prodID)
	}
	if c := pick(ev, CalendarEncryptedFields); hasMoreThanRequired(c) {
		cards.CalendarEncrypted = Wrap(c, prodID)
	}
	if c := pick(ev, AttendeesEncryptedFields); hasMoreThanRequired(c) {
		// The answer lives in the clear attendee list, not in the card, as the
		// web client's fromInternalAttendee moves it.
		for i, p := range c.Props {
			if p.Name == "ATTENDEE" {
				c.Props[i] = p.WithoutParam("PARTSTAT")
			}
		}
		cards.Attendees = Wrap(c, prodID)
	}
	return cards
}

// singleValued properties keep the first value seen when cards are merged; the
// shared signed card is merged first, so it wins for the scheduling fields.
var singleValued = map[string]bool{
	"UID": true, "DTSTAMP": true, "DTSTART": true, "DTEND": true, "DURATION": true,
	"RRULE": true, "RECURRENCE-ID": true, "SEQUENCE": true, "SUMMARY": true,
	"DESCRIPTION": true, "LOCATION": true, "STATUS": true, "TRANSP": true,
	"ORGANIZER": true, "CREATED": true, "LAST-MODIFIED": true, "URL": true,
	"CLASS": true, "PRIORITY": true, "GEO": true, "COLOR": true,
	"X-PM-CONFERENCE-ID": true, "X-PM-CONFERENCE-URL": true, HoldProperty: true,
}

// Merge joins card VEVENTs into one, in the order given. Multi-valued
// properties (EXDATE, ATTENDEE, COMMENT and unknown ones) are unioned by their
// exact line.
func Merge(parts ...*Component) *Component {
	out := &Component{Name: "VEVENT"}
	seenSingle := map[string]bool{}
	seenLine := map[string]bool{}
	for _, part := range parts {
		if part == nil {
			continue
		}
		for _, p := range part.Props {
			name := strings.ToUpper(p.Name)
			if singleValued[name] {
				if seenSingle[name] {
					continue
				}
				seenSingle[name] = true
				out.Props = append(out.Props, p.Clone())
				continue
			}
			line := FormatLine(p)
			if seenLine[line] {
				continue
			}
			seenLine[line] = true
			out.Props = append(out.Props, p.Clone())
		}
		for _, ch := range part.Children {
			out.Children = append(out.Children, ch.Clone())
		}
	}
	return out
}
