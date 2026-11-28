import assert from "node:assert/strict";
import test from "node:test";

import { sameOriginUrl } from "../nautobot_workflow_engine/static/nautobot_workflow_engine/js/shared/urls.js";

const ORIGIN = "https://nautobot.example.com";

test("fills the key into a same-site template", () => {
    assert.equal(sameOriginUrl("/plugins/wfe/__KEY__/edit/", "reboot-device", ORIGIN), "/plugins/wfe/reboot-device/edit/");
});

test("encodes the key so it can't change the path or query", () => {
    assert.equal(sameOriginUrl("/w/__KEY__/edit/", "../../admin?x=<b>", ORIGIN), "/w/..%2F..%2Fadmin%3Fx%3D%3Cb%3E/edit/");
});

test("refuses templates that leave the site", () => {
    assert.equal(sameOriginUrl("javascript:alert(1)//__KEY__", "k", ORIGIN), "");
    assert.equal(sameOriginUrl("//evil.example/__KEY__", "k", ORIGIN), "");
    assert.equal(sameOriginUrl("https://evil.example/__KEY__", "k", ORIGIN), "");
});

test("keeps absolute URLs on the same origin as a path", () => {
    assert.equal(sameOriginUrl(`${ORIGIN}/w/__KEY__/?tab=1`, "a", ORIGIN), "/w/a/?tab=1");
});

test("returns empty for a missing template", () => {
    assert.equal(sameOriginUrl("", "a", ORIGIN), "");
    assert.equal(sameOriginUrl(undefined, "a", ORIGIN), "");
});

test("accepts a finished server URL with no key", () => {
    assert.equal(sameOriginUrl("/plugins/wfe/copy-1/edit/", undefined, ORIGIN), "/plugins/wfe/copy-1/edit/");
    assert.equal(sameOriginUrl("javascript:alert(1)", undefined, ORIGIN), "");
});
