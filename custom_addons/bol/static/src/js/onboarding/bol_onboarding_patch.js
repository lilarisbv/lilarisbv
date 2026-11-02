/** @odoo-module **/
import { patch } from "@web/core/utils/patch";
import { _t } from "@web/core/l10n/translation";
import { markup } from "@odoo/owl";
import { MarketplaceOnboarding } from "@base_marketplace/js/onboarding/marketplace_onboarding";
import { Many2XAutocomplete } from "@web/views/fields/relational_utils";

MarketplaceOnboarding.components = {
    ...(MarketplaceOnboarding.components || {}),
    Many2XAutocomplete,
};

patch(MarketplaceOnboarding.prototype, {
    markupHtml(str) {
        return str ? markup(String(str)) : "";
    },

    _bolMergeWorkflowOption(id, name) {
        const opts = this.state.formData.workflow_options || [];
        if (id && !opts.some((w) => w.id === id)) {
            this.state.formData.workflow_options = [...opts, { id, name }];
        }
    },

    _onWorkflowSelected(opType, records) {
        const idField = opType === "FBB" ? "bol_fbb_workflow_id" : "bol_fbr_workflow_id";
        const nameField = opType === "FBB" ? "bol_fbb_workflow_name" : "bol_fbr_workflow_name";
        const rec = records && records[0];
        if (!rec) {
            this.state.formData[idField] = false;
            this.state.formData[nameField] = "";
        } else {
            const name = rec.display_name || rec.name || "";
            this.state.formData[idField] = rec.id;
            this.state.formData[nameField] = name;
            this._bolMergeWorkflowOption(rec.id, name);
        }
        this.state.stepError = "";
    },

    _bolWorkflowProps(opType) {
        const nameField = opType === "FBB" ? "bol_fbb_workflow_name" : "bol_fbr_workflow_name";
        const fieldString = opType === "FBB" ? _t("FBB Workflow") : _t("FBR Workflow");
        const value = this.state.formData[nameField] || "";
        return {
            activeActions: { create: true, createEdit: true, write: true },
            autoSelect: true,
            fieldString,
            getDomain: () => [],
            placeholder: _t("Type to search or create..."),
            resModel: "order.workflow.config.ts",
            update: (records) => this._onWorkflowSelected(opType, records),
            value,
            context: { default_name: value },
        };
    },

    getFbrWorkflowProps() {
        return this._bolWorkflowProps("FBR");
    },

    getFbbWorkflowProps() {
        return this._bolWorkflowProps("FBB");
    },

    async openWorkflowFormView(opType) {
        const idField = opType === "FBB" ? "bol_fbb_workflow_id" : "bol_fbr_workflow_id";
        const nameField = opType === "FBB" ? "bol_fbb_workflow_name" : "bol_fbr_workflow_name";
        const id = this.state.formData[idField];
        const name = (this.state.formData[nameField] || "").trim();
        const action = {
            type: "ir.actions.act_window",
            res_model: "order.workflow.config.ts",
            views: [[false, "form"]],
            target: "new",
        };
        if (id) {
            action.res_id = id;
        } else {
            action.context = { default_name: name };
        }
        await this.action.doAction(action, {
            onClose: async (closeInfo) => {
                if (id || !closeInfo) return;
                const created = await this.orm.searchRead(
                    "order.workflow.config.ts",
                    [["name", "=", name]],
                    ["id", "name"],
                    { limit: 1, order: "id desc" },
                );
                if (created && created.length) {
                    this._onWorkflowSelected(opType, [{
                        id: created[0].id,
                        display_name: created[0].name,
                    }]);
                }
            },
        });
    },

    openFbrWorkflowFormView() {
        return this.openWorkflowFormView("FBR");
    },

    openFbbWorkflowFormView() {
        return this.openWorkflowFormView("FBB");
    },

    async createDefaultWorkflow(opType) {
        if (this.state.loadingWorkflow) return;
        this.state.loadingWorkflow = true;
        try {
            const instId = this.state.instanceId;
            if (!instId) {
                this.state.stepError = _t("Save the instance before creating a default workflow.");
                return;
            }
            const res = await this.orm.call(
                "mk.instance", "bol_create_default_workflow", [[instId], opType]
            );
            this.state.formData.workflow_options = res.workflow_options || [];
            const found = (res.workflow_options || []).find(w => w.id === res.id);
            if (opType === "FBB") {
                this.state.formData.bol_fbb_workflow_id   = res.id;
                this.state.formData.bol_fbb_workflow_name = found ? found.name : "";
            } else {
                this.state.formData.bol_fbr_workflow_id   = res.id;
                this.state.formData.bol_fbr_workflow_name = found ? found.name : "";
            }
        } finally {
            this.state.loadingWorkflow = false;
        }
    },
});
