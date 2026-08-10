/** @odoo-module **/

import { registry } from "@web/core/registry";
import { kanbanView } from "@web/views/kanban/kanban_view";
import { KanbanRenderer } from "@web/views/kanban/kanban_renderer";

export class MarketplaceKanbanRenderer extends KanbanRenderer {
    onMountedCallback() {
        super.onMountedCallback();
        this.moveOnboardingPanelInside();
    }

    onPatchedCallback() {
        super.onPatchedCallback();
        this.moveOnboardingPanelInside();
    }

    moveOnboardingPanelInside() {
        // Find the onboarding panel
        const panel = document.getElementById("marketplace_onboarding_panel");
        if (!panel) return;

        // Find the kanban renderer (this component's root)
        const rendererEl = this.el || document.querySelector(".o_kanban_renderer");
        if (!rendererEl) return;

        // Only move if panel is not already inside the renderer
        if (!rendererEl.contains(panel)) {
            // Insert panel as the first child of the renderer
            rendererEl.insertBefore(panel, rendererEl.firstChild);
        }
    }
}

export const marketplaceKanbanView = {
    ...kanbanView,
    Renderer: MarketplaceKanbanRenderer,
};

registry.category("views").add("marketplace_kanban", marketplaceKanbanView);
