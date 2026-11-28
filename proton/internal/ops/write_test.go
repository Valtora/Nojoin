package ops

import (
	"bytes"
	"strings"
	"testing"

	"github.com/ProtonMail/gopenpgp/v2/crypto"

	"github.com/Valtora/Nojoin/proton/internal/fakeproton"
	"github.com/Valtora/Nojoin/proton/internal/ical"
	"github.com/Valtora/Nojoin/proton/internal/protocol"
)

func attendees(emails ...string) []map[string]any {
	var out []map[string]any
	for _, e := range emails {
		out = append(out, map[string]any{"email": e, "name": strings.Title(strings.Split(e, "@")[0]) + " Example"}) //nolint:staticcheck
	}
	return out
}

func TestCreateHoldWithAttendeesTellsNobody(t *testing.T) {
	h := newHarness(t)
	res := h.create("hold-1@nojoin", meeting("Hold: Design review", map[string]any{
		"hold": true, "description": "Agenda to follow.", "url": "https://meet.example.com/abc",
		"attendees": append(attendees("bob@proton.me", "carol@example.org"), map[string]any{"email": "alice@example.com"}),
	}), false)
	if !res.Written || !res.Verified || len(res.Events) != 1 {
		t.Fatalf("unexpected result %+v", res)
	}
	ev := res.Events[0]
	if !ev.IsHold || ev.Status != "tentative" || ev.Title != "Hold: Design review" || ev.ShowAs != "busy" {
		t.Fatalf("record %+v", ev)
	}
	if ev.ProviderEventID != "hold-1@nojoin" || ev.OccurrenceType != "single" || ev.SeriesKey != nil {
		t.Fatalf("identity %+v", ev)
	}
	if *ev.StartsAt != "2026-12-01T14:00:00Z" || *ev.StartTimeZone != "Europe/London" || *ev.MeetingURL != "https://meet.example.com/abc" {
		t.Fatalf("times %+v", ev)
	}
	if ev.AttendeeCount != 2 || ev.Organiser == nil || !ev.Organiser.IsSelf || !ev.IsOrganiser {
		t.Fatalf("the user's own address must not be an attendee: %+v", ev.Attendees)
	}
	for _, a := range ev.Attendees {
		if a.Response != "needs_action" || a.IsSelf {
			t.Fatalf("attendee %+v", a)
		}
	}
	// What was sent: no key sharing, no removals, no mail and no key lookups.
	raw := h.rawSync()
	evBody := raw["Events"].([]any)[0].(map[string]any)["Event"].(map[string]any)
	for _, k := range []string{"AddedProtonAttendees", "RemovedAttendeeAddresses"} {
		if _, ok := evBody[k]; ok {
			t.Fatalf("%s sent with share_with_attendees false", k)
		}
	}
	if evBody["Notifications"] != nil || evBody["Color"] != nil {
		t.Fatal("a new event inherits the calendar's reminders and colour (null)")
	}
	for _, a := range evBody["Attendees"].([]any) {
		if c, ok := a.(map[string]any)["Comment"]; !ok || c != nil {
			t.Fatal("clear attendees carry Comment null, as the web client sends it")
		}
	}
	h.noCalls("/mail/", "/core/v4/keys")
	cards := h.stored(h.s.Events(h.cal)[0])
	if !strings.Contains(cards["calendar_encrypted"], "X-NOJOIN-HOLD:TRUE") {
		t.Fatal("the hold marker belongs in the encrypted calendar card")
	}
	for _, k := range []string{"shared_signed", "shared_encrypted", "attendees", "calendar_signed"} {
		if strings.Contains(cards[k], "X-NOJOIN-HOLD") {
			t.Fatalf("the hold marker leaked into %s", k)
		}
	}
	if !strings.Contains(cards["calendar_signed"], "STATUS:CONFIRMED") || strings.Contains(cards["calendar_signed"], "TENTATIVE") {
		t.Fatal("holds are written STATUS:CONFIRMED")
	}
	if !strings.Contains(cards["shared_signed"], "SEQUENCE:0") || !strings.Contains(cards["shared_signed"], "ORGANIZER") {
		t.Fatalf("shared signed card:\n%s", cards["shared_signed"])
	}
	if strings.Contains(cards["attendees"], "PARTSTAT") || !strings.Contains(cards["attendees"], "X-PM-TOKEN=") {
		t.Fatalf("attendee card:\n%s", cards["attendees"])
	}
	if res.ITIP == nil || !strings.Contains(res.ITIP.ICS, "PARTSTAT=NEEDS-ACTION") {
		t.Fatal("the itip source carries each attendee's answer")
	}
	for _, card := range h.s.Events(h.cal)[0].SharedEvents {
		if got := signatureType(t, card.Signature); got != 0x00 {
			t.Fatalf("cards are signed as binary documents (type 0x00), as the web client signs them; got 0x%02x", got)
		}
	}
}

