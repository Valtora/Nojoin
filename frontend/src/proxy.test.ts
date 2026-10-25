import { afterEach, describe, it, expect, vi } from "vitest";
import { proxy } from "./proxy";
import { NextRequest } from "next/server";

function cspDirectives(): Map<string, string> {
  const request = new NextRequest(new URL("https://example.com/dashboard"));
  const csp = proxy(request).headers.get("Content-Security-Policy") ?? "";
  return new Map(
    csp.split("; ").map((directive) => {
      const [name, ...sources] = directive.split(" ");
      return [name, sources.join(" ")];
    }),
  );
}

describe("proxy", () => {
  afterEach(() => {
    vi.unstubAllEnvs();
  });

  it("should set Content-Security-Policy and x-nonce headers", () => {
    // Construct a simulated NextRequest
    const request = new NextRequest(new URL("https://example.com/dashboard"));
    const response = proxy(request);

    expect(response).toBeDefined();

    // Verify Content-Security-Policy is set on the response
    const csp = response.headers.get("Content-Security-Policy");
    expect(csp).toBeDefined();
    expect(typeof csp).toBe("string");
    expect(csp).toContain("default-src 'self'");
    expect(csp).toContain("'strict-dynamic'");

    // Extract the nonce from the CSP header to verify its structure
    const match = csp!.match(/'nonce-([^']+)'/);
    expect(match).not.toBeNull();
    const nonce = match![1];
    expect(nonce).toBeDefined();
    expect(nonce.length).toBeGreaterThan(0);

    // Verify request headers have been configured with the nonce
    // (NextJS NextResponse.next sets it on the request headers internally)
    expect(response.headers.get("x-nonce") || request.headers.get("x-nonce")).toBeDefined();
  });

  it("allows no other host when the API is same-origin", () => {
    // The release build's value. nginx sends no CSP of its own on app pages,
    // so this policy is the whole of it and must not widen to other hosts.
    vi.stubEnv("NEXT_PUBLIC_API_URL", "/api");
    const directives = cspDirectives();

    expect(directives.get("connect-src")).toBe("'self' ws: wss:");
    expect(directives.get("font-src")).toBe("'self'");
    expect(directives.get("img-src")).toBe("'self' data:");
    expect(directives.get("media-src")).toBe("'self' blob:");
    for (const sources of directives.values()) {
      expect(sources.split(" ")).not.toContain("http:");
      expect(sources.split(" ")).not.toContain("https:");
    }
  });

  it("allows an absolute API origin for fetches, recording streams and capture images", () => {
    vi.stubEnv("NEXT_PUBLIC_API_URL", "https://api.example.com/api");
    const directives = cspDirectives();

    expect(directives.get("connect-src")).toBe(
      "'self' https://api.example.com ws: wss:",
    );
    expect(directives.get("media-src")).toBe(
      "'self' https://api.example.com blob:",
    );
    expect(directives.get("img-src")).toBe("'self' data: https://api.example.com");
  });
});
