package fakeproton

import (
	"bytes"
	"encoding/base64"
	"encoding/json"
	"io"
	"mime"
	"mime/multipart"
	"net/http"
	"strconv"
	"strings"
	"time"

	"github.com/ProtonMail/gopenpgp/v2/crypto"

	"github.com/Valtora/Nojoin/proton/internal/calendar"
	"github.com/Valtora/Nojoin/proton/internal/ical"
)

type syncEntry struct {
	ID        string          `json:"ID"`
	Overwrite *int            `json:"Overwrite"`
	Event     json.RawMessage `json:"Event"`
}

// SyncBody is a recorded sync entry's Event, decoded loosely so tests can see
// every field that was sent.
type SyncBody struct {
	Permissions              int
	SharedKeyPacket          string
	CalendarKeyPacket        string
	SharedEventContent       []calendar.Card
	CalendarEventContent     []calendar.Card
	AttendeesEventContent    []calendar.Card
	Attendees                []calendar.ClearAttendee
	Notifications            json.RawMessage
	Color                    json.RawMessage
	AddedProtonAttendees     []calendar.AddedAttendee
	RemovedAttendeeAddresses []string
}

type entryResult struct {
	Index    int `json:"Index"`
	Response struct {
		Code  int                `json:"Code"`
		Error string             `json:"Error,omitempty"`
		Event *calendar.RawEvent `json:"Event,omitempty"`
	} `json:"Response"`
}

// meta is what Proton reads from the shared signed card.
type meta struct {
	uid       string
	start     ical.Moment
	end       ical.Moment
	rrule     *string
	recurID   *int64
	exdates   []int64
	sequence  int
	seqParams bool
	timeLine  string
}

func readMeta(card string) (*meta, error) {
	ev, err := ical.FirstEvent(card)
	if err != nil {
		return nil, err
	}
	m := &meta{}
	if p := ev.Get("UID"); p != nil {
		m.uid = p.Value
	}
	start, end, err := eventTimesOf(ev)
	if err != nil {
		return nil, err
	}
	m.start, m.end = start, end
	if p := ev.Get("RRULE"); p != nil {
		v := p.Value
		m.rrule = &v
	}
	if p := ev.Get("RECURRENCE-ID"); p != nil {
		rm, err := ical.ParseMoment(p)
		if err == nil {
			u := rm.Instant().Unix()
			m.recurID = &u
		}
	}
	for _, t := range ical.ParseExdates(ev.All("EXDATE")) {
		m.exdates = append(m.exdates, t.Unix())
	}
	if p := ev.Get("SEQUENCE"); p != nil {
		m.sequence, _ = strconv.Atoi(strings.TrimSpace(p.Value))
		m.seqParams = len(p.Params) > 0
	}
	var b strings.Builder
	for _, name := range []string{"DTSTART", "DTEND", "RRULE", "EXDATE"} {
		for _, p := range ev.All(name) {
			b.WriteString(ical.FormatLine(p))
			b.WriteString("|")
		}
	}
	m.timeLine = b.String()
	return m, nil
}

func eventTimesOf(ev *ical.Component) (ical.Moment, ical.Moment, error) {
	start, err := ical.ParseMoment(ev.Get("DTSTART"))
	if err != nil {
		return start, start, err
	}
	end := start
	if p := ev.Get("DTEND"); p != nil {
		end, err = ical.ParseMoment(p)
		if err != nil {
			return start, start, err
		}
	} else if start.AllDay {
		end = ical.Moment{AllDay: true, Date: start.Date.AddDate(0, 0, 1)}
	}
	return start, end, nil
}

// checkCards verifies every card's signature with the member's address key,
// and that each encrypted card opens with the session key given.
func (s *Server) checkCards(cards []calendar.Card, sk *crypto.SessionKey) string {
	verify := s.Self.KeyRing()
	for _, c := range cards {
		if _, err := decodeCard(c, sk); err != nil {
			return "card does not decrypt"
		}
		raw := []byte(c.Data)
		if c.Encrypted() {
			data, _ := base64.StdEncoding.DecodeString(c.Data)
			msg, _ := sk.Decrypt(data)
			raw = msg.GetBinary()
		}
		sig, err := crypto.NewPGPSignatureFromArmored(c.Signature)
		if err != nil {
			return "card signature is not armoured"
		}
		if err := verify.VerifyDetached(crypto.NewPlainMessage(raw), sig, crypto.GetUnixTime()); err != nil {
			return "card signature does not verify"
		}
	}
	return ""
}

