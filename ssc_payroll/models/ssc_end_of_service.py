# -*- coding: utf-8 -*-
import logging

from odoo import api, fields, models

from .utils import studio_get as _get

_logger = logging.getLogger(__name__)


class SscEndOfService(models.Model):
    _name = 'ssc.end.of.service'
    _description = "End of Service"
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'id desc'

    # ------------------------------------------------------------------
    # Identity / header
    # ------------------------------------------------------------------
    name = fields.Char(string="Description", compute='_compute_name', store=True, readonly=False)
    employee_id = fields.Many2one('ssc.employee', string="Employee", required=True, tracking=True)
    company_id = fields.Many2one(related='employee_id.company_id', store=True, index=True)
    currency_id = fields.Many2one(related='employee_id.currency_id', store=True)
    joining_date = fields.Date(related='employee_id.joining_date', string="Joining Date", store=True)

    total_amount = fields.Monetary(string="Total Amount", tracking=True)
    profession = fields.Char(string="Profession")
    nationality = fields.Char(string="Nationality")
    date_of_request = fields.Date(string="Date of Request")
    eid_visa_expiry = fields.Date(string="E.I.D/Visa Expiry date")
    last_day_of_duty = fields.Date(string="Last Day of Duty", tracking=True)
    received_payslip = fields.Boolean(string="Received Final Payslip")
    hr_review = fields.Text(string="HR Review & Note")
    nor_document = fields.Binary(string="Signed NOR", attachment=True)
    nor_document_name = fields.Char(string="NOR Filename")

    state = fields.Selection(
        [
            ('new', 'New'),
            ('checked', 'Checked & Verified'),
            ('in_payment', 'In Payments'),
            ('paid', 'Paid'),
        ],
        string="Status", default='new', tracking=True, index=True,
    )

    # ------------------------------------------------------------------
    # E.O.S & Gratuity
    # ------------------------------------------------------------------
    gratuity_calculation_method = fields.Char(string="Calculation Method")
    gratuity_basic_salary = fields.Monetary(string="Basic Salary (Gratuity)")
    gratuity_gross_salary = fields.Monetary(string="Total Gross Salary (Gratuity)")
    gratuity_adjust = fields.Boolean(string="Adjust Amount (Gratuity)")
    gratuity_amount = fields.Monetary(
        string="Total E.O.S & Gratuity Payable", tracking=True)
    years_of_service = fields.Float(string="Years of Service", compute='_compute_service', store=True)
    gratuity_years = fields.Integer(string="Years")
    gratuity_months = fields.Integer(string="Months")
    gratuity_days = fields.Integer(string="Days")
    gratuity_for_years = fields.Monetary(string="Gratuity for Years")
    gratuity_for_months = fields.Monetary(string="Gratuity for Months")
    gratuity_for_days = fields.Monetary(string="Gratuity for Days")
    gratuity_formula_html = fields.Html(string="Gratuity Details", sanitize=False)

    # ------------------------------------------------------------------
    # Leave allowances
    # ------------------------------------------------------------------
    leave_calculation_method = fields.Char(string="Calculation Method (Leave)")
    leave_basic_salary = fields.Monetary(string="Basic Salary (Leave)")
    leave_adjust = fields.Boolean(string="Adjust Amount (Leave)")
    leave_amount_to_pay = fields.Monetary(string="Amount to Pay (Leave)")
    leave_salaries_eligible = fields.Integer(string="Total leave salaries eligible for")
    leave_salaries_balance = fields.Monetary(string="Total Leave Salaries over Balance months")
    leave_total_payable = fields.Monetary(string="Total Payable Amount for Leave Allowances")

    # ------------------------------------------------------------------
    # Final monthly salary (contract snapshot + attendance + salary)
    # ------------------------------------------------------------------
    fm_basic_salary = fields.Monetary(string="Basic Salary")
    fm_house_allowance = fields.Monetary(string="House Allowance")
    fm_travelling_allowance = fields.Monetary(string="Travelling Allowance")
    fm_other_allowance = fields.Monetary(string="Other Allowances")
    fm_gross_salary = fields.Monetary(string="Total Gross Salary")
    fm_rate_per_day = fields.Monetary(string="Rate per Day")
    fm_ot_rate_regular = fields.Float(string="OT rate on regular days")
    fm_ot_rate_off = fields.Float(string="OT rate on off/holidays")
    fm_total_days = fields.Integer(string="Total Days in this Month")
    total_absence_days = fields.Integer(string="Total Absence Days")
    total_ot_regular = fields.Float(string="Total OT hrs (Regular Days)")
    total_ot_off = fields.Float(string="Total OT hrs (Off Days)")
    fm_total_salary_month = fields.Monetary(string="Total Salary of this Month")
    fm_overtime_salary = fields.Monetary(string="Total Overtime Salary")
    fm_salary_adjustment = fields.Monetary(string="Salary Adjustment")
    fm_type_of_adjustment = fields.Char(string="Type of Adjustment")
    salary_html = fields.Html(string="Salary Details", sanitize=False)

    # ------------------------------------------------------------------
    # Settlement total (native records) + Studio link
    # ------------------------------------------------------------------
    other_adjustments = fields.Monetary(string="Other Adjustments")
    total_settlement = fields.Monetary(
        string="Total Settlement", compute='_compute_total', store=True,
    )
    studio_ref_id = fields.Integer(string="Studio Ref", index=True, copy=False)

    @api.depends('employee_id')
    def _compute_name(self):
        for rec in self:
            rec.name = self.env._("End of Service - %s") % (rec.employee_id.name or '')

    @api.depends('joining_date', 'last_day_of_duty')
    def _compute_service(self):
        for rec in self:
            if rec.joining_date and rec.last_day_of_duty and rec.last_day_of_duty >= rec.joining_date:
                rec.years_of_service = (rec.last_day_of_duty - rec.joining_date).days / 365.0
            else:
                rec.years_of_service = 0.0

    @api.depends('gratuity_amount', 'leave_total_payable', 'other_adjustments')
    def _compute_total(self):
        for rec in self:
            rec.total_settlement = (
                rec.gratuity_amount + rec.leave_total_payable + rec.other_adjustments)

    def action_check(self):
        self.write({'state': 'checked'})

    def action_in_payment(self):
        self.write({'state': 'in_payment'})

    def action_set_paid(self):
        self.write({'state': 'paid'})

    def action_reset(self):
        self.write({'state': 'new'})

    # ------------------------------------------------------------------
    # TEMPORARY live bridge with the Studio x_end_of_service master. Driven by
    # an automated action so no Python inherit of the Studio model is needed.
    # ------------------------------------------------------------------
    _STATUS_MAP = {
        'New': 'new', 'Checked & Verified': 'checked',
        'In Payments': 'in_payment', 'Paid': 'paid',
        'status1': 'new', 'status2': 'checked',
        'status3': 'in_payment', 'status4': 'paid',
    }

    @api.model
    def _sync_from_studio(self, studio_records):
        Eos = self.with_context(
            active_test=False, tracking_disable=True,
            mail_create_nolog=True, mail_create_nosubscribe=True)
        for src in studio_records:
            try:
                with self.env.cr.savepoint():
                    vals = Eos._studio_eos_vals(src)
                    if not vals:
                        continue
                    mirror = Eos.search([('studio_ref_id', '=', src.id)], limit=1)
                    if mirror:
                        mirror.write(vals)
                    else:
                        Eos.create(dict(vals, studio_ref_id=src.id))
            except Exception:
                _logger.exception(
                    "ssc_payroll: EOS mirror failed for x_end_of_service id=%s", src.id)
        return True

    @api.model
    def _resolve_studio_employee(self, src):
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
    def _studio_eos_vals(self, src):
        """Map an x_end_of_service record onto ssc.end.of.service. Returns None
        when the employee cannot be resolved (employee_id is required)."""
        employee = self._resolve_studio_employee(src)
        if not employee:
            return None
        adjustment = _get(src, 'x_studio_type_of_adjusment')
        return {
            'employee_id': employee.id,
            'total_amount': _get(src, 'x_studio_value') or 0.0,
            'profession': _get(src, 'x_studio_profession') or False,
            'nationality': _get(src, 'x_studio_nationality') or False,
            'date_of_request': _get(src, 'x_studio_date_of_request') or False,
            'eid_visa_expiry': _get(src, 'x_studio_eidvisa_expiry_date') or False,
            'last_day_of_duty': _get(src, 'x_studio_last_day_of_duty') or False,
            'received_payslip': bool(_get(src, 'x_studio_received_payslip')),
            'hr_review': _get(src, 'x_studio_hr_review') or False,
            'nor_document': _get(src, 'x_studio_nor') or False,
            'nor_document_name': _get(src, 'x_studio_nor_filename') or False,
            'state': self._STATUS_MAP.get(
                _get(src, 'x_studio_selection_field_2jh_1j2rrq7hv'), 'new'),
            # Gratuity
            'gratuity_calculation_method': _get(src, 'x_studio_calculation_method') or False,
            'gratuity_basic_salary': _get(src, 'x_studio_basic_salary') or 0.0,
            'gratuity_gross_salary': _get(src, 'x_studio_total_gross_salary') or 0.0,
            'gratuity_adjust': bool(_get(src, 'x_studio_adjust_amount')),
            'gratuity_amount': _get(src, 'x_studio_total_eos_gratiuty_payable_amount') or 0.0,
            'gratuity_years': _get(src, 'x_studio_years') or 0,
            'gratuity_months': _get(src, 'x_studio_months') or 0,
            'gratuity_days': _get(src, 'x_studio_days') or 0,
            'gratuity_for_years': _get(src, 'x_studio_gratuity_calculation_for_years') or 0.0,
            'gratuity_for_months': _get(src, 'x_studio_gratuity_calculation_for_months') or 0.0,
            'gratuity_for_days': _get(src, 'x_studio_gratuity_calculation_for_days') or 0.0,
            'gratuity_formula_html': _get(src, 'x_studio_eligible_html') or False,
            # Leave allowances
            'leave_calculation_method': _get(src, 'x_studio_calculation_method_1') or False,
            'leave_basic_salary': _get(src, 'x_studio_basic_salary_1') or 0.0,
            'leave_adjust': bool(_get(src, 'x_studio_adjust_amount_1')),
            'leave_amount_to_pay': _get(src, 'x_studio_amount_to_pay_1') or 0.0,
            'leave_salaries_eligible': _get(src, 'x_studio_total_leave_salaries_eligible_for') or 0,
            'leave_salaries_balance': _get(src, 'x_studio_total_leave_salaries_over_balance_months') or 0.0,
            'leave_total_payable': _get(src, 'x_studio_total_payable_amount_for_leave_allowances') or 0.0,
            # Final monthly salary
            'fm_basic_salary': _get(src, 'x_studio_basic_salary_2') or 0.0,
            'fm_house_allowance': _get(src, 'x_studio_house_allowance') or 0.0,
            'fm_travelling_allowance': _get(src, 'x_studio_travelling_allownce') or 0.0,
            'fm_other_allowance': _get(src, 'x_studio_other_allowances') or 0.0,
            'fm_gross_salary': _get(src, 'x_studio_total_gross_salary_2') or 0.0,
            'fm_rate_per_day': _get(src, 'x_studio_rate_per_day') or 0.0,
            'fm_ot_rate_regular': _get(src, 'x_studio_overtime_hrs_rate_on_regular_days') or 0.0,
            'fm_ot_rate_off': _get(src, 'x_studio_overtime_hrs_rate_on_offholidays') or 0.0,
            'fm_total_days': _get(src, 'x_studio_total_days_in_this_month') or 0,
            'total_absence_days': _get(src, 'x_studio_total_absence_days') or 0,
            'total_ot_regular': _get(src, 'x_studio_total_overtime_hrs_this_month_on_regular_days') or 0.0,
            'total_ot_off': _get(src, 'x_studio_total_overtime_hrs_this_month_on_off_days') or 0.0,
            'fm_total_salary_month': _get(src, 'x_studio_total_salary_of_this_month') or 0.0,
            'fm_overtime_salary': _get(src, 'x_studio_total_overtime_salary') or 0.0,
            'fm_salary_adjustment': _get(src, 'x_studio_salary_adjusment') or 0.0,
            'fm_type_of_adjustment': adjustment.display_name if adjustment else False,
            'salary_html': _get(src, 'x_studio_salary_html') or False,
        }


