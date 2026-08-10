/** @odoo-module **/

/**
 * Marketplace Card Gradient Handler
 * Applies marketplace-specific gradients to kanban cards
 * Uses multiple strategies to ensure gradients are applied immediately and maintained
 */

// Store applied gradients to avoid re-processing same cards
const processedCards = new WeakSet();

function getLuminance(bgColor) {
    const match = bgColor.match(/\d+/g);
    if (!match || match.length < 3) return null;
    const r = parseInt(match[0]), g = parseInt(match[1]), b = parseInt(match[2]);
    const a = match.length >= 4 ? parseFloat(match[3]) : 1;
    if (a < 0.05) return null; // transparent — skip
    return (0.299 * r + 0.587 * g + 0.114 * b) / 255;
}

function isDarkMode() {
    // Check multiple candidates: Odoo may set dark bg on html, body, or a wrapper
    const candidates = [
        document.documentElement,
        document.body,
        document.querySelector('.o_web_client'),
        document.querySelector('.o_action_manager'),
        document.querySelector('.o_main_navbar'),
    ];
    for (const el of candidates) {
        if (!el) continue;
        const lum = getLuminance(window.getComputedStyle(el).backgroundColor);
        if (lum !== null) return lum < 0.5;
    }
    return window.matchMedia('(prefers-color-scheme: dark)').matches;
}

