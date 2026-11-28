// Package ops reads one protocol request, runs the operation and builds the
// response. cmd/nojoin-proton wraps it with the process rules (stdin, stdout,
// limits).
package ops

import (
	"context"
	"encoding/json"
	"errors"
	"runtime"
	"sort"
	"time"

	"github.com/Valtora/Nojoin/proton/internal/auth"
	"github.com/Valtora/Nojoin/proton/internal/calendar"
	"github.com/Valtora/Nojoin/proton/internal/papi"
	"github.com/Valtora/Nojoin/proton/internal/protocol"
)

const (
	defaultTimeout = 25 * time.Second
	maxTimeout     = 600 * time.Second
	maxListWindow  = 93 * 24 * time.Hour
)

type need struct{ uid, access, refresh, keys bool }

type handler struct {
	need need
	run  func(ctx context.Context, env *env) (any, error)
}

// env is one operation's context.
type env struct {
	req    protocol.Request
	sess   protocol.Session
	client *papi.Client
}

func (e *env) args(v any) error {
	if perr := protocol.DecodeArgs(e.req.Args, v); perr != nil {
		return perr
	}
	return nil
}

var handlers = map[string]handler{}

// register adds an operation; each operation file registers its own.
func register(name string, h handler) { handlers[name] = h }

var (
	noSession   = need{}
	tokenOnly   = need{uid: true, access: true}
	withKeys    = need{uid: true, access: true, keys: true}
	refreshOnly = need{uid: true, refresh: true}
)

func init() {
	register("version", handler{need: noSession, run: opVersion})
	register("validate", handler{need: noSession, run: opValidate})
	register("login_start", handler{need: noSession, run: opLoginStart})
	register("login_2fa", handler{need: tokenOnly, run: opLogin2FA})
	register("login_unlock", handler{need: tokenOnly, run: opLoginUnlock})
	register("refresh", handler{need: refreshOnly, run: opRefresh})
	register("logout", handler{need: tokenOnly, run: opLogout})
	register("list_calendars", handler{need: tokenOnly, run: opListCalendars})
	register("list_events", handler{need: withKeys, run: opListEvents})
	register("changes", handler{need: withKeys, run: opChanges})
	register("get_event", handler{need: withKeys, run: opGetEvent})
}

// Operations lists the operation names, sorted.
func Operations() []string {
	out := make([]string, 0, len(handlers))
	for name := range handlers {
		out = append(out, name)
	}
	sort.Strings(out)
	return out
}

func fail(e *protocol.Error) protocol.Response {
	if e.Details == nil {
		e.Details = map[string]any{}
	}
	return protocol.Response{V: protocol.Version, OK: false, Error: e}
}

// Run executes one request already read from stdin. It never panics on bad
// input: every failure is a protocol error.
func Run(ctx context.Context, raw []byte) protocol.Response {
	var req protocol.Request
	if err := protocol.DecodeStrict(raw, &req); err != nil {
		return fail(protocol.Errorf(protocol.CodeInvalidRequest, "The request is not a protocol object."))
	}
	if req.V != protocol.Version {
		return fail(protocol.Errorf(protocol.CodeInvalidRequest, "The request is for protocol %d; this helper speaks %d.", req.V, protocol.Version))
	}
	h, ok := handlers[req.Op]
	if !ok {
		return fail(protocol.Errorf(protocol.CodeInvalidRequest, "There is no operation %q.", req.Op))
	}
	var sess protocol.Session
	if req.Session != nil {
		sess = *req.Session
	}
	missing := (h.need.uid && sess.UID == "") || (h.need.access && sess.AccessToken == "") ||
		(h.need.refresh && sess.RefreshToken == "") || (h.need.keys && sess.KeyPassphrase == "")
	if missing {
		return fail(protocol.Errorf(protocol.CodeInvalidRequest, "The session is missing a field %s needs.", req.Op))
	}
	cl := protocol.Client{}
	if req.Client != nil {
		cl = *req.Client
	}
	timeout := defaultTimeout
	if cl.TimeoutMS > 0 {
		timeout = time.Duration(cl.TimeoutMS) * time.Millisecond
		if timeout > maxTimeout {
			timeout = maxTimeout
		}
	}
	ctx, cancel := context.WithTimeout(ctx, timeout)
	defer cancel()
	base := ""
	if cl.APIBaseURL != nil {
		base = *cl.APIBaseURL
	}
	client := papi.New(base, cl.UserAgent)
	client.UID, client.AccessToken = sess.UID, sess.AccessToken
	e := &env{req: req, sess: sess, client: client}
	result, err := h.run(ctx, e)
	if err != nil {
		var pe *protocol.Error
		if !errors.As(err, &pe) {
			pe = papi.ToProtocol(err, false)
		}
		return fail(pe)
	}
	return protocol.Response{V: protocol.Version, OK: true, Result: result}
}

