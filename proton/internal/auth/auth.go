// Package auth runs Proton's sign-in steps, token refresh and logout, and turns
// a stored key passphrase back into the account's keyrings.
//
// The sequence follows proton-cal's pkg/auth (Unlicense) and carbonate's
// internal/proton (MIT, Copyright (c) 2026 Mitchell de Jong): an anonymous
// session before /auth/v4/info, SRP with go-srp, TOTP, the mailbox password in
// two-password mode, and the scope dance for key salts.
package auth

import (
	"bytes"
	"context"
	"crypto/rand"
	"encoding/base64"
	"encoding/json"
	"errors"
	"net/http"
	"net/url"
	"slices"
	"strings"

	proton "github.com/ProtonMail/go-proton-api"
	srp "github.com/ProtonMail/go-srp"
	"github.com/ProtonMail/gopenpgp/v2/crypto"

	"github.com/Valtora/Nojoin/proton/internal/papi"
	"github.com/Valtora/Nojoin/proton/internal/protocol"
)

// SessionOut is the session part of a login or refresh result.
type SessionOut struct {
	UID           string   `json:"uid"`
	AccessToken   string   `json:"access_token"`
	RefreshToken  string   `json:"refresh_token,omitempty"`
	KeyPassphrase string   `json:"key_passphrase,omitempty"`
	Scopes        []string `json:"scopes,omitempty"`
	ExpiresIn     int      `json:"expires_in,omitempty"`
}

// HumanVerification is a solved CAPTCHA token to retry the login with.
type HumanVerification struct {
	Token string `json:"token"`
	Type  string `json:"type"`
}

// LoginStartArgs are login_start's arguments.
type LoginStartArgs struct {
	Username          string             `json:"username"`
	Password          string             `json:"password"`
	HumanVerification *HumanVerification `json:"human_verification"`
}

// TwoFactor says which second factors the account has.
type TwoFactor struct {
	Required bool `json:"required"`
	TOTP     bool `json:"totp"`
	FIDO2    bool `json:"fido2"`
}

// LoginStartResult is login_start's result.
type LoginStartResult struct {
	Session      SessionOut `json:"session"`
	UserID       string     `json:"user_id"`
	PasswordMode int        `json:"password_mode"`
	TwoFactor    TwoFactor  `json:"two_factor"`
	Next         string     `json:"next"`
}

type authResponse struct {
	UserID       string
	UID          string
	AccessToken  string
	RefreshToken string
	ServerProof  string
	Scope        string
	Scopes       []string
	ExpiresIn    int
	TwoFA        proton.TwoFAInfo `json:"2FA"`
	PasswordMode int
}

func (a authResponse) scopes() []string {
	if len(a.Scopes) > 0 {
		return a.Scopes
	}
	if a.Scope != "" {
		return strings.Fields(a.Scope)
	}
	return nil
}

// anonymousSession asks Proton for the session it now wants before
// /auth/v4/info. A server without the endpoint (the test server, older
// deployments) answers 404 or 405, and the login goes ahead without it.
func anonymousSession(ctx context.Context, c *papi.Client) error {
	var out struct {
		UID         string
		AccessToken string
	}
	err := c.Post(ctx, "/auth/v4/sessions", struct{}{}, &out)
	var ae *papi.APIError
	if errors.As(err, &ae) && (ae.Status == http.StatusNotFound || ae.Status == http.StatusMethodNotAllowed) {
		return nil
	}
	if err != nil {
		return err
	}
	if out.UID == "" || out.AccessToken == "" {
		return errors.New("anonymous session without UID or token")
	}
	c.UID, c.AccessToken = out.UID, out.AccessToken
	return nil
}

