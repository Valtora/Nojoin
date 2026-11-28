// Package fakeproton is an in-memory stand-in for the parts of Proton's API
// the helper uses: sign-in (real SRP through go-srp's server), keys, calendars,
// the event sync endpoint, attendee answers, the change feed and mail sending.
// It records every request so tests can assert what was and was not called.
// It never contacts the network and every key in it is made up at start.
package fakeproton

import (
	"crypto/rand"
	"encoding/base64"
	"encoding/hex"
	"fmt"

	srp "github.com/ProtonMail/go-srp"
	"github.com/ProtonMail/gopenpgp/v2/crypto"
)

// testModulus is the clearsigned SRP modulus from go-srp's own tests
// (srp_test.go), signed with Proton's modulus key, so srp.NewAuth accepts it.
//
// Copyright (c) 2019 Proton Technologies AG, MIT License:
// Permission is hereby granted, free of charge, to any person obtaining a copy
// of this software and associated documentation files (the "Software"), to deal
// in the Software without restriction, including without limitation the rights
// to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
// copies of the Software, and to permit persons to whom the Software is
// furnished to do so, subject to the following conditions: The above copyright
// notice and this permission notice shall be included in all copies or
// substantial portions of the Software. THE SOFTWARE IS PROVIDED "AS IS",
// WITHOUT WARRANTY OF ANY KIND.
const testModulus = `-----BEGIN PGP SIGNED MESSAGE-----
Hash: SHA256

W2z5HBi8RvsfYzZTS7qBaUxxPhsfHJFZpu3Kd6s1JafNrCCH9rfvPLrfuqocxWPgWDH2R8neK7PkNvjxto9TStuY5z7jAzWRvFWN9cQhAKkdWgy0JY6ywVn22+HFpF4cYesHrqFIKUPDMSSIlWjBVmEJZ/MusD44ZT29xcPrOqeZvwtCffKtGAIjLYPZIEbZKnDM1Dm3q2K/xS5h+xdhjnndhsrkwm9U9oyA2wxzSXFL+pdfj2fOdRwuR5nW0J2NFrq3kJjkRmpO/Genq1UW+TEknIWAb6VzJJJA244K/H8cnSx2+nSNZO3bbo6Ys228ruV9A8m6DhxmS+bihN3ttQ==
-----BEGIN PGP SIGNATURE-----
Version: ProtonMail
Comment: https://protonmail.com

wl4EARYIABAFAlwB1j0JEDUFhcTpUY8mAAD8CgEAnsFnF4cF0uSHKkXa1GIa
GO86yMV4zDZEZcDSJo0fgr8A/AlupGN9EdHlsrZLmTA1vhIx+rOgxdEff28N
kvNM7qIK
=q6vu
-----END PGP SIGNATURE-----`

func randomID(prefix string) string {
	b := make([]byte, 8)
	_, _ = rand.Read(b)
	return prefix + hex.EncodeToString(b)
}

func randomBytes(n int) []byte {
	b := make([]byte, n)
	_, _ = rand.Read(b)
	return b
}

// Person is a made-up account: its keys and, for the signed-in user, its
// password material.
type Person struct {
	Name  string
	Email string
	Key   *crypto.Key // unlocked
}

// KeyRing returns the person's key as a keyring.
func (p *Person) KeyRing() *crypto.KeyRing {
	kr, _ := crypto.NewKeyRing(p.Key)
	return kr
}

// PublicArmored returns the armoured public key.
func (p *Person) PublicArmored() string {
	s, _ := p.Key.GetArmoredPublicKey()
	return s
}

func newPerson(name, email string) *Person {
	k, err := crypto.GenerateKey(name, email, "x25519", 0)
	if err != nil {
		panic(err)
	}
	return &Person{Name: name, Email: email, Key: k}
}

// account is the signed-in user's key material, built as Proton builds it:
// a user key locked with the salted key passphrase, an address key locked
// with a token encrypted to the user key, and a calendar key locked with a
// passphrase encrypted to the address key.
type account struct {
	userKeyID     string
	userKeyArmor  string
	keySalt       []byte
	saltedKeyPass []byte
	addrKeyID     string
	addrKeyArmor  string
	addrToken     string
	addrTokenSig  string
	addr          *Person
}

func lockArmored(k *crypto.Key, passphrase []byte) string {
	locked, err := k.Lock(passphrase)
	if err != nil {
		panic(err)
	}
	s, err := locked.Armor()
	if err != nil {
		panic(err)
	}
	return s
}

func newAccount(addr *Person, keyPassword string) *account {
	a := &account{addr: addr, userKeyID: randomID("ukey-"), addrKeyID: randomID("akey-"), keySalt: randomBytes(16)}
	hashed, err := srp.MailboxPassword([]byte(keyPassword), a.keySalt)
	if err != nil {
		panic(err)
	}
	a.saltedKeyPass = hashed[len(hashed)-31:]
	userKey, err := crypto.GenerateKey("user key", "", "x25519", 0)
	if err != nil {
		panic(err)
	}
	a.userKeyArmor = lockArmored(userKey, a.saltedKeyPass)
	userKR, _ := crypto.NewKeyRing(userKey)
	token := hex.EncodeToString(randomBytes(32))
	enc, err := userKR.Encrypt(crypto.NewPlainMessage([]byte(token)), nil)
	if err != nil {
		panic(err)
	}
	a.addrToken, _ = enc.GetArmored()
	sig, err := userKR.SignDetached(crypto.NewPlainMessage([]byte(token)))
	if err != nil {
		panic(err)
	}
	a.addrTokenSig, _ = sig.GetArmored()
	a.addrKeyArmor = lockArmored(addr.Key, []byte(token))
	return a
}

// calendarKeys is a calendar's key and its passphrase, encrypted to the
// member's address key.
type calendarKeys struct {
	keyID        string
	passphraseID string
	key          *crypto.Key
	armored      string
	passMsg      string
	passSig      string
}

func newCalendarKeys(addr *Person) *calendarKeys {
	ck := &calendarKeys{keyID: randomID("ckey-"), passphraseID: randomID("cpass-")}
	k, err := crypto.GenerateKey("Calendar key", "", "x25519", 0)
	if err != nil {
		panic(err)
	}
	ck.key = k
	passphrase := base64.StdEncoding.EncodeToString(randomBytes(32))
	ck.armored = lockArmored(k, []byte(passphrase))
	kr := addr.KeyRing()
	enc, err := kr.Encrypt(crypto.NewPlainMessage([]byte(passphrase)), nil)
	if err != nil {
		panic(err)
	}
	ck.passMsg, _ = enc.GetArmored()
	sig, err := kr.SignDetached(crypto.NewPlainMessage([]byte(passphrase)))
	if err != nil {
		panic(fmt.Sprint(err))
	}
	ck.passSig, _ = sig.GetArmored()
	return ck
}

func (ck *calendarKeys) keyRing() *crypto.KeyRing {
	kr, _ := crypto.NewKeyRing(ck.key)
	return kr
}
