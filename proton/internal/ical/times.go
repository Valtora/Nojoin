package ical

import (
	"fmt"
	"strings"
	"time"
	// Compiled-in zone data, so the helper never depends on the image's
	// /usr/share/zoneinfo and gives the same answer everywhere.
	_ "time/tzdata"
)

// Moment is a DTSTART-like value: an all-day date, or an instant with the zone
// it was written in.
type Moment struct {
	AllDay bool
	// Date is midnight UTC of the date for an all-day value.
	Date time.Time
	// Time is the instant for a timed value, in Zone.
	Time time.Time
	// Zone is the TZID, "UTC" for a Z value, or "" for a floating time.
	Zone string
}

// Instant returns the moment as an instant: midnight UTC for a date.
func (m Moment) Instant() time.Time {
	if m.AllDay {
		return m.Date
	}
	return m.Time
}

// IsZero reports whether the moment is unset.
func (m Moment) IsZero() bool { return m.Date.IsZero() && m.Time.IsZero() }

// LoadZone loads an IANA zone, treating "" and "UTC" as UTC.
func LoadZone(name string) (*time.Location, error) {
	if name == "" || strings.EqualFold(name, "UTC") || name == "Etc/UTC" || name == "Z" {
		return time.UTC, nil
	}
	loc, err := time.LoadLocation(name)
	if err != nil {
		return nil, fmt.Errorf("unknown time zone %q", name)
	}
	return loc, nil
}

// ParseMomentValue parses a DATE or DATE-TIME value with its TZID and VALUE
// parameters. A floating time is read in fallback (UTC when nil).
func ParseMomentValue(value, tzid, valueType string, fallback *time.Location) (Moment, error) {
	value = strings.TrimSpace(value)
	if strings.EqualFold(valueType, "DATE") || (len(value) == 8 && !strings.Contains(value, "T")) {
		d, err := time.ParseInLocation("20060102", value, time.UTC)
		if err != nil {
			return Moment{}, fmt.Errorf("bad date %q", value)
		}
		return Moment{AllDay: true, Date: d}, nil
	}
	if strings.HasSuffix(value, "Z") {
		t, err := time.ParseInLocation("20060102T150405Z", value, time.UTC)
		if err != nil {
			return Moment{}, fmt.Errorf("bad UTC time %q", value)
		}
		return Moment{Time: t, Zone: "UTC"}, nil
	}
	loc := fallback
	zone := ""
	if tzid != "" {
		l, err := LoadZone(tzid)
		if err != nil {
			return Moment{}, err
		}
		loc, zone = l, tzid
		if l == time.UTC {
			zone = "UTC"
		}
	}
	if loc == nil {
		loc = time.UTC
	}
	t, err := time.ParseInLocation("20060102T150405", value, loc)
	if err != nil {
		return Moment{}, fmt.Errorf("bad local time %q", value)
	}
	return Moment{Time: t, Zone: zone}, nil
}

// ParseMoment parses a DTSTART-like property.
func ParseMoment(p *Property) (Moment, error) {
	if p == nil {
		return Moment{}, fmt.Errorf("missing date property")
	}
	return ParseMomentValue(p.Value, p.Param("TZID"), p.Param("VALUE"), nil)
}

// MomentProperty renders a moment as a DTSTART-like property: VALUE=DATE for a
// date, Z for UTC, TZID with local time otherwise.
func MomentProperty(name string, m Moment) Property {
	p := Property{Name: strings.ToUpper(name)}
	switch {
	case m.AllDay:
		p.Params = []Param{{Name: "VALUE", Values: []string{"DATE"}}}
		p.Value = m.Date.UTC().Format("20060102")
	case m.Zone == "" || m.Zone == "UTC":
		p.Value = m.Time.UTC().Format("20060102T150405Z")
	default:
		loc, err := LoadZone(m.Zone)
		if err != nil {
			loc = time.UTC
		}
		p.Params = []Param{{Name: "TZID", Values: []string{m.Zone}}}
		p.Value = m.Time.In(loc).Format("20060102T150405")
	}
	return p
}

// ParseExdates returns every instant (midnight UTC for dates) an EXDATE list
// names. Values may be comma separated.
func ParseExdates(props []Property) []time.Time {
	var out []time.Time
	for _, p := range props {
		for _, v := range strings.Split(p.Value, ",") {
			m, err := ParseMomentValue(v, p.Param("TZID"), p.Param("VALUE"), nil)
			if err == nil {
				out = append(out, m.Instant())
			}
		}
	}
	return out
}

// RecurrenceKey renders an occurrence's original start as the stable part of a
// provider_event_id: UTC basic format for a timed series, YYYYMMDD for a date.
func RecurrenceKey(m Moment) string {
	if m.AllDay {
		return m.Date.Format("20060102")
	}
	return m.Time.UTC().Format("20060102T150405Z")
}

// ParseRecurrenceKey reverses RecurrenceKey.
func ParseRecurrenceKey(s string) (time.Time, bool, error) {
	if len(s) == 8 {
		d, err := time.ParseInLocation("20060102", s, time.UTC)
		return d, true, err
	}
	t, err := time.ParseInLocation("20060102T150405Z", s, time.UTC)
	return t, false, err
}