// LoginStart runs SRP authentication.
func LoginStart(ctx context.Context, c *papi.Client, args LoginStartArgs) (*LoginStartResult, error) {
	if strings.TrimSpace(args.Username) == "" {
		return nil, protocol.FieldError(protocol.CodeInvalidArgument, "username", "username is required.")
	}
	if args.Password == "" {
		return nil, protocol.FieldError(protocol.CodeInvalidArgument, "password", "password is required.")
	}
	if err := anonymousSession(ctx, c); err != nil {
		return nil, papi.ToProtocol(err, false)
	}
	var hvHeader http.Header
	if hv := args.HumanVerification; hv != nil && hv.Token != "" {
		typ := hv.Type
		if typ == "" {
			typ = "captcha"
		}
		hvHeader = http.Header{}
		hvHeader.Set("x-pm-human-verification-token", hv.Token)
		hvHeader.Set("x-pm-human-verification-token-type", typ)
	}
	a, err := srpLogin(ctx, c, args.Username, []byte(args.Password), hvHeader)
	if err != nil {
		return nil, err
	}
	c.UID, c.AccessToken = a.UID, a.AccessToken

	tf := TwoFactor{
		TOTP:  a.TwoFA.Enabled&proton.HasTOTP != 0,
		FIDO2: a.TwoFA.Enabled&proton.HasFIDO2 != 0,
	}
	tf.Required = tf.TOTP || tf.FIDO2
	if tf.FIDO2 && !tf.TOTP {
		// Nothing can finish this login; end the partial session.
		_ = c.Delete(ctx, "/auth/v4", nil)
		return nil, protocol.Errorf(protocol.CodeTwoFactorUnsupported,
			"This Proton account signs in with a security key only. Add an authenticator app in Proton's settings, then connect again.")
	}
	mode := a.PasswordMode
	if mode == 0 {
		mode = int(proton.OnePasswordMode)
	}
	next := "unlock"
	if tf.TOTP {
		next = "two_factor"
	}
	return &LoginStartResult{
		Session: SessionOut{
			UID: a.UID, AccessToken: a.AccessToken, RefreshToken: a.RefreshToken,
			Scopes: a.scopes(), ExpiresIn: a.ExpiresIn,
		},
		UserID:       a.UserID,
		PasswordMode: mode,
		TwoFactor:    tf,
		Next:         next,
	}, nil
}

type authInfo struct {
	Version         int
	Modulus         string
	ServerEphemeral string
	Salt            string
	SRPSession      string
}

func srpProofs(ctx context.Context, c *papi.Client, username string, password []byte) (authInfo, *srp.Proofs, error) {
	var info authInfo
	if err := c.Post(ctx, "/auth/v4/info", map[string]string{"Username": username}, &info); err != nil {
		return info, nil, loginError(err)
	}
	// NewAuth checks the modulus signature against Proton's SRP key.
	sa, err := srp.NewAuth(info.Version, username, password, info.Salt, info.Modulus, info.ServerEphemeral)
	if err != nil {
		return info, nil, protocol.Errorf(protocol.CodeProviderRejected, "Proton's sign-in parameters failed verification.")
	}
	proofs, err := sa.GenerateProofs(2048)
	if err != nil {
		return info, nil, protocol.Errorf(protocol.CodeProviderRejected, "Proton's sign-in parameters could not be used.")
	}
	return info, proofs, nil
}

// srpLogin sends the SRP proof. A solved human verification token goes on this
// request only, as go-proton-api sends it.
func srpLogin(ctx context.Context, c *papi.Client, username string, password []byte, hv http.Header) (*authResponse, error) {
	info, proofs, err := srpProofs(ctx, c, username, password)
	if err != nil {
		return nil, err
	}
	c.Header = hv
	defer func() { c.Header = nil }()
	var a authResponse
	if err := c.Post(ctx, "/auth/v4", map[string]string{
		"Username":        username,
		"ClientEphemeral": base64.StdEncoding.EncodeToString(proofs.ClientEphemeral),
		"ClientProof":     base64.StdEncoding.EncodeToString(proofs.ClientProof),
		"SRPSession":      info.SRPSession,
	}, &a); err != nil {
		return nil, loginError(err)
	}
	serverProof, err := base64.StdEncoding.DecodeString(a.ServerProof)
	if err != nil || !bytes.Equal(serverProof, proofs.ExpectedServerProof) {
		return nil, protocol.Errorf(protocol.CodeProviderRejected, "Proton's server proof did not match; the sign-in was abandoned.")
	}
	if a.UID == "" || a.AccessToken == "" {
		return nil, protocol.Errorf(protocol.CodeProviderRejected, "Proton's sign-in answer had no session.")
	}
	return &a, nil
}

// loginError maps sign-in refusals onto the connect flow's codes.
func loginError(err error) error {
	var ae *papi.APIError
	if errors.As(err, &ae) {
		switch {
		case ae.Code == papi.CodeHumanVerification:
			return humanVerificationError(ae)
		case ae.Code == papi.CodeWrongCredentials:
			return protocol.Errorf(protocol.CodeLoginFailed, "Proton did not accept the username or password.").WithProvider(ae.Code, ae.Status)
		}
	}
	if errors.Is(err, papi.ErrAccessTokenExpired) {
		return protocol.Errorf(protocol.CodeProviderRejected, "Proton refused the sign-in session.").WithProvider(0, 401)
	}
	return papi.ToProtocol(err, false)
}

