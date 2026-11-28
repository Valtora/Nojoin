package ops

import (
	"bytes"
	"encoding/base64"
	"encoding/json"
	"strings"
	"testing"

	"github.com/ProtonMail/gopenpgp/v2/crypto"

	"github.com/Valtora/Nojoin/proton/internal/calendar"
	"github.com/Valtora/Nojoin/proton/internal/protocol"
)

func TestCreateSharedWithAttendeesWrapsTheKeyForProtonGuestsOnly(t *testing.T) {
	h := newHarness(t)
	h.create("shared-1@nojoin", meeting("Review", map[string]any{"attendees": attendees("bob@proton.me", "carol@example.org", "broken@example.net")}), true)
	body := h.lastSyncBody()
	if len(body.AddedProtonAttendees) != 1 || body.AddedProtonAttendees[0].Email != "bob@proton.me" {
		t.Fatalf("AddedProtonAttendees %+v", body.AddedProtonAttendees)
	}
	packet, _ := base64.StdEncoding.DecodeString(body.AddedProtonAttendees[0].AddressKeyPacket)
	bobKey, err := h.s.Bob.KeyRing().DecryptSessionKey(packet)
	if err != nil {
		t.Fatal("Bob cannot open his key packet:", err)
	}
	if !bytes.Equal(bobKey.Key, h.sessionKey(body.SharedKeyPacket).Key) {
		t.Fatal("Bob was handed a different key from the event's shared session key")
	}
	h.noCalls("/mail/")
}

func TestUpdateSharesKeysAndNamesRemovedAttendeesOnlyWhenAsked(t *testing.T) {
	h := newHarness(t)
	h.create("share-2@nojoin", meeting("Hold: Lunch", map[string]any{"hold": true, "attendees": attendees("bob@proton.me")}), false)
	var res writeResult
	// Confirming a hold with notify on: Bob was never invited, so the caller
	// names him in share_with and he gets the key.
	h.ok("update_event", map[string]any{"calendar_id": h.cal, "target": map[string]any{"uid": "share-2@nojoin"}, "scope": "single",
		"patch": map[string]any{"hold": false, "title": "Lunch"}, "share_with_attendees": true, "share_with": []string{"bob@proton.me"}}, &res)
	body := h.lastSyncBody()
	if len(body.AddedProtonAttendees) != 1 || body.AddedProtonAttendees[0].Email != "bob@proton.me" {
		t.Fatalf("added %+v", body.AddedProtonAttendees)
	}
	h.ok("update_event", map[string]any{"calendar_id": h.cal, "target": map[string]any{"uid": "share-2@nojoin"}, "scope": "single",
		"patch":                map[string]any{"add_attendees": attendees("carol@example.org"), "remove_attendees": []string{"bob@proton.me"}},
		"share_with_attendees": true}, &res)
	body = h.lastSyncBody()
	if len(body.RemovedAttendeeAddresses) != 1 || body.RemovedAttendeeAddresses[0] != "bob@proton.me" {
		t.Fatalf("removed %+v", body.RemovedAttendeeAddresses)
	}
	if len(body.AddedProtonAttendees) != 0 {
		t.Fatal("carol is not on Proton and bob was removed: nobody gets a key")
	}
	h.noCalls("/mail/")
}

