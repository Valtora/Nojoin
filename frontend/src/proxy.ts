import { NextRequest, NextResponse } from "next/server";

// Generate a per-request nonce and emit a strict Content-Security-Policy.
// Next.js automatically applies the nonce to its framework-injected
// <script> and <style> tags when it observes the `x-nonce` request header.

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

  const csp = [
    "default-src 'self'",
    "base-uri 'self'",
    "connect-src 'self' http: https: ws: wss:",
    "font-src 'self' data: http: https:",
    "frame-ancestors 'none'",
    "form-action 'self'",
    "img-src 'self' blob: data: http: https:",
    "media-src 'self' blob: data: http: https:",
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
