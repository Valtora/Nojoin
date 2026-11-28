// Package ical is a small, tolerant iCalendar reader and writer for Proton's
// calendar cards.
//
// Proton stores each event as several VCALENDAR fragments ("cards") that are
// signed byte for byte, carry no trailing CRLF and sometimes no VERSION or
// PRODID. Strict parsers refuse them, so this package keeps every property as
// it was read (name, ordered parameters, raw escaped value) and only rewrites
// the properties a caller changes.
package ical

import (
	"errors"
	"fmt"
	"strings"
	"unicode/utf8"
)

// Param is one property parameter, in the order it was read.
type Param struct {
	Name   string
	Values []string
}

// Property is one content line. Value is kept escaped, exactly as read.
type Property struct {
	Name   string
	Params []Param
	Value  string
}

// Component is a BEGIN/END block.
type Component struct {
	Name     string
	Props    []Property
	Children []*Component
}

// Param returns the first value of a parameter, or "".
func (p Property) Param(name string) string {
	for _, prm := range p.Params {
		if strings.EqualFold(prm.Name, name) && len(prm.Values) > 0 {
			return prm.Values[0]
		}
	}
	return ""
}

// HasParam reports whether the parameter is present.
func (p Property) HasParam(name string) bool {
	for _, prm := range p.Params {
		if strings.EqualFold(prm.Name, name) {
			return true
		}
	}
	return false
}

// WithParam returns a copy with the parameter set (replacing any existing one).
func (p Property) WithParam(name, value string) Property {
	out := p.WithoutParam(name)
	out.Params = append(out.Params, Param{Name: strings.ToUpper(name), Values: []string{value}})
	return out
}

// WithoutParam returns a copy without the parameter.
func (p Property) WithoutParam(name string) Property {
	out := Property{Name: p.Name, Value: p.Value}
	for _, prm := range p.Params {
		if !strings.EqualFold(prm.Name, name) {
			out.Params = append(out.Params, Param{Name: prm.Name, Values: append([]string(nil), prm.Values...)})
		}
	}
	return out
}

// Clone returns a deep copy of the property.
func (p Property) Clone() Property {
	out := Property{Name: p.Name, Value: p.Value}
	for _, prm := range p.Params {
		out.Params = append(out.Params, Param{Name: prm.Name, Values: append([]string(nil), prm.Values...)})
	}
	return out
}

// Text returns the value unescaped as an RFC 5545 TEXT value.
func (p Property) Text() string { return UnescapeText(p.Value) }

// Clone returns a deep copy.
func (c *Component) Clone() *Component {
	if c == nil {
		return nil
	}
	out := &Component{Name: c.Name}
	for _, p := range c.Props {
		out.Props = append(out.Props, p.Clone())
	}
	for _, ch := range c.Children {
		out.Children = append(out.Children, ch.Clone())
	}
	return out
}

// Get returns the first property with the name, or nil.
func (c *Component) Get(name string) *Property {
	for i := range c.Props {
		if strings.EqualFold(c.Props[i].Name, name) {
			return &c.Props[i]
		}
	}
	return nil
}

// All returns every property with the name.
func (c *Component) All(name string) []Property {
	var out []Property
	for _, p := range c.Props {
		if strings.EqualFold(p.Name, name) {
			out = append(out, p)
		}
	}
	return out
}

// Remove deletes every property with the name.
func (c *Component) Remove(name string) {
	kept := c.Props[:0]
	for _, p := range c.Props {
		if !strings.EqualFold(p.Name, name) {
			kept = append(kept, p)
		}
	}
	c.Props = kept
}

// Set replaces the properties with this name by p, keeping the position of the
// first one, or appends p when there was none.
func (c *Component) Set(p Property) {
	p.Name = strings.ToUpper(p.Name)
	for i := range c.Props {
		if strings.EqualFold(c.Props[i].Name, p.Name) {
			c.Props[i] = p
			rest := c.Props[:i+1]
			for _, q := range c.Props[i+1:] {
				if !strings.EqualFold(q.Name, p.Name) {
					rest = append(rest, q)
				}
			}
			c.Props = rest
			return
		}
	}
	c.Props = append(c.Props, p)
}

