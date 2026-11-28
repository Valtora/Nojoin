package ops

import (
	"context"

	"github.com/Valtora/Nojoin/proton/internal/calendar"
)

func init() {
	register("create_event", handler{need: withKeys, run: opCreate})
	register("update_event", handler{need: withKeys, run: opUpdate})
	register("cancel_event", handler{need: withKeys, run: opCancel})
}

func decodeAndRun[T any](ctx context.Context, e *env, calendarID func(*T) string, run func(*calendar.Access, *T) (any, error)) (any, error) {
	var a T
	if err := e.args(&a); err != nil {
		return nil, err
	}
	access, done, err := e.open(ctx, calendarID(&a))
	if err != nil {
		return nil, err
	}
	defer done()
	return run(access, &a)
}

func opCreate(ctx context.Context, e *env) (any, error) {
	return decodeAndRun(ctx, e, func(a *calendar.CreateArgs) string { return a.CalendarID },
		func(acc *calendar.Access, a *calendar.CreateArgs) (any, error) { return acc.Create(ctx, e.client, *a) })
}

func opUpdate(ctx context.Context, e *env) (any, error) {
	return decodeAndRun(ctx, e, func(a *calendar.UpdateArgs) string { return a.CalendarID },
		func(acc *calendar.Access, a *calendar.UpdateArgs) (any, error) { return acc.Update(ctx, e.client, *a) })
}

func opCancel(ctx context.Context, e *env) (any, error) {
	return decodeAndRun(ctx, e, func(a *calendar.CancelArgs) string { return a.CalendarID },
		func(acc *calendar.Access, a *calendar.CancelArgs) (any, error) { return acc.Cancel(ctx, e.client, *a) })
}
