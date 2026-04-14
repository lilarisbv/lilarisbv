/* @odoo-module */

import {markup} from "@odoo/owl";
import {registry} from "@web/core/registry";

export const marketplaceNotificationService = {
    dependencies: ["bus_service", "notification"],
    start(env, {bus_service, notification: notificationService}) {
        bus_service.subscribe("marketplace_notification", ({message, sticky, title, type, message_is_html}) => {
            notificationService.add(message_is_html ? markup(message) : message, {sticky, title, type});
        });
        bus_service.start();
    },
};

registry.category("services").add("marketplace_notification", marketplaceNotificationService);