// SetText sets a TEXT property, escaping the value.
func (c *Component) SetText(name, value string) {
	c.Set(Property{Name: name, Value: EscapeText(value)})
}

// Add appends a property.
func (c *Component) Add(p Property) {
	p.Name = strings.ToUpper(p.Name)
	c.Props = append(c.Props, p)
}

// Child returns the first child with the name, or nil.
func (c *Component) Child(name string) *Component {
	for _, ch := range c.Children {
		if strings.EqualFold(ch.Name, name) {
			return ch
		}
	}
	return nil
}

// ErrMalformed is returned for text that is not iCalendar at all.
var ErrMalformed = errors.New("ical: malformed")

// Parse reads one or more top-level components. It tolerates LF line endings,
// a missing trailing line break and missing VERSION or PRODID.
func Parse(text string) ([]*Component, error) {
	lines := unfold(text)
	var (
		roots []*Component
		stack []*Component
	)
	for _, line := range lines {
		if line == "" {
			continue
		}
		prop, err := ParseLine(line)
		if err != nil {
			return nil, err
		}
		switch prop.Name {
		case "BEGIN":
			comp := &Component{Name: strings.ToUpper(prop.Value)}
			if len(stack) > 0 {
				parent := stack[len(stack)-1]
				parent.Children = append(parent.Children, comp)
			} else {
				roots = append(roots, comp)
			}
			stack = append(stack, comp)
		case "END":
			if len(stack) == 0 || !strings.EqualFold(stack[len(stack)-1].Name, prop.Value) {
				return nil, fmt.Errorf("%w: unexpected END:%s", ErrMalformed, prop.Value)
			}
			stack = stack[:len(stack)-1]
		default:
			if len(stack) == 0 {
				return nil, fmt.Errorf("%w: property outside a component", ErrMalformed)
			}
			cur := stack[len(stack)-1]
			cur.Props = append(cur.Props, prop)
		}
	}
	if len(stack) != 0 {
		return nil, fmt.Errorf("%w: unterminated %s", ErrMalformed, stack[len(stack)-1].Name)
	}
	if len(roots) == 0 {
		return nil, fmt.Errorf("%w: no component", ErrMalformed)
	}
	return roots, nil
}

// FirstEvent returns the first VEVENT inside the text, whether it is wrapped in
// a VCALENDAR or not.
func FirstEvent(text string) (*Component, error) {
	roots, err := Parse(text)
	if err != nil {
		return nil, err
	}
	for _, r := range roots {
		if r.Name == "VEVENT" {
			return r, nil
		}
		if ev := r.Child("VEVENT"); ev != nil {
			return ev, nil
		}
	}
	return nil, fmt.Errorf("%w: no VEVENT", ErrMalformed)
}

func unfold(text string) []string {
	text = strings.ReplaceAll(text, "\r\n", "\n")
	text = strings.ReplaceAll(text, "\r", "\n")
	raw := strings.Split(text, "\n")
	var out []string
	for _, l := range raw {
		if (strings.HasPrefix(l, " ") || strings.HasPrefix(l, "\t")) && len(out) > 0 {
			out[len(out)-1] += l[1:]
			continue
		}
		out = append(out, l)
	}
	return out
}

