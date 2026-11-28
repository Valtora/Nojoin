package fakeproton

import (
	"encoding/base64"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"strconv"
	"strings"
	"sync"
	"time"

	srp "github.com/ProtonMail/go-srp"
	"github.com/ProtonMail/gopenpgp/v2/crypto"

	"github.com/Valtora/Nojoin/proton/internal/calendar"
	"github.com/Valtora/Nojoin/proton/internal/ical"
)

// Call is one recorded request.
type Call struct {
	Method     string
	Path       string
	Query      string
	Body       []byte
	AppVersion string
}

type session struct {
	uid, access, refresh string
	anonymous            bool
	needs2FA             bool
	unlocked             bool
}

type fault struct {
	method, prefix string
	skip           int
	status, code   int
	message        string
	retryAfter     string
}

// Calendar is one fake calendar.
type Calendar struct {
	ID       string
	MemberID string
	Type     int
	Perms    int
	Name     string
	keys     *calendarKeys
	events   map[string]*calendar.RawEvent
	order    []string
	log      []modelEvent
}

type modelEvent struct {
	ID     string
	Action int
	Event  *calendar.RawEvent
}

// SentMail is one message sent through the fake.
type SentMail struct {
	DraftID    string
	Subject    string
	To         []string
	Packages   []json.RawMessage
	Attachment AttachmentUpload
}

// AttachmentUpload is an uploaded attachment's parts.
type AttachmentUpload struct {
	ID         string
	Filename   string
	MIMEType   string
	KeyPackets []byte
	DataPacket []byte
}

// Server is the fake.
type Server struct {
	*httptest.Server

	mu sync.Mutex

	// Knobs a test may set before use.
	Username        string
	Password        string
	MailboxPassword string // non-empty: two-password mode
	TOTP            string // non-empty: TOTP is required
	FIDO2Only       bool
	RequireAnon     bool
	HumanCheck      bool // the first sign-in asks for a CAPTCHA
	SaltsNeedScope  bool
	TimeZone        string

	Self     *Person
	Bob      *Person // another Proton user
	Outsider string  // an address outside Proton

	account   *account
	sessions  map[string]*session
	srp       map[string]*srp.Server
	calendars map[string]*Calendar
	order     []string
	faults    []fault
	clock     int64
	hvSolved  bool

	calls       []Call
	Sent        []SentMail
	attachments map[string]AttachmentUpload
	drafts      map[string]SentMail
	Personal    []string // event IDs whose personal part was updated
}

// New starts a fake with one personal calendar on a loopback test server.
func New() *Server {
	s := NewUnstarted()
	s.Server = httptest.NewServer(http.HandlerFunc(s.serve))
	return s
}

// NewUnstarted builds the fake without a listener, for a caller that serves
// it itself (cmd/nojoin-proton-fake).
func NewUnstarted() *Server {
	s := &Server{
		Username: "alice",
		Password: "correct horse",
		TimeZone: "Europe/London",
		Self:     newPerson("Alice Example", "alice@example.com"),
		Bob:      newPerson("Bob Example", "bob@proton.me"),
		Outsider: "carol@example.org",
		sessions: map[string]*session{}, srp: map[string]*srp.Server{}, calendars: map[string]*Calendar{},
		attachments: map[string]AttachmentUpload{}, drafts: map[string]SentMail{},
		clock: 1_795_000_000,
	}
	s.AddCalendar("Personal", 0, 127)
	return s
}

// ServeHTTP serves the fake API.
func (s *Server) ServeHTTP(w http.ResponseWriter, r *http.Request) { s.serve(w, r) }

