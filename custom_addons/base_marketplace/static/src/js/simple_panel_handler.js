/** @odoo-module **/

import { _t } from "@web/core/l10n/translation";
import { session } from "@web/session";
import { rpc } from "@web/core/network/rpc";
import { user } from "@web/core/user";

/**
 * Detect Odoo's active theme (not the OS setting). Reads the computed background
 * luminance of the web client so the panel follows Odoo's System/Light/Dark
 * choice instead of `prefers-color-scheme`.
 */
function isOdooDarkMode() {
    const getLuminance = (bgColor) => {
        const match = (bgColor || "").match(/\d+/g);
        if (!match || match.length < 3) {
            return null;
        }
        const r = parseInt(match[0]), g = parseInt(match[1]), b = parseInt(match[2]);
        const a = match.length >= 4 ? parseFloat(match[3]) : 1;
        if (a < 0.05) {
            return null; // transparent — skip
        }
        return (0.299 * r + 0.587 * g + 0.114 * b) / 255;
    };
    const candidates = [
        document.documentElement,
        document.body,
        document.querySelector(".o_web_client"),
        document.querySelector(".o_action_manager"),
        document.querySelector(".o_main_navbar"),
    ];
    for (const el of candidates) {
        if (!el) {
            continue;
        }
        const lum = getLuminance(window.getComputedStyle(el).backgroundColor);
        if (lum !== null) {
            return lum < 0.5;
        }
    }
    return window.matchMedia("(prefers-color-scheme: dark)").matches;
}

class MarketplaceOnboardingPanel {
    constructor() {
        this.env = null;
        this.orm = null;
        this.action = null;
        this.notification = null;
        this.userId = null;
        this.initializeServices();
    }

    /**
     * Bind OWL web client services (orm, action, notification).
     * Uses odoo.__WOWL_DEBUG__ (standard Odoo web client); optional legacy odoo.__debug__.
     */
    bindWebClientServices() {
        try {
            let env = null;
            if (typeof odoo !== "undefined" && odoo.__WOWL_DEBUG__ && odoo.__WOWL_DEBUG__.root) {
                env = odoo.__WOWL_DEBUG__.root.env;
            }
            if (env && env.services) {
                this.env = env;
                this.orm = env.services.orm;
                this.action = env.services.action;
                this.notification = env.services.notification;
            } else if (typeof odoo !== "undefined" && odoo.__debug__ && odoo.__debug__.services) {
                const svc = odoo.__debug__.services;
                this.env = svc;
                this.orm = svc.orm;
                this.action = svc.action;
                this.notification = svc.notification;
            }
            if (session && session.uid) {
                this.userId = session.uid;
            }
        } catch (e) {
            console.error("Marketplace: bindWebClientServices failed:", e);
        }
    }

    async initializeServices() {
        this.bindWebClientServices();

        this.setupPageNavigationListener();
        this.setupThemeListener();

        if (document.readyState === 'loading') {
            document.addEventListener('DOMContentLoaded', () => {
                if (this.isMarketplaceDashboard()) {
                    this.initPanel();
                } else {
                    setTimeout(() => {
                        const kanban = document.querySelector('.o_marketplace_kanban');
                        if (kanban) {
                            this.initPanel();
                        }
                    }, 500);
                }
            });
        } else {
            if (this.isMarketplaceDashboard()) {
                setTimeout(() => {
                    this.initPanel();
                }, 100);
            } else {
                setTimeout(() => {
                    const kanban = document.querySelector('.o_marketplace_kanban');
                    if (kanban) {
                        this.initPanel();
                    }
                }, 500);
            }
        }
    }

    setupPageNavigationListener() {
        const setupObserver = () => {
            if (!document.body) {
                setTimeout(setupObserver, 100);
                return;
            }

            let lastUrl = location.href;
            const observer = new MutationObserver(() => {
                const currentUrl = location.href;
                if (currentUrl !== lastUrl) {
                    lastUrl = currentUrl;

                    const hasKanban = document.querySelector('.o_marketplace_kanban') !== null;

                    if (hasKanban && !document.getElementById('marketplace_onboarding_panel')) {
                        setTimeout(() => this.initPanel(), 1000);
                    }
                    else if (!hasKanban) {
                        const panel = document.getElementById('marketplace_onboarding_panel');
                        if (panel) {
                            panel.remove();
                        }
                        const floatingBtn = document.querySelector('.o_mk_floating_expand_btn');
                        if (floatingBtn) {
                            floatingBtn.remove();
                        }
                    }
                }
            });

            observer.observe(document.body, { childList: true, subtree: true });
        };

        setupObserver();
    }