// signatureType reads the signature type octet of an armoured v4 signature.
func signatureType(t *testing.T, armoured string) byte {
	t.Helper()
	sig, err := crypto.NewPGPSignatureFromArmored(armoured)
	if err != nil {
		t.Fatal(err)
	}
	b := sig.GetBinary()
	if len(b) < 4 || b[0]&0x80 == 0 {
		t.Fatal("not an OpenPGP packet")
	}
	i := 1
	if b[0]&0x40 != 0 { // new packet format: one, two or five length octets
		switch {
		case b[1] < 192:
			i = 2
		case b[1] < 224:
			i = 3
		default:
			i = 6
		}
	} else { // old format: the low two bits give the length size
		i = 1 + []int{1, 2, 4, 0}[b[0]&0x03]
	}
	if b[i] != 4 {
		t.Fatalf("signature packet version %d", b[i])
	}
	return b[i+1]
}

func TestUpdateTitleKeepsSequenceKeysAndUnknownProperties(t *testing.T) {
	h := newHarness(t)
	ev := &ical.Component{Name: "VEVENT"}
	for _, line := range []string{"UID:seeded@example.com", "DTSTAMP:20261101T090000Z", "DTSTART;TZID=Europe/London:20261202T090000",
		"DTEND;TZID=Europe/London:20261202T100000", "SEQUENCE:3", "SUMMARY:Old", "X-THIRD-PARTY;X-A=1:kept", "STATUS:CONFIRMED"} {
		p, _ := ical.ParseLine(line)
		ev.Add(p)
	}
	h.s.Seed(h.cal, ev, fakeproton.SeedOptions{Organiser: true, Notifications: []byte(`[{"Type":1,"Trigger":"-PT15M"}]`), Color: "#3B8A54"})
	before := h.s.Events(h.cal)[0]
	var res writeResult
	h.ok("update_event", map[string]any{"calendar_id": h.cal, "target": map[string]any{"uid": "seeded@example.com"},
		"scope": "single", "patch": map[string]any{"title": "New", "location": "Room 2"}}, &res)
	if !res.Written || res.Events[0].Title != "New" || *res.Events[0].Location != "Room 2" || res.Events[0].Sequence != 3 {
		t.Fatalf("%+v", res.Events)
	}
	body := h.lastSyncBody()
	if body.SharedKeyPacket != "" || body.CalendarKeyPacket != "" {
		t.Fatal("an update must carry no key packets")
	}
	if string(body.Notifications) != `[{"Type":1,"Trigger":"-PT15M"}]` || string(body.Color) != `"#3B8A54"` {
		t.Fatalf("an update re-sends the row's reminders and colour as stored: %s %s", body.Notifications, body.Color)
	}
	after := h.s.Events(h.cal)[0]
	if after.SharedKeyPacket != before.SharedKeyPacket {
		t.Fatal("the stored key packet changed")
	}
	cards := h.stored(after)
	if !strings.Contains(cards["shared_encrypted"], "X-THIRD-PARTY;X-A=1:kept") {
		t.Fatalf("an unknown property was dropped:\n%s", cards["shared_encrypted"])
	}
	if !strings.Contains(cards["shared_signed"], "SEQUENCE:3") {
		t.Fatal("a title edit keeps SEQUENCE")
	}
	if res.Before[0].Title != "Old" {
		t.Fatal("before holds the state before the write")
	}
}