func (s *Server) sessionKeyFor(c *Calendar, packet string) *crypto.SessionKey {
	if packet == "" {
		return nil
	}
	raw, err := base64.StdEncoding.DecodeString(packet)
	if err != nil {
		return nil
	}
	sk, err := c.keys.keyRing().DecryptSessionKey(raw)
	if err != nil {
		return nil
	}
	return sk
}

func refuse(res *entryResult, code int, msg string) {
	res.Response.Code = code
	res.Response.Error = msg
}

func (s *Server) handleSync(w http.ResponseWriter, c *Calendar, raw []byte) {
	var req struct {
		MemberID string
		IsImport *int
		Events   []syncEntry
	}
	if err := json.Unmarshal(raw, &req); err != nil {
		apiError(w, 400, 2001, "Invalid body")
		return
	}
	if req.MemberID != c.MemberID {
		apiError(w, 422, 2001, "Invalid member")
		return
	}
	var results []entryResult
	for i, e := range req.Events {
		res := entryResult{Index: i}
		res.Response.Code = 1000
		switch {
		case len(e.Event) == 0 && e.ID != "":
			ev := c.events[e.ID]
			if ev == nil {
				refuse(&res, 2061, "Event does not exist")
				results = append(results, res)
				continue
			}
			delete(c.events, e.ID)
			c.log = append(c.log, modelEvent{ID: e.ID, Action: 0})
			// A pure delete answers with no entry.
			continue
		default:
			var body SyncBody
			if err := json.Unmarshal(e.Event, &body); err != nil {
				refuse(&res, 2001, "Invalid event")
				break
			}
			if bytes.Contains(e.Event, []byte(`"SharedEventContent":null`)) || bytes.Contains(e.Event, []byte(`"CalendarEventContent":null`)) {
				refuse(&res, 2001, "Content arrays must not be null")
				break
			}
			if e.ID == "" {
				s.syncCreate(c, body, &res)
			} else {
				s.syncUpdate(c, e.ID, body, &res)
			}
		}
		results = append(results, res)
	}
	writeJSON(w, 200, map[string]any{"Code": 1001, "Responses": results})
}

func cardData(cards []calendar.Card, encrypted bool) string {
	for _, c := range cards {
		if c.Encrypted() == encrypted {
			return c.Data
		}
	}
	return ""
}

func (s *Server) validateBody(c *Calendar, body SyncBody, sharedSK, calSK *crypto.SessionKey) (*meta, int, string) {
	signed := cardData(body.SharedEventContent, false)
	if signed == "" {
		return nil, 2001, "Missing shared signed part"
	}
	m, err := readMeta(signed)
	if err != nil {
		return nil, 2001, "Invalid shared signed part"
	}
	if m.seqParams {
		return nil, 500, "Internal error"
	}
	if m.start.Zone == "" && !m.start.AllDay {
		return nil, 2001, "Floating times are not supported"
	}
	if msg := s.checkCards(body.SharedEventContent, sharedSK); msg != "" {
		return nil, 2001, "Shared part: " + msg
	}
	if msg := s.checkCards(body.CalendarEventContent, calSK); msg != "" {
		return nil, 2001, "Calendar part: " + msg
	}
	if msg := s.checkCards(body.AttendeesEventContent, sharedSK); msg != "" {
		return nil, 2001, "Attendees part: " + msg
	}
	if cs := cardData(body.CalendarEventContent, false); cs != "" {
		if ev, err := ical.FirstEvent(cs); err == nil {
			if p := ev.Get("STATUS"); p != nil && strings.EqualFold(p.Value, "TENTATIVE") {
				// Measured by proton-calendar-bridge on 13 November 2026.
				return nil, 2000, "Invalid status"
			}
		}
	}
	return m, 0, ""
}

func (s *Server) masterOf(c *Calendar, uid string) *calendar.RawEvent {
	for _, id := range c.order {
		if ev := c.events[id]; ev != nil && ev.UID == uid && ev.RecurrenceID == nil {
			return ev
		}
	}
	return nil
}

func sequenceOfRow(ev *calendar.RawEvent) int {
	m, err := readMeta(cardData(ev.SharedEvents, false))
	if err != nil {
		return 0
	}
	return m.sequence
}

