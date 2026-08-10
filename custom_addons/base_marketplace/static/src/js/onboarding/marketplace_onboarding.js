/** @odoo-module **/

import { Component, useState, useRef, onWillStart, onMounted, onWillUnmount, onPatched } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { _t } from "@web/core/l10n/translation";
import { url } from "@web/core/utils/urls";
import { session } from "@web/session";
import { standardActionServiceProps } from "@web/webclient/actions/action_service";

export class MarketplaceOnboarding extends Component {
    static template = "base_marketplace.MarketplaceOnboardingMain";
    static props = { ...standardActionServiceProps };

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.notification = useService("notification");
        this.mkOnboardingRootRef = useRef("mkOnboardingRoot");

        const ctxActive = this.props.action?.context?.active_id;
        const activeId = ctxActive ? Number(ctxActive) : null;

        this.state = useState({
            loading: true,
            loadError: "",
            /** "pick" | "steps" */
            wizardPhase: "steps",
            connectors: [],
            /** Draft id without marketplace (from server) to pass into ensure(). */
            pendingActiveId: null,
            pickerLoadingKey: null,
            iconFallbackKeys: {},
            instanceId: activeId,
            /** Marketplace key for creating instance on save (when no existing instance). */
            marketplaceKey: null,
            currentStep: 1,
            totalSteps: 0,
            steps: [],
            marketplace: "",
            formData: {},
            saving: false,
            stepError: "",
            /** "forward" | "backward" | null: drives slide-in animation (null on first paint). */
            stepTransition: null,
            /** Track if instance was created during this onboarding (for cleanup on cancel). */
            isNewlyCreatedDraft: false,
            /** Track if user has saved meaningful data (to prevent accidental deletion). */
            hasSavedData: false,
            /** True when the database is neutralized: onboarding then forces Go Live. */
            isNeutralized: false,
            /** Whether the user has enabled Go Live during onboarding (neutralized db only). */
            goLive: false,
        });

        onWillStart(async () => {
            try {
                const wiz = await this.orm.call("mk.instance", "get_onboarding_wizard_state", [], {
                    active_id: activeId || false,
                });
                if (wiz.error) {
                    this.state.loadError = wiz.error;
                    return;
                }
                const connectors = wiz.connectors || [];
                this.state.connectors = connectors;
                this.state.isNeutralized = Boolean(wiz.is_neutralized);
                const onlyOne = connectors.length === 1;
                const skipPicker = Boolean(wiz.skip_picker) || onlyOne;

                if (skipPicker) {
                    this.state.wizardPhase = "steps";
                    this.state.pendingActiveId = null;
                    let instanceId = wiz.instance_id;
                    // If there's an existing instance, use it
                    if (instanceId) {
                        this.state.instanceId = instanceId;
                        this.state.isNewlyCreatedDraft = false;
                        await this.applyOnboardingPayload(
                            await this.orm.call("mk.instance", "get_onboarding_data", [[instanceId]])
                        );
                    } else if (onlyOne) {
                        // No existing instance - store marketplace key and load data without creating instance
                        this.state.marketplaceKey = connectors[0].key;
                        this.state.instanceId = null;
                        this.state.isNewlyCreatedDraft = false;
                        await this.applyOnboardingPayload(
                            await this.orm.call("mk.instance", "get_onboarding_data_for_marketplace", [this.state.marketplaceKey])
                        );
                    } else {
                        this.state.loadError = _t("Could not start onboarding for this marketplace.");
                        return;
                    }
                } else {
                    this.state.wizardPhase = "pick";
                    this.state.pendingActiveId = wiz.instance_id || null;
                    // When opening from existing instance, don't delete on cancel
                    if (wiz.instance_id) {
                        this.state.isNewlyCreatedDraft = false;
                    }
                }
            } catch (error) {
                this.state.loadError = error.data?.message || error.message || String(error);
            } finally {
                this.state.loading = false;
            }
        });