func TestUpdateTimeBumpsSequenceAndNoChangeWritesNothing(t *testing.T) {
	h := newHarness(t)
	created := h.create("time-1@nojoin", meeting("Standup", nil), false)
	var res writeResult
	h.ok("update_event", map[string]any{"calendar_id": h.cal, "target": map[string]any{"uid": "time-1@nojoin"}, "scope": "single",
		"patch": map[string]any{"start": timed("2026-12-01T15:00:00Z")}, "expected_revision": created.Events[0].Revision}, &res)
	ev := res.Events[0]
	if ev.Sequence != 1 || *ev.StartsAt != "2026-12-01T15:00:00Z" || *ev.EndsAt != "2026-12-01T16:00:00Z" {
		t.Fatalf("moving the start keeps the length and bumps SEQUENCE: %+v", ev)
	}
	syncs := h.countCalls("PUT", "/calendar/v1/"+h.cal+"/events/sync")
	h.ok("update_event", map[string]any{"calendar_id": h.cal, "target": map[string]any{"uid": "time-1@nojoin"}, "scope": "single",
		"patch": map[string]any{"title": "Standup"}}, &res)
	if res.Written || h.countCalls("PUT", "/calendar/v1/"+h.cal+"/events/sync") != syncs {
		t.Fatal("an update that changes nothing must write nothing")
	}
	e := h.fail("update_event", map[string]any{"calendar_id": h.cal, "target": map[string]any{"uid": "time-1@nojoin"}, "scope": "single",
		"patch": map[string]any{"title": "Late"}, "expected_revision": created.Events[0].Revision})
	if e.Code != protocol.CodeConflict || e.Details["current_revision"] != ev.Revision {
		t.Fatalf("a stale revision gave %s %v", e.Code, e.Details)
	}
}

func TestConfirmHoldRemovesTheMarker(t *testing.T) {
	h := newHarness(t)
	h.create("hold-2@nojoin", meeting("Hold: Lunch", map[string]any{"hold": true, "attendees": attendees("bob@proton.me")}), false)
	var res writeResult
	h.ok("update_event", map[string]any{"calendar_id": h.cal, "target": map[string]any{"uid": "hold-2@nojoin"}, "scope": "single",
		"patch": map[string]any{"hold": false, "title": "Lunch"}}, &res)
	if res.Events[0].IsHold || res.Events[0].Status != "confirmed" || res.Events[0].Sequence != 1 {
		t.Fatalf("%+v", res.Events[0])
	}
	if strings.Contains(h.stored(h.s.Events(h.cal)[0])["calendar_encrypted"], "X-NOJOIN-HOLD") {
		t.Fatal("confirming must remove the marker")
	}
	body := h.lastSyncBody()
	if len(body.AddedProtonAttendees) != 0 || len(body.RemovedAttendeeAddresses) != 0 {
		t.Fatal("no key sharing without share_with_attendees")
	}
	h.noCalls("/mail/", "/core/v4/keys")
}

func TestHoldOnAnEventWithoutAPrivatePartIsRefused(t *testing.T) {
	h := newHarness(t)
	ev, _ := ical.FirstEvent("BEGIN:VEVENT\r\nUID:app@example.com\r\nDTSTAMP:20261101T090000Z\r\nDTSTART:20261203T090000Z\r\nDTEND:20261203T100000Z\r\nSUMMARY:Made in the app\r\nEND:VEVENT")
	h.s.Seed(h.cal, ev, fakeproton.SeedOptions{Organiser: true, NoCalendarKey: true})
	e := h.fail("update_event", map[string]any{"calendar_id": h.cal, "target": map[string]any{"uid": "app@example.com"}, "scope": "single",
		"patch": map[string]any{"hold": true}})
	if e.Code != protocol.CodeUnsupported {
		t.Fatalf("got %s", e.Code)
	}
	var res writeResult
	h.ok("update_event", map[string]any{"calendar_id": h.cal, "target": map[string]any{"uid": "app@example.com"}, "scope": "single",
		"patch": map[string]any{"title": "Renamed"}}, &res)
	if res.Events[0].Title != "Renamed" {
		t.Fatal("a title change on such an event still works")
	}
}

func createSeries(h *harness, uid string) writeResult {
	return h.create(uid, map[string]any{
		"title": "Weekly", "start": timed("2026-12-01T10:00:00Z"), "end": timed("2026-12-01T11:00:00Z"),
		"recurrence_rule": "FREQ=WEEKLY;BYDAY=TU;COUNT=5", "attendees": attendees("bob@proton.me"),
	}, false)
}