class SscOnHold(models.Model):
    _name = 'ssc.on.hold'
    _description = "On-Hold Amount"
    _inherit = ['mail.thread']
    _order = 'id desc'

    name = fields.Char(string="Description", required=True)
    employee_id = fields.Many2one('ssc.employee', string="Employee", required=True, tracking=True)
    company_id = fields.Many2one(related='employee_id.company_id', store=True, index=True)
    currency_id = fields.Many2one(related='employee_id.currency_id', store=True)
    amount = fields.Monetary(string="Held Amount", tracking=True)
    reason = fields.Char(string="Reason")
    # Leave allowance this amount was held from.
    allowance_id = fields.Many2one('ssc.leave.expense', string="Allowance link")
    attach = fields.Boolean(string="Attach with Salary")
    state = fields.Selection(
        [('held', 'Created'), ('released', 'Paid')],
        string="Status", default='held', tracking=True, index=True,
    )
    studio_ref_id = fields.Integer(string="Studio Ref", index=True, copy=False)

    def action_release(self):
        self.write({'state': 'released'})

    def action_hold(self):
        self.write({'state': 'held'})

    # ------------------------------------------------------------------
    # TEMPORARY live bridge with the Studio x_on_hold_amounts master.
    # ------------------------------------------------------------------
    _STATUS_MAP = {
        'Created': 'held', 'Paid': 'released',
        'status1': 'held', 'status2': 'released',
    }

    @api.model
    def _sync_from_studio(self, studio_records):
        Hold = self.with_context(
            active_test=False, tracking_disable=True,
            mail_create_nolog=True, mail_create_nosubscribe=True)
        for src in studio_records:
            try:
                with self.env.cr.savepoint():
                    vals = Hold._studio_hold_vals(src)
                    if not vals:
                        continue
                    mirror = Hold.search([('studio_ref_id', '=', src.id)], limit=1)
                    if mirror:
                        mirror.write(vals)
                    else:
                        Hold.create(dict(vals, studio_ref_id=src.id))
            except Exception:
                _logger.exception(
                    "ssc_payroll: on-hold mirror failed for x_on_hold_amounts id=%s", src.id)
        return True

    @api.model
    def _studio_hold_vals(self, src):
        """Map an x_on_hold_amounts record onto ssc.on.hold. Returns None when
        the employee cannot be resolved (employee_id is required)."""
        studio_emp = _get(src, 'x_studio_employee')
        employee = (self.env['ssc.attachment']._resolve_studio_employee(studio_emp)
                    if studio_emp else self.env['ssc.employee'].browse())
        if not employee:
            return None
        # Link back to the mirrored leave allowance, matched by its Studio ref.
        link = _get(src, 'x_studio_allowance_link')
        allowance = self.env['ssc.leave.expense'].search(
            [('studio_ref_id', '=', link.id)], limit=1) if link else False
        return {
            'name': _get(src, 'x_name') or self.env._("On-Hold Amount"),
            'employee_id': employee.id,
            'amount': _get(src, 'x_studio_value') or 0.0,
            'attach': bool(_get(src, 'x_studio_attach')),
            'allowance_id': allowance.id if allowance else False,
            'state': self._STATUS_MAP.get(
                _get(src, 'x_studio_selection_field_21f_1ium554ao'), 'held'),
        }
