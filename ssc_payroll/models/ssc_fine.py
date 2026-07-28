# -*- coding: utf-8 -*-
import logging

from dateutil.relativedelta import relativedelta

from odoo import api, fields, models
from odoo.exceptions import UserError

from .utils import MONTH_ABBR, studio_get as _get

_logger = logging.getLogger(__name__)


class SscFine(models.Model):
    _name = 'ssc.fine'
    _description = "Fine / Deduction"
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'id desc'

    name = fields.Char(string="Description", compute='_compute_name', store=True, readonly=False)
    reference = fields.Char(string="Reference", copy=False, readonly=True)
    employee_id = fields.Many2one('ssc.employee', string="Employee", required=True, tracking=True)
    company_id = fields.Many2one(related='employee_id.company_id', store=True, index=True)
    currency_id = fields.Many2one(related='employee_id.currency_id', store=True)

    total_fine_amount = fields.Monetary(string="Total Fine Amount", tracking=True)
    fine_description = fields.Char(string="Fine Description")
    requested_by = fields.Many2one('res.users', string="Requested by")
    no_of_installments = fields.Integer(string="No. of Installments", default=1)
    fine_date = fields.Date(string="Fine Date", default=fields.Date.context_today)
    supporting_document = fields.Binary(string="Supporting Document")
    supporting_document_name = fields.Char(string="Supporting Document Name")
    first_installment_due_date = fields.Date(string="1st Installment due date")

    installment_ids = fields.One2many('ssc.attachment', 'fine_id', string="Installments")
    claimed_amount = fields.Monetary(
        string="Claimed Amount", compute='_compute_amounts', store=True,
        help="Amount already attached/paid through payslips.",
    )
    outstanding_amount = fields.Monetary(
        string="Outstanding Amount", compute='_compute_amounts', store=True,
    )
    state = fields.Selection(
        [('draft', 'Draft'), ('running', 'Running'), ('closed', 'Closed')],
        string="Status", default='draft', tracking=True, index=True,
    )

    # Live link to the Studio x_fines_deductions record this mirrors. Falls
    # back to the Studio outstanding when no attachments are linked yet.
    studio_ref_id = fields.Integer(string="Studio Ref", index=True, copy=False)
    studio_outstanding = fields.Monetary(string="Outstanding (Studio)")

    @api.depends('reference', 'employee_id')
    def _compute_name(self):
        for rec in self:
            rec.name = "Fine for %s - %s" % (
                rec.employee_id.name or '', rec.reference or '')

    @api.depends('total_fine_amount', 'installment_ids.value', 'installment_ids.state',
                 'studio_ref_id', 'studio_outstanding')
    def _compute_amounts(self):
        for rec in self:
            if rec.studio_ref_id and not rec.installment_ids:
                rec.outstanding_amount = rec.studio_outstanding
                rec.claimed_amount = rec.total_fine_amount - rec.studio_outstanding
            else:
                claimed = sum(
                    rec.installment_ids.filtered(lambda a: a.state in ('attached', 'paid'))
                    .mapped('value'))
                rec.claimed_amount = claimed
                rec.outstanding_amount = rec.total_fine_amount - claimed

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if not vals.get('reference'):
                vals['reference'] = self.env['ir.sequence'].next_by_code('ssc.fine') or '/'
        return super().create(vals_list)

    def action_generate_installments(self):
        """No-op: fine installments are imported from the Studio
        x_attachments_list bridge, so nothing is generated natively."""
        return True

    def action_close(self):
        self.write({'state': 'closed'})

    def action_reset(self):
        self.write({'state': 'draft'})

    # ------------------------------------------------------------------
    # TEMPORARY live bridge with the Studio x_fines_deductions master. Driven
    # by an automated action so no Python inherit of the Studio model is needed.
    # ------------------------------------------------------------------
    @api.model
    def _sync_from_studio(self, studio_records):
        Fine = self.with_context(
            active_test=False, tracking_disable=True,
            mail_create_nolog=True, mail_create_nosubscribe=True)
        for src in studio_records:
            try:
                with self.env.cr.savepoint():
                    vals = Fine._studio_fine_vals(src)
                    if not vals:
                        continue
                    mirror = Fine.search([('studio_ref_id', '=', src.id)], limit=1)
                    if mirror:
                        mirror.write(vals)
                    else:
                        mirror = Fine.create(dict(vals, studio_ref_id=src.id))
                    Fine._sync_attachments(mirror, src)
            except Exception:
                _logger.exception(
                    "ssc_payroll: fine mirror failed for x_fines_deductions id=%s", src.id)
        return True

    # Studio attachment status -> ssc.attachment state.
    _STUDIO_ATT_STATUS = {'status1': 'new', 'status2': 'attached', 'status3': 'paid'}

    @api.model
    def _sync_attachments(self, fine, src):
        """Link the fine's Studio attachments to this fine. They are the same
        x_attachments_list records the attachment bridge already mirrors into
        ssc.attachment, so we find each by its Studio ref and set fine_id;
        any not mirrored yet is created here from the same Studio line."""
        Attachment = self.env['ssc.attachment']
        for line in _get(src, 'x_studio_attachments') or []:
            state = self._STUDIO_ATT_STATUS.get(
                _get(line, 'x_studio_selection_field_8lv_1iiou9pqk'), 'new')
            att = Attachment.with_context(active_test=False).search(
                [('studio_ref_id', '=', line.id)], limit=1)
            if not att:
                att_vals = Attachment._studio_attachment_vals(line)
                if not att_vals:
                    continue
                att = Attachment.create(dict(att_vals, studio_ref_id=line.id))
            write_vals = {'fine_id': fine.id}
            # Never demote an already-paid attachment.
            if att.state != 'paid':
                write_vals['state'] = state
            att.write(write_vals)

    @api.model
    def _resolve_studio_employee(self, src):
        """Find the ssc.employee behind a Studio fine through its employee link."""
        studio_emp = _get(src, 'x_studio_employee')
        if studio_emp:
            return self.env['ssc.attachment']._resolve_studio_employee(studio_emp)
        return self.env['ssc.employee'].browse()

    @api.model
    def _studio_fine_vals(self, src):
        """Map an x_fines_deductions record onto ssc.fine. Returns None when the
        employee cannot be resolved (employee_id is required)."""
        employee = self._resolve_studio_employee(src)
        if not employee:
            return None
        outstanding = _get(src, 'x_studio_outstanding_amount') or 0.0
        vals = {
            'employee_id': employee.id,
            'total_fine_amount': _get(src, 'x_studio_value') or 0.0,
            'fine_description': _get(src, 'x_studio_fine_description') or False,
            'no_of_installments': _get(src, 'x_studio_no_of_installments') or 1,
            'fine_date': _get(src, 'x_studio_fine_date') or False,
            'first_installment_due_date': _get(src, 'x_studio_1st_installment_due_date') or False,
            'supporting_document': _get(src, 'x_studio_supporting_document') or False,
            'supporting_document_name': _get(src, 'x_studio_supporting_document_filename') or False,
            'studio_outstanding': outstanding,
            'state': 'closed' if outstanding <= 0 else 'running',
        }
        # Keep the Studio reference when present; otherwise the sequence assigns
        # one on create.
        ref = _get(src, 'x_studio_ref')
        if ref:
            vals['reference'] = ref
        return vals
