// Package papi makes requests to Proton's API.
//
// It never refreshes a token by itself: a refused access token comes back as
// ErrAccessTokenExpired, and only the refresh operation may rotate tokens. The
// raw-request approach and the header set follow proton-cal's pkg/papi
// (https://github.com/cheeseandcereal/proton-cal, Unlicense).
package papi

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"mime/multipart"
	"net/http"
	"net/http/httptrace"
	"net/url"
	"regexp"
	"strconv"
	"strings"
	"time"
)

// AppVersion is the client identity sent as x-pm-appversion on every request.
// Nojoin identifies honestly as "Other" and never as one of Proton's own apps
// (maintainer decision Q5). It is the only place the value is set.
const AppVersion = "Other"

// DefaultBaseURL is Proton's API, as Proton Mail Bridge uses it.
const DefaultBaseURL = "https://mail.proton.me/api"

// Proton response codes the helper acts on.
const (
	CodeSuccess           = 1000
	CodeSuccessMulti      = 1001
	CodeHumanVerification = 9001
	CodeInsufficientScope = 9101
	CodeWrongCredentials  = 8002
	CodeRefreshInvalid    = 10013
	CodeNotFound          = 2501
	CodeEventNotFound     = 2061
	CodeDuplicateUID      = 2500
	CodeInvalidValue      = 2001
	CodeNotAllowed        = 2000
	CodeUnsupportedProp   = 2011
	CodeAppVersionBad     = 5002
	CodeAppVersionOld     = 5003
	CodeAppVersionFormat  = 2064
)

const maxResponseBytes = 64 << 20

// ErrAccessTokenExpired is returned when Proton refuses the access token.
var ErrAccessTokenExpired = errors.New("access token refused")

// APIError is a non-success answer from Proton.
type APIError struct {
	Status     int
	Code       int
	Message    string
	Details    json.RawMessage
	RetryAfter time.Duration
}

func (e *APIError) Error() string {
	return fmt.Sprintf("proton: status %d code %d: %s", e.Status, e.Code, MaskAddresses(e.Message))
}

// NetworkError wraps a failure to get any answer. Sent is true when the request
// left the client, so a write may have landed.
type NetworkError struct {
	Err  error
	Sent bool
}

func (e *NetworkError) Error() string { return "proton: no answer: " + e.Err.Error() }
func (e *NetworkError) Unwrap() error { return e.Err }

// Call records one request, for tests and for the read-only checks.
type Call struct {
	Method string
	Path   string
}

// Client is a Proton API client bound to one session.
type Client struct {
	BaseURL     string
	UID         string
	AccessToken string
	UserAgent   string
	HTTP        *http.Client
	// Header adds extra headers to every request (human verification).
	Header http.Header
	// Calls lists every request made, in order.
	Calls []Call
}

// New builds a client with Proton's defaults.
func New(baseURL, userAgent string) *Client {
	if baseURL == "" {
		baseURL = DefaultBaseURL
	}
	if userAgent == "" {
		userAgent = "Nojoin"
	}
	transport := &http.Transport{
		Proxy:                 http.ProxyFromEnvironment,
		TLSHandshakeTimeout:   15 * time.Second,
		ResponseHeaderTimeout: 60 * time.Second,
		MaxIdleConns:          4,
		IdleConnTimeout:       30 * time.Second,
		ForceAttemptHTTP2:     true,
	}
	return &Client{
		BaseURL:   strings.TrimRight(baseURL, "/"),
		UserAgent: userAgent,
		HTTP: &http.Client{
			Transport: transport,
			Timeout:   90 * time.Second,
			// Never follow a redirect: a single-use refresh token must not be
			// replayed to another host.
			CheckRedirect: func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse },
		},
	}
}

// Get performs a GET and decodes the answer into out.
func (c *Client) Get(ctx context.Context, path string, query url.Values, out any) error {
	return c.Do(ctx, http.MethodGet, path, query, nil, out)
}

// Put performs a PUT with a JSON body.
func (c *Client) Put(ctx context.Context, path string, body, out any) error {
	return c.Do(ctx, http.MethodPut, path, nil, body, out)
}

// Post performs a POST with a JSON body.
func (c *Client) Post(ctx context.Context, path string, body, out any) error {
	return c.Do(ctx, http.MethodPost, path, nil, body, out)
}

// Delete performs a DELETE.
func (c *Client) Delete(ctx context.Context, path string, out any) error {
	return c.Do(ctx, http.MethodDelete, path, nil, nil, out)
}

// Do performs one request. It makes exactly one attempt: no refresh, no retry.
func (c *Client) Do(ctx context.Context, method, path string, query url.Values, body, out any) error {
	var payload []byte
	if body != nil {
		var err error
		if payload, err = json.Marshal(body); err != nil {
			return fmt.Errorf("encoding request: %w", err)
		}
	}
	return c.send(ctx, method, path, query, payload, "application/json", out)
}

