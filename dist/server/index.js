const SECURITY_HEADERS = {
  "Content-Security-Policy": "default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; connect-src 'self'; base-uri 'self'; frame-ancestors 'none'; form-action 'self'",
  "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
  "Referrer-Policy": "strict-origin-when-cross-origin",
  "X-Content-Type-Options": "nosniff",
  "X-Frame-Options": "DENY"
};

function assetPath(pathname) {
  if (pathname.endsWith("/")) return `${pathname}index.html`;
  const lastSegment = pathname.split("/").pop() || "";
  return lastSegment.includes(".") ? pathname : `${pathname}/index.html`;
}

export default {
  async fetch(request, env) {
    if (request.method !== "GET" && request.method !== "HEAD") {
      return new Response("Method not allowed", {
        status: 405,
        headers: { Allow: "GET, HEAD", ...SECURITY_HEADERS }
      });
    }

    const url = new URL(request.url);
    url.pathname = assetPath(url.pathname);
    const assetRequest = new Request(url, request);
    const assetResponse = await env.ASSETS.fetch(assetRequest);
    const headers = new Headers(assetResponse.headers);
    for (const [name, value] of Object.entries(SECURITY_HEADERS)) {
      headers.set(name, value);
    }

    if (url.pathname.endsWith(".html")) {
      headers.set("Cache-Control", "public, max-age=300, must-revalidate");
    } else if (assetResponse.ok) {
      headers.set("Cache-Control", "public, max-age=604800, immutable");
    }

    return new Response(assetResponse.body, {
      status: assetResponse.status,
      statusText: assetResponse.statusText,
      headers
    });
  }
};
