package ops

import (
	"context"
	"encoding/base64"
	"encoding/json"
	"io"
	"os"
	"strings"
	"testing"
	"time"

	"github.com/ProtonMail/gopenpgp/v2/crypto"
	"github.com/sirupsen/logrus"

	"github.com/Valtora/Nojoin/proton/internal/calendar"
	"github.com/Valtora/Nojoin/proton/internal/fakeproton"
	"github.com/Valtora/Nojoin/proton/internal/ical"
	"github.com/Valtora/Nojoin/proton/internal/protocol"
)

var fixedNow = time.Date(2026, 11, 28, 12, 0, 0, 0, time.UTC)

func TestMain(m *testing.M) {
	logrus.SetOutput(io.Discard)
	calendar.Now = func() time.Time { return fixedNow }
	os.Exit(m.Run())
}

type harness struct {
	t    *testing.T
	s    *fakeproton.Server
	sess protocol.Session
	cal  string
}

func newHarness(t *testing.T, opts ...func(*fakeproton.Server)) *harness {
	t.Helper()
	s := fakeproton.New()
	t.Cleanup(s.Close)
	for _, o := range opts {
		o(s)
	}
	uid, acc, ref, kp := s.Session()
	return &harness{t: t, s: s, sess: protocol.Session{UID: uid, AccessToken: acc, RefreshToken: ref, KeyPassphrase: kp}, cal: s.DefaultCalendar().ID}
}

func (h *harness) request(op string, sess *protocol.Session, args any) protocol.Response {
	h.t.Helper()
	base := h.s.URL
	req := map[string]any{"v": 1, "op": op, "client": map[string]any{"api_base_url": base, "user_agent": "Nojoin/test"}}
	if sess != nil {
		req["session"] = sess
	}
	if args != nil {
		req["args"] = args
	}
	raw, err := json.Marshal(req)
	if err != nil {
		h.t.Fatal(err)
	}
	// Round-trip the response through JSON, as the caller sees it.
	resp := Run(context.Background(), raw)
	var out protocol.Response
	if err := json.Unmarshal(Encode(resp, 32<<20), &out); err != nil {
		h.t.Fatal(err)
	}
	out.Result = resp.Result
	return out
}

func (h *harness) ok(op string, args any, out any) {
	h.t.Helper()
	h.okAs(op, &h.sess, args, out)
}

func (h *harness) okAs(op string, sess *protocol.Session, args any, out any) {
	h.t.Helper()
	resp := h.request(op, sess, args)
	if !resp.OK {
		h.t.Fatalf("%s failed: %+v %v", op, resp.Error, resp.Error.Details)
	}
	if out != nil {
		b, _ := json.Marshal(resp.Result)
		if err := json.Unmarshal(b, out); err != nil {
			h.t.Fatal(err)
		}
	}
}

func (h *harness) fail(op string, args any) *protocol.Error {
	h.t.Helper()
	return h.failAs(op, &h.sess, args)
}

func (h *harness) failAs(op string, sess *protocol.Session, args any) *protocol.Error {
	h.t.Helper()
	resp := h.request(op, sess, args)
	if resp.OK {
		h.t.Fatalf("%s should have failed", op)
	}
	return resp.Error
}

// noCalls fails when any recorded request path starts with a prefix.
func (h *harness) noCalls(prefixes ...string) {
	h.t.Helper()
	for _, c := range h.s.Calls() {
		for _, p := range prefixes {
			if strings.HasPrefix(c.Path, p) {
				h.t.Fatalf("unexpected %s %s", c.Method, c.Path)
			}
		}
	}
}

func (h *harness) countCalls(method, prefix string) int {
	n := 0
	for _, c := range h.s.Calls() {
		if c.Method == method && strings.HasPrefix(c.Path, prefix) {
			n++
		}
	}
	return n
}

func (h *harness) lastSyncBody() fakeproton.SyncBody {
	h.t.Helper()
	bodies := h.s.SyncBodies()
	if len(bodies) == 0 {
		h.t.Fatal("no sync body was sent")
	}
	return bodies[len(bodies)-1]
}

// rawSync returns the last sync request as sent.
func (h *harness) rawSync() map[string]any {
	h.t.Helper()
	calls := h.s.Calls()
	for i := len(calls) - 1; i >= 0; i-- {
		if strings.HasSuffix(calls[i].Path, "/events/sync") {
			var m map[string]any
			_ = json.Unmarshal(calls[i].Body, &m)
			return m
		}
	}
	h.t.Fatal("no sync request")
	return nil
}

// sessionKey opens a key packet with the calendar key.
func (h *harness) sessionKey(packet string) *crypto.SessionKey {
	h.t.Helper()
	raw, err := base64.StdEncoding.DecodeString(packet)
	if err != nil {
		h.t.Fatal(err)
	}
	sk, err := h.s.DefaultCalendar().CalendarKeyRing().DecryptSessionKey(raw)
	if err != nil {
		h.t.Fatal(err)
	}
	return sk
}