func TestSeriesOccurrenceEditCreatesAnException(t *testing.T) {
	h := newHarness(t)
	created := createSeries(h, "series-1@nojoin")
	if len(created.Events) != 5 || created.Events[1].ProviderEventID != "series-1@nojoin#20261208T100000Z" || created.Events[1].OccurrenceType != "occurrence" {
		t.Fatalf("series read back as %+v", created.Events)
	}
	master := h.s.Events(h.cal)[0]
	var res writeResult
	h.ok("update_event", map[string]any{"calendar_id": h.cal, "target": map[string]any{"uid": "series-1@nojoin", "recurrence_id": "20261208T100000Z"},
		"scope": "occurrence", "patch": map[string]any{"title": "Weekly (moved)", "start": timed("2026-12-08T13:00:00Z"), "end": timed("2026-12-08T14:00:00Z")}}, &res)
	edited := recordByKey(t, res.Events, "series-1@nojoin#20261208T100000Z")
	if edited.OccurrenceType != "exception" || edited.Title != "Weekly (moved)" || *edited.StartsAt != "2026-12-08T13:00:00Z" {
		t.Fatalf("edited occurrence %+v", edited)
	}
	if recordByKey(t, res.Events, "series-1@nojoin#20261215T100000Z").Title != "Weekly" {
		t.Fatal("other occurrences keep the series' title")
	}
	if len(res.DeletedKeys) != 0 {
		t.Fatalf("an occurrence edit keeps its key: %v", res.DeletedKeys)
	}
	plain := recordByKey(t, res.Events, "series-1@nojoin#20261215T100000Z").ProviderData
	if edited.ProviderData.ExceptionEventID == nil || *edited.ProviderData.ExceptionEventID != edited.ProviderData.EventID ||
		edited.ProviderData.MasterEventID != plain.MasterEventID || plain.ExceptionEventID != nil || plain.UID != "series-1@nojoin" {
		t.Fatalf("provider_data names the master and the exception row: %+v / %+v", edited.ProviderData, plain)
	}
	rows := h.rowsByUID("series-1@nojoin")
	if len(rows) != 2 {
		t.Fatalf("expected master and exception, got %d rows", len(rows))
	}
	exRow := rows[1]
	cards := h.stored(exRow)
	if !strings.Contains(cards["shared_signed"], "RECURRENCE-ID;TZID=Europe/London:20261208T100000") || strings.Contains(cards["shared_signed"], "RRULE") {
		t.Fatalf("exception card:\n%s", cards["shared_signed"])
	}
	if !strings.Contains(cards["shared_signed"], "SEQUENCE:0") {
		t.Fatal("a new exception takes the master's SEQUENCE")
	}
	if !bytes.Equal(h.sessionKey(exRow.SharedKeyPacket).Key, h.sessionKey(master.SharedKeyPacket).Key) {
		t.Fatal("the exception must reuse the master's shared session key")
	}
	if !strings.Contains(cards["attendees"], "bob@proton.me") {
		t.Fatal("the exception keeps the series' attendees")
	}
}

func TestSeriesTimeChangeDropsExceptionsAndSeriesDateMoveIsRefused(t *testing.T) {
	h := newHarness(t)
	createSeries(h, "series-2@nojoin")
	h.ok("update_event", map[string]any{"calendar_id": h.cal, "target": map[string]any{"uid": "series-2@nojoin", "recurrence_id": "20261215T100000Z"},
		"scope": "occurrence", "patch": map[string]any{"title": "Special"}}, nil)
	var res writeResult
	h.ok("update_event", map[string]any{"calendar_id": h.cal, "target": map[string]any{"uid": "series-2@nojoin", "recurrence_id": "20261208T100000Z"},
		"scope": "series", "patch": map[string]any{"start": timed("2026-12-08T11:30:00Z"), "end": timed("2026-12-08T12:00:00Z")}}, &res)
	if res.Stats["exceptions_removed"] != 1 || len(h.rowsByUID("series-2@nojoin")) != 1 {
		t.Fatalf("exceptions should be deleted with a time change: %+v", res.Stats)
	}
	if len(res.Events) != 5 || *res.Events[0].StartsAt != "2026-12-01T11:30:00Z" || *res.Events[0].EndsAt != "2026-12-01T12:00:00Z" {
		t.Fatalf("the whole series moves by the same time of day and length: %+v", res.Events[0])
	}
	if len(res.DeletedKeys) != 5 || res.Events[0].ProviderEventID != "series-2@nojoin#20261201T113000Z" {
		t.Fatalf("old occurrence keys are deleted, new ones written: %v", res.DeletedKeys)
	}
	if res.Events[0].Sequence != 1 {
		t.Fatal("a series time change bumps SEQUENCE")
	}
	e := h.fail("update_event", map[string]any{"calendar_id": h.cal, "target": map[string]any{"uid": "series-2@nojoin", "recurrence_id": "20261208T113000Z"},
		"scope": "series", "patch": map[string]any{"start": timed("2026-12-09T11:30:00Z"), "end": timed("2026-12-09T12:00:00Z")}})
	if e.Code != protocol.CodeUnsupported {
		t.Fatalf("moving a series to another date gave %s", e.Code)
	}
	if e := h.fail("update_event", map[string]any{"calendar_id": h.cal, "target": map[string]any{"uid": "series-2@nojoin"}, "scope": "single",
		"patch": map[string]any{"title": "x"}}); e.Code != protocol.CodeInvalidArgument || *e.Field != "scope" {
		t.Fatalf("scope single on a series gave %s", e.Code)
	}
}