// MultipartField is one part of a multipart form.
type MultipartField struct {
	Name     string
	Value    string
	FileName string
	Data     []byte
}

// PostMultipart sends a multipart form, as the attachment upload needs.
func (c *Client) PostMultipart(ctx context.Context, path string, fields []MultipartField, out any) error {
	var buf bytes.Buffer
	w := multipart.NewWriter(&buf)
	for _, f := range fields {
		if f.FileName == "" {
			if err := w.WriteField(f.Name, f.Value); err != nil {
				return err
			}
			continue
		}
		part, err := w.CreatePart(map[string][]string{
			"Content-Disposition": {fmt.Sprintf(`form-data; name=%q; filename=%q`, f.Name, f.FileName)},
			"Content-Type":        {"application/octet-stream"},
		})
		if err != nil {
			return err
		}
		if _, err := part.Write(f.Data); err != nil {
			return err
		}
	}
	if err := w.Close(); err != nil {
		return err
	}
	return c.send(ctx, http.MethodPost, path, nil, buf.Bytes(), w.FormDataContentType(), out)
}

func (c *Client) send(ctx context.Context, method, path string, query url.Values, payload []byte, contentType string, out any) error {
	u := c.BaseURL + path
	if len(query) > 0 {
		u += "?" + query.Encode()
	}
	var reader io.Reader
	if payload != nil {
		reader = bytes.NewReader(payload)
	}
	req, err := http.NewRequestWithContext(ctx, method, u, reader)
	if err != nil {
		return fmt.Errorf("building request: %w", err)
	}
	req.GetBody = nil
	if c.UID != "" {
		req.Header.Set("x-pm-uid", c.UID)
	}
	if c.AccessToken != "" {
		req.Header.Set("Authorization", "Bearer "+c.AccessToken)
	}
	req.Header.Set("x-pm-appversion", AppVersion)
	req.Header.Set("User-Agent", c.UserAgent)
	req.Header.Set("Accept", "application/vnd.protonmail.v1+json")
	if payload != nil {
		req.Header.Set("Content-Type", contentType)
	}
	for k, vs := range c.Header {
		for _, v := range vs {
			req.Header.Add(k, v)
		}
	}
	c.Calls = append(c.Calls, Call{Method: method, Path: path})

	// Record whether the request was written, so a lost answer to a write can
	// be reported as an unknown outcome rather than as nothing having happened.
	sent := false
	req = req.WithContext(httptrace.WithClientTrace(req.Context(), &httptrace.ClientTrace{
		WroteRequest: func(httptrace.WroteRequestInfo) { sent = true },
	}))
	res, err := c.HTTP.Do(req)
	if err != nil {
		return &NetworkError{Err: err, Sent: sent}
	}
	defer func() { _ = res.Body.Close() }()
	raw, err := io.ReadAll(io.LimitReader(res.Body, maxResponseBytes))
	if err != nil {
		return &NetworkError{Err: err, Sent: true}
	}
	var envelope struct {
		Code    int
		Error   string
		Details json.RawMessage
	}
	_ = json.Unmarshal(raw, &envelope)

	if res.StatusCode == http.StatusUnauthorized {
		return ErrAccessTokenExpired
	}
	if res.StatusCode < 200 || res.StatusCode >= 300 || (envelope.Code != 0 && envelope.Code != CodeSuccess && envelope.Code != CodeSuccessMulti) {
		apiErr := &APIError{Status: res.StatusCode, Code: envelope.Code, Message: envelope.Error, Details: envelope.Details}
		if res.StatusCode == http.StatusTooManyRequests {
			apiErr.RetryAfter = parseRetryAfter(res.Header.Get("Retry-After"))
		}
		return apiErr
	}
	if out != nil {
		if err := json.Unmarshal(raw, out); err != nil {
			return fmt.Errorf("decoding %s %s: %w", method, path, err)
		}
	}
	return nil
}

func parseRetryAfter(h string) time.Duration {
	if h == "" {
		return 0
	}
	if secs, err := strconv.Atoi(strings.TrimSpace(h)); err == nil && secs >= 0 {
		return time.Duration(secs) * time.Second
	}
	if t, err := http.ParseTime(h); err == nil {
		if d := time.Until(t); d > 0 {
			return d
		}
	}
	return 0
}

var addressPattern = regexp.MustCompile(`[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}`)

// MaskAddresses hides email addresses in provider text before it is logged or
// returned.
func MaskAddresses(s string) string {
	s = addressPattern.ReplaceAllString(s, "<address>")
	if len(s) > 300 {
		s = s[:300]
	}
	return s
}

// IsCode reports whether err is an APIError with the Proton code.
func IsCode(err error, code int) bool {
	var ae *APIError
	return errors.As(err, &ae) && ae.Code == code
}
