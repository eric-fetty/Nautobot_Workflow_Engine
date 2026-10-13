(function () {
    function initializeWorkflowGroupCollapse() {
        var collapseAllButton = document.getElementById("workflow-collapse-all");
        if (!collapseAllButton) {
            return;
        }

        var collapseTargets = document.querySelectorAll(".workflow-category-collapse");
        var groupToggleButtons = document.querySelectorAll(".workflow-group-toggle[aria-controls]");

        function updateButtonState(isExpanded) {
            collapseAllButton.setAttribute("aria-expanded", isExpanded ? "true" : "false");
            collapseAllButton.innerHTML = isExpanded
                ? '<i class="mdi mdi-unfold-less-horizontal"></i> Collapse All Groups'
                : '<i class="mdi mdi-unfold-more-horizontal"></i> Expand All Groups';
        }

        function updateGroupIcon(collapseElement, isExpanded) {
            var button = document.querySelector('[aria-controls="' + collapseElement.id + '"]');
            if (!button) {
                return;
            }
            var icon = button.querySelector("[data-workflow-collapse-icon]");
            if (!icon) {
                return;
            }
            if (isExpanded) {
                icon.classList.remove("mdi-chevron-right");
                icon.classList.add("mdi-chevron-down");
            } else {
                icon.classList.remove("mdi-chevron-down");
                icon.classList.add("mdi-chevron-right");
            }
        }

        function getButtonTarget(button) {
            var targetId = button.getAttribute("aria-controls");
            if (!targetId) {
                return null;
            }
            return document.getElementById(targetId);
        }

        function syncCollapseAllButtonState() {
            if (!groupToggleButtons.length) {
                return;
            }
            var allExpanded = true;
            for (var i = 0; i < groupToggleButtons.length; i++) {
                var button = groupToggleButtons[i];
                if (button.getAttribute("aria-expanded") !== "true") {
                    allExpanded = false;
                }
            }
            updateButtonState(allExpanded);
        }

        function setGroupExpanded(button, shouldExpand) {
            var isExpanded = button.getAttribute("aria-expanded") === "true";
            if (isExpanded === shouldExpand) {
                return;
            }
            var target = getButtonTarget(button);
            if (!target) {
                return;
            }
            target.classList.toggle("show", shouldExpand);
            button.setAttribute("aria-expanded", shouldExpand ? "true" : "false");
            updateGroupIcon(target, shouldExpand);
        }

        function toggleGroup(button) {
            var target = getButtonTarget(button);
            if (!target) {
                return;
            }
            var isExpanded = button.getAttribute("aria-expanded") === "true";
            var nextExpanded = !isExpanded;
            target.classList.toggle("show", nextExpanded);
            button.setAttribute("aria-expanded", nextExpanded ? "true" : "false");
            updateGroupIcon(target, nextExpanded);
            syncCollapseAllButtonState();
        }

        collapseAllButton.addEventListener("click", function () {
            var currentlyExpanded = collapseAllButton.getAttribute("aria-expanded") === "true";
            var shouldExpand = !currentlyExpanded;
            for (var i = 0; i < groupToggleButtons.length; i++) {
                setGroupExpanded(groupToggleButtons[i], shouldExpand);
            }
            syncCollapseAllButtonState();
        });

        for (var i = 0; i < groupToggleButtons.length; i++) {
            (function (button) {
                button.addEventListener("click", function (event) {
                    event.preventDefault();
                    toggleGroup(button);
                });
            })(groupToggleButtons[i]);
        }

        for (var j = 0; j < collapseTargets.length; j++) {
            var target = collapseTargets[j];
            updateGroupIcon(target, target.classList.contains("show"));
        }

        syncCollapseAllButtonState();
    }

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", initializeWorkflowGroupCollapse);
    } else {
        initializeWorkflowGroupCollapse();
    }
})();
