package calendar

import (
	"context"
	"errors"
	"net/mail"
	"strings"
	"unicode/utf8"

	"github.com/ProtonMail/gluon/rfc822"
	proton "github.com/ProtonMail/go-proton-api"
	"github.com/ProtonMail/gopenpgp/v2/crypto"

	"github.com/Valtora/Nojoin/proton/internal/ical"
	"github.com/Valtora/Nojoin/proton/internal/papi"
	"github.com/Valtora/Nojoin/proton/internal/protocol"
)

// Recipient is one person an iTIP message goes to.
type Recipient struct {
	Email string  `json:"email"`
	Name  *string `json:"name"`
}

// SendITIPArgs are send_itip's arguments.
type SendITIPArgs struct {
	CalendarID   string      `json:"calendar_id"`
	Method       string      `json:"method"`
	ICS          string      `json:"ics"`
	RecurrenceID *string     `json:"recurrence_id"`
	Recipients   []Recipient `json:"recipients"`
	Subject      string      `json:"subject"`
	Body         string      `json:"body"`
	Response     *string     `json:"response"`
	Note         *string     `json:"note"`
}

// SentTo is one recipient a message went to.
type SentTo struct {
	Email  string `json:"email"`
	Scheme string `json:"scheme"`
}

// NotSent is one recipient a message did not reach.
type NotSent struct {
	Email  string `json:"email"`
	Error  string `json:"error,omitempty"`
	Reason string `json:"reason,omitempty"`
}

// SendITIPResult is send_itip's result.
type SendITIPResult struct {
	MessageID *string   `json:"message_id"`
	Sent      []SentTo  `json:"sent"`
	Failed    []NotSent `json:"failed"`
	Skipped   []NotSent `json:"skipped"`
}

const (
	maxSubject = 255
	maxBody    = 10000
	maxNote    = 500
	maxICS     = 1 << 20
)

func (args SendITIPArgs) check() *protocol.Error {
	switch args.Method {
	case "REQUEST", "CANCEL", "REPLY":
	default:
		return protocol.FieldError(protocol.CodeInvalidArgument, "method", "method must be REQUEST, CANCEL or REPLY.")
	}
	if args.ICS == "" || len(args.ICS) > maxICS {
		return protocol.FieldError(protocol.CodeInvalidArgument, "ics", "ics must be the itip.ics a write returned.")
	}
	if len(args.Recipients) == 0 || len(args.Recipients) > MaxAttendees {
		return protocol.FieldError(protocol.CodeInvalidArgument, "recipients", "recipients must list 1 to %d people.", MaxAttendees)
	}
	for _, r := range args.Recipients {
		if !validEmail(strings.TrimSpace(r.Email)) {
			return protocol.FieldError(protocol.CodeInvalidArgument, "recipients", "recipients holds something that is not an email address.")
		}
	}
	if strings.TrimSpace(args.Subject) == "" || utf8.RuneCountInString(args.Subject) > maxSubject || strings.ContainsAny(args.Subject, "\r\n") {
		return protocol.FieldError(protocol.CodeInvalidArgument, "subject", "subject must be one line of 1 to %d characters.", maxSubject)
	}
	if utf8.RuneCountInString(args.Body) > maxBody {
		return protocol.FieldError(protocol.CodeInvalidArgument, "body", "body is longer than %d characters.", maxBody)
	}
	if args.Method == "REPLY" {
		if args.Response == nil || (*args.Response != "accepted" && *args.Response != "declined" && *args.Response != "tentative") {
			return protocol.FieldError(protocol.CodeInvalidArgument, "response", "A REPLY needs response accepted, declined or tentative.")
		}
	}
	if args.Note != nil && utf8.RuneCountInString(*args.Note) > maxNote {
		return protocol.FieldError(protocol.CodeInvalidArgument, "note", "note is longer than %d characters.", maxNote)
	}
	if args.RecurrenceID != nil && *args.RecurrenceID != "" && args.Method != "CANCEL" {
		return protocol.FieldError(protocol.CodeInvalidArgument, "recurrence_id", "recurrence_id applies to a CANCEL only.")
	}
	return nil
}

// privateProperties never leave the user's own calendar.
var privateProperties = []string{ical.HoldProperty, "COMMENT", "X-PM-SESSION-KEY", "COLOR"}

