module github.com/Valtora/Nojoin/proton

go 1.27.4

require (
	github.com/ProtonMail/gluon v0.0.0-20260910132611-39e5c6ac84fe
	github.com/ProtonMail/go-proton-api v0.0.0-20261102101905-79fbe0626d9b
	github.com/ProtonMail/go-srp v0.0.7
	github.com/ProtonMail/gopenpgp/v2 v2.11.1-proton
	github.com/sirupsen/logrus v1.9.4
	github.com/teambition/rrule-go v1.8.2
)

require (
	github.com/ProtonMail/bcrypt v0.0.0-20211005172633-e235017c1baf // indirect
	github.com/ProtonMail/go-crypto v1.5.2-proton // indirect
	github.com/ProtonMail/go-mime v0.0.0-20230322103455-7d82a3887f2f // indirect
	github.com/PuerkitoBio/goquery v1.12.0 // indirect
	github.com/andybalholm/cascadia v1.3.3 // indirect
	github.com/bradenaw/juniper v0.15.3 // indirect
	github.com/cloudflare/circl v1.6.3 // indirect
	github.com/cronokirby/saferith v0.33.0 // indirect
	github.com/emersion/go-message v0.16.0 // indirect
	github.com/emersion/go-textwrapper v0.0.0-20200911093747-65d896831594 // indirect
	github.com/emersion/go-vcard v0.0.0-20230331202150-f3d26859ccd3 // indirect
	github.com/go-resty/resty/v2 v2.17.2 // indirect
	github.com/google/uuid v1.6.0 // indirect
	github.com/pkg/errors v0.9.1 // indirect
	gitlab.com/c0b/go-ordered-json v0.0.0-20201030195603-febf46534d5a // indirect
	golang.org/x/crypto v0.58.0 // indirect
	golang.org/x/exp v0.0.0-20260312153236-7ab1446f8b90 // indirect
	golang.org/x/net v0.62.0 // indirect
	golang.org/x/sync v0.24.0 // indirect
	golang.org/x/sys v0.49.0 // indirect
	golang.org/x/text v0.43.0 // indirect
)

// go-proton-api builds only against Proton's fork of resty. A replace in a
// dependency is ignored, so it is repeated here, exactly as go-proton-api's
// own go.mod has it.
replace github.com/go-resty/resty/v2 => github.com/ProtonMail/resty/v2 v2.0.0-20250929142426-e3dc6308c80b