function applyDarkModeStyles(card, darkMode) {
    // Toggle class so SCSS rules with higher specificity handle backgrounds
    card.classList.toggle('mk-dark', darkMode);

    const titleEl = card.querySelector('.mk-saas-store-title');
    if (titleEl) titleEl.style.color = darkMode ? '#FFFFFF' : '';

    const statsPanel = card.querySelector('.mk-saas-stats-panel');
    if (statsPanel) statsPanel.style.background = darkMode ? 'transparent' : 'rgba(255, 255, 255, 0.85)';

    card.querySelectorAll('.mk-saas-stat-box').forEach(box => {
        box.style.background = darkMode ? 'rgba(255, 255, 255, 0.09)' : '';
        box.style.border = darkMode ? '1px solid rgba(255, 255, 255, 0.13)' : '';
    });

    card.querySelectorAll('.mk-saas-stat-value').forEach(el => { el.style.color = darkMode ? '#E8ECF4' : ''; });
    card.querySelectorAll('.mk-saas-stat-label').forEach(el => { el.style.color = darkMode ? '#8A9BB5' : ''; });

    // Dark backgrounds for stat icon circles
    const circleColorMap = darkMode ? {
        'mk-saas-stat-customers':  'rgba(107, 110, 242, 0.18)',
        'mk-saas-stat-listings':   'rgba(56, 165, 108, 0.18)',
        'mk-saas-stat-orders':     'rgba(234, 152, 48, 0.18)',
        'mk-saas-stat-invoices':   'rgba(74, 154, 217, 0.18)',
        'mk-saas-stat-shipments':  'rgba(124, 99, 232, 0.18)',
        'mk-saas-stat-queues':     'rgba(75, 169, 184, 0.18)',
        'mk-saas-stat-sales':      'rgba(231, 162, 58, 0.18)',
    } : {};
    Object.entries(circleColorMap).forEach(([cls, bg]) => {
        card.querySelectorAll(`.${cls} .mk-saas-stat-icon-circle`).forEach(el => {
            el.style.background = bg;
        });
    });
    if (!darkMode) {
        card.querySelectorAll('.mk-saas-stat-icon-circle').forEach(el => { el.style.background = ''; });
    }

    // Integration Health box
    card.querySelectorAll('.mk-saas-stat-health').forEach(box => {
        box.style.background = darkMode ? 'rgba(22, 163, 74, 0.15)' : '';
        box.style.border = darkMode ? '1px solid rgba(22, 163, 74, 0.30)' : '';
    });
    card.querySelectorAll('.mk-saas-stat-health.mk-saas-stat-warning').forEach(box => {
        box.style.background = darkMode ? 'rgba(217, 119, 6, 0.15)' : '';
        box.style.border = darkMode ? '1px solid rgba(217, 119, 6, 0.30)' : '';
    });
    card.querySelectorAll('.mk-saas-stat-health.mk-saas-stat-unhealthy').forEach(box => {
        box.style.background = darkMode ? 'rgba(220, 38, 38, 0.15)' : '';
        box.style.border = darkMode ? '1px solid rgba(220, 38, 38, 0.30)' : '';
    });

    // Health icon circles (left icon in health box)
    card.querySelectorAll('.mk-saas-health-icon').forEach(el => {
        el.style.background = darkMode ? 'rgba(22, 163, 74, 0.20)' : '';
        el.style.color = darkMode ? '#4ADE80' : '';
    });
    card.querySelectorAll('.mk-saas-stat-warning .mk-saas-health-icon').forEach(el => {
        el.style.background = darkMode ? 'rgba(217, 119, 6, 0.20)' : '';
        el.style.color = darkMode ? '#FB923C' : '';
    });
    card.querySelectorAll('.mk-saas-stat-unhealthy .mk-saas-health-icon').forEach(el => {
        el.style.background = darkMode ? 'rgba(220, 38, 38, 0.20)' : '';
        el.style.color = darkMode ? '#F87171' : '';
    });

    // Health label and status text
    card.querySelectorAll('.mk-saas-health-label').forEach(el => { el.style.color = darkMode ? '#8A9BB5' : ''; });
    card.querySelectorAll('.mk-saas-health-status-text').forEach(el => { el.style.color = darkMode ? '#4ADE80' : ''; });
    card.querySelectorAll('.mk-saas-health-status-text.mk-saas-health-warning').forEach(el => { el.style.color = darkMode ? '#FB923C' : ''; });
    card.querySelectorAll('.mk-saas-health-status-text.mk-saas-health-unhealthy').forEach(el => { el.style.color = darkMode ? '#F87171' : ''; });

    // Connected / Test / Failed badges
    card.querySelectorAll('.mk-saas-badge-connected').forEach(el => {
        el.style.background = darkMode ? 'rgba(22, 163, 74, 0.18)' : '';
        el.style.borderColor = darkMode ? 'rgba(22, 163, 74, 0.30)' : '';
        el.style.color = darkMode ? '#4ADE80' : '';
    });
    card.querySelectorAll('.mk-saas-badge-test').forEach(el => {
        el.style.background = darkMode ? 'rgba(245, 158, 11, 0.18)' : '';
        el.style.borderColor = darkMode ? 'rgba(245, 158, 11, 0.30)' : '';
    });
    card.querySelectorAll('.mk-saas-badge-marketplace').forEach(el => {
        el.style.background = darkMode ? 'rgba(255, 255, 255, 0.08)' : '';
        el.style.borderColor = darkMode ? 'rgba(255, 255, 255, 0.12)' : '';
        el.style.color = darkMode ? '#8A9BB5' : '';
    });
    card.querySelectorAll('.mk-saas-badge-failed-queues').forEach(el => {
        el.style.background = darkMode ? 'rgba(220, 38, 38, 0.18)' : '';
        el.style.borderColor = darkMode ? 'rgba(220, 38, 38, 0.30)' : '';
    });

    card.querySelectorAll('.mk-saas-btn-secondary').forEach(btn => {
        btn.style.background = darkMode ? 'rgba(255, 255, 255, 0.07)' : '';
        btn.style.borderColor = darkMode ? 'rgba(255, 255, 255, 0.12)' : '';
        btn.style.color = darkMode ? '#C8D0E0' : '';
    });

    // Graph section title, sample data label and view-logs link
    card.querySelectorAll('.mk-saas-activity-title').forEach(el => {
        el.style.color = darkMode ? '#E8ECF4' : '';
    });
    card.querySelectorAll('.mk-saas-view-logs').forEach(el => {
        el.style.color = darkMode ? '#A09CF7' : '';
    });
    card.querySelectorAll('.o_sample_data_label').forEach(el => {
        el.style.color = darkMode ? '#8A9BB5' : '';
    });

    // Footer sync text
    card.querySelectorAll('.mk-saas-sync-label').forEach(el => { el.style.color = darkMode ? '#8A9BB5' : ''; });
    card.querySelectorAll('.mk-saas-sync-time').forEach(el => { el.style.color = darkMode ? '#20A35A' : ''; });
}

/**
 * Apply gradient to a single card
 */
