package calendar

import (
	"context"

	"github.com/ProtonMail/gopenpgp/v2/crypto"

	"github.com/Valtora/Nojoin/proton/internal/ical"
	"github.com/Valtora/Nojoin/proton/internal/papi"
	"github.com/Valtora/Nojoin/proton/internal/protocol"
)

// Handing an event's key to its attendees arrives with invitations. Until
// then a request for it is refused, never ignored.
func notYetShared() error {
	return protocol.Errorf(protocol.CodeUnsupported, "Sharing an event with its attendees is not available yet.").
		WithDetail("hint", "Write the event with share_with_attendees false.")
}

func (a *Access) shareCreate(_ context.Context, _ *papi.Client, _ *EventBody, _ *crypto.SessionKey, _ *ical.Component, share bool, _ *WriteResult) error {
	if share {
		return notYetShared()
	}
	return nil
}

func (a *Access) share(_ context.Context, _ *papi.Client, _ *EventBody, _ *crypto.SessionKey, _, _ *ical.Component, args UpdateArgs, _ *WriteResult) error {
	if args.ShareWithAttendees {
		return notYetShared()
	}
	return nil
}