func TestSeriesTitleChangeKeepsOwnEditsOfOccurrences(t *testing.T) {
	h := newHarness(t)
	createSeries(h, "series-3@nojoin")
	h.ok("update_event", map[string]any{"calendar_id": h.cal, "target": map[string]any{"uid": "series-3@nojoin", "recurrence_id": "20261215T100000Z"},
		"scope": "occurrence", "patch": map[string]any{"title": "Special"}}, nil)
	h.ok("update_event", map[string]any{"calendar_id": h.cal, "target": map[string]any{"uid": "series-3@nojoin", "recurrence_id": "20261222T100000Z"},
		"scope": "occurrence", "patch": map[string]any{"location": "Room 9"}}, nil)
	var res writeResult
	h.ok("update_event", map[string]any{"calendar_id": h.cal, "target": map[string]any{"uid": "series-3@nojoin"},
		"scope": "series", "patch": map[string]any{"title": "Weekly sync"}}, &res)
	if recordByKey(t, res.Events, "series-3@nojoin#20261215T100000Z").Title != "Special" {
		t.Fatal("an occurrence's own title survives a series title change")
	}
	if recordByKey(t, res.Events, "series-3@nojoin#20261222T100000Z").Title != "Weekly sync" {
		t.Fatal("an occurrence that kept the old title follows the series")
	}
	if res.Stats["exceptions_removed"] != 0 || len(h.rowsByUID("series-3@nojoin")) != 3 {
		t.Fatal("a title change keeps exceptions")
	}
}

func TestCancelOccurrenceAddsAnExdate(t *testing.T) {
	h := newHarness(t)
	createSeries(h, "series-4@nojoin")
	h.ok("update_event", map[string]any{"calendar_id": h.cal, "target": map[string]any{"uid": "series-4@nojoin", "recurrence_id": "20261215T100000Z"},
		"scope": "occurrence", "patch": map[string]any{"title": "Special"}}, nil)
	var res writeResult
	h.ok("cancel_event", map[string]any{"calendar_id": h.cal, "target": map[string]any{"uid": "series-4@nojoin", "recurrence_id": "20261215T100000Z"}, "scope": "occurrence"}, &res)
	if len(res.DeletedKeys) != 1 || res.DeletedKeys[0] != "series-4@nojoin#20261215T100000Z" || len(res.Events) != 4 {
		t.Fatalf("deleted %v, %d left", res.DeletedKeys, len(res.Events))
	}
	if len(res.Before) != 1 || res.Before[0].Title != "Special" {
		t.Fatal("before holds the cancelled occurrence")
	}
	rows := h.rowsByUID("series-4@nojoin")
	if len(rows) != 1 {
		t.Fatal("the occurrence's exception row goes in the same batch")
	}
	cards := h.stored(rows[0])
	if !strings.Contains(cards["shared_signed"], "EXDATE;TZID=Europe/London:20261215T100000") ||
		!strings.Contains(cards["calendar_signed"], "EXDATE;TZID=Europe/London:20261215T100000") {
		t.Fatalf("EXDATE goes in both signed cards, as the web client routes it:\n%s\n%s", cards["shared_signed"], cards["calendar_signed"])
	}
	if !strings.Contains(cards["shared_signed"], "SEQUENCE:1") {
		t.Fatal("an EXDATE bumps SEQUENCE")
	}
}