function applyGradientToCard(card) {
    const darkMode = isDarkMode();

    // Always re-apply dark mode styles
    applyDarkModeStyles(card, darkMode);

    // Skip gradient re-apply if already processed and gradient is intact
    if (processedCards.has(card)) {
        const hasGradient = card.style.background && card.style.background.includes('gradient');
        if (hasGradient) return;
    }

    let hex = card.getAttribute('data-mk-color');
    if (!hex || hex === 'None') return;

    // Fix malformed hex colors (e.g., ##7F54B3 -> #7F54B3)
    hex = hex.replace(/^#+/, '#');

    // Parse hex to RGB
    const r = parseInt(hex.slice(1, 3), 16);
    const g = parseInt(hex.slice(3, 5), 16);
    const b = parseInt(hex.slice(5, 7), 16);

    // Apply gradient: dark at top-left, dark at bottom-right, light in middle
    card.style.background = `linear-gradient(135deg,
        rgba(${r}, ${g}, ${b}, 0.18) 0%,
        rgba(${r}, ${g}, ${b}, 0.03) 50%,
        rgba(${r}, ${g}, ${b}, 0.13) 100%)`;
    card.style.setProperty('--mk-color', hex);

    // Mark as processed
    processedCards.add(card);
}

/**
 * Apply dark mode styles to the onboarding panel banner
 */
function applyOnboardingPanelDarkMode() {
    const panel = document.getElementById('marketplace_onboarding_panel');
    if (!panel) return;
    const darkMode = isDarkMode();
    const textMap = darkMode ? {
        '.o_mk_onboarding_title':            '#E8ECF4',
        '.o_mk_onboarding_description':      '#A0ACBE',
        '.o_mk_onboarding_support':          '#8A9BB5',
        '.o_mk_onboarding_label':            '#A09CF7',
        '.o_mk_onboarding_step_title':       '#C8D0E0',
        '.o_mk_onboarding_step_description': '#8A9BB5',
        '.o_mk_onboarding_step_arrow':       '#5A6480',
    } : {};

    if (darkMode) {
        panel.style.background = 'linear-gradient(270deg, #1a1f35, #1e2240, #1a1f35)';
        panel.style.backgroundSize = '400% 400%';
        panel.style.boxShadow = '0 4px 20px rgba(0, 0, 0, 0.4)';
        Object.entries(textMap).forEach(([sel, color]) => {
            panel.querySelectorAll(sel).forEach(el => { el.style.color = color; });
        });
        panel.querySelectorAll('.o_mk_circle').forEach(circle => {
            circle.style.background = 'rgba(28, 35, 60, 0.80)';
            circle.style.borderColor = 'rgba(255, 255, 255, 0.15)';
        });
        ['.o_mk_onboarding_steps_section', '.o_mk_onboarding_steps_container', '.o_mk_onboarding_step_item'].forEach(sel => {
            panel.querySelectorAll(sel).forEach(el => {
                el.style.outline = 'none';
                el.style.boxShadow = 'none';
                el.style.border = 'none';
                el.style.background = 'transparent';
            });
        });
        panel.querySelectorAll('.o_mk_onboarding_step_number').forEach(el => {
            el.style.background = 'rgba(255, 255, 255, 0.10)';
            el.style.border = '2px solid rgba(140, 140, 220, 0.40)';
            el.style.color = '#C8D0F0';
            el.style.outline = 'none';
            el.style.boxShadow = 'none';
        });
        panel.querySelectorAll('.o_mk_onboarding_primary_btn').forEach(btn => {
            btn.style.background = 'linear-gradient(135deg, #3d3a80 0%, #2a2860 100%)';
            btn.style.color = '#ffffff';
            btn.style.border = 'none';
            btn.style.outline = 'none';
            btn.style.boxShadow = '0 2px 8px rgba(75, 63, 122, 0.5)';
        });
        panel.querySelectorAll('.o_mk_onboarding_secondary_btn').forEach(btn => {
            btn.style.color = '#A09CF7';
        });
        panel.querySelectorAll('.o_mk_onboarding_close').forEach(btn => {
            btn.style.backgroundColor = 'rgba(255, 255, 255, 0.08)';
            btn.style.borderColor = 'rgba(255, 255, 255, 0.15)';
        });
        panel.querySelectorAll('.o_mk_onboarding_close i').forEach(i => {
            i.style.color = '#C8D0E0';
        });
    } else {
        panel.style.background = '';
        panel.style.backgroundSize = '';
        panel.style.boxShadow = '';
        ['.o_mk_onboarding_title', '.o_mk_onboarding_description', '.o_mk_onboarding_support',
         '.o_mk_onboarding_label', '.o_mk_onboarding_step_title', '.o_mk_onboarding_step_description',
         '.o_mk_onboarding_step_arrow'].forEach(sel => {
            panel.querySelectorAll(sel).forEach(el => { el.style.color = ''; });
        });
        panel.querySelectorAll('.o_mk_circle').forEach(circle => {
            circle.style.background = '';
            circle.style.borderColor = '';
        });
        ['.o_mk_onboarding_steps_section', '.o_mk_onboarding_steps_container', '.o_mk_onboarding_step_item'].forEach(sel => {
            panel.querySelectorAll(sel).forEach(el => {
                el.style.outline = '';
                el.style.boxShadow = '';
                el.style.border = '';
                el.style.background = '';
            });
        });
        panel.querySelectorAll('.o_mk_onboarding_step_number').forEach(el => {
            el.style.background = '';
            el.style.border = '';
            el.style.color = '';
            el.style.outline = '';
            el.style.boxShadow = '';
        });
        panel.querySelectorAll('.o_mk_onboarding_primary_btn').forEach(btn => {
            btn.style.background = '';
            btn.style.color = '';
            btn.style.border = '';
            btn.style.outline = '';
            btn.style.boxShadow = '';
        });
        panel.querySelectorAll('.o_mk_onboarding_secondary_btn').forEach(btn => {
            btn.style.color = '';
        });
        panel.querySelectorAll('.o_mk_onboarding_close').forEach(btn => {
            btn.style.backgroundColor = '';
            btn.style.borderColor = '';
        });
        panel.querySelectorAll('.o_mk_onboarding_close i').forEach(i => {
            i.style.color = '';
        });
    }
}

/**
 * Apply gradients to all cards in the marketplace kanban
 */
function applyCardGradients() {
    const cards = document.querySelectorAll('.mk-saas-card[data-mk-color]');
    cards.forEach(applyGradientToCard);
    applyOnboardingPanelDarkMode();
}

/**
 * Throttled version for MutationObserver to avoid excessive calls
 */
let applyScheduled = false;
function scheduleApply() {
    if (!applyScheduled) {
        applyScheduled = true;
        requestAnimationFrame(() => {
            applyCardGradients();
            applyScheduled = false;
        });
    }
}

// ============================================================
// INITIALIZATION - Multiple strategies for reliable application
// ============================================================

// Strategy 1: Immediate application if DOM is ready
function initGradients() {
    // Apply immediately
    applyCardGradients();
    // Apply again after a short delay to catch any late-rendered cards
    setTimeout(applyCardGradients, 100);
    setTimeout(applyCardGradients, 500);
}

if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initGradients);
} else {
    initGradients();
}

