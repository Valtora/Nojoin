package calendar

import (
	"encoding/base64"
	"errors"
	"strings"

	"github.com/ProtonMail/gopenpgp/v2/crypto"

	"github.com/Valtora/Nojoin/proton/internal/ical"
)

// Decoded is a decrypted event row.
type Decoded struct {
	Row               *RawEvent
	SharedSigned      *ical.Component
	SharedEncrypted   *ical.Component
	CalendarSigned    *ical.Component
	CalendarEncrypted *ical.Component
	Attendees         *ical.Component
	// Merged is every card joined into one VEVENT, shared signed card first.
	Merged     *ical.Component
	SharedSK   *crypto.SessionKey
	CalendarSK *crypto.SessionKey
	Warnings   []string
}

// ErrUndecryptable means not even the scheduling card could be read.
var ErrUndecryptable = errors.New("event cannot be read")

func (d *Decoded) warn(w string) {
	for _, x := range d.Warnings {
		if x == w {
			return
		}
	}
	d.Warnings = append(d.Warnings, w)
}

// Decode decrypts a row's cards. A card that cannot be read is skipped with a
// warning; only a missing or unreadable shared signed card fails the event.
func (a *Access) Decode(r *RawEvent) (*Decoded, error) {
	d := &Decoded{Row: r}
	if sk, err := a.sharedSessionKey(r); err == nil {
		d.SharedSK = sk
	}
	if sk, err := a.calendarSessionKey(r); err == nil {
		d.CalendarSK = sk
	}
	for _, c := range r.SharedEvents {
		comp, err := a.readCard(c, d.SharedSK, d)
		if err != nil {
			if c.Encrypted() {
				d.warn("shared_encrypted")
			}
			continue
		}
		if c.Encrypted() {
			d.SharedEncrypted = comp
		} else {
			d.SharedSigned = comp
		}
	}
	for _, c := range r.CalendarEvents {
		comp, err := a.readCard(c, d.CalendarSK, d)
		if err != nil {
			if c.Encrypted() {
				d.warn("calendar_encrypted")
			}
			continue
		}
		if c.Encrypted() {
			d.CalendarEncrypted = comp
		} else {
			d.CalendarSigned = comp
		}
	}
	for _, c := range r.AttendeesEvents {
		comp, err := a.readCard(c, d.SharedSK, d)
		if err != nil {
			d.warn("attendees")
			continue
		}
		d.Attendees = comp
	}
	if d.SharedSigned == nil {
		return nil, ErrUndecryptable
	}
	d.Merged = ical.Merge(d.SharedSigned, d.SharedEncrypted, d.CalendarSigned, d.CalendarEncrypted, d.Attendees)
	return d, nil
}

// readCard returns a card's VEVENT, decrypting it when needed. Signatures are
// checked against the user's own address keys; a card signed by someone else
// (an invitation) cannot be checked without their keys and is read anyway.
func (a *Access) readCard(c Card, sk *crypto.SessionKey, d *Decoded) (*ical.Component, error) {
	var plain []byte
	switch {
	case c.Encrypted():
		if sk == nil {
			return nil, errors.New("no session key")
		}
		data, err := base64.StdEncoding.DecodeString(c.Data)
		if err != nil {
			return nil, err
		}
		msg, err := sk.Decrypt(data)
		if err != nil {
			return nil, err
		}
		// GetBinary, never GetString: the string form rewrites CRLF.
		plain = msg.GetBinary()
	default:
		plain = []byte(c.Data)
	}
	if c.Signature != "" {
		sig, err := crypto.NewPGPSignatureFromArmored(c.Signature)
		if err != nil || a.AllAddrKR.VerifyDetached(crypto.NewPlainMessage(plain), sig, crypto.GetUnixTime()) != nil {
			d.warn("signature_unverified")
		}
	}
	return ical.FirstEvent(string(plain))
}

// propText returns a TEXT property's unescaped value, or "".
func propText(c *ical.Component, name string) string {
	if c == nil {
		return ""
	}
	if p := c.Get(name); p != nil {
		return p.Text()
	}
	return ""
}

// IsHold reports whether the event is a hold.
func (d *Decoded) IsHold() bool {
	if p := d.Merged.Get(ical.HoldProperty); p != nil && strings.EqualFold(strings.TrimSpace(p.Value), "TRUE") {
		return true
	}
	return strings.EqualFold(propText(d.Merged, "STATUS"), "TENTATIVE")
}

// Sequence returns the event's SEQUENCE, 0 when absent.
func (d *Decoded) Sequence() int {
	return sequenceOf(d.Merged)
}

func sequenceOf(c *ical.Component) int {
	p := c.Get("SEQUENCE")
	if p == nil {
		return 0
	}
	n := 0
	for _, ch := range strings.TrimSpace(p.Value) {
		if ch < '0' || ch > '9' {
			return 0
		}
		n = n*10 + int(ch-'0')
		if n > 1<<30 {
			return 0
		}
	}
	return n
}
