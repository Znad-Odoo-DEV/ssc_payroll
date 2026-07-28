/** @odoo-module **/

import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { loadJS } from "@web/core/assets";
import {
    Component, onWillStart, onWillUnmount, useEffect, useRef, useState,
} from "@odoo/owl";

// Professional, high-contrast palette. Readable in both light and dark, and
// distinct enough for categorical bars sitting next to each other.
const PALETTE = [
    "#714B67", "#017E84", "#F06050", "#6EC1A7", "#F4A261", "#5B8FF9",
    "#B37FEB", "#E8684A", "#5AD8A6", "#945FB9", "#FF9F40", "#4BC0C0",
];

// Every chart on the page, with the ref it draws into and how it renders.
// Keeping this declarative avoids eight near-identical blocks of setup code.
const CHARTS = [
    { key: "salary_by_designation", ref: "salaryByDesignation", type: "bar", horizontal: true, tall: true, money: true },
    { key: "salary_by_category", ref: "salaryByCategory", type: "doughnut", money: true },
    { key: "salary_by_project", ref: "salaryByProject", type: "bar", money: true },
    { key: "overtime_by_project", ref: "overtimeByProject", type: "bar", money: true },
    { key: "overtime_by_employee", ref: "overtimeByEmployee", type: "bar", horizontal: true, tall: true, money: true },
    { key: "employees_by_project", ref: "employeesByProject", type: "bar", money: false },
    { key: "top_salary_employees", ref: "topSalaryEmployees", type: "bar", horizontal: true, money: true },
    { key: "top_projects_cost", ref: "topProjectsCost", type: "bar", horizontal: true, money: true },
];

export class SscPayrollDashboard extends Component {
    static template = "ssc_payroll.Dashboard";
    static props = ["*"];

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.state = useState({
            loading: true,
            data: null,
            month: false,
            year: false,
        });
        this.charts = {};
        this.refs = {};
        for (const cfg of CHARTS) {
            this.refs[cfg.ref] = useRef(cfg.ref);
        }
        // Scroll target for the "Total Projects" KPI.
        this.projectCardRef = useRef("projectCard");

        onWillStart(async () => {
            await loadJS("/web/static/lib/Chart/Chart.js");
            await this.loadData();
        });

        // Redraw whenever a fresh dataset arrives (initial load or filter change).
        useEffect(
            () => {
                if (!this.state.loading && this.state.data) {
                    this.renderCharts();
                }
                return () => this.destroyCharts();
            },
            () => [this.state.data]
        );