func humanVerificationError(ae *papi.APIError) *protocol.Error {
	var details struct {
		HumanVerificationMethods []string
		HumanVerificationToken   string
	}
	_ = json.Unmarshal(ae.Details, &details)
	e := protocol.Errorf(protocol.CodeHumanVerification, "Proton wants to check that a person is signing in.").WithProvider(ae.Code, ae.Status)
	methods := details.HumanVerificationMethods
	if methods == nil {
		methods = []string{}
	}
	e.WithDetail("methods", methods).WithDetail("token", details.HumanVerificationToken)
	if slices.Contains(methods, "captcha") && details.HumanVerificationToken != "" {
		e.WithDetail("verify_url", "https://verify.proton.me/?methods=captcha&token="+url.QueryEscape(details.HumanVerificationToken))
	} else {
		e.WithDetail("verify_url", nil)
	}
	return e
}

// Login2FAArgs are login_2fa's arguments.
type Login2FAArgs struct {
	Code string `json:"code"`
}

// Login2FA submits a TOTP code.
func Login2FA(ctx context.Context, c *papi.Client, sess protocol.Session, args Login2FAArgs) (map[string]any, error) {
	code := strings.TrimSpace(args.Code)
	if code == "" {
		return nil, protocol.FieldError(protocol.CodeInvalidArgument, "code", "code is required.")
	}
	var out struct {
		Scopes []string
		Scope  string
	}
	if err := c.Post(ctx, "/auth/v4/2fa", map[string]string{"TwoFactorCode": code}, &out); err != nil {
		var ae *papi.APIError
		if errors.As(err, &ae) && ae.Status >= 400 && ae.Status < 500 && ae.Status != http.StatusTooManyRequests {
			return nil, protocol.Errorf(protocol.CodeTwoFactorFailed, "Proton did not accept the two-factor code.").WithProvider(ae.Code, ae.Status)
		}
		return nil, papi.ToProtocol(err, false)
	}
	scopes := out.Scopes
	if len(scopes) == 0 && out.Scope != "" {
		scopes = strings.Fields(out.Scope)
	}
	return map[string]any{
		"session": SessionOut{UID: sess.UID, AccessToken: sess.AccessToken, RefreshToken: sess.RefreshToken, Scopes: scopes},
		"next":    "unlock",
	}, nil
}

// RefreshResult is refresh's result.
type RefreshResult struct {
	Session SessionOut `json:"session"`
}

// Refresh rotates the session's tokens. The single-use refresh token is sent
// once, and the answer is awaited even if the operation's own deadline passes,
// because losing it would lose the session.
func Refresh(ctx context.Context, c *papi.Client, sess protocol.Session) (*RefreshResult, error) {
	state := make([]byte, 16)
	_, _ = rand.Read(state)
	body := map[string]string{
		"UID":          sess.UID,
		"RefreshToken": sess.RefreshToken,
		"ResponseType": "token",
		"GrantType":    "refresh_token",
		"RedirectURI":  "https://protonmail.ch",
		"State":        base64.RawURLEncoding.EncodeToString(state),
	}
	// The refresh carries its UID in the body and no session headers, as
	// proton-cal and go-proton-api send it.
	c.UID, c.AccessToken = "", ""
	var a authResponse
	err := c.Post(context.WithoutCancel(ctx), "/auth/v4/refresh", body, &a)
	if err != nil {
		var ae *papi.APIError
		refused := errors.Is(err, papi.ErrAccessTokenExpired) ||
			(errors.As(err, &ae) && (ae.Code == papi.CodeRefreshInvalid || ae.Status == http.StatusBadRequest ||
				ae.Status == http.StatusForbidden || ae.Status == http.StatusUnprocessableEntity))
		if refused {
			e := protocol.Errorf(protocol.CodeAuthExpired, "Proton refused the refresh token; reconnect the account.").WithDetail("reason", "refresh_refused")
			if ae != nil {
				e.WithProvider(ae.Code, ae.Status)
			}
			return nil, e
		}
		e := papi.ToProtocol(err, false)
		if e.Code == protocol.CodeProviderUnavailable && papi.WasSent(err) {
			e.WithDetail("outcome_unknown", true)
		}
		return nil, e
	}
	if a.AccessToken == "" || a.RefreshToken == "" {
		return nil, protocol.Errorf(protocol.CodeProviderRejected, "Proton's refresh answer had no tokens.")
	}
	uid := a.UID
	if uid == "" {
		uid = sess.UID
	}
	return &RefreshResult{Session: SessionOut{
		UID: uid, AccessToken: a.AccessToken, RefreshToken: a.RefreshToken,
		ExpiresIn: a.ExpiresIn, Scopes: a.scopes(),
	}}, nil
}