func TestRespondToAnInvitation(t *testing.T) {
	h := newHarness(t)
	id := h.seedInvitation("invite-2@example.org", true)
	var got struct {
		Events []calendar.Record `json:"events"`
	}
	h.ok("get_event", map[string]any{"calendar_id": h.cal, "target": map[string]any{"uid": "invite-2@example.org"}}, &got)
	inv := got.Events[0]
	if inv.IsOrganiser || inv.MyResponse == nil || *inv.MyResponse != "needs_action" || inv.Organiser.IsSelf {
		t.Fatalf("invitation %+v", inv)
	}
	if !containsWarning(inv.DecryptWarnings, "signature_unverified") {
		t.Fatal("cards signed by Bob cannot be checked with the user's keys and say so")
	}
	h.s.ResetCalls()
	var res writeResult
	h.ok("respond", map[string]any{"calendar_id": h.cal, "target": map[string]any{"uid": "invite-2@example.org"}, "response": "accepted",
		"expected_revision": inv.Revision}, &res)
	if !res.Written || *res.Events[0].MyResponse != "accepted" || res.SeesAnswer == nil || !*res.SeesAnswer {
		t.Fatalf("respond %+v", res)
	}
	if res.Shown == nil || len(*res.Shown) != 1 || (*res.Shown)[0].Email != "bob@proton.me" || (*res.Shown)[0].Name != "Bob Example" {
		t.Fatalf("a Proton organiser is listed under shown_by_provider: %+v", res.Shown)
	}
	if h.countCalls("PUT", "/calendar/v1/"+h.cal+"/events/"+id+"/attendees/") != 1 {
		t.Fatal("the answer goes through the attendee endpoint")
	}
	if len(h.s.Personal) != 1 {
		t.Fatal("moving from not answered to accepted re-sends the personal part")
	}
	h.noCalls("/mail/", "/calendar/v1/"+h.cal+"/events/sync")
	h.ok("respond", map[string]any{"calendar_id": h.cal, "target": map[string]any{"uid": "invite-2@example.org"}, "response": "tentative"}, &res)
	if len(h.s.Personal) != 1 {
		t.Fatal("accepted to tentative leaves the personal part alone")
	}
	h.ok("respond", map[string]any{"calendar_id": h.cal, "target": map[string]any{"uid": "invite-2@example.org"}, "response": "tentative"}, &res)
	if res.Written {
		t.Fatal("the same answer again writes nothing")
	}
	h.create("own@nojoin", meeting("Mine", nil), false)
	if e := h.fail("respond", map[string]any{"calendar_id": h.cal, "target": map[string]any{"uid": "own@nojoin"}, "response": "accepted"}); e.Code != protocol.CodeNotAttendee {
		t.Fatalf("respond on own event gave %s", e.Code)
	}
	if e := h.fail("respond", map[string]any{"calendar_id": h.cal, "target": map[string]any{"uid": "invite-2@example.org", "recurrence_id": "20261204T090000Z"}, "response": "accepted"}); e.Code != protocol.CodeUnsupported {
		t.Fatalf("respond to one occurrence gave %s", e.Code)
	}
}

type sendResult struct {
	MessageID *string `json:"message_id"`
	Sent      []struct {
		Email  string `json:"email"`
		Scheme string `json:"scheme"`
	} `json:"sent"`
	Failed []struct {
		Email string `json:"email"`
		Error string `json:"error"`
	} `json:"failed"`
	Skipped []struct {
		Email  string `json:"email"`
		Reason string `json:"reason"`
	} `json:"skipped"`
}

type mailPackage struct {
	Type      int
	Addresses map[string]struct {
		Type                 int
		AttachmentKeyPackets map[string]string
	}
	AttachmentKeys map[string]struct{ Key, Algorithm string }
}

// attachmentFor decrypts the sent invite.ics as one recipient would.
func (h *harness) attachmentFor(email string, key *crypto.KeyRing) string {
	h.t.Helper()
	if len(h.s.Sent) != 1 {
		h.t.Fatalf("%d messages sent", len(h.s.Sent))
	}
	m := h.s.Sent[0]
	for _, raw := range m.Packages {
		var p mailPackage
		if err := json.Unmarshal(raw, &p); err != nil {
			h.t.Fatal(err)
		}
		if _, ok := p.Addresses[email]; !ok {
			continue
		}
		var sk *crypto.SessionKey
		if key != nil {
			packet, _ := base64.StdEncoding.DecodeString(p.Addresses[email].AttachmentKeyPackets[m.Attachment.ID])
			var err error
			if sk, err = key.DecryptSessionKey(packet); err != nil {
				h.t.Fatal(err)
			}
		} else {
			k := p.AttachmentKeys[m.Attachment.ID]
			raw, _ := base64.StdEncoding.DecodeString(k.Key)
			sk = crypto.NewSessionKeyFromToken(raw, k.Algorithm)
		}
		msg, err := sk.Decrypt(m.Attachment.DataPacket)
		if err != nil {
			h.t.Fatal(err)
		}
		// Unfold the 75-octet lines so assertions read whole properties.
		return strings.ReplaceAll(string(msg.GetBinary()), "\r\n ", "")
	}
	h.t.Fatalf("no package for %s", email)
	return ""
}

