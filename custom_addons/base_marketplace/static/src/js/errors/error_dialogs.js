/** @odoo-module **/

import { _t } from "@web/core/l10n/translation";
import { registry } from "@web/core/registry";
import { browser } from "@web/core/browser/browser";
import { WarningDialog } from "@web/core/errors/error_dialogs";

// -----------------------------------------------------------------------------
// MarketplaceErrorDialog Dialog
// -----------------------------------------------------------------------------

export class MarketplaceErrorDialog extends WarningDialog {
    static template = "base_marketplace.MarketplaceErrorDialog";

    setup() {
        const { data, subType } = this.props;
        const [message, title, additionalContext] = data.arguments;
        this.title = title ? _t(title) : _t(this.getRandomErrorTitle());
        this.message = _t(message);
        this.additionalContext = additionalContext;
        this.traceback = this.props.traceback;
        if (this.props.data && this.props.data.debug) {
            this.traceback = `${this.props.data.debug}\nThe above server error caused the following client error:\n${this.traceback}`;
        }
    }

    getRandomErrorTitle() {
        const errorTitles = [
          "Oh snap!",
          "Oops!",
          "Uh-oh!",
          "Error!",
          "Yikes!",
          "Whoops!",
          "Houston, we have a problem!",
          "Oh no!",
          "Epic fail!",
        ];
        const randomIndex = Math.floor(Math.random() * errorTitles.length);
        return errorTitles[randomIndex];
    }

    onClickClipboard() {
        browser.navigator.clipboard.writeText(
            `${this.props.message}\n${this.traceback}`
        );
    }
}

registry
    .category("error_dialogs")
    .add("odoo.addons.base_marketplace.models.exceptions.MarketplaceException", MarketplaceErrorDialog)