// SeedInvitation stores an invitation from Bob to the user and returns its UID.
func (s *Server) SeedInvitation(start time.Time) string {
	uid := randomID("invite-") + "@proton.me"
	ev := &ical.Component{Name: "VEVENT"}
	lines := []string{"UID:" + uid, "DTSTAMP:" + start.UTC().Format("20060102T150405Z"),
		"DTSTART:" + start.UTC().Format("20060102T150405Z"), "DTEND:" + start.Add(time.Hour).UTC().Format("20060102T150405Z"),
		"SEQUENCE:0", "SUMMARY:Planning with Bob", "ORGANIZER;CN=Bob Example:mailto:" + s.Bob.Email,
		"ATTENDEE;CN=Alice Example;ROLE=REQ-PARTICIPANT;X-PM-TOKEN=" + calendar.AttendeeToken(uid, s.Self.Email) + ":mailto:" + s.Self.Email}
	for _, line := range lines {
		p, _ := ical.ParseLine(line)
		ev.Add(p)
	}
	s.Seed(s.order[0], ev, SeedOptions{ProtonInvite: true, Signer: s.Bob})
	return uid
}

// Reset rebuilds the account keys after a test changes a password knob.
func (s *Server) ensureAccount() {
	if s.account == nil {
		pw := s.Password
		if s.MailboxPassword != "" {
			pw = s.MailboxPassword
		}
		s.account = newAccount(s.Self, pw)
	}
}

// AddCalendar adds a calendar and returns it.
func (s *Server) AddCalendar(name string, typ, perms int) *Calendar {
	c := &Calendar{ID: randomID("cal-"), MemberID: randomID("mem-"), Type: typ, Perms: perms, Name: name,
		keys: newCalendarKeys(s.Self), events: map[string]*calendar.RawEvent{}}
	s.calendars[c.ID] = c
	s.order = append(s.order, c.ID)
	return c
}

// DefaultCalendar is the first calendar.
func (s *Server) DefaultCalendar() *Calendar { return s.calendars[s.order[0]] }

// CalendarKeyRing is a calendar's private key, for test assertions.
func (c *Calendar) CalendarKeyRing() *crypto.KeyRing { return c.keys.keyRing() }

// Fail makes the next request matching method and path prefix fail.
func (s *Server) Fail(method, prefix string, status, code int, message string) {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.faults = append(s.faults, fault{method: method, prefix: prefix, status: status, code: code, message: message})
}

// FailLater lets skip matching requests through, then fails the next one.
func (s *Server) FailLater(method, prefix string, skip, status, code int, message string) {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.faults = append(s.faults, fault{method: method, prefix: prefix, skip: skip, status: status, code: code, message: message})
}

// RateLimit makes the next matching request answer 429.
func (s *Server) RateLimit(method, prefix, retryAfter string) {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.faults = append(s.faults, fault{method: method, prefix: prefix, status: 429, code: 2028, message: "Too many requests", retryAfter: retryAfter})
}

// Calls returns the requests recorded so far.
func (s *Server) Calls() []Call {
	s.mu.Lock()
	defer s.mu.Unlock()
	return append([]Call(nil), s.calls...)
}

// ResetCalls forgets recorded requests.
func (s *Server) ResetCalls() {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.calls = nil
}

// Events returns a calendar's rows.
func (s *Server) Events(calID string) []*calendar.RawEvent {
	s.mu.Lock()
	defer s.mu.Unlock()
	c := s.calendars[calID]
	var out []*calendar.RawEvent
	for _, id := range c.order {
		if ev, ok := c.events[id]; ok {
			out = append(out, ev)
		}
	}
	return out
}

// Session returns a valid signed-in session's tokens, made without the SRP
// exchange, for tests of the calendar operations.
func (s *Server) Session() (uid, access, refresh, keyPassphrase string) {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.ensureAccount()
	sess := &session{uid: randomID("uid-"), access: randomID("acc-"), refresh: randomID("ref-")}
	s.sessions[sess.uid] = sess
	return sess.uid, sess.access, sess.refresh, string(s.account.saltedKeyPass)
}

// ExpireAccess invalidates a session's access token, as Proton does when it
// expires.
func (s *Server) ExpireAccess(uid string) {
	s.mu.Lock()
	defer s.mu.Unlock()
	if sess := s.sessions[uid]; sess != nil {
		sess.access = randomID("expired-")
	}
}

// RefreshToken returns the session's current refresh token.
func (s *Server) RefreshToken(uid string) string {
	s.mu.Lock()
	defer s.mu.Unlock()
	if sess := s.sessions[uid]; sess != nil {
		return sess.refresh
	}
	return ""
}

