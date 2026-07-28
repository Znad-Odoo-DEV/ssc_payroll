# -*- coding: utf-8 -*-
import logging

from odoo import api, fields, models

from .utils import studio_get as _get

_logger = logging.getLogger(__name__)


class SscLeaveExpense(models.Model):
    _name = 'ssc.leave.expense'
    _description = "Leave / Ticket Expense"
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'id desc'

    name = fields.Char(string="Description", compute='_compute_name', store=True, readonly=False)
    employee_id = fields.Many2one('ssc.employee', string="Employee", required=True, tracking=True)
    company_id = fields.Many2one(related='employee_id.company_id', store=True, index=True)
    currency_id = fields.Many2one(related='employee_id.currency_id', store=True)
    employee_code = fields.Char(related='employee_id.employee_code', string="Employee ID", store=True)
    request_link = fields.Char(string="Request Link")

    expense_type = fields.Selection(
        [
            ('leave_allowance', 'Leave Allowance'),
            ('ticket_expense', 'Ticket Expense'),
            ('ticket_reimbursement', 'Ticket Reimbursement'),
        ],
        string="Type", default='leave_allowance', required=True, tracking=True,
    )
    payable_amount = fields.Monetary(string="Payable Amount", tracking=True)
    approval_date = fields.Date(string="Approval Date")
    eid_expiry_date = fields.Date(string="E.I.D Expiry date")
    hold_500 = fields.Boolean(string="Hold (500 AED)?")

    # Supporting documents mirrored from Studio.
    approved_ticket = fields.Binary(string="Approved Ticket", attachment=True)
    approved_ticket_filename = fields.Char(string="Approved Ticket Filename")
    ticket_file = fields.Binary(string="Ticket File", attachment=True)
    ticket_file_filename = fields.Char(string="Ticket File Filename")

    payment_method = fields.Selection(
        [('cash', 'Cash'), ('bank', 'Bank'), ('salary', 'Attached with Salary')],
        string="Payment Method", readonly=True,
    )
    # Live link to the Studio x_leave_expenses record this mirrors.
    studio_ref_id = fields.Integer(string="Studio Ref", index=True, copy=False)
    state = fields.Selection(
        [
            ('created', 'Created'),
            ('in_payment', 'In Payments'),
            ('attached', 'Attached W/Salary'),
            ('paid', 'Paid'),
        ],
        string="Status", default='created', tracking=True, index=True,
    )

    TYPE_LABELS = {
        'leave_allowance': 'Leave Allowance',
        'ticket_expense': 'Ticket Allowance',
        'ticket_reimbursement': 'Ticket Reimbursement',
    }

    # Studio x_studio_type stores the label as its technical value.
    STUDIO_TYPE_MAP = {
        'Leave Allowance': 'leave_allowance',
        'Ticket Expense': 'ticket_expense',
        'Ticket Reimbursement': 'ticket_reimbursement',
    }

    @api.depends('expense_type', 'employee_id')
    def _compute_name(self):
        for rec in self:
            label = rec.TYPE_LABELS.get(rec.expense_type, 'Leave Allowance')
            rec.name = "%s for %s" % (label, rec.employee_id.name or '')

    def action_pay_cash(self):
        self.write({'payment_method': 'cash', 'state': 'in_payment'})

    def action_pay_bank(self):
        self.write({'payment_method': 'bank', 'state': 'in_payment'})

    def action_attach_salary(self):
        self.write({'payment_method': 'salary', 'state': 'attached'})

    def action_set_paid(self):
        self.write({'state': 'paid'})

    def action_reset(self):
        self.write({'state': 'created', 'payment_method': False})

    # ------------------------------------------------------------------
    # TEMPORARY live bridge with the Studio x_leave_expenses master. Driven by
    # an automated action so no Python inherit of the Studio model is needed.
    # ------------------------------------------------------------------
    @api.model
    def _sync_from_studio(self, studio_records):
        Exp = self.with_context(
            active_test=False, tracking_disable=True,
            mail_create_nolog=True, mail_create_nosubscribe=True)
        for src in studio_records:
            try:
                with self.env.cr.savepoint():
                    vals = Exp._studio_leave_vals(src)
                    if not vals:
                        continue
                    mirror = Exp.search([('studio_ref_id', '=', src.id)], limit=1)
                    if mirror:
                        mirror.write(vals)
                    else:
                        Exp.create(dict(vals, studio_ref_id=src.id))
            except Exception:
                _logger.exception(
                    "ssc_payroll: leave expense mirror failed for x_leave_expenses id=%s", src.id)
        return True

    @api.model
    def _resolve_studio_employee(self, src):
        """Find the ssc.employee behind a Studio leave expense, by its own
        Employee ID first, then through its employee link."""
        Emp = self.env['ssc.employee'].with_context(active_test=False)
        code = _get(src, 'x_studio_employee_id')
        if code:
            emp = Emp.search([('employee_code', '=', code)], limit=1)
            if emp:
                return emp
        studio_emp = _get(src, 'x_studio_employee')
        if studio_emp:
            return self.env['ssc.attachment']._resolve_studio_employee(studio_emp)
        return Emp.browse()

    @api.model
    def _studio_leave_vals(self, src):
        """Map an x_leave_expenses record onto ssc.leave.expense. Returns None
        when the employee cannot be resolved (employee_id is required)."""
        employee = self._resolve_studio_employee(src)
        if not employee:
            return None
        request = _get(src, 'x_studio_request_link')
        # Payment routing follows the Studio flags; the state is derived from it
        # since the Studio status selection values are not known here.
        if _get(src, 'x_studio_salary'):
            method, state = 'salary', 'attached'
        elif _get(src, 'x_studio_bank'):
            method, state = 'bank', 'in_payment'
        elif _get(src, 'x_studio_cash'):
            method, state = 'cash', 'in_payment'
        else:
            method, state = False, 'created'
        return {
            'employee_id': employee.id,
            'expense_type': self.STUDIO_TYPE_MAP.get(
                _get(src, 'x_studio_type'), 'leave_allowance'),
            'payable_amount': _get(src, 'x_studio_value') or 0.0,
            'approval_date': _get(src, 'x_studio_approval_date') or False,
            'eid_expiry_date': _get(src, 'x_studio_eid_e') or False,
            'hold_500': bool(_get(src, 'x_studio_hold_500aed')),
            'request_link': request.display_name if request else False,
            'approved_ticket': _get(src, 'x_studio_approved_ticket') or False,
            'approved_ticket_filename': _get(src, 'x_studio_approved_ticket_filename') or False,
            'ticket_file': _get(src, 'x_studio_binary_field_20d_1j29ku0t7') or False,
            'ticket_file_filename': _get(src, 'x_studio_binary_field_20d_1j29ku0t7_filename') or False,
            'payment_method': method,
            'state': state,
        }
