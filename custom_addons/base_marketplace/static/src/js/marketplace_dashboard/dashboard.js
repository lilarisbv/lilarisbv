/** @odoo-module **/
import {registry} from "@web/core/registry";
import {Layout} from "@web/search/layout";
import {Component, useSubEnv, useState, onWillStart} from "@odoo/owl";
import {KeepLast} from "@web/core/utils/concurrency";
import {MarketplaceDashboardCharts} from "@base_marketplace/js/marketplace_dashboard/dashboard_charts";
import {MarketplaceController} from "@base_marketplace/js/controller";
import {Dropdown} from "@web/core/dropdown/dropdown";
import {DropdownItem} from "@web/core/dropdown/dropdown_item";
import {DateTimeInput} from '@web/core/datetime/datetime_input';
import {WarningDialog} from "@web/core/errors/error_dialogs";
import {_t} from "@web/core/l10n/translation";
import {rpc} from "@web/core/network/rpc";
import {formatDate} from "@web/core/l10n/dates";

const {DateTime} = luxon;

class MarketplaceDashboard extends Component {

    static defaultComponentsMap = [];

    static customizableComponents = [];


    setup() {
        super.setup();
        this.mk_instance_id = this.props.action.context.active_id || (this.router && this.router.current.hash.active_id) || undefined;
        this.keepLast = new KeepLast();
        this.controller = useState(new MarketplaceController(this.props.action));
        this.state = useState({
            dashboards: {},
        });

        for (const customizableComponent of MarketplaceDashboard.customizableComponents)
            MarketplaceDashboard.defaultComponentsMap[customizableComponent.name] = customizableComponent;

        onWillStart(async () => {
            this.dateOptions = await this.controller.load(this.env);
            if (this.env.controller.options.date) {
                this.dateFilter = this.initDateFilters();
            }
            this.dashboardData = await this.fetchData(this.dateOptions);
        });
        useSubEnv({
            controller: this.controller,
            component: this.getComponent.bind(this),
            template: this.getTemplate.bind(this),
        });
    }

    getComponent(name) {
        return MarketplaceDashboard.defaultComponentsMap[name];
    }

    getTemplate(name) {
        return `base_marketplace.BaseMarketplaceFiltersCustomizable`;
    }

    dateFrom(optionKey) {
        return DateTime.fromISO(this.controller.options[optionKey].date_from);
    }

    dateTo(optionKey) {
        return DateTime.fromISO(this.controller.options[optionKey].date_to);
    }

    setDate(optionKey, type, date) {
        if (date) {
            this.controller.options[optionKey][`date_${type}`] = date;
            this.applyFilters(optionKey);
        }
        else {
            this.dialog.add(WarningDialog, {
                title: _t("Odoo Warning"),
                message: _t("Date cannot be empty"),
            });
        }
    }

    setDateFrom(optionKey, dateFrom) {
        this.setDate(optionKey, 'from', dateFrom);
    }

    setDateTo(optionKey, dateTo) {
        this.setDate(optionKey, 'to', dateTo);
    }

    isPeriodSelected(periodType) {
        return this.controller.options.date.filter.includes(periodType)
    }

    selectDateFilter(periodType, reload = false) {
        this.filterClicked({optionKey: "date.filter", optionValue: this.getDateFilter(periodType)});
        this.filterClicked({optionKey: "date.period", optionValue: this.dateFilter[periodType], reload: reload});
    }

    async filterClicked({optionKey, optionValue = undefined, reload = false}) {
        if (optionValue !== undefined) {
            await this.controller.updateOption(optionKey, optionValue);
        } else {
            await this.controller.toggleOption(optionKey);
        }

        if (reload) {
            await this.applyFilters(optionKey);
        }
    }

    async applyFilters(optionKey = null, delay = 500) {
        // We only call the reload after the delay is finished, to avoid doing 5 calls if you want to click on 5 journals
        if (this.timeout) {
            clearTimeout(this.timeout);
        }

        this.controller.incrementCallNumber();

        this.timeout = setTimeout(async () => {
        // Reload controller options and fetch updated data
        await this.controller.reload(optionKey, this.controller.options);
        this.dateOptions = await this.controller.load(this.env);
        if (this.env.controller.options.date) {
            this.dateFilter = this.initDateFilters();
        }
        this.dashboardData = await this.fetchData(this.dateOptions);
        // Force a re-render of the component
        this.render(true);
    }, delay);

    }

    selectNextPeriod(periodType) {
        this._changePeriod(periodType, 1);
    }

    selectPreviousPeriod(periodType) {
        this._changePeriod(periodType, -1);
    }