func writeJSON(w http.ResponseWriter, status int, v any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(v)
}

func apiError(w http.ResponseWriter, status, code int, msg string) {
	writeJSON(w, status, map[string]any{"Code": code, "Error": msg})
}

func (s *Server) tick() int64 {
	s.clock += 7
	return s.clock
}

func (s *Server) serve(w http.ResponseWriter, r *http.Request) {
	body, _ := io.ReadAll(r.Body)
	s.mu.Lock()
	defer s.mu.Unlock()
	s.ensureAccount()
	s.calls = append(s.calls, Call{Method: r.Method, Path: r.URL.Path, Query: r.URL.RawQuery, Body: body, AppVersion: r.Header.Get("x-pm-appversion")})
	for i, f := range s.faults {
		if f.method == r.Method && strings.HasPrefix(r.URL.Path, f.prefix) {
			if f.skip > 0 {
				s.faults[i].skip--
				break
			}
			s.faults = append(s.faults[:i], s.faults[i+1:]...)
			if f.retryAfter != "" {
				w.Header().Set("Retry-After", f.retryAfter)
			}
			if f.status == 0 {
				// Drop the connection without an answer.
				if hj, ok := w.(http.Hijacker); ok {
					conn, _, _ := hj.Hijack()
					_ = conn.Close()
					return
				}
			}
			apiError(w, f.status, f.code, f.message)
			return
		}
	}
	if r.Header.Get("x-pm-appversion") != "Other" {
		apiError(w, 400, 5002, "Invalid app version")
		return
	}
	path := r.URL.Path
	switch {
	case path == "/auth/v4/sessions" && r.Method == http.MethodPost:
		if !s.RequireAnon {
			apiError(w, 404, 404, "Not found")
			return
		}
		sess := &session{uid: randomID("anon-"), access: randomID("anonacc-"), anonymous: true}
		s.sessions[sess.uid] = sess
		writeJSON(w, 200, map[string]any{"Code": 1000, "UID": sess.uid, "AccessToken": sess.access})
		return
	case path == "/auth/v4/info" && r.Method == http.MethodPost:
		if s.RequireAnon && s.authed(r) == nil {
			apiError(w, 401, 401, "Invalid access token")
			return
		}
		s.handleInfo(w, body)
		return
	case path == "/auth/v4" && r.Method == http.MethodPost:
		if s.RequireAnon && s.authed(r) == nil {
			apiError(w, 401, 401, "Invalid access token")
			return
		}
		s.handleAuth(w, r, body)
		return
	case path == "/auth/v4/refresh" && r.Method == http.MethodPost:
		s.handleRefresh(w, body)
		return
	}
	sess := s.authed(r)
	if sess == nil || sess.anonymous {
		apiError(w, 401, 401, "Invalid access token")
		return
	}
	switch {
	case path == "/auth/v4/2fa" && r.Method == http.MethodPost:
		var req struct{ TwoFactorCode string }
		_ = json.Unmarshal(body, &req)
		if req.TwoFactorCode != s.TOTP {
			apiError(w, 422, 8002, "Incorrect code")
			return
		}
		sess.needs2FA = false
		writeJSON(w, 200, map[string]any{"Code": 1000, "Scopes": []string{"full", "self", "user", "calendar", "mail"}})
		return
	case path == "/auth/v4" && r.Method == http.MethodDelete:
		delete(s.sessions, sess.uid)
		writeJSON(w, 200, map[string]any{"Code": 1000})
		return
	}
	if sess.needs2FA {
		apiError(w, 403, 9101, "Two-factor authentication required")
		return
	}
	switch {
	case path == "/core/v4/users" && r.Method == http.MethodGet:
		writeJSON(w, 200, map[string]any{"Code": 1000, "User": map[string]any{
			"ID": "user-1", "Name": s.Username, "DisplayName": s.Self.Name, "Email": s.Self.Email,
			"Keys": []map[string]any{{"ID": s.account.userKeyID, "PrivateKey": s.account.userKeyArmor, "Primary": 1, "Active": 1, "Flags": 3}},
		}})
	case path == "/core/v4/addresses" && r.Method == http.MethodGet:
		writeJSON(w, 200, map[string]any{"Code": 1000, "Addresses": []map[string]any{{
			"ID": "addr-1", "Email": s.Self.Email, "Send": 1, "Receive": 1, "Status": 1, "Type": 1, "Order": 1, "DisplayName": s.Self.Name,
			"Keys": []map[string]any{{"ID": s.account.addrKeyID, "PrivateKey": s.account.addrKeyArmor, "Token": s.account.addrToken,
				"Signature": s.account.addrTokenSig, "Primary": 1, "Active": 1, "Flags": 3}},
		}}})
	case path == "/core/v4/keys/salts" && r.Method == http.MethodGet:
		if s.SaltsNeedScope && !sess.unlocked {
			apiError(w, 403, 9101, "Access token does not have sufficient scope")
			return
		}
		writeJSON(w, 200, map[string]any{"Code": 1000, "KeySalts": []map[string]any{{"ID": s.account.userKeyID, "KeySalt": base64.StdEncoding.EncodeToString(s.account.keySalt)}}})
	case path == "/core/v4/users/unlock" && r.Method == http.MethodPut:
		s.handleUnlock(w, sess, body)
	case path == "/core/v4/users/lock" && r.Method == http.MethodPut:
		sess.unlocked = false
		writeJSON(w, 200, map[string]any{"Code": 1000})
	case path == "/core/v4/keys" && r.Method == http.MethodGet:
		s.handleKeys(w, r)
	case path == "/settings/calendar" && r.Method == http.MethodGet:
		writeJSON(w, 200, map[string]any{"Code": 1000, "CalendarUserSettings": map[string]any{"DefaultCalendarID": s.order[0], "PrimaryTimezone": s.TimeZone}})
	case path == "/calendar/v1" && r.Method == http.MethodGet:
		s.handleCalendars(w)
	case strings.HasPrefix(path, "/calendar/v2/") && strings.HasSuffix(path, "/bootstrap"):
		s.handleBootstrap(w, strings.TrimSuffix(strings.TrimPrefix(path, "/calendar/v2/"), "/bootstrap"))
	case strings.HasPrefix(path, "/calendar/v1/"):
		s.handleCalendarPath(w, r, strings.TrimPrefix(path, "/calendar/v1/"), body)
	case strings.HasPrefix(path, "/mail/v4/"):
		s.handleMail(w, r, strings.TrimPrefix(path, "/mail/v4/"), body)
	default:
		apiError(w, 404, 404, "No such route "+path)
	}
}