func (h *harness) decrypt(card calendar.Card, sk *crypto.SessionKey) string {
	h.t.Helper()
	if !card.Encrypted() {
		return card.Data
	}
	data, _ := base64.StdEncoding.DecodeString(card.Data)
	msg, err := sk.Decrypt(data)
	if err != nil {
		h.t.Fatal(err)
	}
	return string(msg.GetBinary())
}

// stored returns the decrypted cards of a stored row: shared signed, shared
// encrypted, calendar signed, calendar encrypted, attendees.
func (h *harness) stored(ev *calendar.RawEvent) map[string]string {
	h.t.Helper()
	out := map[string]string{}
	shared := h.sessionKey(ev.SharedKeyPacket)
	var cal *crypto.SessionKey
	if ev.CalendarKeyPacket != "" {
		cal = h.sessionKey(ev.CalendarKeyPacket)
	}
	for _, c := range ev.SharedEvents {
		if c.Encrypted() {
			out["shared_encrypted"] = h.decrypt(c, shared)
		} else {
			out["shared_signed"] = c.Data
		}
	}
	for _, c := range ev.CalendarEvents {
		if c.Encrypted() {
			out["calendar_encrypted"] = h.decrypt(c, cal)
		} else {
			out["calendar_signed"] = c.Data
		}
	}
	for _, c := range ev.AttendeesEvents {
		out["attendees"] = h.decrypt(c, shared)
	}
	return out
}

func (h *harness) rowsByUID(uid string) []*calendar.RawEvent {
	var out []*calendar.RawEvent
	for _, ev := range h.s.Events(h.cal) {
		if ev.UID == uid {
			out = append(out, ev)
		}
	}
	return out
}

func timed(at string) map[string]string {
	return map[string]string{"date_time": at, "time_zone": "Europe/London"}
}

type writeResult struct {
	Written     bool               `json:"written"`
	Verified    bool               `json:"verified"`
	Events      []calendar.Record  `json:"events"`
	DeletedKeys []string           `json:"deleted_keys"`
	Before      []calendar.Record  `json:"before"`
	Revision    *string            `json:"revision"`
	Warnings    []string           `json:"warnings"`
	Stats       map[string]int     `json:"stats"`
	ITIP        *calendar.ITIP     `json:"itip"`
	SeesAnswer  *bool              `json:"organiser_sees_answer"`
	Shown       *[]calendar.Person `json:"shown_by_provider"`
}

func (h *harness) create(uid string, event map[string]any, share bool) writeResult {
	h.t.Helper()
	var res writeResult
	h.ok("create_event", map[string]any{"calendar_id": h.cal, "uid": uid, "event": event, "share_with_attendees": share}, &res)
	return res
}

func meeting(title string, extra map[string]any) map[string]any {
	ev := map[string]any{
		"title": title,
		"start": timed("2026-12-01T14:00:00Z"),
		"end":   timed("2026-12-01T15:00:00Z"),
	}
	for k, v := range extra {
		ev[k] = v
	}
	return ev
}

func recordByKey(t *testing.T, recs []calendar.Record, key string) calendar.Record {
	t.Helper()
	for _, r := range recs {
		if r.ProviderEventID == key {
			return r
		}
	}
	t.Fatalf("no record %s among %d", key, len(recs))
	return calendar.Record{}
}

// seedInvitation stores an invitation from Bob to Alice and Carol.
func (h *harness) seedInvitation(uid string, protonInvite bool) string {
	ev := &ical.Component{Name: "VEVENT"}
	for _, line := range []string{"UID:" + uid, "DTSTAMP:20261101T090000Z", "DTSTART:20261204T090000Z", "DTEND:20261204T100000Z",
		"SEQUENCE:1", "SUMMARY:Bob's planning", "ORGANIZER;CN=Bob Example:mailto:bob@proton.me",
		"ATTENDEE;CN=Alice Example;ROLE=REQ-PARTICIPANT;X-PM-TOKEN=" + calendar.AttendeeToken(uid, "alice@example.com") + ":mailto:alice@example.com",
		"ATTENDEE;CN=Carol Example;ROLE=OPT-PARTICIPANT;X-PM-TOKEN=" + calendar.AttendeeToken(uid, "carol@example.org") + ":mailto:carol@example.org"} {
		p, _ := ical.ParseLine(line)
		ev.Add(p)
	}
	return h.s.Seed(h.cal, ev, fakeproton.SeedOptions{Organiser: false, ProtonInvite: protonInvite, Signer: h.s.Bob,
		Statuses: map[string]int{"carol@example.org": 3}})
}

func containsWarning(ws []string, w string) bool {
	for _, x := range ws {
		if x == w {
			return true
		}
	}
	return false
}
