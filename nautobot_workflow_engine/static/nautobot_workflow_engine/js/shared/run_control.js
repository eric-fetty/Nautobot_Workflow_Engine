/**
 * The Run button on the Workflows list, and the Enabled switch that controls it.
 *
 * The page renders both on the server. When someone flips Enabled, the switch calls
 * bindEnableSwitch's handler, which repaints the Run slot straight away so Run is
 * clickable (or greyed out) without a page refresh, and puts it back if the change fails.
 */

const ON_TITLE = "On. Applies immediately.";
const OFF_TITLE = "Off: nobody can start it. Applies immediately.";

function escapeAttr(value) {
    return String(value ?? "")
        .replace(/&/g, "&amp;")
        .replace(/"/g, "&quot;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;");
}

/** Markup for the Run slot. Matches the server-rendered markup in workflow_list.html. */
export function runSlotHtml({ published, enabled, launchUrl }) {
    if (published && enabled) {
        return `<a class="btn btn-sm nwe-btn-outline-accent" href="${escapeAttr(launchUrl)}"><i class="mdi mdi-play"></i> Run</a>`;
    }
    const why = published ? "It's turned off. Turn on Enabled first." : "Publish it first";
    return `<span title="${escapeAttr(why)}"><button type="button" class="btn btn-sm nwe-btn-outline-accent" disabled><i class="mdi mdi-play"></i> Run</button></span>`;
}

/** Tooltip on the Enabled switch for a published workflow. */
export function switchTitle(enabled) {
    return enabled ? ON_TITLE : OFF_TITLE;
}

/** Show ``enabled`` in the row: the Run slot and the switch's tooltip. */
export function paintRow(input, slot, enabled) {
    if (slot) {
        slot.innerHTML = runSlotHtml({
            published: slot.getAttribute("data-published") === "1",
            enabled,
            launchUrl: slot.getAttribute("data-launch") || "",
        });
    }
    const label = input.closest ? input.closest("label") : null;
    if (label) label.setAttribute("title", switchTitle(enabled));
}

/**
 * Handle one change of an Enabled switch. ``post(url)`` returns a fetch-like promise.
 * Run follows the switch at once; if the server says no, both go back.
 */
export async function onEnableChange(input, slot, post) {
    const on = Boolean(input.checked);
    const url = on ? input.getAttribute("data-enable") : input.getAttribute("data-disable");
    input.disabled = true;
    paintRow(input, slot, on);
    let ok = false;
    try {
        const response = await post(url);
        ok = Boolean(response && response.ok);
    } catch (error) {
        ok = false;
    }
    input.disabled = false;
    if (!ok) {
        input.checked = !on;
        paintRow(input, slot, !on);
    }
    return ok;
}
