package ical

import (
	"strings"
	"testing"
	"time"
)

func TestParseSerialiseRoundTrip(t *testing.T) {
	in := "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nBEGIN:VEVENT\r\nUID:abc\r\nDTSTART;TZID=Europe/London:20261201T100000\r\n" +
		"SUMMARY:Design\\, review\\; part\\n2\r\nATTENDEE;CN=\"Example, Bob\";ROLE=REQ-PARTICIPANT:mailto:bob@example.org\r\n" +
		"X-UNKNOWN;X-P=1:kept as is\r\nEND:VEVENT\r\nEND:VCALENDAR"
	roots, err := Parse(in)
	if err != nil {
		t.Fatal(err)
	}
	if got := Serialize(roots[0]); got != in {
		t.Fatalf("round trip changed the text:\n%q\n%q", got, in)
	}
	ev := roots[0].Child("VEVENT")
	if got := ev.Get("SUMMARY").Text(); got != "Design, review; part\n2" {
		t.Fatalf("unescaped summary = %q", got)
	}
	if got := ev.Get("ATTENDEE").Param("CN"); got != "Example, Bob" {
		t.Fatalf("quoted CN = %q", got)
	}
}

func TestParseToleratesProtonFragments(t *testing.T) {
	// No VERSION, LF line endings, no trailing line break, a folded line.
	in := "BEGIN:VCALENDAR\nBEGIN:VEVENT\nUID:x\nDESCRIPTION:one\n  two\nEND:VEVENT\nEND:VCALENDAR"
	ev, err := FirstEvent(in)
	if err != nil {
		t.Fatal(err)
	}
	if got := ev.Get("DESCRIPTION").Text(); got != "one two" {
		t.Fatalf("unfolded = %q", got)
	}
	if _, err := FirstEvent("BEGIN:VEVENT\nUID:x"); err == nil {
		t.Fatal("an unterminated component should fail")
	}
}

func TestFoldKeepsUTF8AndLimit(t *testing.T) {
	line := "DESCRIPTION:" + strings.Repeat("é", 100)
	folded := Fold(line)
	for _, part := range strings.Split(folded, "\r\n") {
		if len(part) > 75 {
			t.Fatalf("a folded line is %d octets", len(part))
		}
		if !strings.HasPrefix(part, "DESCRIPTION") && !strings.HasPrefix(part, " ") {
			t.Fatalf("continuation line without a space: %q", part)
		}
	}
	ev, err := FirstEvent("BEGIN:VEVENT\r\n" + folded + "\r\nEND:VEVENT")
	if err != nil {
		t.Fatal(err)
	}
	if ev.Get("DESCRIPTION").Value != strings.Repeat("é", 100) {
		t.Fatal("folding split a character")
	}
}

func TestEscapeText(t *testing.T) {
	in := "a\\b;c,d\ne"
	if got := UnescapeText(EscapeText(in)); got != in {
		t.Fatalf("escape round trip = %q", got)
	}
	if got := EscapeText("x\r\ny"); got != `x\ny` {
		t.Fatalf("CRLF escapes to %q", got)
	}
}

func TestMoments(t *testing.T) {
	cases := []struct {
		line   string
		allDay bool
		zone   string
		unix   int64
	}{
		{"DTSTART;VALUE=DATE:20261224", true, "", time.Date(2026, 12, 24, 0, 0, 0, 0, time.UTC).Unix()},
		{"DTSTART:20261201T100000Z", false, "UTC", time.Date(2026, 12, 1, 10, 0, 0, 0, time.UTC).Unix()},
		{"DTSTART;TZID=Europe/London:20260701T100000", false, "Europe/London", time.Date(2026, 7, 1, 9, 0, 0, 0, time.UTC).Unix()},
	}
	for _, c := range cases {
		p, err := ParseLine(c.line)
		if err != nil {
			t.Fatal(err)
		}
		m, err := ParseMoment(&p)
		if err != nil {
			t.Fatal(err)
		}
		if m.AllDay != c.allDay || m.Zone != c.zone || m.Instant().Unix() != c.unix {
			t.Fatalf("%s parsed as %+v", c.line, m)
		}
		if got := FormatLine(MomentProperty("DTSTART", m)); got != c.line {
			t.Fatalf("%s renders back as %s", c.line, got)
		}
	}
	if _, err := ParseMomentValue("20261201T100000", "Not/AZone", "", nil); err == nil {
		t.Fatal("an unknown TZID should fail")
	}
}

func TestRecurrenceKeys(t *testing.T) {
	m := Moment{Time: time.Date(2026, 12, 1, 14, 0, 0, 0, time.UTC), Zone: "UTC"}
	if RecurrenceKey(m) != "20261201T140000Z" {
		t.Fatal(RecurrenceKey(m))
	}
	d := Moment{AllDay: true, Date: time.Date(2026, 12, 24, 0, 0, 0, 0, time.UTC)}
	if RecurrenceKey(d) != "20261224" {
		t.Fatal(RecurrenceKey(d))
	}
	tm, allDay, err := ParseRecurrenceKey("20261224")
	if err != nil || !allDay || !tm.Equal(d.Date) {
		t.Fatal("date key did not parse back")
	}
}

func TestMergePrefersSharedSignedAndUnionsLists(t *testing.T) {
	signed, _ := FirstEvent("BEGIN:VEVENT\r\nUID:u\r\nSEQUENCE:2\r\nEXDATE:20261208T100000Z\r\nEND:VEVENT")
	calSigned, _ := FirstEvent("BEGIN:VEVENT\r\nUID:u\r\nSEQUENCE:9\r\nEXDATE:20261208T100000Z\r\nEXDATE:20261215T100000Z\r\nEND:VEVENT")
	m := Merge(signed, calSigned)
	if m.Get("SEQUENCE").Value != "2" {
		t.Fatal("the shared signed card should win for SEQUENCE")
	}
	if n := len(m.All("EXDATE")); n != 2 {
		t.Fatalf("EXDATE union has %d values", n)
	}
}