// Logout ends the session at Proton.
func Logout(ctx context.Context, c *papi.Client) (map[string]any, error) {
	err := c.Delete(ctx, "/auth/v4", nil)
	if errors.Is(err, papi.ErrAccessTokenExpired) {
		return map[string]any{"ended": false}, nil
	}
	if err != nil {
		return nil, papi.ToProtocol(err, false)
	}
	return map[string]any{"ended": true}, nil
}

// Keyring is the account's unlocked key material for one operation.
type Keyring struct {
	User      proton.User
	Addresses []proton.Address
	UserKR    *crypto.KeyRing
	// AddrKRs maps address ID to its unlocked keyring, primary key first.
	AddrKRs map[string]*crypto.KeyRing
}

// Emails lists every address on the account, lower-cased.
func (k *Keyring) Emails() []string {
	var out []string
	for _, a := range k.Addresses {
		out = append(out, strings.ToLower(a.Email))
	}
	return out
}

// AllAddressKeys is one keyring holding every unlocked address key.
func (k *Keyring) AllAddressKeys() *crypto.KeyRing {
	kr, _ := crypto.NewKeyRing(nil)
	for _, a := range k.Addresses {
		if akr := k.AddrKRs[a.ID]; akr != nil {
			for _, key := range akr.GetKeys() {
				_ = kr.AddKey(key)
			}
		}
	}
	return kr
}

// Clear wipes the private key material.
func (k *Keyring) Clear() {
	if k == nil {
		return
	}
	if k.UserKR != nil {
		k.UserKR.ClearPrivateParams()
	}
	for _, kr := range k.AddrKRs {
		kr.ClearPrivateParams()
	}
}

func fetchUserAndAddresses(ctx context.Context, c *papi.Client) (proton.User, []proton.Address, error) {
	var u struct{ User proton.User }
	if err := c.Get(ctx, "/core/v4/users", nil, &u); err != nil {
		return proton.User{}, nil, err
	}
	var a struct{ Addresses []proton.Address }
	if err := c.Get(ctx, "/core/v4/addresses", nil, &a); err != nil {
		return proton.User{}, nil, err
	}
	return u.User, a.Addresses, nil
}

// Unlock opens the account's keys with a stored salted key passphrase. A
// passphrase that opens nothing means the user changed their password.
func Unlock(ctx context.Context, c *papi.Client, keyPassphrase string) (*Keyring, error) {
	user, addrs, err := fetchUserAndAddresses(ctx, c)
	if err != nil {
		return nil, papi.ToProtocol(err, false)
	}
	kr, err := unlockWith(user, addrs, []byte(keyPassphrase))
	if err != nil {
		return nil, protocol.Errorf(protocol.CodeAuthExpired, "The stored key passphrase no longer opens this Proton account's keys; reconnect it.").
			WithDetail("reason", "key_passphrase_invalid")
	}
	return kr, nil
}

func unlockWith(user proton.User, addrs []proton.Address, saltedKeyPass []byte) (*Keyring, error) {
	if len(user.Keys) == 0 {
		return nil, errors.New("user has no keys")
	}
	userKR, err := user.Keys.Unlock(saltedKeyPass, nil)
	if err != nil || userKR.CountDecryptionEntities() == 0 {
		return nil, errors.New("no user key opened")
	}
	k := &Keyring{User: user, Addresses: addrs, UserKR: userKR, AddrKRs: map[string]*crypto.KeyRing{}}
	for _, a := range addrs {
		ordered := orderPrimaryFirst(a.Keys)
		akr, err := ordered.Unlock(saltedKeyPass, userKR)
		if err != nil || akr.CountDecryptionEntities() == 0 {
			continue
		}
		k.AddrKRs[a.ID] = akr
	}
	if len(k.AddrKRs) == 0 {
		k.Clear()
		return nil, errors.New("no address key opened")
	}
	return k, nil
}

func orderPrimaryFirst(keys proton.Keys) proton.Keys {
	out := make(proton.Keys, 0, len(keys))
	for _, k := range keys {
		if k.Primary {
			out = append(out, k)
		}
	}
	for _, k := range keys {
		if !k.Primary {
			out = append(out, k)
		}
	}
	return out
}

// LoginUnlockArgs are login_unlock's arguments.
type LoginUnlockArgs struct {
	Username        string  `json:"username"`
	Password        string  `json:"password"`
	MailboxPassword *string `json:"mailbox_password"`
}

