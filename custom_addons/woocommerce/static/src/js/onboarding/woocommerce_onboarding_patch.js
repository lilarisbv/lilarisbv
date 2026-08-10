/** @odoo-module **/
import { patch } from "@web/core/utils/patch";
import { markup } from "@odoo/owl";
import { MarketplaceOnboarding } from "@base_marketplace/js/onboarding/marketplace_onboarding";

if (!MarketplaceOnboarding.prototype.markupHtml) {
    patch(MarketplaceOnboarding.prototype, {
        markupHtml(str) {
            return str ? markup(String(str)) : "";
        },
    });
}