func (s *Server) authed(r *http.Request) *session {
	uid := r.Header.Get("x-pm-uid")
	tok := strings.TrimPrefix(r.Header.Get("Authorization"), "Bearer ")
	sess := s.sessions[uid]
	if sess == nil || tok == "" || sess.access != tok {
		return nil
	}
	return sess
}

func (s *Server) handleInfo(w http.ResponseWriter, body []byte) {
	var req struct{ Username string }
	_ = json.Unmarshal(body, &req)
	if req.Username != s.Username && req.Username != s.Self.Email {
		apiError(w, 422, 8002, "Incorrect login credentials")
		return
	}
	salt := randomBytes(10)
	auth, err := srp.NewAuthForVerifier([]byte(s.Password), testModulus, salt)
	if err != nil {
		apiError(w, 500, 500, err.Error())
		return
	}
	verifier, err := auth.GenerateVerifier(2048)
	if err != nil {
		apiError(w, 500, 500, err.Error())
		return
	}
	server, err := srp.NewServerFromSigned(testModulus, verifier, 2048)
	if err != nil {
		apiError(w, 500, 500, err.Error())
		return
	}
	challenge, err := server.GenerateChallenge()
	if err != nil {
		apiError(w, 500, 500, err.Error())
		return
	}
	id := randomID("srp-")
	s.srp[id] = server
	writeJSON(w, 200, map[string]any{"Code": 1000, "Version": 4, "Modulus": testModulus,
		"ServerEphemeral": base64.StdEncoding.EncodeToString(challenge), "Salt": base64.StdEncoding.EncodeToString(salt), "SRPSession": id})
}

