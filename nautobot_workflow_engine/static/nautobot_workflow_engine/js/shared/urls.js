/** Build in-app links from server templates without trusting what goes into them. */

/**
 * Fill ``__KEY__`` in a server-provided URL template and return it only when it stays on this site.
 *
 * The key is URL-encoded, so it can't add a path, query, or scheme. Returns "" for anything that
 * would leave the current origin (``javascript:``, ``//other.host``, absolute URLs elsewhere).
 */
export function sameOriginUrl(template, key, origin) {
    const base = origin || (typeof window !== "undefined" ? window.location.origin : "");
    if (!template || !base) return "";
    const filled = String(template).replace("__KEY__", encodeURIComponent(String(key == null ? "" : key)));
    let url;
    try {
        url = new URL(filled, base);
    } catch (_error) {
        return "";
    }
    if (url.origin !== new URL(base).origin) return "";
    return url.pathname + url.search + url.hash;
}
