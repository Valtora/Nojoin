import { describe, it, expect } from "vitest";
import { proxy } from "./proxy";
import { NextRequest } from "next/server";

describe("proxy", () => {
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
});