// BuildITIP renders the scheduling message's iCalendar body.
func (a *Access) BuildITIP(args SendITIPArgs, recipients []Recipient) (string, *protocol.Error) {
	ev, err := ical.FirstEvent(args.ICS)
	if err != nil {
		return "", protocol.FieldError(protocol.CodeInvalidArgument, "ics", "ics is not an iCalendar event.")
	}
	ev = ev.Clone()
	for _, name := range privateProperties {
		ev.Remove(name)
	}
	ev.Children = nil
	for i, p := range ev.Props {
		if p.Name == "ATTENDEE" {
			ev.Props[i] = p.WithoutParam("X-PM-TOKEN")
		}
	}
	ev.Set(ical.Property{Name: "DTSTAMP", Value: Now().Format("20060102T150405Z")})
	if ev.Get("ORGANIZER") == nil {
		ev.Set(a.organiserProperty())
	}
	switch args.Method {
	case "CANCEL":
		if args.RecurrenceID != nil && *args.RecurrenceID != "" && ev.Get("RRULE") != nil {
			orig, allDay, perr := ical.ParseRecurrenceKey(*args.RecurrenceID)
			if perr != nil {
				return "", protocol.FieldError(protocol.CodeInvalidArgument, "recurrence_id", "recurrence_id is not a recurrence key.")
			}
			start, end, _ := eventTimes(ev)
			m := ical.Moment{AllDay: allDay, Date: orig}
			if !allDay {
				loc, _ := ical.LoadZone(start.Zone)
				m = ical.Moment{Time: orig.In(loc), Zone: start.Zone}
			}
			ev.Remove("RRULE")
			ev.Remove("EXDATE")
			length := end.Instant().Sub(start.Instant())
			var e ical.Moment
			if allDay {
				e = ical.Moment{AllDay: true, Date: m.Date.Add(length)}
			} else {
				e = ical.Moment{Time: m.Time.Add(length), Zone: end.Zone}
			}
			setTimes(ev, m, e)
			ev.Set(ical.MomentProperty("RECURRENCE-ID", m))
		}
		ev.Set(ical.Property{Name: "STATUS", Value: "CANCELLED"})
		ev.Remove("ATTENDEE")
		for _, r := range recipients {
			ev.Add(cancelAttendee(r))
		}
	case "REPLY":
		self := a.selfSet()
		var mine *ical.Property
		for _, p := range ev.All("ATTENDEE") {
			if self[CanonicalEmail(mailtoAddress(p.Value))] {
				q := p.Clone()
				mine = &q
				break
			}
		}
		if mine == nil {
			q := ical.Property{Name: "ATTENDEE", Value: "mailto:" + a.Email}
			mine = &q
		}
		reply := mine.WithParam("PARTSTAT", Partstat(*args.Response)).WithoutParam("RSVP")
		ev.Remove("ATTENDEE")
		ev.Add(reply)
		if args.Note != nil && strings.TrimSpace(*args.Note) != "" {
			ev.SetText("COMMENT", *args.Note)
		}
	}
	cal := &ical.Component{Name: "VCALENDAR", Props: []ical.Property{
		{Name: "VERSION", Value: "2.0"},
		{Name: "PRODID", Value: ProdID},
		{Name: "CALSCALE", Value: "GREGORIAN"},
		{Name: "METHOD", Value: args.Method},
	}, Children: []*ical.Component{ev}}
	return ical.Serialize(cal) + "\r\n", nil
}

func cancelAttendee(r Recipient) ical.Property {
	p := ical.Property{Name: "ATTENDEE", Value: "mailto:" + strings.TrimSpace(r.Email)}
	if r.Name != nil && sanitiseParam(*r.Name) != "" {
		p.Params = []ical.Param{{Name: "CN", Values: []string{sanitiseParam(*r.Name)}}}
	}
	return p
}