// ParseLine parses one unfolded content line.
func ParseLine(line string) (Property, error) {
	var p Property
	i := strings.IndexAny(line, ";:")
	if i <= 0 {
		return p, fmt.Errorf("%w: line without a name", ErrMalformed)
	}
	p.Name = strings.ToUpper(line[:i])
	rest := line[i:]
	for strings.HasPrefix(rest, ";") {
		rest = rest[1:]
		eq := strings.IndexByte(rest, '=')
		if eq <= 0 {
			return p, fmt.Errorf("%w: parameter without a value", ErrMalformed)
		}
		prm := Param{Name: strings.ToUpper(rest[:eq])}
		rest = rest[eq+1:]
		for {
			var val string
			if strings.HasPrefix(rest, `"`) {
				end := strings.IndexByte(rest[1:], '"')
				if end < 0 {
					return p, fmt.Errorf("%w: unterminated quoted parameter", ErrMalformed)
				}
				val = rest[1 : end+1]
				rest = rest[end+2:]
			} else {
				end := strings.IndexAny(rest, ",;:")
				if end < 0 {
					return p, fmt.Errorf("%w: parameter runs to the end of the line", ErrMalformed)
				}
				val = rest[:end]
				rest = rest[end:]
			}
			prm.Values = append(prm.Values, val)
			if strings.HasPrefix(rest, ",") {
				rest = rest[1:]
				continue
			}
			break
		}
		p.Params = append(p.Params, prm)
	}
	if !strings.HasPrefix(rest, ":") {
		return p, fmt.Errorf("%w: missing value separator", ErrMalformed)
	}
	p.Value = rest[1:]
	return p, nil
}

// FormatLine renders one property without folding.
func FormatLine(p Property) string {
	var b strings.Builder
	b.WriteString(strings.ToUpper(p.Name))
	for _, prm := range p.Params {
		b.WriteByte(';')
		b.WriteString(strings.ToUpper(prm.Name))
		b.WriteByte('=')
		for i, v := range prm.Values {
			if i > 0 {
				b.WriteByte(',')
			}
			if strings.ContainsAny(v, ":;,") {
				b.WriteByte('"')
				b.WriteString(v)
				b.WriteByte('"')
			} else {
				b.WriteString(v)
			}
		}
	}
	b.WriteByte(':')
	b.WriteString(p.Value)
	return b.String()
}

// Fold splits a line into 75-octet pieces joined by CRLF and a space, never
// inside a UTF-8 sequence.
func Fold(line string) string {
	const limit = 75
	if len(line) <= limit {
		return line
	}
	var b strings.Builder
	width := limit
	for len(line) > width {
		cut := width
		for cut > 0 && !utf8.RuneStart(line[cut]) {
			cut--
		}
		b.WriteString(line[:cut])
		b.WriteString("\r\n ")
		line = line[cut:]
		width = limit - 1
	}
	b.WriteString(line)
	return b.String()
}

// Serialize renders a component with CRLF line endings and folded lines, with
// no trailing line break.
func Serialize(c *Component) string {
	var lines []string
	var walk func(*Component)
	walk = func(c *Component) {
		lines = append(lines, "BEGIN:"+c.Name)
		for _, p := range c.Props {
			lines = append(lines, Fold(FormatLine(p)))
		}
		for _, ch := range c.Children {
			walk(ch)
		}
		lines = append(lines, "END:"+c.Name)
	}
	walk(c)
	return strings.Join(lines, "\r\n")
}

// Wrap puts a VEVENT (or other component) inside VCALENDAR with VERSION and,
// when given, PRODID, as Proton's web client wraps its cards.
func Wrap(inner *Component, prodID string) string {
	head := "BEGIN:VCALENDAR\r\nVERSION:2.0\r\n"
	if prodID != "" {
		head += "PRODID:" + prodID + "\r\n"
	}
	return head + Serialize(inner) + "\r\nEND:VCALENDAR"
}

// EscapeText escapes a TEXT value (RFC 5545 section 3.3.11).
func EscapeText(s string) string {
	s = strings.ReplaceAll(s, "\r\n", "\n")
	r := strings.NewReplacer(`\`, `\\`, ";", `\;`, ",", `\,`, "\n", `\n`, "\r", `\n`)
	return r.Replace(s)
}

// UnescapeText reverses EscapeText.
func UnescapeText(s string) string {
	var b strings.Builder
	for i := 0; i < len(s); i++ {
		c := s[i]
		if c == '\\' && i+1 < len(s) {
			i++
			switch s[i] {
			case 'n', 'N':
				b.WriteByte('\n')
			default:
				b.WriteByte(s[i])
			}
			continue
		}
		b.WriteByte(c)
	}
	return b.String()
}