func (s *Server) store(c *Calendar, ev *calendar.RawEvent, m *meta, body SyncBody) {
	ev.UID = m.uid
	ev.CalendarID = c.ID
	ev.StartTime, ev.EndTime = m.start.Instant().Unix(), m.end.Instant().Unix()
	ev.FullDay = 0
	ev.StartTimezone, ev.EndTimezone = m.start.Zone, m.end.Zone
	if m.start.AllDay {
		ev.FullDay = 1
		ev.StartTimezone, ev.EndTimezone = "UTC", "UTC"
	}
	ev.RRule = m.rrule
	ev.RecurrenceID = m.recurID
	ev.Exdates = m.exdates
	ev.Permissions = body.Permissions
	ev.SharedEvents = withAuthor(body.SharedEventContent, s.Self.Email)
	ev.CalendarEvents = withAuthor(body.CalendarEventContent, s.Self.Email)
	ev.AttendeesEvents = withAuthor(body.AttendeesEventContent, s.Self.Email)
	var clear []calendar.ClearAttendee
	old := map[string]calendar.ClearAttendee{}
	if ev.AttendeesInfo != nil {
		for _, a := range ev.AttendeesInfo.Attendees {
			old[a.Token] = a
		}
	}
	for _, a := range body.Attendees {
		id := randomID("att-")
		if o, ok := old[a.Token]; ok {
			id = o.ID
		}
		clear = append(clear, calendar.ClearAttendee{ID: id, Token: a.Token, Status: a.Status})
	}
	ev.AttendeesInfo = &struct {
		Attendees     []calendar.ClearAttendee `json:"Attendees"`
		MoreAttendees int                      `json:"MoreAttendees"`
	}{Attendees: clear}
	ev.Notifications = body.Notifications
	if len(body.Color) > 0 && string(body.Color) != "null" {
		var col string
		_ = json.Unmarshal(body.Color, &col)
		ev.Color = &col
	}
	ev.ModifyTime = s.tick()
	ev.LastEditTime = ev.ModifyTime
}

func withAuthor(cards []calendar.Card, author string) []calendar.Card {
	out := make([]calendar.Card, 0, len(cards))
	for _, c := range cards {
		c.Author = author
		out = append(out, c)
	}
	return out
}

func (s *Server) syncCreate(c *Calendar, body SyncBody, res *entryResult) {
	sharedSK := s.sessionKeyFor(c, body.SharedKeyPacket)
	calSK := s.sessionKeyFor(c, body.CalendarKeyPacket)
	if sharedSK == nil {
		refuse(res, 2001, "SharedKeyPacket is required")
		return
	}
	m, code, msg := s.validateBody(c, body, sharedSK, calSK)
	if code != 0 {
		refuse(res, code, msg)
		return
	}
	master := s.masterOf(c, m.uid)
	if m.recurID == nil && master != nil {
		refuse(res, 2500, "An event with this UID already exists")
		return
	}
	if m.recurID != nil && master != nil && m.sequence < sequenceOfRow(master) {
		refuse(res, 2001, "Single edits should have a Sequence greater or equal to main event")
		return
	}
	ev := &calendar.RawEvent{ID: randomID("ev-"), SharedEventID: randomID("sev-"), IsOrganizer: true,
		SharedKeyPacket: body.SharedKeyPacket, CalendarKeyPacket: body.CalendarKeyPacket, CreateTime: s.tick()}
	s.store(c, ev, m, body)
	c.events[ev.ID] = ev
	c.order = append(c.order, ev.ID)
	c.log = append(c.log, modelEvent{ID: ev.ID, Action: 1, Event: ev})
	res.Response.Event = ev
}

