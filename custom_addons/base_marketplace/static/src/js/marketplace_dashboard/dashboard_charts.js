/** @odoo-module **/

import {Component, onWillStart, useEffect, useRef} from "@odoo/owl";
import {formatCurrency} from "@web/core/currency";
import {loadJS} from "@web/core/assets";

export class MarketplaceDashboardCharts extends Component {
    setup() {
        this.data = this.props.dashboardData.dashboards;
        this.total_selling_canvasRef = useRef('total_selling_chart');
        this.top_countries_pie_chart_canvasRef = useRef('top_countries_pie_chart');
        this.bar_chart_canvasRef = useRef('bar_chart');
        this.mk_revenue_pie_chart_canvasRef = useRef('mk_revenue_pieChart');
        this.category_pie_chart_canvasRef = useRef('category_pie_chart');
        this.charts = {}; // Store each chart instance separately
        onWillStart(() => loadJS("/base_marketplace/static/src/js/chart_lib/apexcharts.min.js"));
        useEffect(() => this.renderChart());
    }

    getFormattedPrice(amount) {
        return formatCurrency(amount, this.props.dashboardData.dashboards.currency_id);
    }

    renderLineChart(graph_data, canvasRef) {
        self = this;

        // Check if graph_data and required properties are defined
        if (!graph_data || !graph_data.series || !graph_data.categories) {
            console.error("graph_data or required properties are undefined.");
            return;
        }

        var series = graph_data.series;
        var categories = graph_data.categories;

        var options = {
            series: series,
            chart: {
                height: 350,
                type: 'line',
            },
            grid: {
                show: false,
            },
            stroke: {
                width: 7,
                curve: 'smooth'
            },
            yaxis: {
                title: {
                    text: 'Amount',
                },
                labels: {
                    formatter: function (value) {
                        return self.getFormattedPrice(value);
                    }
                },
            },
            xaxis: {
                type: 'datetime',
                categories: categories,
            },
            title: {
                text: 'Total Selling',
                align: 'left',
                style: {
                    fontSize: "16px",
                    color: '#666'
                }
            },
            markers: {
                colors: ["#FFA41B"],
                strokeColors: "#fff",
                strokeWidth: 2,
                hover: {
                    size: 7,
                }
            }
        };
        // Destroy the chart if it already exists to fully re-render it
        if (this.charts.lineChart) {
            this.charts.lineChart.destroy();
        }

        // Create and render a new chart instance with updated data
        this.charts.lineChart = new ApexCharts(canvasRef, options);
        this.charts.lineChart.render();
    }

    renderPieChart(graph_data, canvasRef, chartName) {
        self = this;
        var series = graph_data.series;
        var labels = graph_data.labels;
        var options = {
            series: series,
            chart: {
                width: 430,
                type: 'pie',
            },
            labels: labels,
            legend: {
                position: 'bottom',
                horizontalAlign: 'center',
            },
            yaxis: {
                labels: {
                    formatter: function (value) {
                        return self.getFormattedPrice(value);
                    }
                },
            },
        };
        if (this.charts[chartName]) {
            this.charts[chartName].destroy();
        }

        this.charts[chartName] = new ApexCharts(canvasRef, options);
        this.charts[chartName].render();
    }

    renderBarChart(graph_data, canvasRef) {
        self = this
        var series = graph_data.series;
        var categories = graph_data.categories;
        var options = {
            title: {
                text: 'Instance wise Selling',
                align: 'left',
                style: {
                    fontSize: "16px",
                    color: '#666'
                }
            },
            chart: {
                height: 350,
                type: 'bar',
            },
            grid: {
                show: false,
            },
            series: series,
            xaxis: {
                categories: categories,
                title: {
                    text: 'Amount',
                },
                labels: {
                    formatter: function (value) {
                        return self.getFormattedPrice(value);
                    }
                },
            },
            tooltip: {
                x: {
                    show: false,
                },
            },
            legend: {
                show: false
            },
            plotOptions: {bar: {horizontal: true,}},
            dataLabels: {
                enabled: true,
                textAnchor: 'start',
                style: {
                    colors: ['#fff']
                },
                formatter: function (value, opts) {
                    return self.getFormattedPrice(value);
                },
                offsetX: 0,
                dropShadow: {
                    enabled: true
                }
            },
        };
        if (this.charts.barChart){
            this.charts.barChart.destroy();
        }
        this.charts.barChart = new ApexCharts(canvasRef, options);
        this.charts.barChart.render();
    }

    renderChart() {
        this.data = this.props.dashboardData.dashboards;
        if (this.data.sale_graph.categories && this.data.sale_graph.series) {
            this.renderLineChart(this.data.sale_graph, this.total_selling_canvasRef.el);
        }
        if (this.data.country_graph && this.data.country_graph.labels && this.data.country_graph.series) {
            this.renderPieChart(this.data.country_graph, this.top_countries_pie_chart_canvasRef.el, 'countryPieChart');
        }
        if (this.data.bar_graph && this.data.bar_graph.categories && this.data.bar_graph.series && this.data.is_general_dashboard) {
            this.renderBarChart(this.data.bar_graph, this.bar_chart_canvasRef.el);
        }
        if (this.data.mk_revenue_pieChart && this.data.mk_revenue_pieChart.labels && this.data.mk_revenue_pieChart.series && this.data.is_general_dashboard) {
            this.renderPieChart(this.data.mk_revenue_pieChart, this.mk_revenue_pie_chart_canvasRef.el, 'revenuePieChart');
        }
        if (this.data.category_graph && this.data.category_graph.labels && this.data.category_graph.series) {
            this.renderPieChart(this.data.category_graph, this.category_pie_chart_canvasRef.el, 'categoryPieChart');
        }
    }
}

MarketplaceDashboardCharts.template = 'base_marketplace.MarketplaceDashboardCharts';