func (s *Server) checkProof(body []byte) ([]byte, bool) {
	var req struct{ ClientEphemeral, ClientProof, SRPSession string }
	_ = json.Unmarshal(body, &req)
	server := s.srp[req.SRPSession]
	delete(s.srp, req.SRPSession)
	if server == nil {
		return nil, false
	}
	ce, _ := base64.StdEncoding.DecodeString(req.ClientEphemeral)
	cp, _ := base64.StdEncoding.DecodeString(req.ClientProof)
	proof, err := server.VerifyProofs(ce, cp)
	if err != nil {
		return nil, false
	}
	return proof, true
}

func (s *Server) handleAuth(w http.ResponseWriter, r *http.Request, body []byte) {
	if s.HumanCheck && !s.hvSolved {
		if r.Header.Get("x-pm-human-verification-token") == "hv-solved" && r.Header.Get("x-pm-human-verification-token-type") == "captcha" {
			s.hvSolved = true
		} else {
			writeJSON(w, 422, map[string]any{"Code": 9001, "Error": "Human verification required",
				"Details": map[string]any{"HumanVerificationMethods": []string{"captcha", "email"}, "HumanVerificationToken": "hv-challenge"}})
			return
		}
	}
	proof, ok := s.checkProof(body)
	if !ok {
		apiError(w, 422, 8002, "Incorrect login credentials. Please try again.")
		return
	}
	sess := &session{uid: randomID("uid-"), access: randomID("acc-"), refresh: randomID("ref-"), needs2FA: s.TOTP != "" || s.FIDO2Only}
	s.sessions[sess.uid] = sess
	enabled := 0
	if s.TOTP != "" {
		enabled |= 1
	}
	if s.FIDO2Only {
		enabled |= 2
	}
	mode := 1
	if s.MailboxPassword != "" {
		mode = 2
	}
	writeJSON(w, 200, map[string]any{"Code": 1000, "UserID": "user-1", "UID": sess.uid, "AccessToken": sess.access, "RefreshToken": sess.refresh,
		"ServerProof": base64.StdEncoding.EncodeToString(proof), "Scope": "full self user calendar", "ExpiresIn": 86400,
		"PasswordMode": mode, "2FA": map[string]any{"Enabled": enabled}})
}

func (s *Server) handleUnlock(w http.ResponseWriter, sess *session, body []byte) {
	proof, ok := s.checkProof(body)
	if !ok {
		apiError(w, 422, 8002, "Incorrect password")
		return
	}
	sess.unlocked = true
	writeJSON(w, 200, map[string]any{"Code": 1000, "ServerProof": base64.StdEncoding.EncodeToString(proof)})
}

func (s *Server) handleRefresh(w http.ResponseWriter, body []byte) {
	var req struct{ UID, RefreshToken, GrantType, ResponseType string }
	_ = json.Unmarshal(body, &req)
	sess := s.sessions[req.UID]
	if sess == nil || sess.refresh == "" || sess.refresh != req.RefreshToken || req.GrantType != "refresh_token" {
		apiError(w, 422, 10013, "Invalid refresh token")
		return
	}
	sess.access, sess.refresh = randomID("acc-"), randomID("ref-")
	writeJSON(w, 200, map[string]any{"Code": 1000, "UID": sess.uid, "AccessToken": sess.access, "RefreshToken": sess.refresh,
		"ExpiresIn": 86400, "Scopes": []string{"full", "calendar"}})
}

func (s *Server) handleKeys(w http.ResponseWriter, r *http.Request) {
	email := strings.ToLower(r.URL.Query().Get("Email"))
	switch email {
	case strings.ToLower(s.Bob.Email):
		writeJSON(w, 200, map[string]any{"Code": 1000, "RecipientType": 1, "Keys": []map[string]any{{"Flags": 3, "PublicKey": s.Bob.PublicArmored()}}})
	case strings.ToLower(s.Self.Email):
		writeJSON(w, 200, map[string]any{"Code": 1000, "RecipientType": 1, "Keys": []map[string]any{{"Flags": 3, "PublicKey": s.Self.PublicArmored()}}})
	case "broken@example.net":
		apiError(w, 422, 33102, "Address lookup failed")
	default:
		writeJSON(w, 200, map[string]any{"Code": 1000, "RecipientType": 2, "Keys": []any{}})
	}
}

