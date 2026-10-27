/**Session-authenticated calls to the builder API. */

export function createApi(config) {
    let requestId = 0;
    let inflight = null;

    async function send(url, { method = "GET", body } = {}) {
        const headers = { Accept: "application/json" };
        if (body !== undefined) {
            headers["Content-Type"] = "application/json";
        }
        if (config.csrf_token && method !== "GET") {
            headers["X-CSRFToken"] = config.csrf_token;
        }
        const response = await fetch(url, {
            method,
            credentials: "same-origin",
            headers,
            body: body === undefined ? undefined : JSON.stringify(body),
        });
        const text = await response.text();
        let data = {};
        if (text) {
            try {
                data = JSON.parse(text);
            } catch (error) {
                data = { detail: text.slice(0, 500) };
            }
        }
        return { ok: response.ok, status: response.status, data };
    }

    function validate(payload) {
        requestId += 1;
        const mine = requestId;
        if (inflight) {
            inflight.abort();
        }
        const controller = new AbortController();
        inflight = controller;
        const headers = {
            Accept: "application/json",
            "Content-Type": "application/json",
            "X-CSRFToken": config.csrf_token || "",
        };
        return fetch(config.validate_url, {
            method: "POST",
            credentials: "same-origin",
            headers,
            body: JSON.stringify({ payload, strict: false }),
            signal: controller.signal,
        })
            .then(async (response) => {
                if (mine !== requestId) {
                    return null;
                }
                const data = await response.json();
                return { ok: response.ok, status: response.status, data };
            })
            .catch((error) => {
                if (error.name === "AbortError" || mine !== requestId) {
                    return null;
                }
                return { ok: false, status: 0, data: { detail: "Couldn't reach the validation service." } };
            });
    }

    return { send, validate };
}
