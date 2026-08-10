/** @odoo-module **/

import { registry } from "@web/core/registry";
import { Component, onMounted, useState } from "@odoo/owl";
import { useService } from "@web/core/utils/hooks";

/**
 * Simple component to load and populate instance boxes
 * This component is mounted in the Quick Onboarding panel
 */
export class QuickOnboardingInstanceBoxes extends Component {
    static template = "base_marketplace.QuickOnboardingInstanceBoxes";
    static props = {};

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");

        this.state = useState({
            instances: [],
            loading: false,
            loaded: false,
        });

        onMounted(() => {
            this.loadAndPopulateInstances();
        });
    }

    async loadAndPopulateInstances() {
        if (this.state.loading || this.state.loaded) return;

        this.state.loading = true;
        try {
            const instances = await this.orm.searchRead(
                "mk.instance",
                [["state", "in", ["draft", "confirmed", "error"]]],
                [
                    "id",
                    "name",
                    "marketplace",
                    "state",
                    "image_small",
                    "mk_listing_count",
                    "mk_order_count",
                    "mk_customer_count",
                    "last_sync_display",
                    "sync_failed_count",
                ],
                { limit: 20, order: "sequence,name" }
            );
            this.state.instances = instances;
            this.state.loaded = true;
            this.populateScrollableContainer();
        } catch (error) {
            console.error("[QuickOnboarding] Failed to load instances:", error);
        } finally {
            this.state.loading = false;
        }
    }

    populateScrollableContainer() {
        const scrollContainer = document.querySelector(".o_mk_onboarding_instances_scroll");
        if (!scrollContainer) return;

        // Don't re-populate if already has boxes
        if (scrollContainer.querySelector(".o_mk_onboarding_instance_box")) {
            return;
        }

        // Update count badge
        const countBadge = document.querySelector(".o_mk_onboarding_instances_count");
        if (countBadge) {
            countBadge.textContent = this.state.instances.length;
        }

        // Clear container
        scrollContainer.innerHTML = "";

        // Handle empty state
        if (this.state.instances.length === 0) {
            scrollContainer.innerHTML = `
                <div class="o_mk_onboarding_instances_empty" style="width: 100%; padding: 32px; text-align: center; color: #6c757d;">
                    <i class="fa fa-inbox" style="font-size: 32px; color: rgba(124, 123, 173, 0.3); margin-bottom: 12px;"></i>
                    <div style="font-size: 13px;">No instances yet. Create your first marketplace instance!</div>
                </div>
            `;
            return;
        }

        // Populate instance boxes
        this.state.instances.forEach((instance) => {
            scrollContainer.appendChild(this.createInstanceBox(instance));
        });

        this.attachListeners();
    }

    createInstanceBox(instance) {
        const box = document.createElement("div");
        box.className = "o_mk_onboarding_instance_box";
        box.setAttribute("data-id", instance.id);
        box.style.cssText = `
            flex-shrink: 0;
            width: 280px;
            background: white;
            border-radius: 10px;
            border: 1px solid rgba(124, 123, 173, 0.2);
            padding: 16px;
            cursor: pointer;
            transition: all 0.2s ease;
            position: relative;
            overflow: hidden;
        `;

        // Hover effects
        box.addEventListener("mouseenter", () => {
            box.style.borderColor = "rgba(124, 123, 173, 0.4)";
            box.style.boxShadow = "0 4px 16px rgba(124, 123, 173, 0.15)";
            box.style.transform = "translateY(-2px)";
        });
        box.addEventListener("mouseleave", () => {
            box.style.borderColor = "rgba(124, 123, 173, 0.2)";
            box.style.boxShadow = "none";
            box.style.transform = "translateY(0)";
        });

        // Overlay
        const overlay = document.createElement("div");
        overlay.className = "o_mk_instance_box_overlay";
        overlay.style.cssText = `
            position: absolute;
            top: 0; left: 0; right: 0; bottom: 0;
            background: linear-gradient(135deg, rgba(124, 123, 173, 0.05) 0%, rgba(124, 123, 173, 0.02) 100%);
            opacity: 0;
            transition: opacity 0.2s ease;
            pointer-events: none;
            border-radius: 10px;
        `;
        box.addEventListener("mouseenter", () => overlay.style.opacity = "1");
        box.addEventListener("mouseleave", () => overlay.style.opacity = "0");
        box.appendChild(overlay);

        // Header
        const header = document.createElement("div");
        header.className = "o_mk_instance_header";
        header.style.cssText = `
            display: flex;
            align-items: center;
            gap: 12px;
            margin-bottom: 12px;
            position: relative;
            z-index: 1;
        `;

        // Logo
        const logo = document.createElement("div");
        logo.className = "o_mk_instance_logo";
        logo.style.cssText = `
            width: 48px;
            height: 48px;
            border-radius: 10px;
            background: linear-gradient(135deg, rgba(124, 123, 173, 0.15) 0%, rgba(124, 123, 173, 0.08) 100%);
            display: flex;
            align-items: center;
            justify-content: center;
            flex-shrink: 0;
            overflow: hidden;
        `;
        if (instance.image_small) {
            const img = document.createElement("img");
            img.src = `/web/image/mk.instance/${instance.id}/image_small/48x48`;
            img.style.cssText = "width: 100%; height: 100%; object-fit: cover;";
            logo.appendChild(img);
        } else {
            const icon = document.createElement("i");
            icon.className = "fa fa-store";
            icon.style.cssText = "font-size: 20px; color: #7c7bad;";
            logo.appendChild(icon);
        }
        header.appendChild(logo);

        // Info
        const info = document.createElement("div");
        info.className = "o_mk_instance_info";
        info.style.cssText = "flex: 1; min-width: 0;";

        const name = document.createElement("div");
        name.className = "o_mk_instance_name";
        name.style.cssText = "font-size: 14px; font-weight: 600; color: #212529; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;";
        name.textContent = instance.name;
        name.title = instance.name;
        info.appendChild(name);

        const marketplace = document.createElement("div");
        marketplace.className = "o_mk_instance_marketplace";
        marketplace.style.cssText = "font-size: 12px; color: #6c757d; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;";
        marketplace.textContent = instance.marketplace || "";
        info.appendChild(marketplace);

        header.appendChild(info);
        box.appendChild(header);

        // Stats
        const stats = document.createElement("div");
        stats.className = "o_mk_instance_stats";
        stats.style.cssText = `
            display: grid;
            grid-template-columns: repeat(3, 1fr);
            gap: 8px;
            position: relative;
            z-index: 1;
        `;

        const statData = [
            { label: "Orders", value: instance.mk_order_count || 0 },
            { label: "Items", value: instance.mk_listing_count || 0 },
            { label: "Customers", value: instance.mk_customer_count || 0 },
        ];

        statData.forEach((stat) => {
            const statEl = document.createElement("div");
            statEl.className = "o_mk_instance_stat";
            statEl.style.cssText = "text-align: center; padding: 8px 4px; background: rgba(124, 123, 173, 0.05); border-radius: 6px;";

            const valueEl = document.createElement("div");
            valueEl.className = "o_mk_instance_stat_value";
            valueEl.style.cssText = "font-size: 14px; font-weight: 600; color: #7c7bad;";
            valueEl.textContent = stat.value;
            statEl.appendChild(valueEl);

            const labelEl = document.createElement("div");
            labelEl.className = "o_mk_instance_stat_label";
            labelEl.style.cssText = "font-size: 10px; color: #6c757d; text-transform: uppercase; letter-spacing: 0.5px;";
            labelEl.textContent = stat.label;
            statEl.appendChild(labelEl);

            stats.appendChild(statEl);
        });

        box.appendChild(stats);

        // Status
        const status = document.createElement("div");
        status.className = "o_mk_instance_status";
        status.style.cssText = `
            margin-top: 12px;
            display: flex;
            align-items: center;
            justify-content: space-between;
            position: relative;
            z-index: 1;
        `;

        let badgeBg, badgeColor, badgeIcon, badgeText;
        if (instance.state === "confirmed") {
            badgeBg = "rgba(40, 167, 69, 0.12)";
            badgeColor = "#28a745";
            badgeIcon = "fa-check-circle";
            badgeText = "Connected";
        } else if (instance.state === "draft") {
            badgeBg = "rgba(255, 193, 7, 0.12)";
            badgeColor = "#ffc107";
            badgeIcon = "fa-clock-o";
            badgeText = "Draft";
        } else {
            badgeBg = "rgba(220, 53, 69, 0.12)";
            badgeColor = "#dc3545";
            badgeIcon = "fa-exclamation-circle";
            badgeText = "Error";
        }

        const badge = document.createElement("div");
        badge.className = `o_mk_instance_badge ${instance.state === "confirmed" ? "o_mk_instance_badge_connected" : instance.state === "draft" ? "o_mk_instance_badge_draft" : "o_mk_instance_badge_error"}`;
        badge.style.cssText = `
            display: inline-flex;
            align-items: center;
            gap: 4px;
            padding: 4px 10px;
            border-radius: 12px;
            font-size: 11px;
            font-weight: 500;
            background: ${badgeBg};
            color: ${badgeColor};
        `;

        const badgeIconEl = document.createElement("i");
        badgeIconEl.className = `fa ${badgeIcon}`;
        badgeIconEl.style.color = badgeColor;
        badge.appendChild(badgeIconEl);

        const badgeSpan = document.createElement("span");
        badgeSpan.textContent = badgeText;
        badge.appendChild(badgeSpan);

        status.appendChild(badge);

        const arrow = document.createElement("i");
        arrow.className = "fa fa-chevron-right o_mk_instance_arrow";
        arrow.style.cssText = "color: #adb5bd; font-size: 12px; transition: transform 0.2s ease;";
        box.addEventListener("mouseenter", () => arrow.style.transform = "translateX(3px)");
        box.addEventListener("mouseleave", () => arrow.style.transform = "translateX(0)");
        status.appendChild(arrow);

        box.appendChild(status);

        // Sync status
        const sync = document.createElement("div");
        sync.className = "o_mk_instance_sync";
        sync.style.cssText = `
            margin-top: 8px;
            padding-top: 8px;
            border-top: 1px solid rgba(0, 0, 0, 0.05);
            display: flex;
            align-items: center;
            gap: 6px;
            font-size: 11px;
            color: #6c757d;
            position: relative;
            z-index: 1;
        `;

        const dot = document.createElement("span");
        dot.className = "o_mk_sync_dot";
        const dotColor = instance.sync_failed_count > 0 ? "#dc3545" : "#28a745";
        dot.style.cssText = `width: 6px; height: 6px; border-radius: 50%; background-color: ${dotColor};`;
        sync.appendChild(dot);

        const syncText = document.createElement("span");
        syncText.textContent = `Last sync: ${instance.last_sync_display || "Never"}`;
        sync.appendChild(syncText);

        box.appendChild(sync);

        return box;
    }

    attachListeners() {
        const boxes = document.querySelectorAll(".o_mk_onboarding_instance_box:not([data-listener='true'])");
        boxes.forEach((box) => {
            box.setAttribute("data-listener", "true");
            box.addEventListener("click", (e) => {
                e.preventDefault();
                e.stopPropagation();
                const instanceId = parseInt(box.getAttribute("data-id"), 10);
                this.openInstance(instanceId);
            });
        });
    }

    async openInstance(instanceId) {
        await this.action.doAction({
            type: "ir.actions.act_window",
            res_model: "mk.instance",
            res_id: instanceId,
            views: [[false, "form"]],
            target: "current",
        });
    }
}

// Register the widget
registry.category("widgets").add("quick_onboarding_instance_boxes", QuickOnboardingInstanceBoxes);
