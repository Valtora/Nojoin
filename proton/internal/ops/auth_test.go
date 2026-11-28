package ops

import (
	"strings"
	"testing"

	"github.com/Valtora/Nojoin/proton/internal/fakeproton"
	"github.com/Valtora/Nojoin/proton/internal/protocol"
)

type loginStartJSON struct {
	Session struct {
		UID          string `json:"uid"`
		AccessToken  string `json:"access_token"`
		RefreshToken string `json:"refresh_token"`
	} `json:"session"`
	PasswordMode int `json:"password_mode"`
	TwoFactor    struct {
		Required bool `json:"required"`
		TOTP     bool `json:"totp"`
		FIDO2    bool `json:"fido2"`
	} `json:"two_factor"`
	Next string `json:"next"`
}

func (l loginStartJSON) sess() *protocol.Session {
	return &protocol.Session{UID: l.Session.UID, AccessToken: l.Session.AccessToken, RefreshToken: l.Session.RefreshToken}
}

type unlockResult struct {
	Session struct {
		KeyPassphrase string `json:"key_passphrase"`
		UID           string `json:"uid"`
	} `json:"session"`
	Calendars []struct {
		ID       string `json:"id"`
		Name     string `json:"name"`
		Writable bool   `json:"writable"`
	} `json:"calendars"`
	Settings struct {
		PrimaryTimeZone string `json:"primary_time_zone"`
	} `json:"settings"`
}

func TestLoginOnePasswordWithAnonymousSession(t *testing.T) {
	h := newHarness(t)
	h.s.RequireAnon = true
	var start loginStartJSON
	h.okAs("login_start", nil, map[string]any{"username": "alice", "password": "correct horse"}, &start)
	if start.Next != "unlock" || start.PasswordMode != 1 || start.TwoFactor.Required {
		t.Fatalf("unexpected login_start result %+v", start)
	}
	var unlocked unlockResult
	h.okAs("login_unlock", start.sess(), map[string]any{"username": "alice", "password": "correct horse"}, &unlocked)
	_, _, _, want := h.s.Session()
	if unlocked.Session.KeyPassphrase != want {
		t.Fatal("login_unlock returned the wrong key passphrase")
	}
	if len(unlocked.Calendars) != 1 || unlocked.Calendars[0].Name != "Personal" || !unlocked.Calendars[0].Writable {
		t.Fatalf("calendars %+v", unlocked.Calendars)
	}
	if unlocked.Settings.PrimaryTimeZone != "Europe/London" {
		t.Fatal("primary time zone not passed on")
	}
	calls := h.s.Calls()
	if calls[0].Path != "/auth/v4/info" || calls[1].Path != "/auth/v4/sessions" || calls[2].Path != "/auth/v4/info" {
		t.Fatalf("an anonymous session must be opened only after info answers 401: %s, %s, %s",
			calls[0].Path, calls[1].Path, calls[2].Path)
	}
	for _, c := range calls {
		if c.AppVersion != "Other" {
			t.Fatalf("%s sent x-pm-appversion %q", c.Path, c.AppVersion)
		}
	}
}

// Proton refused to open an anonymous session for "Other" (422, 8004) on 28 Nov
// 2026 while answering /auth/v4/info without one. Sign-in must not ask for one.
func TestLoginWithoutAnonymousSession(t *testing.T) {
	h := newHarness(t)
	h.s.RefuseAnon = true
	var start loginStartJSON
	h.okAs("login_start", nil, map[string]any{"username": "alice", "password": "correct horse"}, &start)
	if start.Next != "unlock" {
		t.Fatalf("unexpected login_start result %+v", start)
	}
	for _, c := range h.s.Calls() {
		if c.Path == "/auth/v4/sessions" {
			t.Fatal("login_start asked for an anonymous session it did not need")
		}
	}
}

func TestLoginRefusals(t *testing.T) {
	h := newHarness(t)
	if e := h.failAs("login_start", nil, map[string]any{"username": "alice", "password": "wrong"}); e.Code != protocol.CodeLoginFailed {
		t.Fatalf("wrong password gave %s", e.Code)
	}
	if e := h.failAs("login_start", nil, map[string]any{"username": "", "password": "x"}); e.Code != protocol.CodeInvalidArgument {
		t.Fatalf("empty username gave %s", e.Code)
	}
	h.s.FIDO2Only = true
	if e := h.failAs("login_start", nil, map[string]any{"username": "alice", "password": "correct horse"}); e.Code != protocol.CodeTwoFactorUnsupported {
		t.Fatalf("FIDO2-only gave %s", e.Code)
	}
	if h.countCalls("DELETE", "/auth/v4") != 1 {
		t.Fatal("the partial session of a FIDO2-only login should be ended")
	}
}

func TestLoginHumanVerification(t *testing.T) {
	h := newHarness(t)
	h.s.HumanCheck = true
	e := h.failAs("login_start", nil, map[string]any{"username": "alice", "password": "correct horse"})
	if e.Code != protocol.CodeHumanVerification {
		t.Fatalf("got %s", e.Code)
	}
	url, _ := e.Details["verify_url"].(string)
	if url != "https://verify.proton.me/?methods=captcha&token=hv-challenge" || e.Details["token"] != "hv-challenge" {
		t.Fatalf("details %v", e.Details)
	}
	var start loginStartJSON
	h.okAs("login_start", nil, map[string]any{"username": "alice", "password": "correct horse",
		"human_verification": map[string]any{"token": "hv-solved", "type": "captcha"}}, &start)
	if start.Session.UID == "" {
		t.Fatal("no session after the check")
	}
}

