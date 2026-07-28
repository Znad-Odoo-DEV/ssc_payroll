# -*- coding: utf-8 -*-
import logging

from odoo import api, fields, models
from odoo.exceptions import UserError

from .utils import studio_get as _get

_logger = logging.getLogger(__name__)


class SscStaffLoan(models.Model):
    _name = 'ssc.staff.loan'
    _description = "Staff Loan"
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'id desc'

    name = fields.Char(string="Description", compute='_compute_name', store=True, readonly=False)
    reference = fields.Char(string="Reference", copy=False, readonly=True)
    employee_id = fields.Many2one('ssc.employee', string="Employee", required=True, tracking=True)
    company_id = fields.Many2one(related='employee_id.company_id', store=True, index=True)
    currency_id = fields.Many2one(related='employee_id.currency_id', store=True)
    employee_code = fields.Char(related='employee_id.employee_code', string="Employee ID", store=True)
    occupation = fields.Char(related='employee_id.job_position', string="Occupation", store=True)
    joining_date = fields.Date(related='employee_id.joining_date', string="Joining Date", store=True)

    total_amount = fields.Monetary(string="Total Amount", tracking=True)
    loan_reason = fields.Char(string="Loan Reason")
    date_of_request = fields.Date(string="Date of Request", default=fields.Date.context_today)
    no_of_installments = fields.Integer(string="No. of Installments", default=1)
    date_of_first_installment = fields.Date(string="Date of First Installment")
    generate_installment = fields.Boolean(string="Generate Installment")
    approved_by = fields.Many2one('res.users', string="Approved by", readonly=True)
    approval_date = fields.Date(string="Approval Date", readonly=True)
    payment_date = fields.Date(string="Payment Date")

    installment_ids = fields.One2many('ssc.staff.loan.installment', 'loan_id', string="Installments")
    outstanding_amount = fields.Monetary(
        string="Outstanding Amount", compute='_compute_outstanding', store=True,
    )

    # Live link to the Studio x_staff_loan record this mirrors. Falls back to
    # the Studio outstanding when no installments were mirrored.
    studio_ref_id = fields.Integer(string="Studio Ref", index=True, copy=False)
    studio_outstanding = fields.Monetary(string="Outstanding (Studio)")
    state = fields.Selection(
        [
            ('new', 'New Request'),
            ('submitted', 'Submitted'),
            ('in_payment', 'In Payments'),
            ('running', 'Running'),
            ('closed', 'Closed'),
            ('rejected', 'Rejected'),
        ],
        string="Status", default='new', tracking=True, index=True,
    )

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if not vals.get('reference'):
                vals['reference'] = self.env['ir.sequence'].next_by_code('ssc.staff.loan') or '/'
        return super().create(vals_list)

    @api.depends('reference', 'employee_id')
    def _compute_name(self):
        for rec in self:
            rec.name = "%s-Loan Request for %s" % (
                rec.reference or '', rec.employee_id.name or '')

    @api.depends('total_amount', 'installment_ids.paid', 'installment_ids.amount',
                 'studio_ref_id', 'studio_outstanding')
    def _compute_outstanding(self):
        for rec in self:
            if rec.studio_ref_id and not rec.installment_ids:
                rec.outstanding_amount = rec.studio_outstanding
            else:
                paid = sum(rec.installment_ids.filtered('paid').mapped('amount'))
                rec.outstanding_amount = rec.total_amount - paid

    def action_generate_installments(self):
        for rec in self:
            if not rec.no_of_installments:
                raise UserError(self.env._("Set the number of installments first."))
            rec.installment_ids.unlink()
            base = round(rec.total_amount / rec.no_of_installments, 2) if rec.no_of_installments else 0.0
            commands, allocated = [], 0.0
            for i in range(1, rec.no_of_installments + 1):
                amount = base if i < rec.no_of_installments else round(rec.total_amount - allocated, 2)
                allocated += amount
                commands.append((0, 0, {
                    'name': self.env._("Installment no.%02d") % i,
                    'amount': amount,
                }))
            rec.installment_ids = commands
        return True

    def action_submit(self):
        self.write({'state': 'submitted'})

    def action_confirm_payment(self):
        self.write({'state': 'in_payment'})

    def action_approve(self):
        self.write({
            'state': 'running',
            'approved_by': self.env.uid,
            'approval_date': fields.Date.context_today(self),
        })

    def action_close(self):
        self.write({'state': 'closed'})

    def action_reject(self):
        self.write({'state': 'rejected'})

    # ------------------------------------------------------------------
    # TEMPORARY live bridge with the Studio x_staff_loan master. Driven by an
    # automated action so no Python inherit of the Studio model is needed.
    # ------------------------------------------------------------------
    @api.model
    def _sync_from_studio(self, studio_records):
        Loan = self.with_context(
            active_test=False, tracking_disable=True,
            mail_create_nolog=True, mail_create_nosubscribe=True)
        for src in studio_records:
            try:
                with self.env.cr.savepoint():
                    vals = Loan._studio_loan_vals(src)
                    if not vals:
                        continue
                    mirror = Loan.search([('studio_ref_id', '=', src.id)], limit=1)
                    if mirror:
                        mirror.write(vals)
                    else:
                        mirror = Loan.create(dict(vals, studio_ref_id=src.id))
                    Loan._sync_installments(mirror, src)
            except Exception:
                _logger.exception(
                    "ssc_payroll: loan mirror failed for x_staff_loan id=%s", src.id)
        return True

    @api.model
    def _sync_installments(self, loan, src):
        """Mirror the Studio installments onto the loan, matched by their
        description. Amounts follow Studio; the paid flag is seeded once on
        creation, then left editable here without being overwritten."""
        Inst = self.env['ssc.staff.loan.installment']
        existing = {i.name: i for i in loan.installment_ids}
        for line in _get(src, 'x_studio_installments') or []:
            name = _get(line, 'x_name') or self.env._("Installment")
            amount = _get(line, 'x_studio_amount') or 0.0
            inst = existing.get(name)
            if inst:
                if inst.amount != amount:
                    inst.amount = amount
            else:
                Inst.create({
                    'loan_id': loan.id,
                    'name': name,
                    'amount': amount,
                    'paid': bool(_get(line, 'x_studio_paid')),
                })

    @api.model
    def _resolve_studio_employee(self, src):
        """Find the ssc.employee behind a Studio loan, by the loan's own
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
    def _studio_loan_vals(self, src):
        """Map an x_staff_loan record onto ssc.staff.loan. Returns None when the
        employee cannot be resolved (employee_id is required)."""
        employee = self._resolve_studio_employee(src)
        if not employee:
            return None
        outstanding = _get(src, 'x_studio_outstanding_amount') or 0.0
        return {
            'employee_id': employee.id,
            'total_amount': _get(src, 'x_studio_value') or 0.0,
            'loan_reason': _get(src, 'x_studio_loan_reason') or False,
            'date_of_request': _get(src, 'x_studio_date_of_request') or False,
            'no_of_installments': _get(src, 'x_studio_no_of_installments') or 1,
            'date_of_first_installment': _get(src, 'x_studio_date_of_first_installment') or False,
            'generate_installment': bool(_get(src, 'x_studio_generate_installment')),
            'approval_date': _get(src, 'x_studio_approval_date_1') or False,
            'payment_date': _get(src, 'x_studio_payment_date') or False,
            'studio_outstanding': outstanding,
            # No selection values known for the Studio status, so the state
            # follows the outstanding: settled when nothing is left, else running.
            'state': 'closed' if outstanding <= 0 else 'running',
        }


class SscStaffLoanInstallment(models.Model):
    _name = 'ssc.staff.loan.installment'
    _description = "Staff Loan Installment"
    _order = 'loan_id, id'

    loan_id = fields.Many2one(
        'ssc.staff.loan', string="Loan", required=True, ondelete='cascade', index=True)
    company_id = fields.Many2one(related='loan_id.company_id', store=True)
    currency_id = fields.Many2one(related='loan_id.currency_id', store=True)
    name = fields.Char(string="No. of Installment", required=True)
    amount = fields.Monetary(string="Amount")
    paid = fields.Boolean(string="Paid")
