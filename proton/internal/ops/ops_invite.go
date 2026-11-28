package ops

import (
	"context"

	"github.com/Valtora/Nojoin/proton/internal/calendar"
)

func init() {
	register("respond", handler{need: withKeys, run: opRespond})
	register("send_itip", handler{need: withKeys, run: opSendITIP})
}

func opRespond(ctx context.Context, e *env) (any, error) {
	return decodeAndRun(ctx, e, func(a *calendar.RespondArgs) string { return a.CalendarID },
		func(acc *calendar.Access, a *calendar.RespondArgs) (any, error) {
			return acc.Respond(ctx, e.client, *a)
		})
}

func opSendITIP(ctx context.Context, e *env) (any, error) {
	return decodeAndRun(ctx, e, func(a *calendar.SendITIPArgs) string { return a.CalendarID },
		func(acc *calendar.Access, a *calendar.SendITIPArgs) (any, error) {
			return acc.SendITIP(ctx, e.client, *a)
		})
}