    /**
     * Keep the panel's dark styling in sync with Odoo's theme. Re-applies on OS
     * scheme changes and on theme class/style changes Odoo makes to <html>.
     */
    setupThemeListener() {
        try {
            const handler = () => this.applyPanelTheme();
            const mq = window.matchMedia('(prefers-color-scheme: dark)');
            if (mq.addEventListener) {
                mq.addEventListener('change', handler);
            } else if (mq.addListener) {
                mq.addListener(handler);
            }
            const observer = new MutationObserver(handler);
            observer.observe(document.documentElement, {
                attributes: true,
                attributeFilter: ['class', 'style', 'data-bs-theme', 'data-color-scheme'],
            });
        } catch (e) {
            console.error("Marketplace: setupThemeListener failed:", e);
        }
    }

    /**
     * Toggle the `o_mk_onboarding_dark` class on the panel based on Odoo's theme.
     */
    applyPanelTheme() {
        const panel = document.getElementById('marketplace_onboarding_panel');
        if (panel) {
            panel.classList.toggle('o_mk_onboarding_dark', isOdooDarkMode());
        }
    }

    isMarketplaceDashboard() {
        const currentUrl = window.location.href;

        const hasMarketplaceInUrl = currentUrl.includes('marketplace') ||
                                   currentUrl.includes('mk.instance') ||
                                   currentUrl.includes('mk_');

        const hasMarketplaceKanban = document.querySelector('.o_marketplace_kanban') !== null;

        const hasMarketplaceElements = document.querySelector('[data-model="mk.instance"]') !== null ||
                                       document.querySelector('.o_marketplace_dashboard') !== null;

        const isDomReady = document.readyState !== 'loading';

        let isDashboard;
        if (!isDomReady) {
            isDashboard = hasMarketplaceInUrl;
        } else {
            isDashboard = hasMarketplaceInUrl || hasMarketplaceKanban || hasMarketplaceElements;
        }

        return isDashboard;
    }

    async isMarketplaceManager() {
        // The Setup Wizard creates mk.instance records, and mk.instance perm_create is 0 for
        // base_marketplace.group_base_marketplace. Without this gate a Marketplace User can walk
        // the whole wizard and only fails on the final save. Cached so repeated panel
        // re-initialisations (navigation, MutationObserver, retries) do not re-issue the RPC.
        if (this._isManagerPromise === undefined) {
            this._isManagerPromise = user
                .hasGroup("base_marketplace.group_base_marketplace_manager")
                .catch(() => false);
        }
        return this._isManagerPromise;
    }

    async initPanel() {
        this.bindWebClientServices();

        // Quick Onboarding is a Manager-only surface; bail out before touching the DOM.
        if (!(await this.isMarketplaceManager())) {
            return;
        }

        // Don't show panel on mobile devices
        if (window.innerWidth < 768) {
            return;
        }

        if (!this.isMarketplaceDashboard()) {
            return;
        }

        const kanban = document.querySelector('.o_marketplace_kanban');
        if (!kanban) {
            if (!this.retryCount) {
                this.retryCount = 0;
            }
            if (this.retryCount < 10) {
                this.retryCount++;
                setTimeout(() => this.initPanel(), 1000);
            }
            return;
        }

        // Read persisted preference: true = expanded panel, false = collapsed (floating button)
        const shouldShow = await this.checkUserPreference();

        const existingPanel = document.getElementById('marketplace_onboarding_panel');
        const existingFloatingBtn = document.querySelector('.o_mk_floating_expand_btn');

        // If panel exists but state says it should be collapsed, fix it
        if (existingPanel && !shouldShow) {
            existingPanel.remove();
            this.showFloatingExpandButton();
            return;
        }

        // If floating button exists but state says it should be expanded, fix it
        if (existingFloatingBtn && shouldShow) {
            existingFloatingBtn.remove();
        }

        // If either exists (after state check above), don't reinitialize
        if (existingPanel || existingFloatingBtn) {
            return;
        }

        try {
            const hasMarketplace = await this.hasInstalledMarketplace();
            if (!hasMarketplace) {
                return;
            }

            if (shouldShow) {
                this.createPanel(kanban);
            } else {
                this.showFloatingExpandButton();
            }
        } catch (error) {
            console.error("Marketplace: Error initializing panel:", error);
        }
    }