// SendITIP mails a scheduling message. It is the only operation that touches a
// mail endpoint. The route is carbonate's, live-verified there: a draft, the
// encrypted invite.ics attachment, then send, with Proton recipients in an
// internal package encrypted to their keys and everyone else in a clear one.
func (a *Access) SendITIP(ctx context.Context, c *papi.Client, args SendITIPArgs) (*SendITIPResult, error) {
	if perr := args.check(); perr != nil {
		return nil, perr
	}
	res := &SendITIPResult{Sent: []SentTo{}, Failed: []NotSent{}, Skipped: []NotSent{}}
	self := a.selfSet()
	seen := map[string]bool{}
	var recipients []Recipient
	for _, r := range args.Recipients {
		canon := CanonicalEmail(r.Email)
		if self[canon] {
			res.Skipped = append(res.Skipped, NotSent{Email: r.Email, Reason: "is_you"})
			continue
		}
		if seen[canon] {
			continue
		}
		seen[canon] = true
		recipients = append(recipients, r)
	}
	ics, perr := a.BuildITIP(args, recipients)
	if perr != nil {
		return nil, perr
	}
	internal := map[string]proton.SendPreferences{}
	external := map[string]proton.SendPreferences{}
	var to []*mail.Address
	for _, r := range recipients {
		kr, err := lookupProtonKeys(ctx, c, r.Email)
		if errors.Is(err, papi.ErrAccessTokenExpired) {
			return nil, papi.ToProtocol(err, false)
		}
		if err != nil {
			res.Failed = append(res.Failed, NotSent{Email: r.Email, Error: papi.ToProtocol(err, false).Code})
			continue
		}
		name := ""
		if r.Name != nil {
			name = *r.Name
		}
		to = append(to, &mail.Address{Name: name, Address: r.Email})
		if kr != nil {
			internal[r.Email] = proton.SendPreferences{Encrypt: true, PubKey: kr, SignatureType: proton.DetachedSignature,
				EncryptionScheme: proton.InternalScheme, MIMEType: rfc822.TextPlain}
			res.Sent = append(res.Sent, SentTo{Email: r.Email, Scheme: "internal"})
		} else {
			external[r.Email] = proton.SendPreferences{SignatureType: proton.NoSignature, EncryptionScheme: proton.ClearScheme, MIMEType: rfc822.TextPlain}
			res.Sent = append(res.Sent, SentTo{Email: r.Email, Scheme: "clear"})
		}
	}
	if len(to) == 0 {
		return res, nil
	}
	key, err := a.AddrKR.FirstKey()
	if err != nil {
		return nil, protocol.Errorf(protocol.CodeProviderRejected, "The sender's address key cannot be used.")
	}
	enc, err := key.Encrypt(crypto.NewPlainMessageFromString(args.Body), nil)
	if err != nil {
		return nil, err
	}
	armoured, err := enc.GetArmored()
	if err != nil {
		return nil, err
	}
	draftReq := proton.CreateDraftReq{Message: proton.DraftTemplate{
		Subject:  args.Subject,
		Sender:   &mail.Address{Name: a.displayName(), Address: a.Email},
		ToList:   to,
		CCList:   []*mail.Address{},
		BCCList:  []*mail.Address{},
		Body:     armoured,
		MIMEType: rfc822.TextPlain,
	}, AttachmentKeyPackets: []string{}}
	var draft struct{ Message proton.Message }
	if err := c.Post(ctx, "/mail/v4/messages", draftReq, &draft); err != nil {
		return nil, papi.ToProtocol(err, false)
	}
	mime := "text/calendar; method=" + args.Method + "; charset=utf-8"
	sig, err := key.SignDetached(crypto.NewPlainMessage([]byte(ics)))
	if err != nil {
		return nil, err
	}
	split, err := key.EncryptAttachment(crypto.NewPlainMessage([]byte(ics)), "invite.ics")
	if err != nil {
		return nil, err
	}
	var att struct{ Attachment proton.Attachment }
	if err := c.PostMultipart(ctx, "/mail/v4/attachments", []papi.MultipartField{
		{Name: "MessageID", Value: draft.Message.ID},
		{Name: "Filename", Value: "invite.ics"},
		{Name: "MIMEType", Value: mime},
		{Name: "Disposition", Value: "attachment"},
		{Name: "ContentID", Value: ""},
		{Name: "KeyPackets", FileName: "blob", Data: split.KeyPacket},
		{Name: "DataPacket", FileName: "blob", Data: split.DataPacket},
		{Name: "Signature", FileName: "blob", Data: sig.GetBinary()},
	}, &att); err != nil {
		return nil, papi.ToProtocol(err, false)
	}
	attKey, err := key.DecryptSessionKey(split.KeyPacket)
	if err != nil {
		return nil, err
	}
	var send proton.SendDraftReq
	for _, group := range []map[string]proton.SendPreferences{internal, external} {
		if len(group) == 0 {
			continue
		}
		if err := send.AddTextPackage(a.AddrKR, args.Body, rfc822.TextPlain, group, map[string]*crypto.SessionKey{att.Attachment.ID: attKey}); err != nil {
			return nil, protocol.Errorf(protocol.CodeProviderRejected, "The message could not be prepared for its recipients.")
		}
	}
	var sent struct{ Sent proton.Message }
	if err := c.Post(ctx, "/mail/v4/messages/"+draft.Message.ID, send, &sent); err != nil {
		return nil, papi.ToProtocol(err, papi.WasSent(err))
	}
	id := sent.Sent.ID
	if id == "" {
		id = draft.Message.ID
	}
	res.MessageID = &id
	return res, nil
}
