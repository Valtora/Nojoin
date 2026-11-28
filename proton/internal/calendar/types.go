// Package calendar reads and writes Proton Calendar events: the wire types,
// the calendar keychain, card encryption, recurrence expansion and the
// operations the helper exposes.
//
// Wire shapes follow proton-cal's docs/api.md and pkg/event (Unlicense) and
// carbonate's internal/calendar (MIT, Copyright (c) 2026 Mitchell de Jong),
// checked against Proton's web client (GPL-3.0, read as a reference only).
package calendar

import (
	"bytes"
	"encoding/json"
	"strconv"
	"time"
)

// Now is the clock, replaceable in tests.
var Now = func() time.Time { return time.Now().UTC() }

// ITIP is the opaque source send_itip builds mail from.
type ITIP struct {
	ICS      string `json:"ics"`
	UID      string `json:"uid"`
	Sequence int    `json:"sequence"`
}

// Target names an event or one occurrence.
type Target struct {
	UID          string  `json:"uid"`
	RecurrenceID *string `json:"recurrence_id"`
}

// Card types, as Proton numbers them.
const (
	CardClear              = 0
	CardEncrypted          = 1
	CardSigned             = 2
	CardEncryptedAndSigned = 3
)

// Attendee statuses in Proton's clear list.
const (
	StatusNeedsAction = 0
	StatusTentative   = 1
	StatusDeclined    = 2
	StatusAccepted    = 3
)

// Calendar member permission bits.
const (
	PermWrite = 16
)

// Card is one part of an event as Proton stores it.
type Card struct {
	Type      int    `json:"Type"`
	Data      string `json:"Data"`
	Signature string `json:"Signature,omitempty"`
	Author    string `json:"Author,omitempty"`
}

// Encrypted reports whether the card's data is an encrypted packet.
func (c Card) Encrypted() bool { return c.Type == CardEncrypted || c.Type == CardEncryptedAndSigned }

// ClearAttendee is one entry of the clear attendee list.
type ClearAttendee struct {
	ID         string          `json:"ID,omitempty"`
	Token      string          `json:"Token"`
	Status     int             `json:"Status"`
	UpdateTime int64           `json:"UpdateTime,omitempty"`
	Comment    json.RawMessage `json:"Comment,omitempty"`
}

// Flag decodes a field Proton sends as a number or a boolean.
type Flag bool

// UnmarshalJSON accepts true, false, 0, 1 and null.
func (f *Flag) UnmarshalJSON(b []byte) error {
	b = bytes.TrimSpace(b)
	switch string(b) {
	case "true":
		*f = true
	case "false", "null", "":
		*f = false
	default:
		n, err := strconv.ParseFloat(string(b), 64)
		if err != nil {
			return err
		}
		*f = n != 0
	}
	return nil
}

// RawEvent is one event row as Proton returns it.
type RawEvent struct {
	ID                   string          `json:"ID"`
	UID                  string          `json:"UID"`
	CalendarID           string          `json:"CalendarID"`
	SharedEventID        string          `json:"SharedEventID"`
	CreateTime           int64           `json:"CreateTime"`
	ModifyTime           int64           `json:"ModifyTime"`
	LastEditTime         int64           `json:"LastEditTime"`
	StartTime            int64           `json:"StartTime"`
	EndTime              int64           `json:"EndTime"`
	StartTimezone        string          `json:"StartTimezone"`
	EndTimezone          string          `json:"EndTimezone"`
	FullDay              int             `json:"FullDay"`
	RRule                *string         `json:"RRule"`
	RecurrenceID         *int64          `json:"RecurrenceID"`
	Exdates              []int64         `json:"Exdates"`
	Permissions          int             `json:"Permissions"`
	IsOrganizer          Flag            `json:"IsOrganizer"`
	IsProtonProtonInvite Flag            `json:"IsProtonProtonInvite"`
	IsPersonalSingleEdit Flag            `json:"IsPersonalSingleEdit"`
	Author               string          `json:"Author"`
	SharedKeyPacket      string          `json:"SharedKeyPacket"`
	CalendarKeyPacket    string          `json:"CalendarKeyPacket"`
	AddressKeyPacket     string          `json:"AddressKeyPacket"`
	AddressID            string          `json:"AddressID"`
	SharedEvents         []Card          `json:"SharedEvents"`
	CalendarEvents       []Card          `json:"CalendarEvents"`
	AttendeesEvents      []Card          `json:"AttendeesEvents"`
	PersonalEvents       []Card          `json:"PersonalEvents"`
	Attendees            []ClearAttendee `json:"Attendees"`
	AttendeesInfo        *struct {
		Attendees     []ClearAttendee `json:"Attendees"`
		MoreAttendees int             `json:"MoreAttendees"`
	} `json:"AttendeesInfo"`
	Notifications json.RawMessage `json:"Notifications"`
	Color         *string         `json:"Color"`
}

// ClearAttendees returns the clear attendee list from whichever field holds it.
func (r *RawEvent) ClearAttendees() []ClearAttendee {
	if r.AttendeesInfo != nil && len(r.AttendeesInfo.Attendees) > 0 {
		return r.AttendeesInfo.Attendees
	}
	return r.Attendees
}