func (s *Server) memberJSON(c *Calendar) map[string]any {
	return map[string]any{"ID": c.MemberID, "CalendarID": c.ID, "AddressID": "addr-1", "Email": s.Self.Email, "Permissions": c.Perms,
		"Name": c.Name, "Description": "", "Color": "#5252cc", "Display": 1, "Flags": 1}
}

func (s *Server) handleCalendars(w http.ResponseWriter) {
	var list []map[string]any
	for _, id := range s.order {
		c := s.calendars[id]
		// Names live on the member now; the top-level fields stay empty.
		list = append(list, map[string]any{"ID": c.ID, "Type": c.Type, "Name": "", "Members": []any{s.memberJSON(c)}})
	}
	writeJSON(w, 200, map[string]any{"Code": 1000, "Calendars": list})
}

func (s *Server) handleBootstrap(w http.ResponseWriter, calID string) {
	c := s.calendars[calID]
	if c == nil {
		apiError(w, 404, 2501, "Calendar does not exist")
		return
	}
	writeJSON(w, 200, map[string]any{"Code": 1000,
		"Keys":             []map[string]any{{"ID": c.keys.keyID, "CalendarID": c.ID, "PassphraseID": c.keys.passphraseID, "PrivateKey": c.keys.armored, "Flags": 3}},
		"Passphrase":       map[string]any{"ID": c.keys.passphraseID, "MemberPassphrases": []map[string]any{{"MemberID": c.MemberID, "Passphrase": c.keys.passMsg, "Signature": c.keys.passSig}}},
		"Members":          []any{s.memberJSON(c)},
		"CalendarSettings": map[string]any{"DefaultEventDuration": 30},
	})
}

func (s *Server) handleCalendarPath(w http.ResponseWriter, r *http.Request, rest string, body []byte) {
	parts := strings.Split(rest, "/")
	c := s.calendars[parts[0]]
	if c == nil {
		apiError(w, 404, 2501, "Calendar does not exist")
		return
	}
	switch {
	case len(parts) == 2 && parts[1] == "events" && r.Method == http.MethodGet:
		s.handleEventQuery(w, r, c)
	case len(parts) == 3 && parts[1] == "events" && parts[2] == "sync" && r.Method == http.MethodPut:
		s.handleSync(w, c, body)
	case len(parts) == 3 && parts[1] == "events" && r.Method == http.MethodGet:
		ev := c.events[parts[2]]
		if ev == nil {
			apiError(w, 404, 2061, "Event does not exist")
			return
		}
		writeJSON(w, 200, map[string]any{"Code": 1000, "Event": ev})
	case len(parts) == 5 && parts[1] == "events" && parts[3] == "attendees" && r.Method == http.MethodPut:
		s.handleAnswer(w, c, parts[2], parts[4], body)
	case len(parts) == 4 && parts[1] == "events" && parts[3] == "personal" && r.Method == http.MethodPut:
		s.Personal = append(s.Personal, parts[2])
		writeJSON(w, 200, map[string]any{"Code": 1000})
	case len(parts) == 3 && parts[1] == "modelevents":
		s.handleModelEvents(w, c, parts[2])
	default:
		apiError(w, 404, 404, "No such calendar route")
	}
}