func TestLoginTwoFactorAndMailboxPassword(t *testing.T) {
	h := newHarness(t, func(s *fakeproton.Server) {
		s.TOTP = "123456"
		s.MailboxPassword = "mailbox secret"
		s.SaltsNeedScope = true
	})
	var start loginStartJSON
	h.okAs("login_start", nil, map[string]any{"username": "alice@example.com", "password": "correct horse"}, &start)
	if start.Next != "two_factor" || start.PasswordMode != 2 || !start.TwoFactor.TOTP {
		t.Fatalf("unexpected %+v", start)
	}
	if e := h.failAs("login_2fa", start.sess(), map[string]any{"code": "000000"}); e.Code != protocol.CodeTwoFactorFailed {
		t.Fatalf("wrong code gave %s", e.Code)
	}
	var tf struct {
		Next string `json:"next"`
	}
	h.okAs("login_2fa", start.sess(), map[string]any{"code": "123456"}, &tf)
	if tf.Next != "unlock" {
		t.Fatal(tf.Next)
	}
	e := h.failAs("login_unlock", start.sess(), map[string]any{"username": "alice", "password": "correct horse"})
	if e.Code != protocol.CodeWrongMailboxPassword || !strings.Contains(e.Message, "mailbox password") {
		t.Fatalf("missing mailbox password gave %s: %s", e.Code, e.Message)
	}
	var unlocked unlockResult
	h.okAs("login_unlock", start.sess(), map[string]any{"username": "alice", "password": "correct horse", "mailbox_password": "mailbox secret"}, &unlocked)
	if unlocked.Session.KeyPassphrase == "" {
		t.Fatal("no key passphrase")
	}
	if h.countCalls("PUT", "/core/v4/users/unlock") == 0 || h.countCalls("PUT", "/core/v4/users/lock") == 0 {
		t.Fatal("a salts read without scope should unlock the scope and lock it again")
	}
}

func TestRefreshRotatesAndOldTokenDies(t *testing.T) {
	h := newHarness(t)
	var first struct {
		Session struct {
			UID          string `json:"uid"`
			AccessToken  string `json:"access_token"`
			RefreshToken string `json:"refresh_token"`
		} `json:"session"`
	}
	h.okAs("refresh", &protocol.Session{UID: h.sess.UID, RefreshToken: h.sess.RefreshToken}, map[string]any{}, &first)
	if first.Session.RefreshToken == h.sess.RefreshToken || first.Session.AccessToken == h.sess.AccessToken {
		t.Fatal("refresh did not rotate the tokens")
	}
	if first.Session.RefreshToken != h.s.RefreshToken(h.sess.UID) {
		t.Fatal("the returned refresh token is not the one Proton holds")
	}
	e := h.failAs("refresh", &protocol.Session{UID: h.sess.UID, RefreshToken: h.sess.RefreshToken}, map[string]any{})
	if e.Code != protocol.CodeAuthExpired || e.Details["reason"] != "refresh_refused" {
		t.Fatalf("an old refresh token gave %s %v", e.Code, e.Details)
	}
	if h.countCalls("POST", "/auth/v4/refresh") != 2 {
		t.Fatal("each refresh sends the token exactly once")
	}
}

func TestOnlyRefreshRotatesTokens(t *testing.T) {
	h := newHarness(t)
	h.s.ExpireAccess(h.sess.UID)
	for _, op := range []struct {
		name string
		args any
	}{
		{"list_calendars", map[string]any{}},
		{"list_events", map[string]any{"calendar_id": h.cal, "window": map[string]string{"start": "2026-12-01T00:00:00Z", "end": "2026-12-31T00:00:00Z"}}},
	} {
		e := h.fail(op.name, op.args)
		if e.Code != protocol.CodeAccessTokenExpired {
			t.Fatalf("%s gave %s", op.name, e.Code)
		}
	}
	h.noCalls("/auth/v4/refresh", "/calendar/v1/"+h.cal+"/events/sync")
}

func TestLogout(t *testing.T) {
	h := newHarness(t)
	var out map[string]bool
	h.ok("logout", map[string]any{}, &out)
	if !out["ended"] {
		t.Fatal("logout did not end the session")
	}
	h.ok("logout", map[string]any{}, &out)
	if out["ended"] {
		t.Fatal("a second logout should report the session already over")
	}
	if e := h.fail("list_calendars", map[string]any{}); e.Code != protocol.CodeAccessTokenExpired {
		t.Fatalf("got %s", e.Code)
	}
}

func TestInvalidRequests(t *testing.T) {
	h := newHarness(t)
	cases := []string{
		`not json`,
		`{"v":2,"op":"version"}`,
		`{"v":1,"op":"nope"}`,
		`{"v":1,"op":"version","extra":1}`,
		`{"v":1,"op":"list_calendars"}`,
		`{"v":1,"op":"list_events","session":{"uid":"u","access_token":"a"},"args":{}}`,
		`{"v":1,"op":"list_calendars","session":{"uid":"u","access_token":"a"},"args":{"unknown":1}}`,
	}
	for _, c := range cases {
		resp := Run(t.Context(), []byte(c))
		if resp.OK || resp.Error.Code != protocol.CodeInvalidRequest {
			t.Fatalf("%s gave %+v", c, resp.Error)
		}
	}
	_ = h
}

func TestVersion(t *testing.T) {
	resp := Run(t.Context(), []byte(`{"v":1,"op":"version"}`))
	if !resp.OK {
		t.Fatal(resp.Error)
	}
	m := resp.Result.(map[string]any)
	ops := m["operations"].([]string)
	if m["app_version"] != "Other" || m["protocol"] != 1 || len(ops) != len(handlers) || ops[0] > ops[len(ops)-1] {
		t.Fatalf("version result %v", m)
	}
}
