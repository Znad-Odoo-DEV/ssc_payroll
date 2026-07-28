# -*- coding: utf-8 -*-
import logging

from odoo import api, fields, models
from odoo.exceptions import UserError

from .utils import studio_get as _get

_logger = logging.getLogger(__name__)


class SscAdvance(models.Model):
    _name = 'ssc.advance'
    _description = "Advance Salary"
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'id desc'

    name = fields.Char(string="Description", compute='_compute_name', store=True, readonly=False)
    employee_id = fields.Many2one('ssc.employee', string="Employee", required=True, tracking=True)
    company_id = fields.Many2one(related='employee_id.company_id', store=True, index=True)
    currency_id = fields.Many2one(related='employee_id.currency_id', store=True)
    employee_code = fields.Char(related='employee_id.employee_code', string="Employee ID", store=True)

    total_amount = fields.Monetary(string="Total Amount", tracking=True)
    no_of_installments = fields.Integer(string="No. of Installments", default=1)
    date = fields.Date(string="Date", default=fields.Date.context_today)
    approval_date = fields.Date(string="Approval Date")
    request_link = fields.Char(string="Request Link")

    installment_ids = fields.One2many('ssc.advance.installment', 'advance_id', string="Installments")
    outstanding_amount = fields.Monetary(
        string="Outstanding Amount", compute='_compute_outstanding', store=True,
    )

    # Live link to the Studio x_advance_salaries record this mirrors. Mirrored
    # advances carry no local installments yet, so their outstanding is taken
    # straight from Studio instead of being recomputed to the full amount.
    studio_ref_id = fields.Integer(string="Studio Ref", index=True, copy=False)
    studio_outstanding = fields.Monetary(string="Outstanding (Studio)")
    state = fields.Selection(
        [
            ('new', 'New'),
            ('in_payment', 'In Payments'),
            ('running', 'Running'),
            ('closed', 'Closed'),
        ],
        string="Status", default='new', tracking=True, index=True,
    )

    @api.depends('employee_id')
    def _compute_name(self):
        for rec in self:
            rec.name = self.env._("Advance for %s") % (rec.employee_id.name or '')

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
            rec.installment_ids = rec._build_installments(
                rec.total_amount, rec.no_of_installments)
        return True

    def _build_installments(self, total, count):
        """Split ``total`` into ``count`` installments, rounding the last one
        to absorb any remainder."""
        self.ensure_one()
        base = round(total / count, 2) if count else 0.0
        commands = []
        allocated = 0.0
        for i in range(1, count + 1):
            amount = base if i < count else round(total - allocated, 2)
            allocated += amount
            commands.append((0, 0, {
                'name': self.env._("Installment no.%02d") % i,
                'amount': amount,
            }))
        return commands

    def action_confirm_payment(self):
        self.write({'state': 'in_payment'})

    def action_run(self):
        self.write({'state': 'running'})

    def action_close(self):
        self.write({'state': 'closed'})

    def action_reset(self):
        self.write({'state': 'new'})

    # ------------------------------------------------------------------
    # TEMPORARY live bridge with the Studio x_advance_salaries master.
    # Mirrors headers only (outstanding is taken from Studio); driven by an
    # automated action so no Python inherit of the Studio model is needed.
    # ------------------------------------------------------------------
    @api.model
    def _sync_from_studio(self, studio_records):
        Adv = self.with_context(
            active_test=False, tracking_disable=True,
            mail_create_nolog=True, mail_create_nosubscribe=True)
        for src in studio_records:
            try:
                with self.env.cr.savepoint():
                    vals = Adv._studio_advance_vals(src)
                    if not vals:
                        continue
                    mirror = Adv.search([('studio_ref_id', '=', src.id)], limit=1)
                    if mirror:
                        mirror.write(vals)
                    else:
                        mirror = Adv.create(dict(vals, studio_ref_id=src.id))
                    Adv._sync_installments(mirror, src)
            except Exception:
                _logger.exception(
                    "ssc_payroll: advance mirror failed for x_advance_salaries id=%s", src.id)
        return True

    @api.model
    def _sync_installments(self, advance, src):
        """Mirror the Studio installments onto the advance, matched by their
        description. Amounts always follow Studio; the paid flag is seeded from
        Studio when the installment is first created, then left alone so it can
        be managed here without being overwritten on the next sync."""
        Inst = self.env['ssc.advance.installment']
        existing = {i.name: i for i in advance.installment_ids}
        for line in _get(src, 'x_studio_installments') or []:
            name = _get(line, 'x_name') or self.env._("Installment")
            amount = _get(line, 'x_studio_amount') or 0.0
            inst = existing.get(name)
            if inst:
                if inst.amount != amount:
                    inst.amount = amount
            else:
                Inst.create({
                    'advance_id': advance.id,
                    'name': name,
                    'amount': amount,
                    'paid': bool(_get(line, 'x_studio_paid')),
                })

    @api.model
    def _resolve_studio_employee(self, src):
        """Find the ssc.employee behind a Studio advance, by the advance's own
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
    def _studio_advance_vals(self, src):
        """Map an x_advance_salaries record onto ssc.advance. Returns None when
        the employee cannot be resolved (employee_id is required)."""
        employee = self._resolve_studio_employee(src)
        if not employee:
            return None
        outstanding = _get(src, 'x_studio_outstanding_amount') or 0.0
        request = _get(src, 'x_studio_request_link')
        return {
            'employee_id': employee.id,
            'total_amount': _get(src, 'x_studio_value') or 0.0,
            'no_of_installments': _get(src, 'x_studio_no_of_installments') or 1,
            'date': _get(src, 'x_studio_date') or False,
            'approval_date': _get(src, 'x_studio_approval_date') or False,
            'request_link': request.display_name if request else False,
            'studio_outstanding': outstanding,
            # No local installments are mirrored, so the state follows the
            # Studio outstanding: settled when nothing is left, else running.
            'state': 'closed' if outstanding <= 0 else 'running',
        }


class SscAdvanceInstallment(models.Model):
    _name = 'ssc.advance.installment'
    _description = "Advance Installment"
    _order = 'advance_id, id'

    advance_id = fields.Many2one(
        'ssc.advance', string="Advance", required=True, ondelete='cascade', index=True)
    company_id = fields.Many2one(related='advance_id.company_id', store=True)
    currency_id = fields.Many2one(related='advance_id.currency_id', store=True)
    name = fields.Char(string="Installment Description", required=True)
    amount = fields.Monetary(string="Amount")
    paid = fields.Boolean(string="Paid")