// AddressOut is one address in login_unlock's result.
type AddressOut struct {
	ID           string `json:"id"`
	Email        string `json:"email"`
	DisplayName  string `json:"display_name"`
	Status       string `json:"status"`
	KeysUnlocked bool   `json:"keys_unlocked"`
}

// UnlockAccount derives the salted key passphrase from the password, checks
// it opens the keys, and returns it with the keyring.
func UnlockAccount(ctx context.Context, c *papi.Client, args LoginUnlockArgs) (*Keyring, string, []AddressOut, error) {
	if args.Password == "" {
		return nil, "", nil, protocol.FieldError(protocol.CodeInvalidArgument, "password", "password is required.")
	}
	keyPassword := args.Password
	if args.MailboxPassword != nil && *args.MailboxPassword != "" {
		keyPassword = *args.MailboxPassword
	}
	user, addrs, err := fetchUserAndAddresses(ctx, c)
	if err != nil {
		return nil, "", nil, papi.ToProtocol(err, false)
	}
	salts, err := fetchSalts(ctx, c, args.Username, args.Password)
	if err != nil {
		return nil, "", nil, err
	}
	if len(user.Keys) == 0 {
		return nil, "", nil, protocol.Errorf(protocol.CodeProviderRejected, "This Proton account has no keys.")
	}
	primary := user.Keys[0].ID
	for _, k := range user.Keys {
		if k.Primary {
			primary = k.ID
			break
		}
	}
	salted, err := salts.SaltForKey([]byte(keyPassword), primary)
	if err != nil || len(salted) == 0 {
		return nil, "", nil, protocol.Errorf(protocol.CodeProviderRejected, "Proton gave no salt for the account's primary key.")
	}
	kr, err := unlockWith(user, addrs, salted)
	if err != nil {
		msg := "The password did not open this Proton account's keys."
		if args.MailboxPassword == nil {
			msg = "The password did not open this Proton account's keys. If the account uses a separate mailbox password, enter that too."
		}
		return nil, "", nil, protocol.Errorf(protocol.CodeWrongMailboxPassword, "%s", msg)
	}
	var out []AddressOut
	for _, a := range addrs {
		status := "disabled"
		switch a.Status {
		case proton.AddressStatusEnabled:
			status = "enabled"
		case proton.AddressStatusDeleting:
			status = "deleting"
		}
		out = append(out, AddressOut{ID: a.ID, Email: a.Email, DisplayName: a.DisplayName, Status: status, KeysUnlocked: kr.AddrKRs[a.ID] != nil})
	}
	return kr, string(salted), out, nil
}

// fetchSalts reads the key salts. A session without the scope for them (9101)
// gains it with an SRP proof of the login password, reads them, and drops the
// scope again.
func fetchSalts(ctx context.Context, c *papi.Client, username, password string) (proton.Salts, error) {
	var out struct{ KeySalts proton.Salts }
	err := c.Get(ctx, "/core/v4/keys/salts", nil, &out)
	if err == nil {
		return out.KeySalts, nil
	}
	if !papi.IsCode(err, papi.CodeInsufficientScope) {
		return nil, papi.ToProtocol(err, false)
	}
	if strings.TrimSpace(username) == "" {
		return nil, protocol.FieldError(protocol.CodeInvalidArgument, "username", "username is required to read this account's key salts.")
	}
	info, proofs, perr := srpProofs(ctx, c, username, []byte(password))
	if perr != nil {
		return nil, perr
	}
	var unlocked struct{ ServerProof string }
	if err := c.Put(ctx, "/core/v4/users/unlock", map[string]string{
		"ClientEphemeral": base64.StdEncoding.EncodeToString(proofs.ClientEphemeral),
		"ClientProof":     base64.StdEncoding.EncodeToString(proofs.ClientProof),
		"SRPSession":      info.SRPSession,
	}, &unlocked); err != nil {
		if papi.IsCode(err, papi.CodeWrongCredentials) {
			return nil, protocol.Errorf(protocol.CodeLoginFailed, "Proton did not accept the password.")
		}
		return nil, papi.ToProtocol(err, false)
	}
	sp, err := base64.StdEncoding.DecodeString(unlocked.ServerProof)
	if err != nil || !bytes.Equal(sp, proofs.ExpectedServerProof) {
		return nil, protocol.Errorf(protocol.CodeProviderRejected, "Proton's server proof did not match.")
	}
	defer func() { _ = c.Put(context.WithoutCancel(ctx), "/core/v4/users/lock", struct{}{}, nil) }()
	if err := c.Get(ctx, "/core/v4/keys/salts", nil, &out); err != nil {
		return nil, papi.ToProtocol(err, false)
	}
	return out.KeySalts, nil
}