        this._onDocumentKeydown = (ev) => {
            if (ev.key !== "Escape") {
                return;
            }
            if (this.state.loading || this.state.saving) {
                return;
            }
            ev.preventDefault();
            ev.stopPropagation();
            this.onCancel();
        };
        onMounted(() => {
            document.addEventListener("keydown", this._onDocumentKeydown, true);
        });
        onWillUnmount(() => {
            document.removeEventListener("keydown", this._onDocumentKeydown, true);
        });

        /**
         * Step transitions: avoid t-key on slide zones (full DOM remount caused blink).
         * After OWL patches new step content, restart the CSS animation in one reflow.
         */
        this._slideAnimStep = undefined;
        onPatched(() => {
            if (this.state.wizardPhase !== "steps") {
                return;
            }
            const root = this.mkOnboardingRootRef.el;
            if (!root || this.state.loading) {
                return;
            }
            const step = this.state.currentStep;
            if (step === this._slideAnimStep) {
                return;
            }
            this._slideAnimStep = step;
            const t = this.state.stepTransition;
            if (!t) {
                return;
            }
            const zones = root.querySelectorAll(
                ".o_mk_edge_left_slide, .o_mk_edge_right_slide, .o_mk_edge_pagination_slide"
            );
            const cls = t === "forward" ? "o_mk_edge_slide_enter_fwd" : "o_mk_edge_slide_enter_back";
            for (const el of zones) {
                el.classList.remove("o_mk_edge_slide_enter_fwd", "o_mk_edge_slide_enter_back");
            }
            void root.offsetWidth;
            for (const el of zones) {
                el.classList.add(cls);
            }
        });