func (s *Server) syncUpdate(c *Calendar, id string, body SyncBody, res *entryResult) {
	ev := c.events[id]
	if ev == nil {
		refuse(res, 2061, "Event does not exist")
		return
	}
	if body.SharedKeyPacket != "" || body.CalendarKeyPacket != "" {
		// Measured by proton-calendar-bridge.
		refuse(res, 2000, "SharedKeyPacket is not allowed")
		return
	}
	sharedSK := s.sessionKeyFor(c, ev.SharedKeyPacket)
	calSK := s.sessionKeyFor(c, ev.CalendarKeyPacket)
	m, code, msg := s.validateBody(c, body, sharedSK, calSK)
	if code != 0 {
		refuse(res, code, msg)
		return
	}
	old, err := readMeta(cardData(ev.SharedEvents, false))
	if err == nil && old.timeLine != m.timeLine && m.sequence <= old.sequence {
		refuse(res, 2000, "Cannot update time data without also incrementing Sequence")
		return
	}
	if m.recurID != nil {
		if master := s.masterOf(c, m.uid); master != nil && m.sequence < sequenceOfRow(master) {
			refuse(res, 2001, "Single edits should have a Sequence greater or equal to main event")
			return
		}
	}
	s.store(c, ev, m, body)
	c.log = append(c.log, modelEvent{ID: ev.ID, Action: 2, Event: ev})
	res.Response.Event = ev
}

// SyncBodies returns every sync Event body recorded, in order.
func (s *Server) SyncBodies() []SyncBody {
	var out []SyncBody
	for _, call := range s.Calls() {
		if !strings.HasSuffix(call.Path, "/events/sync") {
			continue
		}
		var req struct{ Events []syncEntry }
		_ = json.Unmarshal(call.Body, &req)
		for _, e := range req.Events {
			if len(e.Event) == 0 {
				continue
			}
			var b SyncBody
			_ = json.Unmarshal(e.Event, &b)
			out = append(out, b)
		}
	}
	return out
}

func (s *Server) handleMail(w http.ResponseWriter, r *http.Request, rest string, body []byte) {
	switch {
	case rest == "messages" && r.Method == http.MethodPost:
		var req struct {
			Message struct {
				Subject string
				ToList  []struct{ Address string }
			}
		}
		_ = json.Unmarshal(body, &req)
		id := randomID("msg-")
		m := SentMail{DraftID: id, Subject: req.Message.Subject}
		for _, t := range req.Message.ToList {
			m.To = append(m.To, t.Address)
		}
		s.drafts[id] = m
		writeJSON(w, 200, map[string]any{"Code": 1000, "Message": map[string]any{"ID": id}})
	case rest == "attachments" && r.Method == http.MethodPost:
		_, params, err := mime.ParseMediaType(r.Header.Get("Content-Type"))
		if err != nil {
			apiError(w, 400, 2001, "Not multipart")
			return
		}
		mr := multipart.NewReader(bytes.NewReader(body), params["boundary"])
		up := AttachmentUpload{ID: randomID("att-")}
		msgID := ""
		for {
			part, err := mr.NextPart()
			if err != nil {
				break
			}
			data, _ := io.ReadAll(part)
			switch part.FormName() {
			case "MessageID":
				msgID = string(data)
			case "Filename":
				up.Filename = string(data)
			case "MIMEType":
				up.MIMEType = string(data)
			case "KeyPackets":
				up.KeyPackets = data
			case "DataPacket":
				up.DataPacket = data
			}
		}
		d, ok := s.drafts[msgID]
		if !ok {
			apiError(w, 422, 2061, "No such draft")
			return
		}
		d.Attachment = up
		s.drafts[msgID] = d
		s.attachments[up.ID] = up
		writeJSON(w, 200, map[string]any{"Code": 1000, "Attachment": map[string]any{"ID": up.ID, "KeyPackets": base64.StdEncoding.EncodeToString(up.KeyPackets)}})
	case strings.HasPrefix(rest, "messages/") && r.Method == http.MethodPost:
		id := strings.TrimPrefix(rest, "messages/")
		d, ok := s.drafts[id]
		if !ok {
			apiError(w, 422, 2061, "No such draft")
			return
		}
		var req struct{ Packages []json.RawMessage }
		_ = json.Unmarshal(body, &req)
		d.Packages = req.Packages
		s.Sent = append(s.Sent, d)
		writeJSON(w, 200, map[string]any{"Code": 1000, "Sent": map[string]any{"ID": id}})
	default:
		apiError(w, 404, 404, "No such mail route")
	}
}

// SeedOptions shape an event seeded as if another client had written it.
type SeedOptions struct {
	// Organiser false makes it an invitation from someone else.
	Organiser bool
	// ProtonInvite marks a Proton-to-Proton invitation.
	ProtonInvite bool
	// Signer signs the cards; nil signs with the user's own address key.
	Signer *Person
	// Statuses sets the clear attendee statuses by email.
	Statuses map[string]int
	// NoCalendarKey leaves out the calendar key packet and private card, as
	// Proton's own apps sometimes do.
	NoCalendarKey bool
	// Notifications and Color are the row's reminders and colour.
	Notifications json.RawMessage
	Color         string
}

