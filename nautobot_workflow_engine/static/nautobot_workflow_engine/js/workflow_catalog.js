import { onEnableChange } from "./shared/run_control.js";
import { sameOriginUrl } from "./shared/urls.js";

(function () {
    function csrf() {
        var input = document.querySelector("#nwe-catalog-csrf input[name=csrfmiddlewaretoken]");
        if (input) return input.value;
        var match = document.cookie.match(/(?:^|; )csrftoken=([^;]+)/);
        return match ? decodeURIComponent(match[1]) : "";
    }

    function post(url, body) {
        return fetch(url, {
            method: "POST",
            credentials: "same-origin",
            headers: {
                "Content-Type": "application/json",
                "X-CSRFToken": csrf(),
            },
            body: JSON.stringify(body || {}),
        });
    }

    document.querySelectorAll("[data-nwe-enable]").forEach(function (input) {
        input.addEventListener("change", function () {
            var row = input.closest("tr");
            var slot = row ? row.querySelector("[data-nwe-run-slot]") : null;
            onEnableChange(input, slot, function (url) { return post(url, {}); });
        });
    });

    document.querySelectorAll("[data-nwe-retry]").forEach(function (button) {
        button.addEventListener("click", function () {
            var urls = (button.getAttribute("data-nwe-retry") || "").split(" ").filter(Boolean);
            button.disabled = true;
            Promise.all(urls.map(function (url) { return post(url, {}); })).then(function () {
                window.location.reload();
            });
        });
    });

    document.querySelectorAll("[data-nwe-delete]").forEach(function (button) {
        button.addEventListener("click", function () {
            var key = button.getAttribute("data-key") || "";
            if (!window.confirm("Delete " + key + "? It leaves the list. Run history stays.")) return;
            post(button.getAttribute("data-nwe-delete"), { confirm_key: key }).then(function (response) {
                if (response.ok) window.location.reload();
            });
        });
    });

    document.querySelectorAll("[data-nwe-duplicate]").forEach(function (button) {
        button.addEventListener("click", function () {
            var key = window.prompt("Key for the copy");
            var name = window.prompt("Name for the copy");
            if (!key || !name) return;
            post(button.getAttribute("data-nwe-duplicate"), { key: key, name: name }).then(function (response) {
                if (!response.ok) return;
                var next = sameOriginUrl(button.getAttribute("data-edit-template") || "", key);
                if (next) window.location.assign(next);
            });
        });
    });

    document.querySelectorAll("[data-nwe-yaml]").forEach(function (button) {
        button.addEventListener("click", function () {
            fetch(button.getAttribute("data-nwe-yaml"), { credentials: "same-origin" }).then(function (response) {
                return response.json();
            }).then(function (rows) {
                var published = (rows || []).filter(function (row) { return row.state === "published"; }).pop();
                var chosen = published || (rows || [])[0];
                if (!chosen || !chosen.yaml) return;
                var blob = new Blob([chosen.yaml], { type: "text/yaml" });
                var link = document.createElement("a");
                link.href = URL.createObjectURL(blob);
                link.download = (button.getAttribute("data-key") || "workflow") + ".yaml";
                link.click();
                URL.revokeObjectURL(link.href);
            });
        });
    });

    // Bulk edit: row checkboxes feed Nautobot's standard bulk edit page (add or remove tags).
    var bulkButton = document.getElementById("nwe-bulk-edit");
    var selectAll = document.getElementById("nwe-select-all");
    var boxes = Array.prototype.slice.call(document.querySelectorAll("[data-nwe-select]"));
    function paintBulk() {
        var picked = boxes.filter(function (box) { return box.checked; }).length;
        if (bulkButton) {
            bulkButton.disabled = picked === 0;
            var count = bulkButton.querySelector("[data-nwe-bulk-count]");
            if (count) count.textContent = picked ? String(picked) : "";
        }
        if (selectAll) {
            selectAll.checked = boxes.length > 0 && picked === boxes.length;
            selectAll.indeterminate = picked > 0 && picked < boxes.length;
            selectAll.disabled = boxes.length === 0;
        }
    }
    boxes.forEach(function (box) { box.addEventListener("change", paintBulk); });
    if (selectAll) {
        selectAll.addEventListener("change", function () {
            boxes.forEach(function (box) { box.checked = selectAll.checked; });
            paintBulk();
        });
    }
    paintBulk();
})();