        onWillUnmount(() => this.destroyCharts());
    }

    async loadData() {
        this.state.loading = true;
        const data = await this.orm.call(
            "ssc.payroll.dashboard", "get_dashboard_data",
            [this.state.month || null, this.state.year || null]
        );
        this.state.month = data.month;
        this.state.year = data.year;
        this.state.data = data;
        this.state.loading = false;
    }

    onMonthChange(ev) {
        this.state.month = ev.target.value;
        this.loadData();
    }

    onYearChange(ev) {
        this.state.year = ev.target.value;
        this.loadData();
    }

    // ------------------------------------------------------------------
    // Formatting
    // ------------------------------------------------------------------
    get symbol() {
        return this.state.data ? this.state.data.currency_symbol : "";
    }

    formatMoney(value) {
        const n = (value || 0).toLocaleString(undefined, {
            minimumFractionDigits: 2, maximumFractionDigits: 2,
        });
        const before = this.state.data && this.state.data.currency_position === "before";
        return before ? `${this.symbol} ${n}` : `${n} ${this.symbol}`;
    }

    formatInt(value) {
        return (value || 0).toLocaleString();
    }

    formatHours(value) {
        const n = (value || 0).toLocaleString(undefined, {
            minimumFractionDigits: 0, maximumFractionDigits: 1,
        });
        return `${n} h`;
    }

    // Tall inner height so many-category charts scroll instead of cramming.
    // The wrapping element caps the visible height and scrolls.
    tallHeight(key) {
        const n = this.state.data ? (this.state.data[key].labels || []).length : 0;
        return Math.max(300, n * 28);
    }

    // ------------------------------------------------------------------
    // KPI card actions
    // ------------------------------------------------------------------
    openEmployees() {
        this.action.doAction({
            type: "ir.actions.act_window",
            name: `Payslips – ${this.state.month} ${this.state.year}`,
            res_model: "ssc.payslip",
            domain: [["month", "=", this.state.month], ["year", "=", this.state.year]],
            views: [[false, "list"], [false, "form"]],
            target: "current",
        });
    }

    openBatches() {
        // Batch names start with e.g. "2026JUN/...", so a prefix match scopes
        // them to the selected period.
        this.action.doAction({
            type: "ir.actions.act_window",
            name: `Salary Batches – ${this.state.month} ${this.state.year}`,
            res_model: "ssc.salary.batch",
            domain: [["name", "like", `${this.state.year}${this.state.month}`]],
            views: [[false, "list"], [false, "form"]],
            target: "current",
        });
    }

    // Overtime drill-down: the projects each employee worked overtime on in
    // the period. kind = "all" | "reg" | "off" picks which overtime counts.
    openOvertime(kind) {
        const domain = [
            ["month", "=", this.state.month],
            ["year", "=", this.state.year],
        ];
        if (kind === "reg") {
            domain.push(["total_overtime_reg", ">", 0]);
        } else if (kind === "off") {
            domain.push(["total_overtime_off", ">", 0]);
        } else {
            domain.push("|", ["total_overtime_reg", ">", 0], ["total_overtime_off", ">", 0]);
        }
        const label = { reg: "Regular", off: "Off" }[kind] || "";
        this.action.doAction({
            type: "ir.actions.act_window",
            name: `Overtime ${label} – ${this.state.month} ${this.state.year}`.replace("  ", " "),
            res_model: "ssc.payslip.project",
            domain,
            views: [[false, "list"]],
            context: { group_by: ["employee_id"] },
            target: "current",
        });
    }

    scrollToProjects() {
        const el = this.projectCardRef.el;
        if (el) {
            el.scrollIntoView({ behavior: "smooth", block: "start" });
        }
    }

    // ------------------------------------------------------------------
    // Charts
    // ------------------------------------------------------------------
    destroyCharts() {
        for (const chart of Object.values(this.charts)) {
            chart.destroy();
        }
        this.charts = {};
    }

    renderCharts() {
        this.destroyCharts();
        for (const cfg of CHARTS) {
            const canvas = this.refs[cfg.ref].el;
            const dataset = this.state.data[cfg.key];
            if (!canvas || !dataset) {
                continue;
            }
            this.charts[cfg.key] = new Chart(canvas, this.chartConfig(cfg, dataset));
        }
    }

    chartConfig(cfg, dataset) {
        const fmt = cfg.money ? (v) => this.formatMoney(v) : (v) => this.formatInt(v);
        const isDoughnut = cfg.type === "doughnut";
        const colors = dataset.labels.map((_, i) => PALETTE[i % PALETTE.length]);

        // Build the axes so ONLY the value axis reformats its ticks. The
        // category axis is left untouched: giving it a callback (even an
        // undefined one) overrides Chart.js's default and makes it print the
        // category index instead of the label.
        let scales = {};
        if (!isDoughnut) {
            const valueAxis = {
                beginAtZero: true,
                ticks: { callback: (v) => fmt(v) },
                grid: { display: cfg.horizontal },
            };
            const categoryAxis = {
                ticks: {
                    autoSkip: !cfg.horizontal,
                    maxRotation: cfg.horizontal ? 0 : 55,
                    minRotation: 0,
                },
                grid: { display: !cfg.horizontal },
            };
            scales = cfg.horizontal
                ? { x: valueAxis, y: categoryAxis }
                : { x: categoryAxis, y: valueAxis };
        }

        return {
            type: cfg.type,
            data: {
                labels: dataset.labels,
                datasets: [{
                    label: "",
                    data: dataset.data,
                    backgroundColor: isDoughnut || cfg.horizontal ? colors : PALETTE[0],
                    borderColor: isDoughnut ? "#ffffff" : undefined,
                    borderWidth: isDoughnut ? 2 : 0,
                    borderRadius: isDoughnut ? 0 : 6,
                    maxBarThickness: 34,
                }],
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                indexAxis: cfg.horizontal ? "y" : "x",
                plugins: {
                    legend: { display: isDoughnut, position: "right" },
                    tooltip: {
                        callbacks: {
                            label: (ctx) => {
                                const value = isDoughnut
                                    ? ctx.parsed
                                    : ctx.parsed[cfg.horizontal ? "x" : "y"];
                                return ` ${fmt(value)}`;
                            },
                        },
                    },
                },
                scales,
            },
        };
    }
}

registry.category("actions").add("ssc_payroll_dashboard", SscPayrollDashboard);
