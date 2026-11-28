package calendar

import (
	"context"
	"encoding/base64"
	"errors"
	"strings"

	"github.com/ProtonMail/gopenpgp/v2/crypto"

	"github.com/Valtora/Nojoin/proton/internal/auth"
	"github.com/Valtora/Nojoin/proton/internal/papi"
	"github.com/Valtora/Nojoin/proton/internal/protocol"
)

// Access is everything one operation needs to read or write a calendar.
type Access struct {
	CalendarID string
	Member     CalendarMember
	// Email is the member's own address: the organiser of events we write.
	Email string
	// CalKR holds every calendar key that opened; CalPrimary only the key new
	// session keys are wrapped to.
	CalKR      *crypto.KeyRing
	CalPrimary *crypto.KeyRing
	// AddrKR is the member's address keyring, primary key first: it signs
	// every card we write.
	AddrKR *crypto.KeyRing
	// AllAddrKR holds every address key, for invitations whose shared key is
	// wrapped to an address key.
	AllAddrKR *crypto.KeyRing
	Keys      *auth.Keyring
}

// Writable reports whether the member may write to the calendar.
func (a *Access) Writable() bool { return a.Member.Permissions&PermWrite != 0 }

// Open unlocks a calendar's keys with one bootstrap call.
func Open(ctx context.Context, c *papi.Client, keys *auth.Keyring, calendarID string) (*Access, error) {
	if calendarID == "" || strings.ContainsAny(calendarID, "/?#") {
		return nil, protocol.FieldError(protocol.CodeInvalidArgument, "calendar_id", "calendar_id is not a Proton calendar ID.")
	}
	var boot Bootstrap
	if err := c.Get(ctx, "/calendar/v2/"+calendarID+"/bootstrap", nil, &boot); err != nil {
		return nil, papi.ToProtocol(err, false)
	}
	emails := map[string]string{}
	for _, a := range keys.Addresses {
		emails[strings.ToLower(a.Email)] = a.ID
	}
	var member CalendarMember
	addrID := ""
	for _, m := range boot.Members {
		if id, ok := emails[strings.ToLower(m.Email)]; ok {
			member, addrID = m, id
			break
		}
	}
	if member.ID == "" {
		return nil, protocol.Errorf(protocol.CodeNotFound, "This Proton account is not a member of that calendar.")
	}
	if member.AddressID != "" {
		addrID = member.AddressID
	}
	addrKR := keys.AddrKRs[addrID]
	if addrKR == nil {
		return nil, protocol.Errorf(protocol.CodeAuthExpired, "The calendar's address key could not be opened; reconnect the account.").
			WithDetail("reason", "key_passphrase_invalid")
	}
	all := keys.AllAddressKeys()
	if boot.Passphrase == nil {
		return nil, protocol.Errorf(protocol.CodeProviderRejected, "The calendar has no key set up yet.")
	}
	var passphrase []byte
	for _, mp := range boot.Passphrase.MemberPassphrases {
		if mp.MemberID != member.ID {
			continue
		}
		msg, err := crypto.NewPGPMessageFromArmored(mp.Passphrase)
		if err != nil {
			break
		}
		// The passphrase may be encrypted to any of the account's address keys.
		plain, err := all.Decrypt(msg, nil, 0)
		if err != nil {
			break
		}
		passphrase = plain.GetBinary()
	}
	if passphrase == nil {
		return nil, protocol.Errorf(protocol.CodeAuthExpired, "The calendar's passphrase could not be decrypted; reconnect the account.").
			WithDetail("reason", "key_passphrase_invalid")
	}
	calKR, _ := crypto.NewKeyRing(nil)
	primary, _ := crypto.NewKeyRing(nil)
	for _, k := range boot.Keys {
		locked, err := crypto.NewKeyFromArmored(k.PrivateKey)
		if err != nil {
			continue
		}
		key, err := locked.Unlock(passphrase)
		if err != nil {
			continue
		}
		_ = calKR.AddKey(key)
		if k.Flags&2 != 0 && primary.CountEntities() == 0 {
			_ = primary.AddKey(key)
		}
	}
	if calKR.CountEntities() == 0 {
		return nil, protocol.Errorf(protocol.CodeAuthExpired, "The calendar's keys could not be opened; reconnect the account.").
			WithDetail("reason", "key_passphrase_invalid")
	}
	if primary.CountEntities() == 0 {
		first, _ := calKR.GetKey(0)
		_ = primary.AddKey(first)
	}
	return &Access{
		CalendarID: calendarID, Member: member, Email: member.Email,
		CalKR: calKR, CalPrimary: primary, AddrKR: addrKR, AllAddrKR: all, Keys: keys,
	}, nil
}

// signDetached signs plaintext with the member's address key, binary mode.
func (a *Access) signDetached(plain string) (string, error) {
	sig, err := a.AddrKR.SignDetached(crypto.NewPlainMessage([]byte(plain)))
	if err != nil {
		return "", err
	}
	return sig.GetArmored()
}

// sealEncrypted encrypts plain under sk (data packet only) and signs the
// plaintext.
func (a *Access) sealEncrypted(plain string, sk *crypto.SessionKey) (Card, error) {
	sig, err := a.signDetached(plain)
	if err != nil {
		return Card{}, err
	}
	data, err := sk.Encrypt(crypto.NewPlainMessage([]byte(plain)))
	if err != nil {
		return Card{}, err
	}
	return Card{Type: CardEncryptedAndSigned, Data: base64.StdEncoding.EncodeToString(data), Signature: sig}, nil
}

func (a *Access) sealSigned(plain string) (Card, error) {
	sig, err := a.signDetached(plain)
	if err != nil {
		return Card{}, err
	}
	return Card{Type: CardSigned, Data: plain, Signature: sig}, nil
}

// wrapSessionKey encrypts a session key to the calendar's primary key.
func (a *Access) wrapSessionKey(sk *crypto.SessionKey) (string, error) {
	packet, err := a.CalPrimary.EncryptSessionKey(sk)
	if err != nil {
		return "", err
	}
	return base64.StdEncoding.EncodeToString(packet), nil
}

// sessionKey opens a base64 key packet with the calendar keys, or with the
// address keys for an invitation whose key was wrapped to them.
func (a *Access) sessionKey(packetB64 string, byAddress bool) (*crypto.SessionKey, error) {
	if packetB64 == "" {
		return nil, errors.New("no key packet")
	}
	packet, err := base64.StdEncoding.DecodeString(packetB64)
	if err != nil {
		return nil, err
	}
	if byAddress {
		return a.AllAddrKR.DecryptSessionKey(packet)
	}
	return a.CalKR.DecryptSessionKey(packet)
}

// sharedSessionKey opens the event's shared session key.
func (a *Access) sharedSessionKey(r *RawEvent) (*crypto.SessionKey, error) {
	if r.AddressKeyPacket != "" {
		return a.sessionKey(r.AddressKeyPacket, true)
	}
	return a.sessionKey(r.SharedKeyPacket, false)
}

// calendarSessionKey opens the event's calendar session key, or nil when the
// event has none (events from Proton's own apps may not).
func (a *Access) calendarSessionKey(r *RawEvent) (*crypto.SessionKey, error) {
	if r.CalendarKeyPacket == "" {
		return nil, nil
	}
	return a.sessionKey(r.CalendarKeyPacket, false)
}