func (s *Server) handleEventQuery(w http.ResponseWriter, r *http.Request, c *Calendar) {
	q := r.URL.Query()
	pageSize, _ := strconv.Atoi(q.Get("PageSize"))
	if pageSize > 100 {
		apiError(w, 400, 2021, "Invalid page size parameter")
		return
	}
	if pageSize == 0 {
		pageSize = 100
	}
	page, _ := strconv.Atoi(q.Get("Page"))
	var match []*calendar.RawEvent
	if uid := q.Get("UID"); uid != "" {
		for _, id := range c.order {
			if ev := c.events[id]; ev != nil && ev.UID == uid {
				match = append(match, ev)
			}
		}
	} else {
		start, _ := strconv.ParseInt(q.Get("Start"), 10, 64)
		end, _ := strconv.ParseInt(q.Get("End"), 10, 64)
		if end-start > 93*86400 {
			apiError(w, 422, 2000, "Time window is too big")
			return
		}
		typ, err := strconv.Atoi(q.Get("Type"))
		if err != nil {
			apiError(w, 422, 2001, "Type is required for a windowed query")
			return
		}
		for _, id := range c.order {
			ev := c.events[id]
			if ev == nil || (ev.FullDay == 1) != (typ >= 2) {
				continue
			}
			inside := ev.StartTime >= start && ev.StartTime < end
			before := ev.StartTime < start && (ev.EndTime > start || ev.RRule != nil)
			if (typ%2 == 0 && inside) || (typ%2 == 1 && before) {
				match = append(match, ev)
			}
		}
	}
	from := page * pageSize
	more := 0
	var out []*calendar.RawEvent
	if from < len(match) {
		to := from + pageSize
		if to < len(match) {
			more = 1
		} else {
			to = len(match)
		}
		out = match[from:to]
	}
	writeJSON(w, 200, map[string]any{"Code": 1000, "Events": out, "More": more})
}

func (s *Server) handleAnswer(w http.ResponseWriter, c *Calendar, eventID, attendeeID string, body []byte) {
	ev := c.events[eventID]
	if ev == nil {
		apiError(w, 404, 2061, "Event does not exist")
		return
	}
	var req struct {
		Status     int
		UpdateTime int64
		Comment    json.RawMessage
	}
	_ = json.Unmarshal(body, &req)
	if ev.AttendeesInfo == nil {
		apiError(w, 422, 2001, "No attendees")
		return
	}
	for i := range ev.AttendeesInfo.Attendees {
		if ev.AttendeesInfo.Attendees[i].ID == attendeeID {
			ev.AttendeesInfo.Attendees[i].Status = req.Status
			ev.AttendeesInfo.Attendees[i].UpdateTime = req.UpdateTime
			ev.ModifyTime = s.tick()
			c.log = append(c.log, modelEvent{ID: ev.ID, Action: 2, Event: ev})
			writeJSON(w, 200, map[string]any{"Code": 1000, "Attendee": ev.AttendeesInfo.Attendees[i]})
			return
		}
	}
	apiError(w, 404, 2061, "Attendee does not exist")
}

func (s *Server) handleModelEvents(w http.ResponseWriter, c *Calendar, cursor string) {
	if cursor == "latest" {
		writeJSON(w, 200, map[string]any{"Code": 1000, "CalendarModelEventID": fmt.Sprintf("m%d", len(c.log))})
		return
	}
	n, err := strconv.Atoi(strings.TrimPrefix(cursor, "m"))
	if err != nil || !strings.HasPrefix(cursor, "m") || n > len(c.log) {
		apiError(w, 404, 2501, "Event ID does not exist")
		return
	}
	const page = 3
	end := n + page
	more := 1
	if end >= len(c.log) {
		end, more = len(c.log), 0
	}
	var items []map[string]any
	for _, e := range c.log[n:end] {
		item := map[string]any{"ID": e.ID, "Action": e.Action}
		if e.Action != 0 {
			item["Event"] = e.Event
		}
		items = append(items, item)
	}
	writeJSON(w, 200, map[string]any{"Code": 1000, "CalendarModelEventID": fmt.Sprintf("m%d", end), "More": more, "Refresh": 0, "CalendarEvents": items})
}

func decodeCard(card calendar.Card, sk *crypto.SessionKey) (*ical.Component, error) {
	plain := []byte(card.Data)
	if card.Encrypted() {
		if sk == nil {
			return nil, fmt.Errorf("no session key")
		}
		data, err := base64.StdEncoding.DecodeString(card.Data)
		if err != nil {
			return nil, err
		}
		msg, err := sk.Decrypt(data)
		if err != nil {
			return nil, err
		}
		plain = msg.GetBinary()
	}
	return ical.FirstEvent(string(plain))
}