func TestSendInvitationEncryptsForProtonGuestsAndClearForOthers(t *testing.T) {
	h := newHarness(t)
	res := h.create("mail-1@nojoin", meeting("Hold: Planning", map[string]any{"hold": true, "attendees": attendees("bob@proton.me", "carol@example.org")}), false)
	h.s.ResetCalls()
	var out sendResult
	h.ok("send_itip", map[string]any{"calendar_id": h.cal, "method": "REQUEST", "ics": res.ITIP.ICS,
		"recipients": []map[string]any{{"email": "bob@proton.me"}, {"email": "carol@example.org"}, {"email": "Alice@Example.com"}},
		"subject":    "Invitation: Planning @ Tue 1 Dec 14:00", "body": "Alice Example has invited you to Planning."}, &out)
	if out.MessageID == nil || len(out.Sent) != 2 || out.Sent[0].Scheme != "internal" || out.Sent[1].Scheme != "clear" {
		t.Fatalf("sent %+v", out)
	}
	if len(out.Skipped) != 1 || out.Skipped[0].Reason != "is_you" {
		t.Fatalf("the user's own address is skipped: %+v", out.Skipped)
	}
	if h.countCalls("POST", "/mail/v4/messages") != 2 || h.countCalls("POST", "/mail/v4/attachments") != 1 {
		t.Fatal("a draft, one attachment, then send")
	}
	m := h.s.Sent[0]
	if m.Attachment.Filename != "invite.ics" || m.Attachment.MIMEType != "text/calendar; method=REQUEST; charset=utf-8" || len(m.Packages) != 2 {
		t.Fatalf("attachment %+v, %d packages", m.Attachment, len(m.Packages))
	}
	for _, email := range []string{"bob@proton.me", "carol@example.org"} {
		var key *crypto.KeyRing
		if email == "bob@proton.me" {
			key = h.s.Bob.KeyRing()
		}
		ics := h.attachmentFor(email, key)
		for _, want := range []string{"METHOD:REQUEST", "UID:mail-1@nojoin", "SUMMARY:Hold: Planning", "PARTSTAT=NEEDS-ACTION", "ORGANIZER"} {
			if !strings.Contains(ics, want) {
				t.Fatalf("%s's invitation lacks %s:\n%s", email, want, ics)
			}
		}
		for _, banned := range []string{"X-PM-TOKEN", "X-NOJOIN-HOLD", "COMMENT"} {
			if strings.Contains(ics, banned) {
				t.Fatalf("%s's invitation carries %s", email, banned)
			}
		}
	}
	if h.countCalls("PUT", "/calendar/v1/"+h.cal+"/events/sync") != 0 {
		t.Fatal("send_itip never writes the event")
	}
}

func TestSendCancellationOfOneOccurrence(t *testing.T) {
	h := newHarness(t)
	createSeries(h, "mail-2@nojoin")
	var res writeResult
	h.ok("cancel_event", map[string]any{"calendar_id": h.cal, "target": map[string]any{"uid": "mail-2@nojoin", "recurrence_id": "20261208T100000Z"}, "scope": "occurrence"}, &res)
	h.ok("send_itip", map[string]any{"calendar_id": h.cal, "method": "CANCEL", "ics": res.ITIP.ICS,
		"recipients": []map[string]any{{"email": "bob@proton.me", "name": "Bob Example"}}, "subject": "Cancelled: Weekly", "body": "Cancelled."}, nil)
	ics := h.attachmentFor("bob@proton.me", h.s.Bob.KeyRing())
	for _, want := range []string{"METHOD:CANCEL", "STATUS:CANCELLED", "RECURRENCE-ID;TZID=Europe/London:20261208T100000", "ATTENDEE;CN=Bob Example:mailto:bob@proton.me"} {
		if !strings.Contains(ics, want) {
			t.Fatalf("cancellation lacks %s:\n%s", want, ics)
		}
	}
	if strings.Contains(ics, "RRULE") {
		t.Fatal("an occurrence cancellation carries no RRULE")
	}
}

