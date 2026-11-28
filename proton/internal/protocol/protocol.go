// Package protocol holds the JSON contract between Nojoin's Python backend and
// nojoin-proton, as PROTOCOL.md describes it.
package protocol

import (
	"bytes"
	"encoding/json"
	"errors"
	"fmt"
	"io"
)

// Version is the protocol version this helper speaks.
const Version = 1

// Session carries the Proton session secrets. Only the fields an operation
// needs are required.
type Session struct {
	UID           string `json:"uid,omitempty"`
	AccessToken   string `json:"access_token,omitempty"`
	RefreshToken  string `json:"refresh_token,omitempty"`
	KeyPassphrase string `json:"key_passphrase,omitempty"`
}

// Client carries per-call settings that are not secrets.
type Client struct {
	UserAgent  string  `json:"user_agent,omitempty"`
	TimeoutMS  int     `json:"timeout_ms,omitempty"`
	APIBaseURL *string `json:"api_base_url,omitempty"`
}

// Request is the one JSON object read from stdin.
type Request struct {
	V       int             `json:"v"`
	Op      string          `json:"op"`
	Session *Session        `json:"session,omitempty"`
	Client  *Client         `json:"client,omitempty"`
	Args    json.RawMessage `json:"args,omitempty"`
}

// Response is the one JSON object written to stdout.
type Response struct {
	V      int    `json:"v"`
	OK     bool   `json:"ok"`
	Result any    `json:"result,omitempty"`
	Error  *Error `json:"error,omitempty"`
}

// Error codes, as PROTOCOL.md lists them.
const (
	CodeAccessTokenExpired    = "access_token_expired"
	CodeInvalidRequest        = "invalid_request"
	CodeOutputTooLarge        = "output_too_large"
	CodeLoginFailed           = "login_failed"
	CodeHumanVerification     = "human_verification_required"
	CodeTwoFactorFailed       = "two_factor_failed"
	CodeTwoFactorUnsupported  = "two_factor_unsupported"
	CodeWrongMailboxPassword  = "wrong_mailbox_password"
	CodeInvalidArgument       = "invalid_argument"
	CodeInvalidTime           = "invalid_time"
	CodeUnsupported           = "unsupported"
	CodeNotFound              = "not_found"
	CodeNotWritable           = "not_writable"
	CodeNotOrganiser          = "not_organiser"
	CodeNotAttendee           = "not_attendee"
	CodeConflict              = "conflict"
	CodeAuthExpired           = "auth_expired"
	CodeRateLimited           = "rate_limited"
	CodeProviderUnavailable   = "provider_unavailable"
	CodeProviderRejected      = "provider_rejected"
	defaultInternalErrMessage = "The helper failed."
)

// Error is the protocol's error object. It implements error so operations can
// return it directly.
type Error struct {
	Code         string         `json:"code"`
	Message      string         `json:"message"`
	Retryable    bool           `json:"retryable"`
	ProviderCode *int           `json:"provider_code"`
	HTTPStatus   *int           `json:"http_status"`
	Field        *string        `json:"field"`
	Details      map[string]any `json:"details"`
}

func (e *Error) Error() string { return e.Code + ": " + e.Message }

var retryable = map[string]bool{
	CodeAccessTokenExpired:   true,
	CodeTwoFactorFailed:      true,
	CodeWrongMailboxPassword: true,
	CodeConflict:             true,
	CodeRateLimited:          true,
	CodeProviderUnavailable:  true,
}

// Errorf builds an error with the given code.
func Errorf(code, format string, args ...any) *Error {
	return &Error{Code: code, Message: fmt.Sprintf(format, args...), Retryable: retryable[code], Details: map[string]any{}}
}

// FieldError builds an error that names the argument at fault.
func FieldError(code, field, format string, args ...any) *Error {
	e := Errorf(code, format, args...)
	e.Field = &field
	return e
}

// WithDetail adds a detail and returns the error.
func (e *Error) WithDetail(key string, value any) *Error {
	if e.Details == nil {
		e.Details = map[string]any{}
	}
	e.Details[key] = value
	return e
}

// WithProvider records Proton's code and HTTP status.
func (e *Error) WithProvider(code, status int) *Error {
	if code != 0 {
		c := code
		e.ProviderCode = &c
	}
	if status != 0 {
		s := status
		e.HTTPStatus = &s
	}
	return e
}

// AsError converts any error into a protocol error, defaulting to
// provider_unavailable for errors that carry no code of their own.
func AsError(err error) *Error {
	var pe *Error
	if errors.As(err, &pe) {
		if pe.Details == nil {
			pe.Details = map[string]any{}
		}
		return pe
	}
	return Errorf(CodeProviderUnavailable, defaultInternalErrMessage)
}

// DecodeStrict decodes JSON into v, refusing unknown fields and trailing data.
func DecodeStrict(data []byte, v any) error {
	if len(bytes.TrimSpace(data)) == 0 {
		data = []byte("{}")
	}
	dec := json.NewDecoder(bytes.NewReader(data))
	dec.DisallowUnknownFields()
	if err := dec.Decode(v); err != nil {
		return err
	}
	if _, err := dec.Token(); !errors.Is(err, io.EOF) {
		return errors.New("trailing data after the JSON object")
	}
	return nil
}

// DecodeArgs decodes an operation's arguments strictly.
func DecodeArgs(raw json.RawMessage, v any) *Error {
	if err := DecodeStrict(raw, v); err != nil {
		return Errorf(CodeInvalidRequest, "The arguments could not be read: %s.", sanitiseJSONError(err))
	}
	return nil
}

// sanitiseJSONError keeps a decoding error short and free of request values.
func sanitiseJSONError(err error) string {
	var typeErr *json.UnmarshalTypeError
	if errors.As(err, &typeErr) {
		return fmt.Sprintf("field %q has the wrong type", typeErr.Field)
	}
	var syntaxErr *json.SyntaxError
	if errors.As(err, &syntaxErr) {
		return fmt.Sprintf("invalid JSON at offset %d", syntaxErr.Offset)
	}
	msg := err.Error()
	if len(msg) > 120 {
		msg = msg[:120]
	}
	return msg
}