func TestCancelSeriesDeletesTheWholeUIDGroupInOneBatch(t *testing.T) {
	h := newHarness(t)
	createSeries(h, "series-5@nojoin")
	h.ok("update_event", map[string]any{"calendar_id": h.cal, "target": map[string]any{"uid": "series-5@nojoin", "recurrence_id": "20261215T100000Z"},
		"scope": "occurrence", "patch": map[string]any{"title": "Special"}}, nil)
	h.s.ResetCalls()
	var res writeResult
	h.ok("cancel_event", map[string]any{"calendar_id": h.cal, "target": map[string]any{"uid": "series-5@nojoin"}, "scope": "series"}, &res)
	if len(h.rowsByUID("series-5@nojoin")) != 0 || h.countCalls("PUT", "/calendar/v1/"+h.cal+"/events/sync") != 1 {
		t.Fatal("master and exceptions go in one batch")
	}
	entries := h.rawSync()["Events"].([]any)
	if len(entries) != 2 {
		t.Fatalf("%d delete entries", len(entries))
	}
	if len(res.DeletedKeys) != 5 || len(res.Before) != 5 {
		t.Fatalf("deleted %d, before %d", len(res.DeletedKeys), len(res.Before))
	}
	h.noCalls("/mail/")
}

func TestWritesRefuseSomeoneElsesInvitationAndReadOnlyCalendars(t *testing.T) {
	h := newHarness(t)
	h.seedInvitation("invite-1@example.org", true)
	target := map[string]any{"uid": "invite-1@example.org"}
	if e := h.fail("update_event", map[string]any{"calendar_id": h.cal, "target": target, "scope": "single", "patch": map[string]any{"title": "x"}}); e.Code != protocol.CodeNotOrganiser {
		t.Fatalf("update gave %s", e.Code)
	}
	if e := h.fail("cancel_event", map[string]any{"calendar_id": h.cal, "target": target, "scope": "single"}); e.Code != protocol.CodeNotOrganiser {
		t.Fatalf("cancel gave %s", e.Code)
	}
	ro := h.s.AddCalendar("Shared with me", 0, 96)
	if e := h.fail("create_event", map[string]any{"calendar_id": ro.ID, "uid": "ro@nojoin", "event": meeting("x", nil)}); e.Code != protocol.CodeNotWritable || e.Details["reason"] != "calendar_read_only" {
		t.Fatalf("read-only calendar gave %s", e.Code)
	}
	if e := h.fail("create_event", map[string]any{"calendar_id": h.cal, "uid": "dup@nojoin", "event": meeting("x", map[string]any{"description": strings.Repeat("d", 3001)})}); e.Code != protocol.CodeInvalidArgument || *e.Field != "event.description" {
		t.Fatalf("long description gave %s", e.Code)
	}
	h.create("dup@nojoin", meeting("First", nil), false)
	if e := h.fail("create_event", map[string]any{"calendar_id": h.cal, "uid": "dup@nojoin", "event": meeting("Second", nil)}); e.Code != protocol.CodeConflict || e.Details["reason"] != "uid_exists" {
		t.Fatalf("duplicate UID gave %s", e.Code)
	}
}