    async checkUserPreference() {
        const uid = user.userId;
        if (!uid) {
            return true;
        }

        try {
            const result = await rpc("/web/dataset/call_kw", {
                model: "res.users",
                method: "search_read",
                args: [[['id', '=', uid]], ['show_marketplace_quick_onboarding']],
                kwargs: {},
            });

            if (result.length > 0) {
                return result[0].show_marketplace_quick_onboarding;
            }
            return true;
        } catch (error) {
            console.error("Marketplace: Failed to check user preference:", error);
            return true;
        }
    }

    async hasInstalledMarketplace() {
        this.bindWebClientServices();
        if (!this.orm) {
            return true;
        }

        try {
            const result = await this.orm.call('mk.instance', 'get_installed_marketplace_connectors');
            return result && result.length > 0;
        } catch (error) {
            console.error("Marketplace: Failed to check installed marketplaces:", error);
            return true;
        }
    }

    showNotification(message, type = 'info') {
        if (!this.notification) {
            alert(message);
            return;
        }

        try {
            this.notification.add(message, { type });
        } catch (error) {
            console.error("Marketplace: Failed to show notification:", error);
            alert(message);
        }
    }

    createPanel(kanban) {
        // Guard against an already-inserted panel AND a build already in flight.
        // The DOM insert happens in the setTimeout below, so without the in-flight
        // flag a second call within that 100ms window would pass the getElementById
        // check and create a duplicate panel.
        if (document.getElementById('marketplace_onboarding_panel') || this._panelInserting) {
            return;
        }
        this._panelInserting = true;

        const panel = document.createElement('div');
        panel.id = 'marketplace_onboarding_panel';
        panel.className = 'o_marketplace_quick_onboarding_panel';
        // Match Odoo's theme up-front so the panel paints correctly with no flash.
        if (isOdooDarkMode()) {
            panel.classList.add('o_mk_onboarding_dark');
        }

        const closeButton = document.createElement('div');
        closeButton.className = 'o_mk_onboarding_close';
        closeButton.innerHTML = '<i class="fa fa-times"></i>';
        closeButton.onclick = () => {
            this.collapsePanel();
        };

        const content = document.createElement('div');
        content.className = 'o_mk_onboarding_content';

        const leftSide = document.createElement('div');
        leftSide.className = 'o_mk_onboarding_left_side';

        const icon = document.createElement('div');
        icon.className = 'o_mk_onboarding_icon';
        icon.innerHTML = '<i class="fa fa-rocket"></i>';

        const textContent = document.createElement('div');
        textContent.className = 'o_mk_onboarding_text_content';

        const label = document.createElement('div');
        label.className = 'o_mk_onboarding_label';
        label.textContent = 'GETTING STARTED';

        const title = document.createElement('h3');
        title.className = 'o_mk_onboarding_title';
        title.textContent = 'Quick Onboarding';

        const description = document.createElement('p');
        description.className = 'o_mk_onboarding_description';
        description.textContent = 'Easily set up and connect your marketplace account to sync customers, products, inventory, and orders.';

        const support = document.createElement('p');
        support.className = 'o_mk_onboarding_support';
        support.textContent = 'Follow the guided steps to set up your marketplace account.';

        const actions = document.createElement('div');
        actions.className = 'o_mk_onboarding_actions';

        const setupButton = document.createElement('button');
        setupButton.type = 'button';
        setupButton.className = 'btn btn-primary o_mk_onboarding_primary_btn';
        setupButton.innerHTML = '<i class="fa fa-magic"></i> Start Quick Setup';
        setupButton.onclick = async () => {
            this.bindWebClientServices();
            console.log("Marketplace: Start Quick Setup clicked");
            console.log("Marketplace: orm available?", !!this.orm);
            console.log("Marketplace: action available?", !!this.action);

            const existingButton = document.querySelector('button[name="action_open_setup_wizard_header"]');
            if (existingButton) {
                console.log("Marketplace: Found existing button, clicking it");
                existingButton.click();
                return;
            }

            if (!this.orm) {
                console.error("Marketplace: ORM service not available");
                this.showNotification(
                    _t("The application is still loading. Please wait a moment and try again."),
                    "warning",
                );
                return;
            }

            try {
                // Same as header Setup Wizard: picker flow, no instance required (empty recordset).
                console.log("Marketplace: Calling action_open_setup_wizard_header...");
                const actionDescriptor = await this.orm.call(
                    "mk.instance",
                    "action_open_setup_wizard_header",
                    [],
                );
                console.log("Marketplace: actionDescriptor:", actionDescriptor);

                if (!this.action) {
                    console.error("Marketplace: Action service not available");
                } else if (!actionDescriptor) {
                    console.error("Marketplace: actionDescriptor is falsy");
                } else if (typeof actionDescriptor !== "object") {
                    console.error("Marketplace: actionDescriptor is not an object:", typeof actionDescriptor);
                } else if (!actionDescriptor.type) {
                    console.error("Marketplace: actionDescriptor.type is missing:", actionDescriptor);
                } else {
                    console.log("Marketplace: Calling doAction with:", actionDescriptor);
                    await this.action.doAction(actionDescriptor);
                    return;
                }
            } catch (error) {
                console.error("Marketplace: Failed to open setup wizard:", error);
            }

            this.showNotification(
                _t("Could not open the Setup Wizard. Please use the Setup Wizard control in the screen header or contact your administrator."),
                "warning",
            );
        };

        const learnMoreLink = document.createElement('a');
        learnMoreLink.href = 'https://docs.teqstars.com/19.0/';
        learnMoreLink.target = '_blank';
        learnMoreLink.rel = 'noopener noreferrer';
        learnMoreLink.className = 'o_mk_onboarding_secondary_btn o_mk_learn_more';
        learnMoreLink.textContent = 'Documentation';

        actions.appendChild(setupButton);
        actions.appendChild(learnMoreLink);

        textContent.appendChild(label);
        textContent.appendChild(title);
        textContent.appendChild(description);
        textContent.appendChild(support);
        textContent.appendChild(actions);

        leftSide.appendChild(icon);
        leftSide.appendChild(textContent);

        const stepsSection = document.createElement('div');
        stepsSection.className = 'o_mk_onboarding_steps_section';

        const stepsContainer = document.createElement('div');
        stepsContainer.className = 'o_mk_onboarding_steps_container';

        const stepsData = [
            { number: 1, title: 'Connect Store', description: 'Authorize your marketplace account.' },
            { number: 2, title: 'Configure Settings', description: 'Set sync preferences and mappings.' },
            { number: 3, title: 'Import Data', description: 'Import products, customers, and inventory.' },
            { number: 4, title: 'Go Live', description: 'Start syncing orders and updates.' }
        ];

        stepsData.forEach((step, index) => {
            const stepItem = document.createElement('div');
            stepItem.className = 'o_mk_onboarding_step_item';

            const stepNumber = document.createElement('div');
            stepNumber.className = 'o_mk_onboarding_step_number';
            stepNumber.textContent = step.number;

            const stepTitle = document.createElement('div');
            stepTitle.className = 'o_mk_onboarding_step_title';
            stepTitle.textContent = step.title;

            const stepDescription = document.createElement('div');
            stepDescription.className = 'o_mk_onboarding_step_description';
            stepDescription.textContent = step.description;

            stepItem.appendChild(stepNumber);
            stepItem.appendChild(stepTitle);
            stepItem.appendChild(stepDescription);

            stepsContainer.appendChild(stepItem);

            if (index < stepsData.length - 1) {
                const arrow = document.createElement('div');
                arrow.className = 'o_mk_onboarding_step_arrow';
                arrow.innerHTML = '&rarr;';
                stepsContainer.appendChild(arrow);
            }
        });

        stepsSection.appendChild(stepsContainer);

        const rightSide = document.createElement('div');
        rightSide.className = 'o_mk_onboarding_right';

        const visual = document.createElement('div');
        visual.className = 'o_mk_onboarding_visual';

        const circles = document.createElement('div');
        circles.className = 'o_mk_onboarding_circles';

        const createCircle = (title, iconPath, iconSize, width, top, left, isClickable, url) => {
            const circle = document.createElement('div');
            const slug = title.toLowerCase().replace(/\s/g, '_');
            circle.className = `o_mk_circle o_mk_circle_${slug}${isClickable ? ' o_mk_circle_clickable' : ''}`;
            circle.setAttribute('title', title);
            // Position is per-circle data, kept inline. All other styling lives in SCSS.
            circle.style.width = `${width}px`;
            circle.style.height = `${width}px`;
            circle.style.top = `${top}px`;
            circle.style.left = `${left}px`;

            const img = document.createElement('img');
            img.src = iconPath;
            img.alt = title;
            img.style.width = `${iconSize}px`;
            img.style.height = `${iconSize}px`;
            circle.appendChild(img);

            if (isClickable) {
                circle.onclick = () => {
                    window.open(url, '_blank', 'noopener,noreferrer');
                };
            }

            return circle;
        };

        circles.appendChild(createCircle('Shopify', '/base_marketplace/static/description/img/onboarding/shopify-icon.png', 40, 70, 10, 10, true, 'https://apps.odoo.com/apps/modules/19.0/shopify'));
        circles.appendChild(createCircle('bol.com', '/base_marketplace/static/description/img/onboarding/bol-Icon.png', 40, 70, 5, 100, true, 'https://apps.odoo.com/apps/modules/19.0/bol'));
        circles.appendChild(createCircle('WooCommerce', '/base_marketplace/static/description/img/onboarding/woo-icon.png', 60, 70, 100, 15, true, 'https://apps.odoo.com/apps/modules/19.0/woocommerce'));
        circles.appendChild(createCircle('Mirakl', '/base_marketplace/static/description/img/onboarding/mirakl-Icon.png', 50, 70, 55, 85, true, 'https://apps.odoo.com/apps/modules/19.0/mirakl'));
        circles.appendChild(createCircle('eBay', '/base_marketplace/static/description/img/onboarding/ebay-icon.png', 40, 70, 105, 100, true, 'https://apps.odoo.com/apps/modules/19.0/ebay'));
        circles.appendChild(createCircle('Faire', '/base_marketplace/static/description/img/onboarding/faire-icon.png', 40, 70, 10, 185, false, null));
        circles.appendChild(createCircle('BigCommerce', '/base_marketplace/static/description/img/onboarding/big-icon.png', 40, 60, 55, 195, true, 'https://apps.odoo.com/apps/modules/19.0/bigcommerce_ts'));
        circles.appendChild(createCircle('Etsy', '/base_marketplace/static/description/img/onboarding/etsy-icon.png', 40, 70, 105, 165, true, 'https://apps.odoo.com/apps/modules/19.0/etsy_ts'));
        circles.appendChild(createCircle('Prestashop', '/base_marketplace/static/description/img/onboarding/prestashop-icon.png', 40, 70, 55, -20, true, 'https://apps.odoo.com/apps/modules/19.0/prestashop_ts'));

        visual.appendChild(circles);
        rightSide.appendChild(visual);

        content.appendChild(leftSide);
        content.appendChild(stepsSection);
        content.appendChild(rightSide);

        panel.appendChild(closeButton);
        panel.appendChild(content);

        // Find the kanban renderer (inside o_content) to insert panel there
        // so it scrolls together with kanban cards
        // Use a small delay to ensure the renderer is created
        setTimeout(() => {
            // Another panel may have been inserted meanwhile — never add a duplicate.
            if (document.getElementById('marketplace_onboarding_panel')) {
                this._panelInserting = false;
                return;
            }
            const kanbanRenderer = document.querySelector('.o_kanban_renderer');
            if (kanbanRenderer) {
                // Check if kanban has any records
                const hasRecords = kanbanRenderer.querySelector('.o_kanban_record');
                if (hasRecords) {
                    // Insert panel as first child of the renderer so it scrolls with cards
                    kanbanRenderer.insertBefore(panel, kanbanRenderer.firstChild);
                } else {
                    // Empty kanban - insert after the view's content wrapper
                    const viewContent = document.querySelector('.o_content');
                    if (viewContent) {
                        viewContent.insertBefore(panel, viewContent.firstChild);
                    } else {
                        kanbanRenderer.insertBefore(panel, kanbanRenderer.firstChild);
                    }
                }
            } else {
                // Fallback: try to insert in the original location
                const controlPanel = kanban.querySelector('.o_control_panel');
                if (controlPanel && controlPanel.parentNode) {
                    controlPanel.parentNode.insertBefore(panel, controlPanel.nextSibling);
                } else {
                    kanban.insertBefore(panel, kanban.firstChild);
                }
            }
            this._panelInserting = false;
        }, 100);
    }