func opVersion(context.Context, *env) (any, error) {
	ops := Operations()
	return map[string]any{
		"helper":      "nojoin-proton",
		"protocol":    protocol.Version,
		"go_version":  runtime.Version(),
		"app_version": papi.AppVersion,
		"operations":  ops,
	}, nil
}

type validateArgs struct {
	Event  *calendar.EventFields `json:"event"`
	Patch  *calendar.Patch       `json:"patch"`
	AllDay *bool                 `json:"all_day"`
}

func opValidate(_ context.Context, e *env) (any, error) {
	var a validateArgs
	if err := e.args(&a); err != nil {
		return nil, err
	}
	problems := []calendar.Problem{}
	switch {
	case a.Event != nil && a.Patch == nil:
		problems = append(problems, calendar.ValidateFields(*a.Event, "event")...)
	case a.Patch != nil && a.Event == nil:
		problems = append(problems, calendar.ValidatePatch(*a.Patch, "patch")...)
		allDay := a.AllDay != nil && *a.AllDay
		if a.Patch.AllDay != nil {
			allDay = *a.Patch.AllDay
		}
		for _, t := range []struct {
			v     *calendar.TimeArg
			field string
		}{{a.Patch.Start, "patch.start"}, {a.Patch.End, "patch.end"}} {
			if t.v != nil {
				if _, perr := calendar.ParseTime(t.v, t.field, allDay); perr != nil {
					problems = append(problems, calendar.Problem{Field: t.field, Code: perr.Code, Message: perr.Message})
				}
			}
		}
	default:
		return nil, protocol.Errorf(protocol.CodeInvalidRequest, "validate takes event or patch.")
	}
	return map[string]any{"problems": problems}, nil
}

func opLoginStart(ctx context.Context, e *env) (any, error) {
	var a auth.LoginStartArgs
	if err := e.args(&a); err != nil {
		return nil, err
	}
	e.client.UID, e.client.AccessToken = "", ""
	return auth.LoginStart(ctx, e.client, a)
}

func opLogin2FA(ctx context.Context, e *env) (any, error) {
	var a auth.Login2FAArgs
	if err := e.args(&a); err != nil {
		return nil, err
	}
	return auth.Login2FA(ctx, e.client, e.sess, a)
}

func opLoginUnlock(ctx context.Context, e *env) (any, error) {
	var a auth.LoginUnlockArgs
	if err := e.args(&a); err != nil {
		return nil, err
	}
	keys, passphrase, addresses, err := auth.UnlockAccount(ctx, e.client, a)
	if err != nil {
		return nil, err
	}
	defer keys.Clear()
	settings, cals, err := calendar.ListCalendars(ctx, e.client)
	if err != nil {
		return nil, papi.ToProtocol(err, false)
	}
	warnings := []string{}
	for _, ad := range addresses {
		if !ad.KeysUnlocked && ad.Status == "enabled" {
			warnings = append(warnings, "address_keys_locked")
			break
		}
	}
	return map[string]any{
		"session": auth.SessionOut{UID: e.sess.UID, AccessToken: e.sess.AccessToken, RefreshToken: e.sess.RefreshToken, KeyPassphrase: passphrase},
		"user": map[string]string{
			"id": keys.User.ID, "name": keys.User.Name, "display_name": keys.User.DisplayName, "email": keys.User.Email,
		},
		"addresses": addresses,
		"settings":  settings,
		"calendars": cals,
		"warnings":  warnings,
	}, nil
}

func opRefresh(ctx context.Context, e *env) (any, error) {
	if err := e.args(&struct{}{}); err != nil {
		return nil, err
	}
	return auth.Refresh(ctx, e.client, e.sess)
}