        // The Instance Name field lives in the per-marketplace step template, so the Go Live
        // toggle has no base-template anchor. It is built and injected by JS (not by OWL) so
        // it survives step changes that re-render the card content; after each render we make
        // sure it exists right after the first field and reflects the current state.
        onMounted(() => this._ensureGoLiveToggle());
        onPatched(() => this._ensureGoLiveToggle());
    }

    /**
     * Ensure the Go Live toggle exists inside the step card, right after the first field
     * (the Instance Name field on step 1). Building it in plain DOM keeps it independent of
     * OWL, so re-rendering a step (which replaces the card content) never loses it. Only
     * relevant on a neutralized database.
     */
    _ensureGoLiveToggle() {
        const root = this.mkOnboardingRootRef.el;
        if (!root) {
            return;
        }
        const card = root.querySelector(".o_mk_onboarding_card");
        const existing = card ? card.querySelector(".o_mk_onboarding_golive") : null;
        // Only show it on step 1 (next to the Instance Name field) of a neutralized-database
        // onboarding. Remove any leftover toggle on other steps.
        const shouldShow =
            this.state.wizardPhase === "steps" &&
            !this.state.loading &&
            this.state.isNeutralized &&
            this.state.currentStep === 1;
        if (!shouldShow || !card) {
            if (existing) {
                existing.remove();
            }
            return;
        }
        const block = existing || this._buildGoLiveToggle();
        // (Re)position right after the first field on every render, so it never drifts to
        // the bottom when the step content is re-rendered.
        const anchor = card.querySelector(".mb-3") || card.firstElementChild;
        if (anchor) {
            if (block.previousElementSibling !== anchor) {
                anchor.insertAdjacentElement("afterend", block);
            }
        } else if (block.parentElement !== card) {
            card.appendChild(block);
        }
        // Keep the checkbox in sync with the stored choice (e.g. when rebuilt on return).
        const input = block.querySelector("input");
        if (input) {
            input.checked = this.state.goLive;
        }
    }

    /**
     * Build the Go Live toggle DOM node (Bootstrap form-switch, matching the other onboarding
     * toggles). The change listener syncs the choice back into component state for the gate.
     */
    _buildGoLiveToggle() {
        const block = document.createElement("div");
        block.className = "o_mk_onboarding_golive form-check form-switch mt-3";
        const input = document.createElement("input");
        input.className = "form-check-input";
        input.type = "checkbox";
        input.id = "mk_onb_go_live";
        input.addEventListener("change", (ev) => this.setGoLive(ev.target.checked));
        const label = document.createElement("label");
        label.className = "form-check-label";
        label.setAttribute("for", "mk_onb_go_live");
        label.textContent = _t("Go Live");
        block.appendChild(input);
        block.appendChild(label);
        return block;
    }

    async applyOnboardingPayload(data) {
        this.state.steps = data.steps || [];
        this.state.totalSteps = data.total_steps || this.state.steps.length;
        this.state.marketplace = data.marketplace || "";
        this.state.formData = { ...(data.values || {}) };
        if (!this.state.formData.fulfillment_status_ids) {
            this.state.formData.fulfillment_status_ids = [];
        }
        if (!this.state.formData.tax_account_options) {
            this.state.formData.tax_account_options = [];
        }
        if (this.state.formData.name === undefined) {
            this.state.formData.name = "";
        }
        if (this.state.formData.import_order_after_date === undefined) {
            this.state.formData.import_order_after_date = "";
        }
        const needTaxAccounts =
            this.state.formData.tax_system !== "default" &&
            this.state.instanceId &&
            !(this.state.formData.tax_account_options || []).length;
        if (needTaxAccounts) {
            try {
                const accOpts = await this.orm.call("mk.instance", "get_onboarding_tax_account_options", [
                    [this.state.instanceId],
                ]);
                this.state.formData.tax_account_options = accOpts || [];
            } catch {
                /* keep empty; user can change Tax behaviour to refetch */
            }
        }
        this.state.currentStep = 1;
        this.state.stepTransition = null;
        this._slideAnimStep = undefined;

        // If no onboarding steps are available, show notification and redirect to form
        if (this.state.totalSteps === 0) {
            this.state.hasSavedData = true; // Mark to prevent deletion
            this.notification.add(
                _t("Quick onboarding is not available for this marketplace. You can configure it manually."),
                { type: "info" }
            );
            // Small delay to show the notification before redirecting
            setTimeout(() => this.openInstanceForm(), 500);
        }
    }

    /**
     * Get tax account options. Uses different method depending on whether instance exists.
     */
    async _getTaxAccountOptions() {
        if (this.state.instanceId) {
            return await this.orm.call("mk.instance", "get_onboarding_tax_account_options", [
                [this.state.instanceId],
            ]);
        } else {
            return await this.orm.call("mk.instance", "get_onboarding_tax_account_options_default_company", []);
        }
    }

    makePickHandler(key) {
        return async () => {
            await this.onPickMarketplace(key);
        };
    }

    makeConnectorIconErrorHandler(key) {
        return () => {
            this.state.iconFallbackKeys = { ...this.state.iconFallbackKeys, [key]: true };
        };
    }

    connectorShowsImage(connector) {
        return Boolean(connector.icon_url && !this.state.iconFallbackKeys[connector.key]);
    }

    /**
     * Full URL for picker images: ``web.base.url`` when set (reverse proxy / subpath), else Odoo ``url()`` helper.
     */
    pickerIconSrc(connector) {
        const path = connector.icon_url || "/web/static/img/placeholder.png";
        if (path.startsWith("http://") || path.startsWith("https://")) {
            return path;
        }
        const base = (session["web.base.url"] || "").replace(/\/+$/, "");
        if (base && path.startsWith("/")) {
            return base + path;
        }
        return url(path);
    }

    async onPickMarketplace(key) {
        if (this.state.pickerLoadingKey) {
            return;
        }
        this.state.pickerLoadingKey = key;
        try {
            const wasPending = this.state.pendingActiveId;
            // If there's an existing instance (pendingActiveId), bind it
            if (wasPending) {
                const iid = await this.orm.call(
                    "mk.instance",
                    "ensure_onboarding_instance_for_marketplace",
                    [key, wasPending]
                );
                this.state.instanceId = iid;
                this.state.pendingActiveId = null;
                this.state.isNewlyCreatedDraft = false;
                const data = await this.orm.call("mk.instance", "get_onboarding_data", [[iid]]);
                await this.applyOnboardingPayload(data);
            } else {
                // No existing instance - store marketplace key and load data without creating instance
                this.state.marketplaceKey = key;
                this.state.instanceId = null;
                this.state.isNewlyCreatedDraft = false;
                const data = await this.orm.call("mk.instance", "get_onboarding_data_for_marketplace", [key]);
                await this.applyOnboardingPayload(data);
            }
            this.state.wizardPhase = "steps";
        } catch (error) {
            const msg = error.data?.message || error.message || String(error);
            this.notification.add(msg, { type: "danger" });
        } finally {
            this.state.pickerLoadingKey = null;
        }
    }

    get pickerTitle() {
        return _t("Choose a marketplace");
    }

    get pickerLead() {
        return _t("Pick where you sell. We will prepare a draft instance for that connector if needed.");
    }

    get activeStepTemplate() {
        const step = this.state.steps[this.state.currentStep - 1];
        return step?.template || "base_marketplace.MarketplaceOnboarding_fallback_step";
    }

    get currentStepInfo() {
        return this.state.steps[this.state.currentStep - 1] || {};
    }

    get closeWizardLabel() {
        return _t("Close");
    }

    get dotSteps() {
        const n = this.state.totalSteps || 0;
        return Array.from({ length: n }, (_, i) => i + 1);
    }

    formatStepNum(step) {
        return `${step}.`;
    }

    stepNavTitle(step) {
        const meta = this.state.steps[step - 1];
        return meta?.title || "";
    }

    stepTabClass(step) {
        const parts = ["o_mk_edge_bottom_tab"];
        if (step === this.state.currentStep) {
            parts.push("is-selected");
        }
        if (step > this.state.currentStep) {
            parts.push("is-future");
        }
        return parts.join(" ");
    }

    makeStepTabHandler(step) {
        return async () => {
            await this.onStepTabClick(step);
        };
    }

    /**
     * Jump to a step: backward freely; forward only after validating intermediate steps.
     */
    async onStepTabClick(step) {
        if (step === this.state.currentStep) {
            return;
        }
        const total = this.state.totalSteps || 0;
        if (step < 1 || step > total) {
            return;
        }
        if (step < this.state.currentStep) {
            this.state.stepTransition = "backward";
            this.state.currentStep = step;
            this.state.stepError = "";
            return;
        }
        this.state.stepError = "";
        let s = this.state.currentStep;
        try {
            while (s < step) {
                const res = await this._validateStep(s);
                if (!res || !res.valid) {
                    this.state.stepError = res?.error || _t("Complete this step before continuing.");
                    return;
                }
                s += 1;
            }
            this.state.stepTransition = "forward";
            this.state.currentStep = step;
        } catch (error) {
            const msg = error.data?.message || error.message || String(error);
            this.notification.add(msg, { type: "danger" });
        }
    }

    /**
     * Validate a single step. Uses different method depending on whether instance exists.
     */
    async _validateStep(step) {
        if (this.state.instanceId) {
            return await this.orm.call("mk.instance", "validate_onboarding_step", [
                [this.state.instanceId],
                step,
                this.state.formData,
            ]);
        } else {
            // Use marketplace key for validation when no instance exists
            const marketplaceKey = this.state.marketplaceKey || this.state.marketplace;
            return await this.orm.call("mk.instance", "validate_onboarding_step_data", [
                marketplaceKey,
                step,
                this.state.formData,
            ]);
        }
    }

    onInput(field) {
        return (ev) => {
            const t = ev.target;
            const v = t.type === "checkbox" ? t.checked : t.value;
            this.state.formData[field] = v;
            this.state.stepError = "";
        };
    }

    onSelectSync(value) {
        this.state.formData.sync_product_with = value;
        const opts = this.state.formData.sync_product_with_options || [];
        const found = opts.find((o) => o.value === value);
        if (found) {
            this.state.formData.sync_product_with_label = found.label;
        }
        this.state.stepError = "";
    }

    async onSelectTax(value) {
        this.state.formData.tax_system = value;
        const opts = this.state.formData.tax_system_options || [];
        const found = opts.find((o) => o.value === value);
        if (found) {
            this.state.formData.tax_system_label = found.label;
        }
        if (value === "default") {
            this.state.formData.tax_account_id = false;
            this.state.formData.tax_refund_account_id = false;
        } else {
            try {
                const accOpts = await this._getTaxAccountOptions();
                this.state.formData.tax_account_options = accOpts || [];
            } catch {
                this.state.formData.tax_account_options = [];
            }
        }
        this.state.stepError = "";
    }

    onTaxChange(ev) {
        void this.onSelectTax(ev.target.value);
    }

    makeSelectSyncHandler(value) {
        return () => {
            this.onSelectSync(value);
        };
    }

    makeFulfillmentToggleHandler(statusId) {
        return () => {
            this.toggleFulfillmentStatus(statusId);
        };
    }

    toggleFulfillmentStatus(statusId) {
        const ids = [...(this.state.formData.fulfillment_status_ids || [])];
        const sid = typeof statusId === "number" ? statusId : parseInt(statusId, 10);
        const idx = ids.indexOf(sid);
        if (idx >= 0) {
            ids.splice(idx, 1);
        } else {
            ids.push(sid);
        }
        this.state.formData.fulfillment_status_ids = ids;
        this.state.stepError = "";
    }

    isFulfillmentSelected(statusId) {
        const sid = typeof statusId === "number" ? statusId : parseInt(statusId, 10);
        return (this.state.formData.fulfillment_status_ids || []).includes(sid);
    }

    syncOptionLabel(value) {
        const opts = this.state.formData.sync_product_with_options || [];
        const found = opts.find((o) => o.value === value);
        return found ? found.label : value;
    }

    taxOptionLabel(value) {
        const opts = this.state.formData.tax_system_options || [];
        const found = opts.find((o) => o.value === value);
        return found ? found.label : value;
    }

    /**
     * Label for onboarding tax account dropdowns (matches instance form domain options).
     * @param {"invoice"|"refund"} which
     */
    taxAccountLabel(which) {
        const fid = which === "invoice" ? "tax_account_id" : "tax_refund_account_id";
        const accId = this.state.formData[fid];
        const opts = this.state.formData.tax_account_options || [];
        const found = opts.find((o) => o.id === accId);
        return found ? found.name : accId ? String(accId) : "";
    }

    makeTaxAccountSelectHandler(field) {
        return (ev) => {
            const raw = ev.target.value;
            this.state.formData[field] = raw ? parseInt(raw, 10) : false;
            this.state.stepError = "";
        };
    }

    fulfillmentSummaryText() {
        const opts = this.state.formData.fulfillment_status_options || [];
        const idSet = new Set(this.state.formData.fulfillment_status_ids || []);
        const names = opts.filter((o) => idSet.has(o.id)).map((o) => o.name);
        return names.length ? names.join(", ") : _t("None selected");
    }

    /**
     * Set the Go Live choice during onboarding. Only relevant on a neutralized database,
     * where the user must enable this before advancing or saving.
     */
    setGoLive(value) {
        this.state.goLive = value;
        this.state.formData.go_live = value;
        this.state.stepError = "";
    }

    /**
     * On a neutralized database, block navigation until Go Live is enabled.
     * @returns {boolean} true when blocked (caller should stop).
     */
    _blockedByGoLive() {
        if (this.state.isNeutralized && !this.state.goLive) {
            this.state.stepError = _t("Please enable Go Live to continue.");
            return true;
        }
        return false;
    }

    async onNext() {
        this.state.stepError = "";
        if (this._blockedByGoLive()) {
            return;
        }
        try {
            const res = await this._validateStep(this.state.currentStep);
            if (!res || !res.valid) {
                this.state.stepError = res?.error || _t("Validation failed.");
                return;
            }
            if (this.state.currentStep < this.state.totalSteps) {
                this.state.stepTransition = "forward";
                this.state.currentStep += 1;
            }
        } catch (error) {
            const msg = error.data?.message || error.message || String(error);
            this.notification.add(msg, { type: "danger" });
        }
    }

    onPrevious() {
        if (this.state.currentStep > 1) {
            this.state.stepTransition = "backward";
            this.state.currentStep -= 1;
            this.state.stepError = "";
        }
    }

    async onCancel() {
        if (this.state.wizardPhase === "pick") {
            if (this.state.pendingActiveId) {
                await this.action.doAction({
                    type: "ir.actions.act_window",
                    res_model: "mk.instance",
                    res_id: this.state.pendingActiveId,
                    views: [[false, "form"]],
                    target: "current",
                });
            } else {
                await this.action.doAction("base_marketplace.action_marketplace_overview");
            }
            return;
        }
        // If this is a newly created draft instance from onboarding, always delete it on cancel
        // The instance should only persist if user explicitly clicked "Save"
        if (this.state.isNewlyCreatedDraft && !this.state.hasSavedData && this.state.instanceId) {
            try {
                await this.orm.call("mk.instance", "unlink_onboarding_draft", [[this.state.instanceId]]);
            } catch {
                // Ignore cleanup errors (instance may have been modified/deleted by another user)
            }
            // Clear the instance ID so we redirect to overview
            this.state.instanceId = null;
        }
        await this.openInstanceForm();
    }

    async openInstanceForm() {
        // If instance was deleted during cleanup (null ID) or was a newly created draft that wasn't saved, go to overview
        if (!this.state.instanceId) {
            await this.action.doAction("base_marketplace.action_marketplace_overview");
            return;
        }
        // Open the instance the same way a direct confirm does (named action +
        // dedicated form view), so the breadcrumb shows the instance instead of "Unnamed".
        const action = await this.orm.call(
            "mk.instance",
            "action_marketplace_open_instance_view",
            [[this.state.instanceId]],
        );
        await this.action.doAction(action);
    }

    async onDone() {
        this.state.stepError = "";
        if (this._blockedByGoLive()) {
            return;
        }
        this.state.saving = true;
        try {
            let res;
            if (this.state.instanceId) {
                // Existing instance - use normal save
                res = await this.orm.call("mk.instance", "save_onboarding_data", [
                    [this.state.instanceId],
                    this.state.formData,
                ]);
            } else {
                // No instance yet - create it and save data
                const marketplaceKey = this.state.marketplaceKey || this.state.marketplace;
                res = await this.orm.call("mk.instance", "save_onboarding_create_instance", [
                    marketplaceKey,
                    this.state.formData,
                ]);
                // Update state with the newly created instance ID
                if (res && res.instance_id) {
                    this.state.instanceId = res.instance_id;
                }
            }
            // Hook for connectors to handle the save result before the default
            // "open instance form" behavior (e.g. Shopify shows an inline scope check).
            if (this._onSaveResult(res)) {
                return;
            }
            if (res && res.success !== false) {
                // Mark that user has saved data, so we don't delete on cancel
                this.state.hasSavedData = true;
                this.notification.add(_t("Configuration saved."), { type: "success" });
                if (res.action) {
                    await this.action.doAction(res.action);
                } else {
                    await this.openInstanceForm();
                }
            }
        } catch (error) {
            const msg = error.data?.message || error.message || String(error);
            this.notification.add(msg, { type: "danger" });
        } finally {
            this.state.saving = false;
        }
    }

    /**
     * Hook: connectors may override to handle the save result before the default
     * "open instance form" behavior. Return true to stop the default handling.
     */
    _onSaveResult(res) {
        return false;
    }
}

registry.category("actions").add("marketplace_onboarding", MarketplaceOnboarding);
