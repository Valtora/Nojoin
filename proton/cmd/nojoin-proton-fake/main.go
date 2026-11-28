// Command nojoin-proton-fake serves the in-memory fake of Proton's API
// (internal/fakeproton) for end-to-end test stacks, as design section 12.3
// describes: point the helper's client.api_base_url at it. It holds made-up
// accounts only, speaks plain HTTP, and is never built into an image.
//
//	nojoin-proton-fake -listen 0.0.0.0:8080
//
// It prints one JSON line with the sign-in details, then serves until killed.
package main

import (
	"encoding/json"
	"flag"
	"net/http"
	"os"
	"time"

	"github.com/Valtora/Nojoin/proton/internal/fakeproton"
)

func main() {
	listen := flag.String("listen", "127.0.0.1:8080", "address to serve on")
	username := flag.String("username", "alice", "the fake account's username")
	password := flag.String("password", "correct horse", "its login password")
	mailbox := flag.String("mailbox-password", "", "a separate mailbox password (two-password mode)")
	totp := flag.String("totp", "", "a TOTP code to require")
	anon := flag.Bool("require-anonymous-session", true, "answer /auth/v4/info only within a session, as Proton now does")
	invite := flag.Bool("seed-invitation", true, "store an invitation from another Proton user")
	flag.Parse()

	s := fakeproton.NewUnstarted()
	s.Username, s.Password, s.MailboxPassword, s.TOTP, s.RequireAnon = *username, *password, *mailbox, *totp, *anon
	info := map[string]any{
		"base_url":     "http://" + *listen,
		"username":     s.Username,
		"email":        s.Self.Email,
		"password":     s.Password,
		"calendar_id":  s.DefaultCalendar().ID,
		"proton_guest": s.Bob.Email,
	}
	if *invite {
		info["invitation_uid"] = s.SeedInvitation(time.Now().Add(72 * time.Hour).Truncate(time.Hour))
	}
	_ = json.NewEncoder(os.Stdout).Encode(info)
	server := &http.Server{Addr: *listen, Handler: s, ReadHeaderTimeout: 10 * time.Second}
	if err := server.ListenAndServe(); err != nil {
		os.Exit(1)
	}
}
