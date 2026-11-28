package calendar

import (
	"context"
	"encoding/json"
	"errors"
	"strings"

	"github.com/ProtonMail/gopenpgp/v2/crypto"

	"github.com/Valtora/Nojoin/proton/internal/ical"
	"github.com/Valtora/Nojoin/proton/internal/papi"
	"github.com/Valtora/Nojoin/proton/internal/protocol"
)

// ProdID marks Nojoin as the author of the last change to a card, as the web
// client marks its own.
const ProdID = "-//Nojoin//nojoin-proton//EN"

var jsonNull = json.RawMessage("null")

// errNoCalendarKey means an event has no calendar session key, so nothing can
// be added to its private calendar card.
var errNoCalendarKey = errors.New("event has no calendar key packet")

// sealCards splits a VEVENT and signs and encrypts its parts. Encrypted shared
// and attendee cards use sharedSK; the encrypted calendar card uses calSK.
func (a *Access) sealCards(ev *ical.Component, sharedSK, calSK *crypto.SessionKey) (shared, cal, att []Card, err error) {
	cards := ical.Split(ev, ProdID)
	s1, err := a.sealSigned(cards.SharedSigned)
	if err != nil {
		return nil, nil, nil, err
	}
	s2, err := a.sealEncrypted(cards.SharedEncrypted, sharedSK)
	if err != nil {
		return nil, nil, nil, err
	}
	shared = []Card{s1, s2}
	cal = []Card{}
	if cards.CalendarSigned != "" {
		c, err := a.sealSigned(cards.CalendarSigned)
		if err != nil {
			return nil, nil, nil, err
		}
		cal = append(cal, c)
	}
	if cards.CalendarEncrypted != "" {
		if calSK == nil {
			return nil, nil, nil, errNoCalendarKey
		}
		c, err := a.sealEncrypted(cards.CalendarEncrypted, calSK)
		if err != nil {
			return nil, nil, nil, err
		}
		cal = append(cal, c)
	}
	att = []Card{}
	if cards.Attendees != "" {
		c, err := a.sealEncrypted(cards.Attendees, sharedSK)
		if err != nil {
			return nil, nil, nil, err
		}
		att = append(att, c)
	}
	return shared, cal, att, nil
}

// clearAttendees builds the clear list for the VEVENT's attendees, keeping each
// existing attendee's live status and comment.
func clearAttendees(ev *ical.Component, existing []ClearAttendee) []ClearAttendeeIn {
	byToken := map[string]ClearAttendee{}
	for _, c := range existing {
		byToken[c.Token] = c
	}
	out := []ClearAttendeeIn{}
	for _, p := range ev.All("ATTENDEE") {
		token := p.Param("X-PM-TOKEN")
		in := ClearAttendeeIn{Token: token, Status: StatusCode(partstatName(p.Param("PARTSTAT"))), Comment: jsonNull}
		if c, ok := byToken[token]; ok {
			in.Status = c.Status
			if len(c.Comment) > 0 {
				in.Comment = c.Comment
			}
		}
		out = append(out, in)
	}
	return out
}

// buildBody seals a VEVENT into a sync body. A create carries fresh key
// packets; an update carries none and re-sends the row's reminders and
// colour.
func (a *Access) buildBody(ev *ical.Component, sharedSK, calSK *crypto.SessionKey, row *RawEvent, create bool) (*EventBody, error) {
	shared, cal, att, err := a.sealCards(ev, sharedSK, calSK)
	if err != nil {
		return nil, err
	}
	var existing []ClearAttendee
	notifications, color := jsonNull, jsonNull
	if row != nil {
		existing = row.ClearAttendees()
		notifications, color = row.NotificationsOrNull(), row.ColorOrNull()
	}
	body := &EventBody{
		Permissions:           1,
		IsOrganizer:           1,
		SharedEventContent:    shared,
		CalendarEventContent:  cal,
		AttendeesEventContent: att,
		Attendees:             clearAttendees(ev, existing),
		Notifications:         notifications,
		Color:                 color,
	}
	if create {
		if body.SharedKeyPacket, err = a.wrapSessionKey(sharedSK); err != nil {
			return nil, err
		}
		if calSK != nil {
			if body.CalendarKeyPacket, err = a.wrapSessionKey(calSK); err != nil {
				return nil, err
			}
		}
	}
	return body, nil
}

// putSync sends one sync batch and checks every entry's verdict. Top-level 1001
// is batch acceptance; each entry's own code decides. A pure delete answers
// 1001 with no entries, which is success.
func putSync(ctx context.Context, c *papi.Client, calendarID string, req SyncRequest) (*SyncResponse, error) {
	var resp SyncResponse
	if err := c.Put(ctx, "/calendar/v1/"+calendarID+"/events/sync", req, &resp); err != nil {
		var ae *papi.APIError
		if errors.As(err, &ae) {
			return nil, syncEntryError(ae.Code, ae.Message, ae.Status)
		}
		if errors.Is(err, papi.ErrAccessTokenExpired) {
			return nil, papi.ToProtocol(err, false)
		}
		return nil, papi.ToProtocol(err, papi.WasSent(err))
	}
	var failed *protocol.Error
	succeeded := 0
	for _, r := range resp.Responses {
		if r.Response.Code != papi.CodeSuccess && r.Response.Code != 0 {
			if failed == nil {
				failed = syncEntryError(r.Response.Code, r.Response.Error, 0)
			}
			continue
		}
		succeeded++
	}
	if failed != nil {
		if succeeded > 0 {
			// Part of the batch landed: the caller must re-read the event.
			failed.WithDetail("partial", true)
		}
		return &resp, failed
	}
	return &resp, nil
}

// syncEntryError maps a refused sync entry.
func syncEntryError(code int, message string, status int) *protocol.Error {
	msg := papi.MaskAddresses(message)
	lower := strings.ToLower(msg)
	var e *protocol.Error
	switch {
	case status == 429:
		e = protocol.Errorf(protocol.CodeRateLimited, "Proton asked Nojoin to slow down.").WithDetail("retry_after_seconds", 30)
	case code == papi.CodeDuplicateUID:
		e = protocol.Errorf(protocol.CodeConflict, "Proton already has an event with this UID.").WithDetail("reason", "uid_exists")
	case code == papi.CodeEventNotFound || code == papi.CodeNotFound:
		e = protocol.Errorf(protocol.CodeNotFound, "Proton has no such event.")
	case (code == papi.CodeInvalidValue || code == papi.CodeNotAllowed) && (strings.Contains(lower, "timezone") || strings.Contains(lower, "time zone") || strings.Contains(lower, "tzid") || strings.Contains(lower, "floating")):
		e = protocol.Errorf(protocol.CodeInvalidTime, "Proton refused the event's time: %s", msg)
	case code == papi.CodeInvalidValue && (strings.Contains(lower, "rrule") || strings.Contains(lower, "recurr")):
		f := "recurrence_rule"
		e = protocol.Errorf(protocol.CodeInvalidArgument, "Proton refused the recurrence rule: %s", msg)
		e.Field = &f
	case code == papi.CodeUnsupportedProp:
		e = protocol.Errorf(protocol.CodeInvalidArgument, "Proton refused a value: %s", msg)
	case status >= 500:
		e = protocol.Errorf(protocol.CodeProviderUnavailable, "Proton is unavailable.")
	default:
		e = protocol.Errorf(protocol.CodeProviderRejected, "Proton refused the change: %s", msg)
	}
	return e.WithProvider(code, status)
}
