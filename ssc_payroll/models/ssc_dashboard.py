# -*- coding: utf-8 -*-
from odoo import api, fields, models

from .utils import MONTH_ABBR


class SscPayrollDashboard(models.TransientModel):
    _name = 'ssc.payroll.dashboard'
    _description = "Payroll Dashboard"

    currency_id = fields.Many2one(
        'res.currency', default=lambda self: self.env.company.currency_id)

    # Current period
    period_label = fields.Char(string="Current Period", compute='_compute_kpis')

    # Money KPIs (current calendar month)
    total_net_month = fields.Monetary(string="Net Payroll (This Month)", compute='_compute_kpis')
    total_ot_month = fields.Monetary(string="Overtime (This Month)", compute='_compute_kpis')

    # Headcount
    employee_count = fields.Integer(string="Employees", compute='_compute_kpis')
    staff_count = fields.Integer(string="Staff / Engineers", compute='_compute_kpis')
    labour_count = fields.Integer(string="Labour", compute='_compute_kpis')

    # Outstanding balances
    outstanding_advances = fields.Monetary(string="Outstanding Advances", compute='_compute_kpis')
    outstanding_loans = fields.Monetary(string="Outstanding Loans", compute='_compute_kpis')
    outstanding_fines = fields.Monetary(string="Outstanding Fines", compute='_compute_kpis')

    # Workflow counters
    batches_pending = fields.Integer(string="Batches to Approve", compute='_compute_kpis')
    payslips_new = fields.Integer(string="Payslips to Process", compute='_compute_kpis')

    def _compute_kpis(self):
        today = fields.Date.context_today(self)
        month = MONTH_ABBR[today.month]
        year = str(today.year)

        Employee = self.env['ssc.employee']
        Payslip = self.env['ssc.payslip']
        Advance = self.env['ssc.advance']
        Loan = self.env['ssc.staff.loan']
        Fine = self.env['ssc.fine']
        Batch = self.env['ssc.salary.batch']

        slips = Payslip.search([('month', '=', month), ('year', '=', year)])
        advances = Advance.search([('state', '!=', 'closed')])
        loans = Loan.search([('state', 'not in', ('closed', 'rejected'))])
        fines = Fine.search([('state', '=', 'running')])

        for rec in self:
            rec.period_label = "%s %s" % (month, year)
            rec.total_net_month = sum(slips.mapped('net_amount'))
            rec.total_ot_month = sum(slips.mapped('overtime_salary'))
            rec.employee_count = Employee.search_count([])
            rec.staff_count = Employee.search_count([('is_engineer_office', '=', True)])
            rec.labour_count = Employee.search_count([('is_engineer_office', '=', False)])
            rec.outstanding_advances = sum(advances.mapped('outstanding_amount'))
            rec.outstanding_loans = sum(loans.mapped('outstanding_amount'))
            rec.outstanding_fines = sum(fines.mapped('outstanding_amount'))
            rec.batches_pending = Batch.search_count([('state', 'in', ('created', 'submitted'))])
            rec.payslips_new = Payslip.search_count([('state', '=', 'new')])

    # ------------------------------------------------------------------
    # Quick actions
    # ------------------------------------------------------------------
    def _action(self, xmlid, domain=None):
        action = self.env['ir.actions.act_window']._for_xml_id('ssc_payroll.%s' % xmlid)
        if domain is not None:
            action['domain'] = domain
        return action

    def action_open_payslips(self):
        return self._action('action_ssc_payslip')

    def action_open_payslips_new(self):
        return self._action('action_ssc_payslip', [('state', '=', 'new')])

    def action_open_batches(self):
        return self._action('action_ssc_salary_batch')

    def action_open_batches_pending(self):
        return self._action('action_ssc_salary_batch', [('state', 'in', ('created', 'submitted'))])

    def action_open_employees(self):
        return self._action('action_ssc_employee')

    def action_open_advances(self):
        return self._action('action_ssc_advance', [('state', '!=', 'closed')])

    def action_open_loans(self):
        return self._action('action_ssc_staff_loan', [('state', 'not in', ('closed', 'rejected'))])

    def action_open_fines(self):
        return self._action('action_ssc_fine', [('state', '=', 'running')])

    # ------------------------------------------------------------------
    # Analytics endpoint for the OWL dashboard.
    # One read_group per axis keeps this cheap on large payslip sets.
    # ------------------------------------------------------------------
    @api.model
    def get_dashboard_data(self, month=None, year=None):
        """Return every KPI and chart dataset for one payroll period.

        month is a JAN..DEC abbreviation and year a string, matching how
        payslips are keyed. Both default to the current calendar month.
        """
        today = fields.Date.context_today(self)
        month = month or MONTH_ABBR[today.month]
        year = str(year or today.year)
        currency = self.env.company.currency_id
        # Scope every figure to the companies switched on in the top bar, so
        # the dashboard follows the same multi-company selection as the rest
        # of the app.
        company_ids = self.env.companies.ids

        Payslip = self.env['ssc.payslip']
        ProjectLine = self.env['ssc.payslip.project']
        domain = [('month', '=', month), ('year', '=', year),
                  ('company_id', 'in', company_ids)]
        line_domain = [('payslip_id.month', '=', month),
                       ('payslip_id.year', '=', year),
                       ('payslip_id.company_id', 'in', company_ids)]

        emp_rows = Payslip.read_group(
            domain,
            ['net_amount:sum', 'overtime_salary:sum', 'total_salary:sum',
             'overtime_reg:sum', 'overtime_off:sum',
             'overtime_reg_amount:sum', 'overtime_off_amount:sum'],
            ['employee_id'], lazy=False)
        emp_rows.sort(key=lambda r: r['net_amount'] or 0.0, reverse=True)

        des_rows = Payslip.read_group(
            domain, ['net_amount:sum'], ['designation'], lazy=False)
        des_rows.sort(key=lambda r: r['net_amount'] or 0.0, reverse=True)

        # Staff/Engineer vs Labour split (payslip carries the staff flag).
        cat_rows = Payslip.read_group(
            domain, ['net_amount:sum'], ['is_staff'], lazy=False)

        proj_rows = ProjectLine.read_group(
            line_domain,
            ['allocated_total_amount:sum', 'total_overtime_amount:sum'],
            ['project_id'], lazy=False)

        def _name(val, fallback):
            return val[1] if isinstance(val, (list, tuple)) else (val or fallback)

        # KPI money totals reuse the per-employee aggregation.
        total_payroll = sum(r['net_amount'] or 0.0 for r in emp_rows)
        total_overtime = sum(r['overtime_salary'] or 0.0 for r in emp_rows)
        total_salary = sum(r['total_salary'] or 0.0 for r in emp_rows)
        total_ot_reg_hours = sum(r['overtime_reg'] or 0.0 for r in emp_rows)
        total_ot_off_hours = sum(r['overtime_off'] or 0.0 for r in emp_rows)
        total_ot_reg_amount = sum(r['overtime_reg_amount'] or 0.0 for r in emp_rows)
        total_ot_off_amount = sum(r['overtime_off_amount'] or 0.0 for r in emp_rows)
        employee_count = len(emp_rows)
        avg_salary = total_payroll / employee_count if employee_count else 0.0

        # Per-project tuples: (name, cost, overtime, headcount).
        projects = [
            (_name(r['project_id'], 'Unassigned'),
             r['allocated_total_amount'] or 0.0,
             r['total_overtime_amount'] or 0.0,
             r['__count'])
            for r in proj_rows if r.get('project_id')
        ]
        by_cost = sorted(projects, key=lambda p: p[1], reverse=True)
        by_ot = sorted((p for p in projects if p[2] > 0),
                       key=lambda p: p[2], reverse=True)
        by_head = sorted(projects, key=lambda p: p[3], reverse=True)

        emp_names = [_name(r['employee_id'], 'Unknown') for r in emp_rows]
        emp_net = [r['net_amount'] or 0.0 for r in emp_rows]
        emp_ot = sorted(
            ((_name(r['employee_id'], 'Unknown'), r['overtime_salary'] or 0.0)
             for r in emp_rows if (r['overtime_salary'] or 0.0) > 0),
            key=lambda e: e[1], reverse=True)

        top_emp = emp_rows[0] if emp_rows else None
        top_proj = by_cost[0] if by_cost else None

        return {
            'month': month,
            'year': year,
            'months': [MONTH_ABBR[i] for i in range(1, 13)],
            'years': self._dashboard_years(year),
            'currency_symbol': currency.symbol or '',
            'currency_position': currency.position,
            'kpis': {
                'total_payroll': total_payroll,
                'total_cost': total_salary + total_overtime,
                'total_overtime': total_overtime,
                'ot_reg_hours': total_ot_reg_hours,
                'ot_off_hours': total_ot_off_hours,
                'ot_reg_amount': total_ot_reg_amount,
                'ot_off_amount': total_ot_off_amount,
                'ot_total_hours': total_ot_reg_hours + total_ot_off_hours,
                'ot_total_amount': total_ot_reg_amount + total_ot_off_amount,
                'total_employees': employee_count,
                'total_projects': len(projects),
                'average_salary': avg_salary,
                'highest_paid_employee': (
                    _name(top_emp['employee_id'], '-') if top_emp else '-'),
                'highest_paid_amount': (
                    (top_emp['net_amount'] or 0.0) if top_emp else 0.0),
                'highest_cost_project': top_proj[0] if top_proj else '-',
                'highest_cost_amount': top_proj[1] if top_proj else 0.0,
            },
            'salary_by_designation': {
                'labels': [_name(r['designation'], 'Unspecified') for r in des_rows],
                'data': [r['net_amount'] or 0.0 for r in des_rows],
            },
            'salary_by_category': {
                'labels': ['Staff / Engineer' if r['is_staff'] else 'Labour'
                           for r in cat_rows],
                'data': [r['net_amount'] or 0.0 for r in cat_rows],
            },
            'salary_by_project': {
                'labels': [p[0] for p in by_cost],
                'data': [p[1] for p in by_cost],
            },
            'overtime_by_project': {
                'labels': [p[0] for p in by_ot],
                'data': [p[2] for p in by_ot],
            },
            'overtime_by_employee': {
                'labels': [e[0] for e in emp_ot],
                'data': [e[1] for e in emp_ot],
            },
            'employees_by_project': {
                'labels': [p[0] for p in by_head],
                'data': [p[3] for p in by_head],
            },
            'top_salary_employees': {
                'labels': emp_names[:10],
                'data': emp_net[:10],
            },
            'top_projects_cost': {
                'labels': [p[0] for p in by_cost[:10]],
                'data': [p[1] for p in by_cost[:10]],
            },
        }

    @api.model
    def _dashboard_years(self, current):
        rows = self.env['ssc.payslip'].read_group([], ['year'], ['year'], lazy=False)
        years = {r['year'] for r in rows if r.get('year')}
        years.add(current)
        return sorted(years, reverse=True)
