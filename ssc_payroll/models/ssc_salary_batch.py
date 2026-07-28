# -*- coding: utf-8 -*-
import logging
import re
from datetime import date

from odoo import api, fields, models

from .ssc_project_chart import render_project_chart
from .utils import studio_get as _get

_logger = logging.getLogger(__name__)

# Batch names start with the period, e.g. "2026JUL/ACM/WPS/...".
_MONTH_ABBR = {
    'JAN': 1, 'FEB': 2, 'MAR': 3, 'APR': 4, 'MAY': 5, 'JUN': 6,
    'JUL': 7, 'AUG': 8, 'SEP': 9, 'OCT': 10, 'NOV': 11, 'DEC': 12,
}
_PERIOD_RE = re.compile(r'^\s*(\d{4})\s*([A-Za-z]{3})')


class SscSalaryBatch(models.Model):
    _name = 'ssc.salary.batch'
    _description = "Salary Batch"
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'period_date desc, id desc'

    name = fields.Char(string="Batch Description", required=True, index=True)
    # First day of the batch's month/year, parsed from the name prefix, so
    # batches sort chronologically (the month abbreviation alone would sort
    # alphabetically: APR, AUG, DEC...).
    period_date = fields.Date(
        string="Period", compute='_compute_period_date', store=True, index=True,
    )
    company_id = fields.Many2one(
        'res.company', string="Company", required=True, index=True,
        default=lambda self: self.env.company,
    )
    currency_id = fields.Many2one(related='company_id.currency_id', store=True)
    attendance_sheet_id = fields.Many2one('ssc.attendance.sheet', string="Attendance Sheet")
    submitted_by = fields.Many2one('res.users', string="Att. Submitted by")

    batch_type = fields.Selection(
        [('wps', 'WPS'), ('cash', 'Cash')], string="Payment Type",
    )
    is_staff = fields.Boolean(string="Staff Batch")

    show_project_distribution = fields.Boolean(string="Show Project Distribution", default=True)

    payslip_ids = fields.One2many('ssc.payslip', 'batch_id', string="Payslips")
    project_ids = fields.One2many('ssc.salary.batch.project', 'batch_id', string="Projects")

    total_salaries = fields.Monetary(
        string="Total Salaries Amount", compute='_compute_totals', store=True,
    )
    total_overtime = fields.Monetary(
        string="Total Overtime Amount", compute='_compute_totals', store=True,
    )
    total_net = fields.Monetary(
        string="Total Net Amount", compute='_compute_totals', store=True,
    )
    payslip_count = fields.Integer(compute='_compute_totals', store=True)

    state = fields.Selection(
        [
            ('created', 'Created'),
            ('submitted', 'Submitted'),
            ('approved', 'Approved'),
            ('je_created', 'Journal Entry Created'),
            ('paid', 'Paid'),
        ],
        string="Status", default='created', tracking=True, index=True,
    )
    # Live link to the Studio x_salary_batches record this mirrors.
    studio_ref_id = fields.Integer(string="Studio Ref", index=True, copy=False)

    # ------------------------------------------------------------------
    # TEMPORARY live bridge with the Studio x_salary_batches master. Header
    # only; the totals stay computed from the mirrored payslips.
    # ------------------------------------------------------------------
    @api.model
    def _sync_from_studio(self, studio_records):
        Batch = self.with_context(
            tracking_disable=True, mail_create_nolog=True,
            mail_create_nosubscribe=True)
        for src in studio_records:
            try:
                with self.env.cr.savepoint():
                    vals = Batch._studio_batch_vals(src)
                    if not vals:
                        continue
                    mirror = Batch.search([('studio_ref_id', '=', src.id)], limit=1)
                    if mirror:
                        mirror.write(vals)
                    else:
                        Batch.create(dict(vals, studio_ref_id=src.id))
            except Exception:
                _logger.exception(
                    "ssc_payroll: batch mirror failed for x_salary_batches id=%s", src.id)
        return True

    @api.model
    def _studio_batch_vals(self, src):
        company = _get(src, 'x_studio_company') or self.env.company
        name = _get(src, 'x_name') or self.env._("Salary Batch")
        sheet_link = _get(src, 'x_studio_attendance_sheet_1')
        sheet = self.env['ssc.attendance.sheet'].search(
            [('studio_ref_id', '=', sheet_link.id)], limit=1) if sheet_link else False
        if _get(src, 'x_studio_paid'):
            state = 'paid'
        elif _get(src, 'x_studio_done'):
            state = 'approved'
        elif _get(src, 'x_studio_submittal_date') or _get(src, 'x_studio_submitted_by'):
            state = 'submitted'
        else:
            state = 'created'
        return {
            'name': name,
            'company_id': company.id,
            'attendance_sheet_id': sheet.id if sheet else False,
            'is_staff': bool(_get(src, 'x_studio_staff')),
            'batch_type': 'cash' if 'CASH' in name.upper() else 'wps',
            'show_project_distribution': bool(_get(src, 'x_studio_show_project_distribution')),
            'state': state,
        }

    @api.depends('payslip_ids.total_salary', 'payslip_ids.overtime_salary',
                 'payslip_ids.net_amount')
    def _compute_totals(self):
        for batch in self:
            slips = batch.payslip_ids
            batch.total_salaries = sum(slips.mapped('total_salary'))
            batch.total_overtime = sum(slips.mapped('overtime_salary'))
            batch.total_net = sum(slips.mapped('net_amount'))
            batch.payslip_count = len(slips)

    @api.depends('name')
    def _compute_period_date(self):
        for batch in self:
            batch.period_date = self._parse_period(batch.name)

    @api.model
    def _parse_period(self, name):
        """First day of the month/year encoded at the start of a batch name
        ("2026JUL/..." -> 2026-07-01). Returns False when unparseable."""
        match = _PERIOD_RE.match(name or '')
        if not match:
            return False
        month = _MONTH_ABBR.get(match.group(2).upper())
        if not month:
            return False
        return date(int(match.group(1)), month, 1)

    # ------------------------------------------------------------------
    # Workflow
    # ------------------------------------------------------------------
    def action_submit(self):
        self.write({'state': 'submitted'})

    def action_approve(self):
        self.write({'state': 'approved'})
        # Approving the batch attaches its payslips automatically (no per-slip
        # button): each not-yet-attached slip links its pending salary
        # attachments and moves to 'attached'. Paid/attached slips are left.
        for batch in self:
            slips = batch.payslip_ids.filtered(
                lambda s: s.state in ('new', 'checked'))
            if slips:
                slips.action_attach()

    def action_set_paid(self):
        self.write({'state': 'paid'})
        self.mapped('payslip_ids').action_set_paid()

    project_chart = fields.Html(
        string="Worked Days", compute='_compute_project_chart',
        sanitize=False, readonly=True,
    )
    project_chart_allocated = fields.Html(
        string="Including Advanced Days", compute='_compute_project_chart',
        sanitize=False, readonly=True,
    )

    @api.depends('project_ids.total_amount', 'project_ids.share',
                 'project_ids.allocated_total_amount',
                 'project_ids.allocated_share')
    def _compute_project_chart(self):
        for batch in self:
            label = lambda l: l.project_id.display_name
            batch.project_chart = render_project_chart(
                batch.project_ids, label_of=label,
                amount_of=lambda l: l.total_amount,
                share_of=lambda l: l.share,
                currency=batch.currency_id,
            )
            batch.project_chart_allocated = render_project_chart(
                batch.project_ids, label_of=label,
                amount_of=lambda l: l.allocated_total_amount,
                share_of=lambda l: l.allocated_share,
                currency=batch.currency_id,
            )

    def action_compute_project_distribution(self):
        """Roll the payslips' project lines up per project.

        Every payslip already carries what each project cost it: attendance
        days at the daily rate, and overtime hours at their own rates. Summing
        those is exact, so nothing is apportioned here. Salary attachments are
        deliberately left out: a project carries worked time, not the sick
        leaves, fines or advances settled on the payslip. The total therefore
        matches the batch's Total Salaries + Total Overtime, not its Net.
        """
        for batch in self:
            batch.project_ids.unlink()
            totals = {}
            for slip in batch.payslip_ids:
                for pline in slip.project_ids:
                    bucket = totals.setdefault(pline.project_id.id, {
                        'days': 0.0, 'salary': 0.0,
                        'ot_reg_h': 0.0, 'ot_off_h': 0.0,
                        'ot_reg': 0.0, 'ot_off': 0.0,
                        'alloc_days': 0.0, 'alloc_salary': 0.0,
                    })
                    bucket['days'] += pline.total_attendance
                    bucket['salary'] += pline.salary_amount
                    bucket['ot_reg_h'] += pline.total_overtime_reg
                    bucket['ot_off_h'] += pline.total_overtime_off
                    bucket['ot_reg'] += pline.ot_reg_amount
                    bucket['ot_off'] += pline.ot_off_amount
                    bucket['alloc_days'] += pline.allocated_attendance
                    bucket['alloc_salary'] += pline.allocated_salary_amount
            batch.project_ids = [
                (0, 0, {
                    'project_id': pid,
                    'total_days': v['days'],
                    'total_salaries': v['salary'],
                    'total_overtime_reg': v['ot_reg_h'],
                    'total_overtime_off': v['ot_off_h'],
                    'ot_reg_amount': v['ot_reg'],
                    'ot_off_amount': v['ot_off'],
                    'allocated_days': v['alloc_days'],
                    'allocated_salaries': v['alloc_salary'],
                })
                for pid, v in totals.items()
            ]
        return True

    def action_view_payslips(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': self.env._("Payslips"),
            'res_model': 'ssc.payslip',
            'view_mode': 'list,form',
            'domain': [('batch_id', '=', self.id)],
            'context': {'default_batch_id': self.id},
        }