func TestNoWriteOperationTouchesMailOrSharesKeysUnlessAsked(t *testing.T) {
	h := newHarness(t)
	h.create("quiet-1@nojoin", meeting("Quiet", map[string]any{"attendees": attendees("bob@proton.me", "carol@example.org")}), false)
	createSeries(h, "quiet-2@nojoin")
	for _, args := range []map[string]any{
		{"target": map[string]any{"uid": "quiet-1@nojoin"}, "scope": "single", "patch": map[string]any{"title": "Quieter", "add_attendees": attendees("dan@proton.me"), "remove_attendees": []string{"carol@example.org"}}},
		{"target": map[string]any{"uid": "quiet-1@nojoin"}, "scope": "single", "patch": map[string]any{"start": timed("2026-12-02T14:00:00Z"), "end": timed("2026-12-02T15:00:00Z"), "hold": true}},
		{"target": map[string]any{"uid": "quiet-2@nojoin", "recurrence_id": "20261208T100000Z"}, "scope": "occurrence", "patch": map[string]any{"title": "One off", "add_attendees": attendees("erin@proton.me")}},
		{"target": map[string]any{"uid": "quiet-2@nojoin"}, "scope": "series", "patch": map[string]any{"remove_attendees": []string{"bob@proton.me"}}},
	} {
		args["calendar_id"] = h.cal
		h.ok("update_event", args, nil)
	}
	h.ok("cancel_event", map[string]any{"calendar_id": h.cal, "target": map[string]any{"uid": "quiet-2@nojoin", "recurrence_id": "20261215T100000Z"}, "scope": "occurrence"}, nil)
	h.ok("cancel_event", map[string]any{"calendar_id": h.cal, "target": map[string]any{"uid": "quiet-1@nojoin"}, "scope": "single"}, nil)
	h.ok("cancel_event", map[string]any{"calendar_id": h.cal, "target": map[string]any{"uid": "quiet-2@nojoin"}, "scope": "series"}, nil)
	h.noCalls("/mail/", "/core/v4/keys")
	syncs := 0
	for _, c := range h.s.Calls() {
		if !strings.HasSuffix(c.Path, "/events/sync") {
			continue
		}
		syncs++
		for _, field := range []string{"AddedProtonAttendees", "RemovedAttendeeAddresses"} {
			if strings.Contains(string(c.Body), field) {
				t.Fatalf("%s sent without share_with_attendees:\n%s", field, c.Body)
			}
		}
	}
	if syncs != 9 {
		t.Fatalf("expected 9 sync writes, saw %d", syncs)
	}
}

func TestWriteFailuresMap(t *testing.T) {
	h := newHarness(t)
	sync := "/calendar/v1/" + h.cal + "/events/sync"
	h.s.Fail("PUT", sync, 401, 401, "Invalid access token")
	if e := h.fail("create_event", map[string]any{"calendar_id": h.cal, "uid": "f1@nojoin", "event": meeting("x", nil)}); e.Code != protocol.CodeAccessTokenExpired {
		t.Fatalf("401 on the write gave %s", e.Code)
	}
	h.s.Fail("PUT", sync, 0, 0, "")
	e := h.fail("create_event", map[string]any{"calendar_id": h.cal, "uid": "f2@nojoin", "event": meeting("x", nil)})
	if e.Code != protocol.CodeProviderUnavailable || e.Details["outcome_unknown"] != true {
		t.Fatalf("a lost answer to a write gave %s %v", e.Code, e.Details)
	}
	h.s.Fail("PUT", sync, 503, 0, "down")
	if e := h.fail("create_event", map[string]any{"calendar_id": h.cal, "uid": "f3@nojoin", "event": meeting("x", nil)}); e.Code != protocol.CodeProviderUnavailable {
		t.Fatalf("503 gave %s", e.Code)
	}
	if len(h.s.Events(h.cal)) != 0 {
		t.Fatal("none of those writes should have landed")
	}
	h.s.Fail("GET", "/calendar/v1/"+h.cal+"/events", 500, 0, "read failed")
	res := h.create("f4@nojoin", meeting("Read back fails", nil), false)
	if !res.Written || res.Verified || !containsWarning(res.Warnings, "read_back_failed") || len(res.Events) != 1 || res.Events[0].Title != "Read back fails" {
		t.Fatalf("a failed read-back after a write %+v", res)
	}
	h.s.FailLater("GET", "/calendar/v1/"+h.cal+"/events", 1, 500, 0, "read failed")
	var upd writeResult
	h.ok("update_event", map[string]any{"calendar_id": h.cal, "target": map[string]any{"uid": "f4@nojoin"}, "scope": "single", "patch": map[string]any{"title": "Sent title"}}, &upd)
	if upd.Verified || !containsWarning(upd.Warnings, "read_back_failed") || upd.Events[0].Title != "Sent title" {
		t.Fatalf("an unverified update reports what was sent: %+v", upd)
	}
	h.s.ExpireAccess(h.sess.UID)
	if e := h.fail("update_event", map[string]any{"calendar_id": h.cal, "target": map[string]any{"uid": "f4@nojoin"}, "scope": "single", "patch": map[string]any{"title": "y"}}); e.Code != protocol.CodeAccessTokenExpired {
		t.Fatalf("an expired token before any write gave %s", e.Code)
	}
}
