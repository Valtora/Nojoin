package calendar

import (
	"context"
	"encoding/base64"
	"errors"
	"net/url"
	"sort"
	"strconv"

	proton "github.com/ProtonMail/go-proton-api"
	"github.com/ProtonMail/gopenpgp/v2/crypto"

	"github.com/Valtora/Nojoin/proton/internal/ical"
	"github.com/Valtora/Nojoin/proton/internal/papi"
)

// lookupProtonKeys returns the keyring to share an event with an address on
// Proton, or nil for anyone else. This reads core/v4/keys only, never mail.
func lookupProtonKeys(ctx context.Context, c *papi.Client, email string) (*crypto.KeyRing, error) {
	var res struct {
		Keys          []proton.PublicKey
		RecipientType proton.RecipientType
	}
	q := url.Values{}
	q.Set("Email", email)
	if err := c.Get(ctx, "/core/v4/keys", q, &res); err != nil {
		return nil, err
	}
	if res.RecipientType != proton.RecipientTypeInternal {
		return nil, nil
	}
	var usable proton.PublicKeys
	for _, k := range res.Keys {
		if k.Flags&proton.KeyStateActive != 0 {
			usable = append(usable, k)
		}
	}
	if len(usable) == 0 {
		return nil, nil
	}
	return usable.GetKeyRing()
}

// shareWith wraps the shared session key to each address on Proton. Addresses
// whose keys cannot be read are counted, not fatal: the event is still written.
func (a *Access) shareWith(ctx context.Context, c *papi.Client, sk *crypto.SessionKey, emails []string) ([]AddedAttendee, int, error) {
	var out []AddedAttendee
	failed := 0
	self := map[string]bool{}
	for _, e := range a.Keys.Emails() {
		self[CanonicalEmail(e)] = true
	}
	seen := map[string]bool{}
	for _, email := range emails {
		canon := CanonicalEmail(email)
		if self[canon] || seen[canon] {
			continue
		}
		seen[canon] = true
		kr, err := lookupProtonKeys(ctx, c, email)
		if errors.Is(err, papi.ErrAccessTokenExpired) {
			return nil, 0, err
		}
		if err != nil {
			failed++
			continue
		}
		if kr == nil {
			continue
		}
		packet, err := kr.EncryptSessionKey(sk)
		if err != nil {
			failed++
			continue
		}
		out = append(out, AddedAttendee{Email: email, AddressKeyPacket: base64.StdEncoding.EncodeToString(packet)})
	}
	return out, failed, nil
}

// shareCreate hands a new event's shared key to its Proton attendees when the
// caller asked for attendees to be told. Without it nothing is looked up and
// no AddedProtonAttendees is sent.
func (a *Access) shareCreate(ctx context.Context, c *papi.Client, body *EventBody, sk *crypto.SessionKey, ev *ical.Component, share bool, res *WriteResult) error {
	if !share {
		return nil
	}
	var emails []string
	for _, p := range ev.All("ATTENDEE") {
		emails = append(emails, mailtoAddress(p.Value))
	}
	added, failed, err := a.shareWith(ctx, c, sk, emails)
	if err != nil {
		return papi.ToProtocol(err, false)
	}
	body.AddedProtonAttendees = added
	if failed > 0 {
		res.warn("attendee_keys_unavailable:" + strconv.Itoa(failed))
	}
	return nil
}

// shareTargets are the addresses a write hands the shared key to: attendees it
// adds, plus those the caller says were never invited.
func shareTargets(before, after *ical.Component, extra []string) []string {
	old := attendeeEmails(before)
	now := attendeeEmails(after)
	var out []string
	for canon, e := range now {
		if _, ok := old[canon]; !ok {
			out = append(out, e)
		}
	}
	for _, e := range extra {
		if _, ok := now[CanonicalEmail(e)]; ok {
			out = append(out, e)
		}
	}
	sort.Strings(out)
	return out
}

func removedAddresses(before, after *ical.Component) []string {
	now := attendeeEmails(after)
	var out []string
	for canon, e := range attendeeEmails(before) {
		if _, ok := now[canon]; !ok {
			out = append(out, e)
		}
	}
	sort.Strings(out)
	return out
}

// share adds key packets and removed addresses to a body when the caller asked
// for attendees to be told. Without it, neither field is ever sent.
func (a *Access) share(ctx context.Context, c *papi.Client, body *EventBody, sk *crypto.SessionKey, before, after *ical.Component, args UpdateArgs, res *WriteResult) error {
	if !args.ShareWithAttendees {
		return nil
	}
	added, failed, err := a.shareWith(ctx, c, sk, shareTargets(before, after, args.ShareWith))
	if err != nil {
		return papi.ToProtocol(err, false)
	}
	body.AddedProtonAttendees = added
	body.RemovedAttendeeAddresses = removedAddresses(before, after)
	if failed > 0 {
		res.warn("attendee_keys_unavailable:" + strconv.Itoa(failed))
	}
	return nil
}
