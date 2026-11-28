package papi

import (
	"context"
	"errors"
	"math"

	"github.com/Valtora/Nojoin/proton/internal/protocol"
)

// ToProtocol maps a request error onto the protocol's error codes. wrote says
// whether a write had already been sent in this operation, which turns a lost
// answer into an unknown outcome.
func ToProtocol(err error, wrote bool) *protocol.Error {
	if err == nil {
		return nil
	}
	var pe *protocol.Error
	if errors.As(err, &pe) {
		return pe
	}
	if errors.Is(err, ErrAccessTokenExpired) {
		if wrote {
			return protocol.Errorf(protocol.CodeProviderUnavailable, "Proton refused the session after a write was sent.").
				WithDetail("outcome_unknown", true)
		}
		return protocol.Errorf(protocol.CodeAccessTokenExpired, "Proton refused the access token.").WithProvider(0, 401)
	}
	var ne *NetworkError
	if errors.As(err, &ne) || errors.Is(err, context.DeadlineExceeded) || errors.Is(err, context.Canceled) {
		e := protocol.Errorf(protocol.CodeProviderUnavailable, "Proton did not answer in time.")
		if wrote {
			e.WithDetail("outcome_unknown", true)
		}
		return e
	}
	var ae *APIError
	if errors.As(err, &ae) {
		return fromAPIError(ae)
	}
	return protocol.Errorf(protocol.CodeProviderUnavailable, "The request to Proton failed.")
}

func fromAPIError(ae *APIError) *protocol.Error {
	msg := MaskAddresses(ae.Message)
	if msg == "" {
		msg = "Proton refused the request."
	}
	var e *protocol.Error
	switch {
	case ae.Status == 429:
		e = protocol.Errorf(protocol.CodeRateLimited, "Proton asked Nojoin to slow down.")
		secs := int(math.Ceil(ae.RetryAfter.Seconds()))
		if secs <= 0 {
			secs = 30
		}
		e.WithDetail("retry_after_seconds", secs)
	case ae.Code == CodeAppVersionBad || ae.Code == CodeAppVersionOld || ae.Code == CodeAppVersionFormat:
		e = protocol.Errorf(protocol.CodeProviderRejected, "Proton refused Nojoin's client identity.").WithDetail("reason", "app_version_rejected")
	case ae.Code == CodeRefreshInvalid:
		e = protocol.Errorf(protocol.CodeAuthExpired, "Proton ended the session.").WithDetail("reason", "session_revoked")
	case ae.Code == CodeInsufficientScope:
		e = protocol.Errorf(protocol.CodeAuthExpired, "The Proton session lost the access it needs.").WithDetail("reason", "scope_lost")
	case ae.Code == CodeHumanVerification:
		e = protocol.Errorf(protocol.CodeAuthExpired, "Proton asked for a human verification check.").WithDetail("reason", "human_verification_required")
	case ae.Code == CodeNotFound || ae.Code == CodeEventNotFound || ae.Status == 404 || ae.Status == 410:
		e = protocol.Errorf(protocol.CodeNotFound, "Proton has no such item.")
	case ae.Code == CodeUnsupportedProp:
		e = protocol.Errorf(protocol.CodeInvalidArgument, "Proton refused a value: %s", msg)
	case ae.Status >= 500:
		e = protocol.Errorf(protocol.CodeProviderUnavailable, "Proton is unavailable.")
	default:
		e = protocol.Errorf(protocol.CodeProviderRejected, "Proton refused the request: %s", msg)
	}
	return e.WithProvider(ae.Code, ae.Status)
}

// WasSent reports whether a failed request had been written to the wire, so a
// write's outcome is unknown.
func WasSent(err error) bool {
	var ne *NetworkError
	if errors.As(err, &ne) {
		return ne.Sent
	}
	return errors.Is(err, context.DeadlineExceeded)
}
