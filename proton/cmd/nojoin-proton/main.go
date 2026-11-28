// Command nojoin-proton runs one Proton Calendar operation for Nojoin: one
// JSON request on stdin, one JSON response on stdout, exit 0 either way. See
// PROTOCOL.md. A non-zero exit means the helper crashed.
package main

import (
	"context"
	"fmt"
	"io"
	"os"

	"github.com/sirupsen/logrus"

	"github.com/Valtora/Nojoin/proton/internal/ops"
	"github.com/Valtora/Nojoin/proton/internal/protocol"
)

const (
	maxRequestBytes  = 4 << 20
	maxResponseBytes = 32 << 20
)

func main() {
	os.Exit(run(os.Stdin, os.Stdout, os.Stderr))
}

func run(stdin io.Reader, stdout, stderr io.Writer) int {
	// go-proton-api's key unlocking logs through logrus; the helper logs
	// nothing but one error code on stderr.
	logrus.SetOutput(io.Discard)

	raw, err := io.ReadAll(io.LimitReader(stdin, maxRequestBytes+1))
	var resp protocol.Response
	switch {
	case err != nil:
		resp = protocol.Response{V: protocol.Version, Error: protocol.Errorf(protocol.CodeInvalidRequest, "The request could not be read.")}
	case len(raw) > maxRequestBytes:
		resp = protocol.Response{V: protocol.Version, Error: protocol.Errorf(protocol.CodeInvalidRequest, "The request is larger than 4 MiB.")}
	default:
		resp = ops.Run(context.Background(), raw)
	}
	if resp.Error != nil && resp.Error.Details == nil {
		resp.Error.Details = map[string]any{}
	}
	out := ops.Encode(resp, maxResponseBytes)
	if _, err := stdout.Write(out); err != nil {
		return 3
	}
	if resp.Error != nil {
		fmt.Fprintf(stderr, "nojoin-proton: %s\n", resp.Error.Code)
	}
	return 0
}
