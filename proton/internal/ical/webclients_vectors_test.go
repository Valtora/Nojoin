// Test vectors ported as data from Proton's web client,
// packages/shared/tests/calendar/serialize.spec.ts in
// https://github.com/ProtonMail/WebClients. No WebClients code is copied or run;
// the inputs and the expected card text are reproduced to check that this
// package splits an event as Proton's own client does.
//
// The web client is Copyright (c) Proton AG and licensed under the GNU General
// Public License version 3. This file is distributed with Nojoin under the GNU
// Affero General Public License version 3, which is compatible with it.

package ical

import (
	"strings"
	"testing"
)

// webClientsEvent is getVevent() from serialize.spec.ts: a UTC event with a
// title, a comment, three attendees and a colour.
func webClientsEvent() *Component {
	ev := &Component{Name: "VEVENT"}
	ev.Add(Property{Name: "UID", Value: "123"})
	ev.Add(Property{Name: "DTSTAMP", Value: "20191211T121212Z"})
	ev.Add(Property{Name: "DTSTART", Value: "20191211T121212Z"})
	ev.Add(Property{Name: "DTEND", Value: "20191212T121212Z"})
	ev.Add(Property{Name: "SUMMARY", Value: "my title"})
	ev.Add(Property{Name: "COMMENT", Value: "asdasd"})
	ev.Add(Property{Name: "COLOR", Value: "#3B8A54"})
	attendee := func(email, role, rsvp, partstat, token, cn string) Property {
		return Property{Name: "ATTENDEE", Value: "mailto:" + email, Params: []Param{
			{Name: "CUTYPE", Values: []string{"INDIVIDUAL"}},
			{Name: "ROLE", Values: []string{role}},
			{Name: "RSVP", Values: []string{rsvp}},
			{Name: "PARTSTAT", Values: []string{partstat}},
			{Name: "X-PM-TOKEN", Values: []string{token}},
			{Name: "CN", Values: []string{cn}},
		}}
	}
	ev.Add(attendee("james@bond.co.uk", "REQ-PARTICIPANT", "TRUE", "NEEDS-ACTION", "abc", "james@bond.co.uk"))
	ev.Add(attendee("dr.no@mi6.co.uk", "REQ-PARTICIPANT", "TRUE", "TENTATIVE", "bcd", "Dr No."))
	ev.Add(attendee("moneypenny@mi6.co.uk", "NON-PARTICIPANT", "FALSE", "ACCEPTED", "cde", "Miss Moneypenny"))
	ev.Children = []*Component{{Name: "VALARM", Props: []Property{{Name: "ACTION", Value: "DISPLAY"}, {Name: "TRIGGER", Value: "-PT15H"}}}}
	return ev
}

func TestSplitMatchesWebClientsSharedSignedCard(t *testing.T) {
	const prodID = "Proton Calendar"
	cards := Split(webClientsEvent(), prodID)
	// SharedEventContent[0].Data in the spec: wrap(..., dummyProdId).
	want := "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:Proton Calendar\r\n" +
		"BEGIN:VEVENT\r\nUID:123\r\nDTSTAMP:20191211T121212Z\r\nDTSTART:20191211T121212Z\r\nDTEND:20191212T121212Z\r\nEND:VEVENT" +
		"\r\nEND:VCALENDAR"
	if cards.SharedSigned != want {
		t.Fatalf("shared signed card:\n%q\nwant\n%q", cards.SharedSigned, want)
	}
	if strings.HasSuffix(cards.SharedSigned, "\r\n") {
		t.Fatal("cards carry no trailing line break")
	}
}

func TestSplitMatchesWebClientsCardPresence(t *testing.T) {
	cards := Split(webClientsEvent(), "Proton Calendar")
	// The spec expects CalendarEventContent to hold only the encrypted card:
	// with no STATUS, TRANSP or EXDATE the signed calendar card is dropped.
	if cards.CalendarSigned != "" {
		t.Fatalf("an empty calendar signed card was written: %q", cards.CalendarSigned)
	}
	enc, err := FirstEvent(cards.CalendarEncrypted)
	if err != nil {
		t.Fatal(err)
	}
	if enc.Get("COMMENT") == nil || enc.Get("COMMENT").Value != "asdasd" {
		t.Fatal("COMMENT belongs in the encrypted calendar card")
	}
	shared, err := FirstEvent(cards.SharedEncrypted)
	if err != nil {
		t.Fatal(err)
	}
	if shared.Get("SUMMARY") == nil || shared.Get("COMMENT") != nil || shared.Get("COLOR") != nil || shared.Get("ATTENDEE") != nil {
		t.Fatalf("shared encrypted card holds the wrong properties:\n%s", cards.SharedEncrypted)
	}
	if strings.Contains(cards.SharedEncrypted, "VALARM") || strings.Contains(cards.SharedSigned, "VALARM") {
		t.Fatal("reminders travel in Notifications, not in the shared cards")
	}
	att, err := FirstEvent(cards.Attendees)
	if err != nil {
		t.Fatal(err)
	}
	list := att.All("ATTENDEE")
	if len(list) != 3 {
		t.Fatalf("attendee card has %d attendees", len(list))
	}
	for i, token := range []string{"abc", "bcd", "cde"} {
		if list[i].Param("X-PM-TOKEN") != token {
			t.Fatalf("attendee %d lost its token", i)
		}
		if list[i].HasParam("PARTSTAT") {
			t.Fatal("PARTSTAT moves to the clear list, as fromInternalAttendee does")
		}
	}
	if att.Get("DTSTAMP") != nil {
		t.Fatal("the attendee card holds UID and ATTENDEE only")
	}
}

func TestSplitRoutesUnknownPropertiesAndTheHoldMarker(t *testing.T) {
	ev := webClientsEvent()
	ev.Add(Property{Name: "X-THIRD-PARTY", Value: "kept"})
	ev.Add(Property{Name: "STATUS", Value: "CONFIRMED"})
	ev.Add(Property{Name: HoldProperty, Value: "TRUE"})
	cards := Split(ev, "")
	if !strings.Contains(cards.SharedEncrypted, "X-THIRD-PARTY:kept") {
		t.Fatal("unknown properties go to the shared encrypted card")
	}
	if strings.Contains(cards.SharedEncrypted, HoldProperty) || strings.Contains(cards.SharedSigned, HoldProperty) {
		t.Fatal("the hold marker must never reach a card guests can read")
	}
	if !strings.Contains(cards.CalendarEncrypted, HoldProperty+":TRUE") {
		t.Fatal("the hold marker belongs in the encrypted calendar card")
	}
	if !strings.Contains(cards.CalendarSigned, "STATUS:CONFIRMED") {
		t.Fatal("STATUS belongs in the signed calendar card")
	}
	if strings.Contains(cards.SharedSigned, "PRODID") {
		t.Fatal("no PRODID line when none is given")
	}
}
