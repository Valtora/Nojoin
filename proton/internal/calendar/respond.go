package calendar

import (
	"context"

	"github.com/Valtora/Nojoin/proton/internal/papi"
	"github.com/Valtora/Nojoin/proton/internal/protocol"
)

// RespondArgs are respond's arguments.
type RespondArgs struct {
	CalendarID       string     `json:"calendar_id"`
	Target           Target     `json:"target"`
	Response         string     `json:"response"`
	ExpectedRevision *string    `json:"expected_revision"`
	Window           *WindowArg `json:"window"`
}

// Respond records the user's answer to someone else's invitation.
func (a *Access) Respond(ctx context.Context, c *papi.Client, args RespondArgs) (*WriteResult, error) {
	switch args.Response {
	case "accepted", "declined", "tentative":
	default:
		return nil, protocol.FieldError(protocol.CodeInvalidArgument, "response", "response must be accepted, declined or tentative.")
	}
	if args.Target.RecurrenceID != nil && *args.Target.RecurrenceID != "" {
		return nil, protocol.Errorf(protocol.CodeUnsupported, "Answering one occurrence of a recurring invitation is not supported yet.").
			WithDetail("hint", "Answer the whole series, or answer that occurrence in Proton's own app.")
	}
	w, werr := ResolveWindow(args.Window, Now(), "window", 0)
	if werr != nil {
		return nil, werr
	}
	s, err := a.loadSeries(ctx, c, args.Target.UID)
	if err != nil {
		return nil, err
	}
	row := s.master
	if row == nil {
		return nil, protocol.Errorf(protocol.CodeNotFound, "Proton has no such invitation.")
	}
	if bool(row.Row.IsOrganizer) {
		return nil, protocol.Errorf(protocol.CodeNotAttendee, "This is the user's own event: change it with update or cancel instead.")
	}
	if perr := checkRevision(args.ExpectedRevision, row); perr != nil {
		return nil, perr
	}
	clear := row.Row.ClearAttendees()
	self := a.selfSet()
	var mine *ClearAttendee
	for _, p := range row.Merged.All("ATTENDEE") {
		if !self[CanonicalEmail(mailtoAddress(p.Value))] {
			continue
		}
		token := p.Param("X-PM-TOKEN")
		if token == "" {
			token = AttendeeToken(s.uid, mailtoAddress(p.Value))
		}
		for i := range clear {
			if clear[i].Token == token {
				mine = &clear[i]
				break
			}
		}
		if mine != nil {
			break
		}
	}
	if mine == nil {
		for _, e := range a.Keys.Emails() {
			token := AttendeeToken(s.uid, e)
			for i := range clear {
				if clear[i].Token == token {
					mine = &clear[i]
				}
			}
		}
	}
	if mine == nil || mine.ID == "" {
		return nil, protocol.Errorf(protocol.CodeNotAttendee, "The user is not on this event's guest list.")
	}
	res := newResult()
	res.Before = a.Records(Expand(s.rows, w, true))
	answer := StatusCode(args.Response)
	sees := bool(row.Row.IsProtonProtonInvite)
	res.OrganiserSeesAnswer = &sees
	// A Proton-to-Proton invitation is one shared event, so its organiser sees
	// the answer in their own calendar with no mail sent.
	shown := []Person{}
	if org := row.Merged.Get("ORGANIZER"); sees && org != nil {
		shown = append(shown, Person{Name: org.Param("CN"), Email: mailtoAddress(org.Value)})
	}
	res.ShownByProvider = &shown
	ev := row.Merged.Clone()
	statuses := clearAttendees(ev, clear)
	for i := range statuses {
		if statuses[i].Token == mine.Token {
			statuses[i].Status = answer
		}
	}
	res.ITIP = itipSource(ev, statuses)
	if mine.Status == answer {
		res.Events, res.Verified = res.Before, true
		rev := Revision(row.Row, row.Sequence())
		res.Revision = &rev
		return res, nil
	}
	body := map[string]any{"Status": answer, "UpdateTime": Now().Unix(), "Comment": nil}
	if err := c.Put(ctx, "/calendar/v1/"+a.CalendarID+"/events/"+row.Row.ID+"/attendees/"+mine.ID, body, nil); err != nil {
		return nil, papi.ToProtocol(err, papi.WasSent(err))
	}
	res.Written = true
	attending := func(s int) bool { return s == StatusAccepted || s == StatusTentative }
	if attending(mine.Status) != attending(answer) {
		// The web client re-sends the personal part when an answer moves
		// between attending and not, so reminders follow the answer.
		personal := map[string]any{"Notifications": row.Row.NotificationsOrNull(), "Color": row.Row.ColorOrNull()}
		if err := c.Put(ctx, "/calendar/v1/"+a.CalendarID+"/events/"+row.Row.ID+"/personal", personal, nil); err != nil {
			res.warn("personal_part_not_updated")
		}
	}
	a.readBack(ctx, c, s.uid, w, res, answered(s.rows, row, mine.Token, answer))
	res.DeletedKeys = deletedKeys(res.Before, res.Events)
	return res, nil
}

// answered is the series with the user's answer applied, for when the
// read-back fails.
func answered(rows []*Decoded, master *Decoded, token string, status int) []*Decoded {
	out := make([]*Decoded, 0, len(rows))
	for _, d := range rows {
		if d != master {
			out = append(out, d)
			continue
		}
		copied := *d.Row
		list := append([]ClearAttendee(nil), d.Row.ClearAttendees()...)
		for i := range list {
			if list[i].Token == token {
				list[i].Status = status
			}
		}
		copied.Attendees, copied.AttendeesInfo = list, nil
		out = append(out, &Decoded{Row: &copied, Merged: d.Merged, Warnings: d.Warnings})
	}
	return out
}
