/** @odoo-module **/
import { _t } from "@web/core/l10n/translation";
import { registry } from "@web/core/registry";

registry.category("web_tour.tours").add('help_tour', {
    url: "/odoo",
    steps: () => [
        {
            content: _t("Need help? Click here to view the system guide and watch the training video!"),
            trigger: 'button[name="action_open_help_page"]',
            tooltipPosition: "bottom",
            run: 'click',
        },
    ]
});