// Seed stores an event written by another client, returning its row ID.
func (s *Server) Seed(calID string, ev *ical.Component, opt SeedOptions) string {
	s.mu.Lock()
	defer s.mu.Unlock()
	c := s.calendars[calID]
	signer := s.Self
	if opt.Signer != nil {
		signer = opt.Signer
	}
	skr := signer.KeyRing()
	sign := func(plain string) string {
		sig, _ := skr.SignDetached(crypto.NewPlainMessage([]byte(plain)))
		a, _ := sig.GetArmored()
		return a
	}
	seal := func(plain string, sk *crypto.SessionKey) calendar.Card {
		data, _ := sk.Encrypt(crypto.NewPlainMessage([]byte(plain)))
		return calendar.Card{Type: 3, Data: base64.StdEncoding.EncodeToString(data), Signature: sign(plain), Author: signer.Email}
	}
	cards := ical.Split(ev, "-//Proton AG//WebCalendar 5.0//EN")
	sharedSK, _ := crypto.GenerateSessionKey()
	calSK, _ := crypto.GenerateSessionKey()
	ckr := c.keys.keyRing()
	sp, _ := ckr.EncryptSessionKey(sharedSK)
	row := &calendar.RawEvent{ID: randomID("ev-"), SharedEventID: randomID("sev-"), IsOrganizer: calendar.Flag(opt.Organiser),
		IsProtonProtonInvite: calendar.Flag(opt.ProtonInvite), SharedKeyPacket: base64.StdEncoding.EncodeToString(sp), CreateTime: s.tick(), Permissions: 1}
	row.SharedEvents = []calendar.Card{{Type: 2, Data: cards.SharedSigned, Signature: sign(cards.SharedSigned), Author: signer.Email}, seal(cards.SharedEncrypted, sharedSK)}
	if cards.CalendarSigned != "" {
		row.CalendarEvents = append(row.CalendarEvents, calendar.Card{Type: 2, Data: cards.CalendarSigned, Signature: sign(cards.CalendarSigned)})
	}
	if !opt.NoCalendarKey {
		cp, _ := ckr.EncryptSessionKey(calSK)
		row.CalendarKeyPacket = base64.StdEncoding.EncodeToString(cp)
		if cards.CalendarEncrypted != "" {
			row.CalendarEvents = append(row.CalendarEvents, seal(cards.CalendarEncrypted, calSK))
		}
	}
	if cards.Attendees != "" {
		row.AttendeesEvents = []calendar.Card{seal(cards.Attendees, sharedSK)}
	}
	m, _ := readMeta(cards.SharedSigned)
	uid := m.uid
	var clear []calendar.ClearAttendee
	for _, p := range ev.All("ATTENDEE") {
		email := strings.TrimPrefix(strings.ToLower(p.Value), "mailto:")
		clear = append(clear, calendar.ClearAttendee{ID: randomID("att-"), Token: calendar.AttendeeToken(uid, email), Status: opt.Statuses[email]})
	}
	notifications := opt.Notifications
	if notifications == nil {
		notifications = json.RawMessage("null")
	}
	body := SyncBody{Permissions: 1, SharedEventContent: row.SharedEvents, CalendarEventContent: row.CalendarEvents, AttendeesEventContent: row.AttendeesEvents, Notifications: notifications}
	if opt.Color != "" {
		body.Color, _ = json.Marshal(opt.Color)
	}
	for _, a := range clear {
		body.Attendees = append(body.Attendees, a)
	}
	s.store(c, row, m, body)
	// store re-authors the cards as the user; put the real signer back.
	row.SharedEvents = withAuthor(row.SharedEvents, signer.Email)
	for i := range row.AttendeesInfo.Attendees {
		row.AttendeesInfo.Attendees[i].ID = clear[i].ID
	}
	c.events[row.ID] = row
	c.order = append(c.order, row.ID)
	c.log = append(c.log, modelEvent{ID: row.ID, Action: 1, Event: row})
	return row.ID
}

// Clock returns the fake's current time.
func (s *Server) Clock() time.Time { return time.Unix(s.clock, 0).UTC() }