func TestSendReplyCarriesOnlyTheUserAndTheNote(t *testing.T) {
	h := newHarness(t)
	h.seedInvitation("mail-3@example.org", false)
	var res writeResult
	h.ok("respond", map[string]any{"calendar_id": h.cal, "target": map[string]any{"uid": "mail-3@example.org"}, "response": "declined"}, &res)
	if res.SeesAnswer == nil || *res.SeesAnswer || res.Shown == nil || len(*res.Shown) != 0 {
		t.Fatal("a non-Proton-to-Proton invitation's organiser does not see the answer")
	}
	h.ok("send_itip", map[string]any{"calendar_id": h.cal, "method": "REPLY", "ics": res.ITIP.ICS, "response": "declined", "note": "Away that week",
		"recipients": []map[string]any{{"email": "bob@proton.me"}}, "subject": "Declined: Bob's planning", "body": "Alice Example declined."}, nil)
	ics := h.attachmentFor("bob@proton.me", h.s.Bob.KeyRing())
	if strings.Count(ics, "ATTENDEE") != 1 || !strings.Contains(ics, "PARTSTAT=DECLINED") || !strings.Contains(ics, "mailto:alice@example.com") ||
		!strings.Contains(ics, "COMMENT:Away that week") || !strings.Contains(ics, "METHOD:REPLY") {
		t.Fatalf("reply:\n%s", ics)
	}
}

func TestSendITIPRefusals(t *testing.T) {
	h := newHarness(t)
	res := h.create("mail-4@nojoin", meeting("x", map[string]any{"attendees": attendees("bob@proton.me")}), false)
	base := func() map[string]any {
		return map[string]any{"calendar_id": h.cal, "method": "REQUEST", "ics": res.ITIP.ICS, "recipients": []map[string]any{{"email": "bob@proton.me"}}, "subject": "s", "body": "b"}
	}
	for field, change := range map[string]func(map[string]any){
		"method":     func(a map[string]any) { a["method"] = "PUBLISH" },
		"subject":    func(a map[string]any) { a["subject"] = "two\nlines" },
		"recipients": func(a map[string]any) { a["recipients"] = []map[string]any{{"email": "nobody"}} },
		"response":   func(a map[string]any) { a["method"] = "REPLY" },
		"ics":        func(a map[string]any) { a["ics"] = "not ics" },
	} {
		a := base()
		change(a)
		e := h.fail("send_itip", a)
		if e.Code != protocol.CodeInvalidArgument || *e.Field != field {
			t.Fatalf("%s: got %s %v", field, e.Code, e.Field)
		}
	}
	a := base()
	a["recipients"] = []map[string]any{{"email": "broken@example.net"}}
	var out sendResult
	h.ok("send_itip", a, &out)
	if len(out.Failed) != 1 || out.MessageID != nil || h.countCalls("POST", "/mail/v4/messages") != 0 {
		t.Fatalf("a recipient whose keys cannot be read is not mailed in the clear: %+v", out)
	}
}

func TestVersionListsEveryProtocolOperation(t *testing.T) {
	want := "cancel_event changes create_event get_event list_calendars list_events login_2fa login_start login_unlock logout refresh respond send_itip update_event validate version"
	if got := strings.Join(Operations(), " "); got != want {
		t.Fatalf("operations %s", got)
	}
}
