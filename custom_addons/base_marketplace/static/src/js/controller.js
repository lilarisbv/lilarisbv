/** @odoo-module */

import {browser} from "@web/core/browser/browser";
import {useService} from "@web/core/utils/hooks";
import {user} from "@web/core/user";

export class MarketplaceController {
    constructor(action) {
        this.action = action;
        this.actionService = useService("action");
        this.dialog = useService("dialog");
        this.orm = useService("orm");
    }

    async load(env) {
        this.env = env;
        this.loadingCallNumberByCacheKey = new Proxy(
            {},
            {
                get(target, name) {
                    return name in target ? target[name] : 0;
                },
                set(target, name, newValue) {
                    target[name] = newValue;
                    return true;
                },
            }
        );
        this.reportOptionsMap = {};
        this.dashboardOptionsMap = {};
        this.dashboardInformationMap = {};
        this.dashboardReportId = 'mk_instance_dashboard'
        const mainReportOptions = await this.loadReportOptions(this.dashboardReportId, false, this.action.params?.ignore_session);
        const cacheKey = this.getCacheKey(mainReportOptions['sections_source_id'], mainReportOptions['report_id']);
        this.options = mainReportOptions;
        this.incrementCallNumber(cacheKey);
        this.options["loading_call_number"] = this.loadingCallNumberByCacheKey[cacheKey];
        this.reportOptionsMap[cacheKey] = mainReportOptions;
        this.saveSessionOptions(mainReportOptions);
        const activeSectionPromise = this.displayReport(mainReportOptions['report_id']);
        await activeSectionPromise;
        return this.options
    }

    getCacheKey(sectionsSourceId, reportId) {
        return `${sectionsSourceId}_${reportId}`
    }

    async displayReport(reportId) {
        const cacheKey = await this.loadReport();
        this.options = await this.dashboardOptionsMap[cacheKey];
        this.data = await this.dashboardInformationMap[cacheKey];
        this.saveSessionOptions(this.options);
    }

    async reload(optionPath, newOptions) {
        const rootOptionKey = optionPath.split('.')[0]
        for (const [cacheKey, cachedOptionsPromise] of Object.entries(this.dashboardOptionsMap)) {
            let cachedOptions = await cachedOptionsPromise;
            if (cachedOptions.hasOwnProperty(rootOptionKey)) {
                delete this.dashboardOptionsMap[cacheKey];
                delete this.dashboardInformationMap[cacheKey];
            }
        }
        this.saveSessionOptions(newOptions);
        await this.displayReport('mk_instance_dashboard');
    }

    incrementCallNumber(cacheKey = null) {
        if (!cacheKey) {
            cacheKey = this.getCacheKey(this.options['sections_source_id'], this.options['report_id']);
        }
        this.loadingCallNumberByCacheKey[cacheKey] += 1;
    }

    async _updateOption(operationType, optionPath, optionValue = null, reloadUI = false) {
        const optionKeys = optionPath.split(".");

        let currentOptionKey = null;
        let option = this.options;

        while (optionKeys.length > 1) {
            currentOptionKey = optionKeys.shift();
            option = option[currentOptionKey];

            if (option === undefined)
                throw new Error(`Invalid option key in _updateOption(): ${currentOptionKey} (${optionPath})`);
        }

        switch (operationType) {
            case "update":
                option[optionKeys[0]] = optionValue;
                break;
            case "delete":
                delete option[optionKeys[0]];
                break;
            case "toggle":
                option[optionKeys[0]] = !option[optionKeys[0]];
                break;
            default:
                throw new Error(`Invalid operation type in _updateOption(): ${operationType}`);
        }

        if (reloadUI) {
            this.incrementCallNumber();
            await this.reload(optionPath, this.options);
        }
    }

    async updateOption(optionPath, optionValue) {
        await this._updateOption('update', optionPath, optionValue);
    }

        async toggleOption(optionPath, reloadUI=false) {
        await this._updateOption('toggle', optionPath, null, reloadUI);
    }

    sessionOptionsID() {
        return `mk.instance:${this.dashboardReportId}:${user.defaultCompany.id}`;
    }

    hasSessionOptions() {
        return Boolean(browser.sessionStorage.getItem(this.sessionOptionsID()))
    }

    saveSessionOptions(options) {
        browser.sessionStorage.setItem(this.sessionOptionsID(), JSON.stringify(options));
    }

    sessionOptions() {
        return JSON.parse(browser.sessionStorage.getItem(this.sessionOptionsID()));
    }

    async loadReport() {
        const busEventPayload = {data: {id: 'mk_instance_dashboard', params: {}}, settings: {}};
        this.env.bus.trigger("RPC:REQUEST", busEventPayload);

        const options = await this.loadReportOptions('mk_instance_dashboard'); // This also sets the promise in the cache
        const reportToDisplayId = options['report_id'];

        const cacheKey = this.getCacheKey(options['sections_source_id'], reportToDisplayId)
        if (!this.dashboardInformationMap[cacheKey]) {
            this.dashboardInformationMap[cacheKey] = {}
        }
        await this.dashboardInformationMap[cacheKey];

        return cacheKey;
    }

    async loadReportOptions(reportId, preloading=false, ignore_session=false) {
        const loadOptions = (ignore_session || !this.hasSessionOptions()) ? (this.action.params?.options || {}) : this.sessionOptions();
        const cacheKey = this.getCacheKey(loadOptions['sections_source_id'] || reportId, reportId);

        if (!(cacheKey in this.loadingCallNumberByCacheKey)) {
            this.incrementCallNumber(cacheKey);
        }
        loadOptions["loading_call_number"] = this.loadingCallNumberByCacheKey[cacheKey];

        if (!this.dashboardOptionsMap[cacheKey]) {
            loadOptions['selected_section_id'] = reportId;

            this.dashboardOptionsMap[cacheKey] = this.orm.silent.call(
                "mk.instance",
                "get_options",
                [reportId, loadOptions],
                {context: this.action.context},
            );
            let reportOptions = await this.dashboardOptionsMap[cacheKey];

            const loadedOptionsCacheKey = this.getCacheKey(reportOptions['sections_source_id'], reportOptions['report_id']);
            if (loadedOptionsCacheKey !== cacheKey) {
                /* We delete the rerouting report from the cache, to avoid redoing this reroute when reloading the cached options, as it would mean
                route reports can never be opened directly if they open some variant by default.*/
                delete this.reportOptionsMap[cacheKey];
                this.reportOptionsMap[loadedOptionsCacheKey] = reportOptions;

                this.loadingCallNumberByCacheKey[loadedOptionsCacheKey] = 1;
                delete this.loadingCallNumberByCacheKey[cacheKey];
                return reportOptions;
            }
        }

        return this.dashboardOptionsMap[cacheKey];
    }
}
