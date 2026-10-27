import assert from "node:assert/strict";
import test from "node:test";

import { onEnableChange, runSlotHtml, switchTitle } from "../nautobot_workflow_engine/static/nautobot_workflow_engine/js/shared/run_control.js";

function fakeRow({ checked, published = true }) {
    const label = { attrs: {}, setAttribute(name, value) { this.attrs[name] = value; } };
    const input = {
        checked,
        disabled: false,
        attrs: { "data-enable": "/api/enable/", "data-disable": "/api/disable/" },
        getAttribute(name) { return this.attrs[name]; },
        closest(selector) { return selector === "label" ? label : null; },
    };
    const slot = {
        innerHTML: "",
        attrs: { "data-published": published ? "1" : "0", "data-launch": "/workflows/reboot/launch/" },
        getAttribute(name) { return this.attrs[name]; },
    };
    return { input, slot, label };
}

const ok = () => Promise.resolve({ ok: true });
const refused = () => Promise.resolve({ ok: false });

test("Run is a live link only when the workflow is published and enabled", () => {
    assert.match(runSlotHtml({ published: true, enabled: true, launchUrl: "/x/" }), /<a [^>]*href="\/x\/"[^>]*>.*Run<\/a>/);
    assert.match(runSlotHtml({ published: true, enabled: false, launchUrl: "/x/" }), /disabled.*Run/);
    assert.match(runSlotHtml({ published: true, enabled: false, launchUrl: "/x/" }), /Turn on Enabled first/);
    assert.match(runSlotHtml({ published: false, enabled: true, launchUrl: "/x/" }), /Publish it first/);
    assert.match(runSlotHtml({ published: true, enabled: true, launchUrl: '/x/"><script>' }), /&quot;&gt;&lt;script&gt;/);
});

test("turning Enabled on makes Run clickable straight away", async () => {
    const { input, slot, label } = fakeRow({ checked: true });
    const urls = [];
    const pending = onEnableChange(input, slot, (url) => { urls.push(url); return ok(); });
    assert.match(slot.innerHTML, /<a [^>]*href="\/workflows\/reboot\/launch\/"/, "Run is live before the server answers");
    assert.equal(input.disabled, true);
    assert.equal(await pending, true);
    assert.deepEqual(urls, ["/api/enable/"]);
    assert.equal(input.disabled, false);
    assert.equal(label.attrs.title, switchTitle(true));
});

test("turning Enabled off greys Run out straight away", async () => {
    const { input, slot, label } = fakeRow({ checked: false });
    const urls = [];
    await onEnableChange(input, slot, (url) => { urls.push(url); return ok(); });
    assert.deepEqual(urls, ["/api/disable/"]);
    assert.match(slot.innerHTML, /<button [^>]*disabled/);
    assert.doesNotMatch(slot.innerHTML, /<a /);
    assert.equal(label.attrs.title, switchTitle(false));
});

test("a refused or failed change puts the switch and Run back", async () => {
    const refusedRow = fakeRow({ checked: true });
    assert.equal(await onEnableChange(refusedRow.input, refusedRow.slot, refused), false);
    assert.equal(refusedRow.input.checked, false);
    assert.match(refusedRow.slot.innerHTML, /disabled/);

    const offline = fakeRow({ checked: false });
    await onEnableChange(offline.input, offline.slot, () => Promise.reject(new Error("offline")));
    assert.equal(offline.input.checked, true);
    assert.match(offline.slot.innerHTML, /<a /);
    assert.equal(offline.input.disabled, false);
});