func opLogout(ctx context.Context, e *env) (any, error) {
	if err := e.args(&struct{}{}); err != nil {
		return nil, err
	}
	return auth.Logout(ctx, e.client)
}

func opListCalendars(ctx context.Context, e *env) (any, error) {
	if err := e.args(&struct{}{}); err != nil {
		return nil, err
	}
	settings, cals, err := calendar.ListCalendars(ctx, e.client)
	if err != nil {
		return nil, papi.ToProtocol(err, false)
	}
	return map[string]any{"settings": settings, "calendars": cals}, nil
}

// open unlocks the account and one calendar.
func (e *env) open(ctx context.Context, calendarID string) (*calendar.Access, func(), error) {
	keys, err := auth.Unlock(ctx, e.client, e.sess.KeyPassphrase)
	if err != nil {
		return nil, func() {}, err
	}
	access, err := calendar.Open(ctx, e.client, keys, calendarID)
	if err != nil {
		keys.Clear()
		return nil, func() {}, err
	}
	return access, keys.Clear, nil
}

type listArgs struct {
	CalendarID    string              `json:"calendar_id"`
	Window        *calendar.WindowArg `json:"window"`
	IncludeCursor bool                `json:"include_cursor"`
}

func opListEvents(ctx context.Context, e *env) (any, error) {
	var a listArgs
	if err := e.args(&a); err != nil {
		return nil, err
	}
	if a.Window == nil {
		return nil, protocol.FieldError(protocol.CodeInvalidArgument, "window", "list_events needs a window.")
	}
	w, err := calendar.ResolveWindow(a.Window, calendar.Now(), "window", maxListWindow)
	if err != nil {
		return nil, err
	}
	access, done, err := e.open(ctx, a.CalendarID)
	if err != nil {
		return nil, err
	}
	defer done()
	var cursor *string
	if a.IncludeCursor {
		cur, err := calendar.LatestCursor(ctx, e.client, a.CalendarID)
		if err != nil {
			return nil, papi.ToProtocol(err, false)
		}
		cursor = &cur
	}
	records, stats, err := access.ListEvents(ctx, e.client, w)
	if err != nil {
		return nil, papi.ToProtocol(err, false)
	}
	return map[string]any{"events": records, "cursor": cursor, "stats": stats}, nil
}

type changesArgs struct {
	CalendarID string              `json:"calendar_id"`
	Cursor     string              `json:"cursor"`
	Window     *calendar.WindowArg `json:"window"`
}

func opChanges(ctx context.Context, e *env) (any, error) {
	var a changesArgs
	if err := e.args(&a); err != nil {
		return nil, err
	}
	w, err := calendar.ResolveWindow(a.Window, calendar.Now(), "window", 0)
	if err != nil {
		return nil, err
	}
	access, done, err := e.open(ctx, a.CalendarID)
	if err != nil {
		return nil, err
	}
	defer done()
	res, err := access.Changes(ctx, e.client, a.Cursor, w)
	if err != nil {
		return nil, papi.ToProtocol(err, false)
	}
	return res, nil
}

type getArgs struct {
	CalendarID string              `json:"calendar_id"`
	Target     calendar.Target     `json:"target"`
	Window     *calendar.WindowArg `json:"window"`
}

func opGetEvent(ctx context.Context, e *env) (any, error) {
	var a getArgs
	if err := e.args(&a); err != nil {
		return nil, err
	}
	w, err := calendar.ResolveWindow(a.Window, calendar.Now(), "window", 0)
	if err != nil {
		return nil, err
	}
	access, done, err := e.open(ctx, a.CalendarID)
	if err != nil {
		return nil, err
	}
	defer done()
	return access.Get(ctx, e.client, a.Target, w)
}

// Encode renders a response, replacing one larger than limit by
// output_too_large.
func Encode(resp protocol.Response, limit int) []byte {
	b, err := json.Marshal(resp)
	if err != nil {
		b, _ = json.Marshal(fail(protocol.Errorf(protocol.CodeProviderUnavailable, "The result could not be encoded.")))
	}
	if len(b) > limit {
		b, _ = json.Marshal(fail(protocol.Errorf(protocol.CodeOutputTooLarge, "The result is larger than the helper may write; narrow the window.")))
	}
	return b
}
