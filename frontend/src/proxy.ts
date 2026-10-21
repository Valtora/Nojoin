import { NextRequest, NextResponse } from "next/server";

// Generate a per-request nonce and emit a strict Content-Security-Policy.
// Next.js automatically applies the nonce to its framework-injected
// <script> and <style> tags when it observes the `x-nonce` request header.
//
// This is the only CSP on app pages: the bundled nginx adds none on `/`.
// Images, fonts and media come from the app's own origin. The API does too,
// unless the image was built with an absolute NEXT_PUBLIC_API_URL, and then
// fetches and recording streams need that origin allowed as well.

// Mirrors the fallback in lib/api/client.ts. A relative URL such as the
// release build's "/api" is same-origin and adds nothing.
function apiOrigin(): string | null {
  try {
    return new URL(process.env.NEXT_PUBLIC_API_URL || "https://localhost:14443")
      .origin;
  } catch {
    return null;
  }
}

function generateNonce(): string {
  const bytes = new Uint8Array(16);
  crypto.getRandomValues(bytes);
  let binary = "";
  for (let i = 0; i < bytes.length; i++) {
    binary += String.fromCharCode(bytes[i]);
  }
  return btoa(binary);
}

export function proxy(request: NextRequest) {
  const nonce = generateNonce();
  const api = apiOrigin();
  const apiSource = api ? ` ${api}` : "";

  const csp = [
    "default-src 'self'",
    "base-uri 'self'",
    `connect-src 'self'${apiSource} ws: wss:`,
    "font-src 'self'",
    "frame-ancestors 'none'",
    "form-action 'self'",
    "img-src 'self' data:",
    `media-src 'self'${apiSource} blob:`,
    "object-src 'none'",
    `script-src 'self' 'nonce-${nonce}' 'strict-dynamic'`,
    `style-src 'self' 'nonce-${nonce}'`,
    "style-src-attr 'unsafe-inline'",
  ].join("; ");

  const requestHeaders = new Headers(request.headers);
  requestHeaders.set("x-nonce", nonce);

  const response = NextResponse.next({
    request: {
      headers: requestHeaders,
    },
  });

  response.headers.set("Content-Security-Policy", csp);
  return response;
}

export const config = {
  // Skip static assets and image optimization output. Everything else
  // (pages, API routes, RSC payloads) flows through the middleware so
  // the CSP nonce is available during render.
  matcher: [
    "/((?!_next/static|_next/image|favicon.ico|.*\\.(?:png|jpg|jpeg|gif|webp|svg|ico|css|js|map|txt|woff|woff2|ttf|otf)$).*)",
  ],
};