// NotificationsOrNull returns the row's reminders as sent, keeping Proton's
// tri-state: null (calendar default), [] (none) or a list.
func (r *RawEvent) NotificationsOrNull() json.RawMessage {
	n := bytes.TrimSpace(r.Notifications)
	if len(n) == 0 {
		return json.RawMessage("null")
	}
	return json.RawMessage(n)
}

// ColorOrNull returns the row's colour as JSON.
func (r *RawEvent) ColorOrNull() json.RawMessage {
	if r.Color == nil || *r.Color == "" {
		return json.RawMessage("null")
	}
	b, _ := json.Marshal(*r.Color)
	return b
}

// EventBody is the Event object inside a sync entry. The content arrays must be
// [] rather than null; Notifications, Color and Attendees are re-sent on
// update so they are not wiped.
type EventBody struct {
	Permissions              int               `json:"Permissions"`
	IsOrganizer              int               `json:"IsOrganizer,omitempty"`
	SharedKeyPacket          string            `json:"SharedKeyPacket,omitempty"`
	CalendarKeyPacket        string            `json:"CalendarKeyPacket,omitempty"`
	SharedEventContent       []Card            `json:"SharedEventContent"`
	CalendarEventContent     []Card            `json:"CalendarEventContent"`
	AttendeesEventContent    []Card            `json:"AttendeesEventContent"`
	Attendees                []ClearAttendeeIn `json:"Attendees"`
	Notifications            json.RawMessage   `json:"Notifications"`
	Color                    json.RawMessage   `json:"Color"`
	AddedProtonAttendees     []AddedAttendee   `json:"AddedProtonAttendees,omitempty"`
	RemovedAttendeeAddresses []string          `json:"RemovedAttendeeAddresses,omitempty"`
}

// ClearAttendeeIn is a clear attendee entry as sent in a sync body.
type ClearAttendeeIn struct {
	Token   string          `json:"Token"`
	Status  int             `json:"Status"`
	Comment json.RawMessage `json:"Comment"`
}

// AddedAttendee hands a Proton guest the shared session key.
type AddedAttendee struct {
	Email            string `json:"Email"`
	AddressKeyPacket string `json:"AddressKeyPacket"`
}

// SyncEntry is one element of a sync batch: create (Overwrite + Event), update
// (ID + Event) or delete (ID only).
type SyncEntry struct {
	ID        string     `json:"ID,omitempty"`
	Overwrite *int       `json:"Overwrite,omitempty"`
	Event     *EventBody `json:"Event,omitempty"`
}

// SyncRequest is the PUT .../events/sync body.
type SyncRequest struct {
	MemberID string      `json:"MemberID"`
	IsImport *int        `json:"IsImport,omitempty"`
	Events   []SyncEntry `json:"Events"`
}

// SyncResponse is the sync answer.
type SyncResponse struct {
	Code      int `json:"Code"`
	Responses []struct {
		Index    int `json:"Index"`
		Response struct {
			Code  int       `json:"Code"`
			Error string    `json:"Error"`
			Event *RawEvent `json:"Event"`
		} `json:"Response"`
	} `json:"Responses"`
}

// CalendarMember is our membership of a calendar.
type CalendarMember struct {
	ID          string `json:"ID"`
	CalendarID  string `json:"CalendarID"`
	AddressID   string `json:"AddressID"`
	Email       string `json:"Email"`
	Permissions int    `json:"Permissions"`
	Name        string `json:"Name"`
	Description string `json:"Description"`
	Color       string `json:"Color"`
	Display     int    `json:"Display"`
	Flags       int    `json:"Flags"`
}

// CalendarRow is one entry of GET /calendar/v1.
type CalendarRow struct {
	ID          string           `json:"ID"`
	Type        int              `json:"Type"`
	Name        string           `json:"Name"`
	Description string           `json:"Description"`
	Color       string           `json:"Color"`
	Display     int              `json:"Display"`
	Flags       int              `json:"Flags"`
	Members     []CalendarMember `json:"Members"`
}

// CalendarKey is one calendar private key.
type CalendarKey struct {
	ID           string `json:"ID"`
	CalendarID   string `json:"CalendarID"`
	PassphraseID string `json:"PassphraseID"`
	PrivateKey   string `json:"PrivateKey"`
	Flags        int    `json:"Flags"`
}

// MemberPassphrase is the calendar passphrase encrypted to one member.
type MemberPassphrase struct {
	MemberID   string `json:"MemberID"`
	Passphrase string `json:"Passphrase"`
	Signature  string `json:"Signature"`
}

// Bootstrap is GET /calendar/v2/{id}/bootstrap.
type Bootstrap struct {
	Keys       []CalendarKey `json:"Keys"`
	Passphrase *struct {
		ID                string             `json:"ID"`
		MemberPassphrases []MemberPassphrase `json:"MemberPassphrases"`
	} `json:"Passphrase"`
	Members          []CalendarMember `json:"Members"`
	CalendarSettings json.RawMessage  `json:"CalendarSettings"`
}

// UserSettings is the calendar part of GET /settings/calendar.
type UserSettings struct {
	DefaultCalendarID string `json:"DefaultCalendarID"`
	PrimaryTimezone   string `json:"PrimaryTimezone"`
}