    _changePeriod(periodType, increment) {
        this.dateFilter[periodType] = this.dateFilter[periodType] + increment;

        this.controller.updateOption("date.filter", this.getDateFilter(periodType));
        this.controller.updateOption("date.period", this.dateFilter[periodType]);

        this.applyFilters("date.period");
    }

    getDateFilter(periodType) {
        if (this.dateFilter[periodType] > 0) {
            return `next_${periodType}`;
        } else if (this.dateFilter[periodType] === 0) {
            return `this_${periodType}`;
        } else {
            return `previous_${periodType}`;
        }
    }

    displayPeriod(periodType) {
        const dateTo = DateTime.now();

        switch (periodType) {
            case "month":
                return this._displayMonth(dateTo);
            case "quarter":
                return this._displayQuarter(dateTo);
            case "year":
                return this._displayYear(dateTo);
            case "tax_period":
                return this._displayTaxPeriod(dateTo);
            default:
                throw new Error(`Invalid period type in displayPeriod(): ${ periodType }`);
        }
    }

    _displayMonth(dateTo) {
        return dateTo.plus({ months: this.dateFilter.month }).toFormat("MMMM yyyy");
    }

    _displayQuarter(dateTo) {
        const quarterMonths = {
            1: {'start': 1, 'end': 3},
            2: {'start': 4, 'end': 6},
            3: {'start': 7, 'end': 9},
            4: {'start': 10, 'end': 12},
        }
        dateTo = dateTo.plus({months: this.dateFilter.quarter * 3});

        const quarterDateFrom = DateTime.utc(dateTo.year, quarterMonths[dateTo.quarter]['start'], 1)
        const quarterDateTo = DateTime.utc(dateTo.year, quarterMonths[dateTo.quarter]['end'], 1)

        return `${formatDate(quarterDateFrom, {format: "MMM"})} - ${formatDate(quarterDateTo, {format: "MMM yyyy"})}`;
    }

    _displayYear(dateTo) {
        return dateTo.plus({ years: this.dateFilter.year }).toFormat("yyyy");
    }

    _displayTaxPeriod(dateTo) {
        const periodicitySettings = this.controller.options.tax_periodicity;
        const targetDateInPeriod = dateTo.plus({months: periodicitySettings.months_per_period * this.dateFilter['tax_period']})
        const [start, end] = this._computeTaxPeriodDates(periodicitySettings, targetDateInPeriod);

        if (periodicitySettings.start_month == 1 && periodicitySettings.start_day == 1) {
            switch (periodicitySettings.months_per_period) {
                case 1: return end.toFormat("MMMM yyyy");
                case 3: return `Q${end.quarter} ${dateTo.year}`;
                case 12: return end.toFormat("yyyy");
            }
        }

        return formatDate(start) + ' - ' + formatDate(end);
    }

    dateFilters(mode) {
        switch (mode) {
            case "single":
                return [
                    {"name": _t("End of Month"), "period": "month"},
                    {"name": _t("End of Quarter"), "period": "quarter"},
                    {"name": _t("End of Year"), "period": "year"},
                ];
            case "range":
                return [
                    {"name": _t("Month"), "period": "month"},
                    {"name": _t("Quarter"), "period": "quarter"},
                    {"name": _t("Year"), "period": "year"},
                ];
            default:
                throw new Error(`Invalid mode in dateFilters(): ${mode}`);
        }
    }

    initDateFilters() {
        const filters = {
            "month": 0,
            "quarter": 0,
            "year": 0,
            "tax_period": 0
        };

        const specifier = this.controller.options.date.filter.split('_')[0];
        const periodType = this.controller.options.date.period_type;
        // In case the period is fiscalyear it will be computed exactly like a year period.
        const period = periodType === "fiscalyear" ? "year" : periodType;
        // Set the filter value based on the specifier
        filters[period] = this.controller.options.date.period || (specifier === 'previous' ? -1 : specifier === 'next' ? 1 : 0);

        return filters;
    }

    async fetchData(dateFilter) {
        const dashboardData = await this.keepLast.add(
            rpc("/base_marketplace/get_dashboard_data", {
                mk_instance_id: this.mk_instance_id,
                date_from: dateFilter.date.date_from,
                date_to: dateFilter.date.date_to,
                date_filter: dateFilter.date.filter,
            })
        );
        Object.assign(this.state, dashboardData);
        return dashboardData
    }
}

MarketplaceDashboard.template = "base_marketplace.MarketplaceDashboardMain";
MarketplaceDashboard.components = {
    Layout,
    Dropdown,
    DropdownItem,
    DateTimeInput,
    MarketplaceDashboardCharts,
};

registry.category("actions").add("backend_mk_general_dashboard", MarketplaceDashboard);