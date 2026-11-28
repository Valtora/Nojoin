package main

import (
	"bytes"
	"encoding/json"
	"go/ast"
	"go/parser"
	"go/token"
	"io/fs"
	"path/filepath"
	"strconv"
	"strings"
	"testing"
)

func runWith(t *testing.T, stdin string) (map[string]any, string, int) {
	t.Helper()
	var out, errw bytes.Buffer
	code := run(strings.NewReader(stdin), &out, &errw)
	var resp map[string]any
	if err := json.Unmarshal(out.Bytes(), &resp); err != nil {
		t.Fatalf("stdout is not one JSON object: %q", out.String())
	}
	return resp, errw.String(), code
}

func TestVersionOverStdio(t *testing.T) {
	resp, stderr, code := runWith(t, `{"v":1,"op":"version"}`)
	if code != 0 || resp["ok"] != true || stderr != "" {
		t.Fatalf("code %d, resp %v, stderr %q", code, resp, stderr)
	}
}

func TestFailuresExitZeroAndKeepSecretsOffStderr(t *testing.T) {
	secret := "s3cret-refresh-token"
	resp, stderr, code := runWith(t, `{"v":1,"op":"refresh","session":{"uid":"u","refresh_token":"`+secret+`"},"args":{"extra":true}}`)
	if code != 0 || resp["ok"] != false {
		t.Fatalf("a refused request still exits 0 with ok false: %d %v", code, resp)
	}
	if stderr != "nojoin-proton: invalid_request\n" {
		t.Fatalf("stderr carries one short code line, got %q", stderr)
	}
	b, _ := json.Marshal(resp)
	if strings.Contains(stderr, secret) || strings.Contains(string(b), secret) {
		t.Fatal("a secret was echoed")
	}
	resp, _, _ = runWith(t, strings.Repeat(" ", maxRequestBytes+1))
	if resp["error"].(map[string]any)["code"] != "invalid_request" {
		t.Fatalf("an oversized request gave %v", resp)
	}
}

// sourceFiles parses every non-test Go file in the module.
func sourceFiles(t *testing.T) map[string]*ast.File {
	t.Helper()
	fset := token.NewFileSet()
	files := map[string]*ast.File{}
	root := filepath.Join("..", "..")
	err := filepath.WalkDir(root, func(path string, d fs.DirEntry, err error) error {
		if err != nil {
			return err
		}
		if d.IsDir() && (d.Name() == ".cache" || d.Name() == "fakeproton" || d.Name() == "nojoin-proton-fake") {
			// The fake server and its command are test infrastructure, never
			// linked into the helper.
			return filepath.SkipDir
		}
		if d.IsDir() || !strings.HasSuffix(path, ".go") || strings.HasSuffix(path, "_test.go") {
			return nil
		}
		f, err := parser.ParseFile(fset, path, nil, 0)
		if err != nil {
			return err
		}
		files[filepath.ToSlash(path)] = f
		return nil
	})
	if err != nil {
		t.Fatal(err)
	}
	if len(files) < 10 {
		t.Fatalf("found only %d source files", len(files))
	}
	return files
}

func TestProcessHygiene(t *testing.T) {
	banned := map[string]bool{
		"os.Getenv": true, "os.LookupEnv": true, "os.Environ": true, "os.Setenv": true,
		"os.Create": true, "os.WriteFile": true, "os.OpenFile": true, "os.Mkdir": true, "os.MkdirAll": true,
		"os.CreateTemp": true, "os.MkdirTemp": true, "fmt.Print": true, "fmt.Printf": true, "fmt.Println": true,
	}
	for path, f := range sourceFiles(t) {
		for _, imp := range f.Imports {
			p, _ := strconv.Unquote(imp.Path.Value)
			if p == "log" || p == "log/slog" || p == "os/exec" {
				t.Errorf("%s imports %s: the helper logs nothing and runs nothing", path, p)
			}
		}
		ast.Inspect(f, func(n ast.Node) bool {
			sel, ok := n.(*ast.SelectorExpr)
			if !ok {
				return true
			}
			if x, ok := sel.X.(*ast.Ident); ok && banned[x.Name+"."+sel.Sel.Name] {
				t.Errorf("%s uses %s.%s: secrets come on stdin only and the helper writes no files", path, x.Name, sel.Sel.Name)
			}
			return true
		})
	}
}

func TestOnlySendITIPReachesMailAndOnePlaceSetsTheClientIdentity(t *testing.T) {
	for path, f := range sourceFiles(t) {
		ast.Inspect(f, func(n ast.Node) bool {
			lit, ok := n.(*ast.BasicLit)
			if !ok || lit.Kind != token.STRING {
				return true
			}
			v, _ := strconv.Unquote(lit.Value)
			if strings.Contains(v, "/mail/") && !strings.HasSuffix(path, "internal/calendar/itip.go") {
				t.Errorf("%s names a mail endpoint; only send_itip may", path)
			}
			if strings.EqualFold(v, "x-pm-appversion") && !strings.HasSuffix(path, "internal/papi/papi.go") {
				t.Errorf("%s sets the client identity header; only papi may", path)
			}
			return true
		})
	}
}
