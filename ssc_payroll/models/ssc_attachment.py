# -*- coding: utf-8 -*-
import calendar
import logging

from odoo import api, fields, models

from .utils import MONTH_ABBR, SICK_LEAVE_APPROVED, studio_get as _get

_logger = logging.getLogger(__name__)

class SscAttachment(models.Model):
    _name = 'ssc.attachment'
    _description = "Salary Attachment"
    _inherit = ['mail.thread']
    _order = 'year desc, month desc, id desc'

    name = fields.Char(string="Description", required=True)
    employee_id = fields.Many2one(
        'ssc.employee', string="Employee", required=True, index=True, tracking=True,
    )
    company_id = fields.Many2one(related='employee_id.company_id', store=True, index=True)
    currency_id = fields.Many2one(related='employee_id.currency_id', store=True)
    type_id = fields.Many2one('ssc.attachment.type', string="Type", required=True)
    factor = fields.Integer(related='type_id.factor', store=True)
    # Period the attachment belongs to (matched against the payslip period).
    # Prefilled from today's date on a new record, but editable.
    month = fields.Selection(
        [
            ('JAN', 'JAN'), ('FEB', 'FEB'), ('MAR', 'MAR'), ('APR', 'APR'),
            ('MAY', 'MAY'), ('JUN', 'JUN'), ('JUL', 'JUL'), ('AUG', 'AUG'),
            ('SEP', 'SEP'), ('OCT', 'OCT'), ('NOV', 'NOV'), ('DEC', 'DEC'),
        ],
        string="Month", required=True, default=lambda self: self._default_month(),
    )
    year = fields.Char(
        string="Year", required=True, default=lambda self: self._default_year())
    # Day the attachment refers to, when the month is not specific enough: an
    # employee can have several sick leaves inside one month, and each must
    # produce its own reimbursement.
    day = fields.Date(string="Day")
    value = fields.Monetary(string="Value")
    signed_value = fields.Monetary(
        string="Signed Value", compute='_compute_signed_value', store=True,
        help="Value with the type's sign applied (+ addition / - deduction).",
    )
    payslip_id = fields.Many2one('ssc.payslip', string="Payslip", index=True)
    # Optional source documents this attachment was generated from.
    fine_id = fields.Many2one('ssc.fine', string="Fine", index=True, ondelete='cascade')
    advance_id = fields.Many2one('ssc.advance', string="Advance", index=True, ondelete='cascade')
    loan_id = fields.Many2one('ssc.staff.loan', string="Staff Loan", index=True, ondelete='cascade')
    # Staff last-month absence deduction: kept in step with the staff line's
    # last_month_absence (fix the day count, the value follows - no delete/recreate).
    staff_line_id = fields.Many2one(
        'ssc.staff.attendance.line', string="Staff Line", index=True, ondelete='cascade')
    leave_expense_id = fields.Many2one('ssc.leave.expense', string="Leave Expense", index=True, ondelete='cascade')
    state = fields.Selection(
        [('new', 'New'), ('attached', 'Attached'), ('paid', 'Paid')],
        string="Status", default='new', tracking=True, index=True,
    )
    # TEMPORARY bridge: id of the mirrored x_attachments_list Studio record.
    studio_ref_id = fields.Integer(string="Studio Ref", index=True, copy=False)

    @api.model
    def _default_month(self):
        return MONTH_ABBR[fields.Date.context_today(self).month]

    @api.model
    def _default_year(self):
        return str(fields.Date.context_today(self).year)

    @api.depends('value', 'factor')
    def _compute_signed_value(self):
        for rec in self:
            rec.signed_value = rec.value * (rec.factor or 0)

    def write(self, vals):
        res = super().write(vals)
        # Detaching from a payslip (its link cleared, e.g. the payslip was
        # deleted and rebuilt) frees the attachment: reset it to New so the next
        # payslip for the same period re-collects it. Paid ones are never demoted.
        if 'payslip_id' in vals and not vals.get('payslip_id'):
            self.filtered(lambda a: a.state == 'attached').write({'state': 'new'})
        return res

    @api.model
    def _exists_for_period(self, employee, month, year, type_id, day=False):
        """True when this employee already carries an attachment of this type
        for the period. Identity is the period and the type, not the wording of
        the description: the same fact reaching us through another route would
        otherwise be paid twice. ``day`` separates several occurrences of the
        same type inside one month (sick leaves)."""
        return bool(self.search_count([
            ('employee_id', '=', employee.id),
            ('month', '=', month), ('year', '=', str(year)),
            ('type_id', '=', type_id),
            ('day', '=', day or False),
            ('state', '!=', 'paid'),
        ]))

    @api.model
    def _create_phone_bill_for(self, employee, month, year):
        """Generate the monthly Phone Bill allowance for an eligible employee
        (``phone_bill_allowance`` set and a positive ``agreed_monthly_allowance``),
        once per period. Studio never auto-creates it, so this is its only
        source; nothing is created if one already exists for the period."""
        value = employee.agreed_monthly_allowance or 0.0
        if not employee.phone_bill_allowance or value <= 0:
            return
        phone_type = self.env.ref('ssc_payroll.attachment_type_phone_bill')
        if self._exists_for_period(employee, month, str(year), phone_type.id):
            return
        self.create({
            'name': self.env._("Mobile Phone Allowance"),
            'employee_id': employee.id,
            'type_id': phone_type.id,
            'month': month,
            'year': str(year),
            'value': value,
            'state': 'new',
        })

    # ------------------------------------------------------------------
    # TEMPORARY bridge: mirror the Studio x_attachments_list into this model
    # so attachments created by the not-yet-migrated Studio modules appear
    # (and flow into payslips) here. Create-only: existing mirrors are left
    # untouched so payslip links / paid states are never clobbered.
    # ------------------------------------------------------------------
    @api.model
    def _cron_sync_studio_attachments(self):
        Studio = self.env.get('x_attachments_list')
        if Studio is None:
            return
        already = set(self.with_context(active_test=False)
                      .search([('studio_ref_id', '!=', False)])
                      .mapped('studio_ref_id'))
        created = 0
        for src in Studio.with_context(active_test=False).search([]):
            if src.id in already:
                continue
            try:
                vals = self._studio_attachment_vals(src)
                if not vals:
                    continue
                self.create(dict(vals, studio_ref_id=src.id))
                created += 1
            except Exception:
                _logger.exception(
                    "ssc_payroll: could not mirror x_attachments_list id=%s.", src.id)
        if created:
            _logger.info("ssc_payroll: mirrored %s Studio attachment(s).", created)
        return created

    def action_sync_studio_attachments(self):
        self._cron_sync_studio_attachments()
        return True

    def _studio_attachment_vals(self, src):
        """Map an x_attachments_list record onto ssc.attachment. Returns None
        when the employee cannot be resolved into a migrated ssc.employee."""
        employee = self._resolve_studio_employee(_get(src, 'x_studio_employee'))
        if not employee:
            return None
        studio_type = _get(src, 'x_studio_type')
        # The phone-bill allowance is generated natively at payroll time (Studio
        # never auto-creates it), so it is never imported here - that would
        # double it.
        if _get(studio_type, 'x_name') == 'Phone Bill Reimbursement':
            return None
        # Advance/fine installments and negative factors are deductions.
        is_deduction = (
            (_get(src, 'x_studio_factor') or 0) < 0
            or bool(_get(src, 'x_studio_advance_link'))
            or bool(_get(src, 'x_studio_fine_link'))
        )
        att_type = self._resolve_studio_type(studio_type, is_deduction)
        month = _get(src, 'x_studio_month_1') or ''
        return {
            'name': _get(src, 'x_name') or self.env._("Attachment"),
            'employee_id': employee.id,
            'type_id': att_type.id,
            'month': month if month in dict(self._fields['month'].selection) else 'JAN',
            'year': _get(src, 'x_studio_year') or '',
            'value': _get(src, 'x_studio_value') or 0.0,
            # Already used on a Studio payslip -> mark attached so it is not
            # re-attached to a new payslip; otherwise leave it new.
            'state': 'attached' if _get(src, 'x_studio_pay_slip') else 'new',
        }

    def _resolve_studio_employee(self, studio_emp):
        Emp = self.env['ssc.employee']
        if not studio_emp:
            return Emp.browse()
        for local_field, studio_field in (
                ('employee_code', 'x_studio_employee_id'),
                ('attendance_code', 'x_studio_attendance_id'),
                ('name', 'x_name')):
            val = _get(studio_emp, studio_field)
            if val:
                emp = Emp.with_context(active_test=False).search(
                    [(local_field, '=', val)], limit=1)
                if emp:
                    return emp
        return Emp.browse()

    def _resolve_studio_type(self, studio_type, is_deduction):
        """Always return a valid attachment type. Resolve by name, creating it
        from the Studio type if missing; fall back to a generic seeded type so
        the required type_id is never null."""
        Type = self.env['ssc.attachment.type']
        name = _get(studio_type, 'x_name')
        if name:
            att_type = Type.search([('name', '=', name)], limit=1)
            if att_type:
                return att_type
            return Type.create({
                'name': name,
                'kind': 'deduction' if is_deduction else 'addition',
            })
        xmlid = ('ssc_payroll.attachment_type_salary_deduction' if is_deduction
                 else 'ssc_payroll.attachment_type_salary_addition')
        return self.env.ref(xmlid)