class SscSalaryBatchProject(models.Model):
    _name = 'ssc.salary.batch.project'
    _description = "Salary Batch Project Distribution"
    _order = 'batch_id, id'

    batch_id = fields.Many2one(
        'ssc.salary.batch', string="Batch", required=True, ondelete='cascade', index=True,
    )
    project_id = fields.Many2one('x_projects_list', string="Project", required=True)
    currency_id = fields.Many2one(related='batch_id.currency_id', store=True)

    total_days = fields.Float(string="Attendance Days")
    total_overtime_reg = fields.Float(string="OT hrs - Regular")
    total_overtime_off = fields.Float(string="OT hrs - Off")

    total_salaries = fields.Monetary(string="Total Salaries")
    ot_reg_amount = fields.Monetary(string="OT Regular Amount")
    ot_off_amount = fields.Monetary(string="OT Off Amount")
    total_overtime_amount = fields.Monetary(
        string="Total Overtime", compute='_compute_totals', store=True)
    total_amount = fields.Monetary(
        string="Total Cost", compute='_compute_totals', store=True)
    share = fields.Float(
        string="Share %", compute='_compute_share', store=True,
        help="This project's share of the batch's total project cost.",
    )

    # Same figures once the advanced days are attributed to the projects the
    # employees were on, so nothing worked in the period sits outside a project.
    allocated_days = fields.Float(string="Allocated Days")
    allocated_salaries = fields.Monetary(string="Allocated Salaries")
    allocated_total_amount = fields.Monetary(
        string="Allocated Cost", compute='_compute_totals', store=True)
    allocated_share = fields.Float(
        string="Allocated Share %", compute='_compute_share', store=True)

    @api.depends('total_salaries', 'allocated_salaries',
                 'ot_reg_amount', 'ot_off_amount')
    def _compute_totals(self):
        for rec in self:
            rec.total_overtime_amount = rec.ot_reg_amount + rec.ot_off_amount
            rec.total_amount = rec.total_salaries + rec.total_overtime_amount
            rec.allocated_total_amount = (
                rec.allocated_salaries + rec.total_overtime_amount)

    @api.depends('total_amount', 'allocated_total_amount',
                 'batch_id.project_ids.total_amount',
                 'batch_id.project_ids.allocated_total_amount')
    def _compute_share(self):
        for rec in self:
            lines = rec.batch_id.project_ids
            total = sum(lines.mapped('total_amount'))
            rec.share = 100.0 * rec.total_amount / total if total else 0.0
            allocated = sum(lines.mapped('allocated_total_amount'))
            rec.allocated_share = (
                100.0 * rec.allocated_total_amount / allocated if allocated else 0.0)