// Strategy 2: MutationObserver for dynamic changes
const observer = new MutationObserver((mutations) => {
    // Only react if marketplace cards might be affected
    let hasRelevantChange = false;
    for (const mutation of mutations) {
        if (mutation.addedNodes.length > 0) {
            for (const node of mutation.addedNodes) {
                if (node.nodeType === 1) { // Element node
                    if (node.classList?.contains('mk-saas-card') ||
                        node.classList?.contains('o_marketplace_kanban') ||
                        node.querySelector?.('.mk-saas-card') ||
                        node.id === 'marketplace_onboarding_panel' ||
                        node.classList?.contains('o_marketplace_quick_onboarding_panel') ||
                        node.querySelector?.('#marketplace_onboarding_panel, .o_marketplace_quick_onboarding_panel')) {
                        hasRelevantChange = true;
                        break;
                    }
                }
            }
        }
        if (hasRelevantChange) break;
    }
    if (hasRelevantChange) {
        scheduleApply();
    }
});

// Start observing when body is available
function startObserver() {
    if (document.body) {
        observer.observe(document.body, { childList: true, subtree: true });
    }
}

if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', startObserver);
} else {
    startObserver();
}

// Strategy 3: Export function for manual triggering
export function setupMarketplaceCardGradients() {
    applyCardGradients();
}