    savePreference(value) {
        // Persist the onboarding preference via a direct rpc so it does not depend on
        // the (sometimes unavailable) bound orm service. true = expanded, false = collapsed.
        const uid = user.userId;
        if (!uid) {
            console.warn("Marketplace: No user id, cannot save onboarding preference.");
            return;
        }
        rpc("/web/dataset/call_kw", {
            model: "res.users",
            method: "write",
            args: [[uid], { 'show_marketplace_quick_onboarding': value }],
            kwargs: {},
        }).catch((error) => {
            console.error("Marketplace: Failed to save onboarding preference:", error);
        });
    }

    collapsePanel() {
        // Persist collapsed state on the user (false = collapsed) so it survives tabs/sessions.
        this.savePreference(false);

        const panel = document.getElementById('marketplace_onboarding_panel');
        if (panel) {
            panel.style.transition = 'all 0.3s ease';
            panel.style.opacity = '0';
            panel.style.transform = 'translateY(-20px)';

            setTimeout(() => {
                panel.style.display = 'none';
                this.showFloatingExpandButton();
            }, 300);
        }
    }

    showFloatingExpandButton() {
        // Don't show floating button on mobile devices
        if (window.innerWidth < 768) {
            return;
        }

        if (!this.isMarketplaceDashboard()) {
            return;
        }
        if (document.querySelector('.o_mk_floating_expand_btn')) {
            return;
        }

        const floatingBtn = document.createElement('div');
        floatingBtn.className = 'o_mk_floating_expand_btn';

        const icon = document.createElement('i');
        icon.className = 'fa fa-rocket';
        floatingBtn.appendChild(icon);

        const tooltip = document.createElement('div');
        tooltip.className = 'o_mk_floating_tooltip';
        tooltip.textContent = 'Quick Onboarding';
        floatingBtn.appendChild(tooltip);

        floatingBtn.onclick = () => {
            this.expandPanelFromFloating();
        };

        document.body.appendChild(floatingBtn);
    }

    expandPanelFromFloating() {
        if (!this.isMarketplaceDashboard()) {
            const floatingBtn = document.querySelector('.o_mk_floating_expand_btn');
            if (floatingBtn) {
                floatingBtn.remove();
            }
            return;
        }

        // Persist expanded state on the user (true = expanded) so it survives tabs/sessions.
        this.savePreference(true);

        const floatingBtn = document.querySelector('.o_mk_floating_expand_btn');
        if (floatingBtn) {
            floatingBtn.style.opacity = '0';
            floatingBtn.style.transform = 'scale(0)';
            setTimeout(() => {
                floatingBtn.remove();
            }, 300);
        }

        let panel = document.getElementById('marketplace_onboarding_panel');
        if (!panel) {
            const kanban = document.querySelector('.o_marketplace_kanban');
            if (kanban) {
                this.createPanel(kanban);
                panel = document.getElementById('marketplace_onboarding_panel');
            }
        }

        if (panel) {
            panel.style.display = 'block';
            panel.style.opacity = '1';
            panel.style.transform = 'translateY(0)';
        }
    }
}

new MarketplaceOnboardingPanel();
