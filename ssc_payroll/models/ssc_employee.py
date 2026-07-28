# -*- coding: utf-8 -*-
import logging

from odoo import api, fields, models

from .utils import studio_get as _get

_logger = logging.getLogger(__name__)

# Studio "Visa's Company" selection value -> ssc.employee.visa_company key.
# Adapt both this map and the ``visa_company`` selection below to the companies
# of the group the module is deployed for.
VISA_COMPANY_MAP = {
    'Company A': 'company_a',
    'Company B': 'company_b',
    'Company C': 'company_c',
    'Company D': 'company_d',
}


class SscEmployee(models.Model):
    _name = 'ssc.employee'
    _description = "Payroll Employee"
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'name'

    # ------------------------------------------------------------------
    # Identity
    # ------------------------------------------------------------------
    name = fields.Char(string="Name", required=True, tracking=True)
    active = fields.Boolean(string="Active", default=True)
    # Live link to the Studio x_employeeslist master this record mirrors.
    studio_ref_id = fields.Integer(string="Studio Ref", index=True, copy=False)
    # Company/WPS employee code, e.g. ACME-384. Its presence marks the
    # employee as paid through WPS; its absence means the employee is paid CASH.
    employee_code = fields.Char(string="Employee ID", tracking=True, index=True)
    # Attendance identifier used on the labour attendance sheets, e.g. ACM-600.
    attendance_code = fields.Char(string="Attendance ID", index=True)
    # Odoo HR employee matched by Badge ID (attendance_code -> barcode).
    hr_employee_id = fields.Many2one(
        'hr.employee', string="HR Employee", index=True, copy=False)
    company_id = fields.Many2one(
        'res.company', string="Company", required=True, tracking=True,
        default=lambda self: self.env.company,
    )
    currency_id = fields.Many2one(
        'res.currency', related='company_id.currency_id',
        string="Currency", store=True, readonly=True,
    )
    job_position = fields.Char(string="Designation")
    nationality = fields.Char(string="Nationality")
    date_of_birth = fields.Date(string="Date of Birth")
    phone = fields.Char(string="Phone")

    # ------------------------------------------------------------------
    # Classification
    # ------------------------------------------------------------------
    # Engineer/Office staff are the ones handled by the monthly staff
    # attendance sheet; everyone else (including plain "Staff") is labour.
    is_engineer_office = fields.Boolean(
        string="Engineer / Office Staff", tracking=True,
        help="Engineer/Office staff use the monthly staff attendance sheet and "
             "project distribution by percentage; everybody else is labour.",
    )
    is_staff = fields.Boolean(
        string="Staff", tracking=True,
        help="Studio 'Staff' flag. Informational only - it does not move the "
             "employee out of the labour attendance sheet.",
    )
    overtime_eligible = fields.Boolean(
        string="Overtime Eligible",
        help="Whether overtime hours are paid for this employee.",
    )
    # Any of these withholds the end-of-month advance.
    is_cancelled = fields.Boolean(string="Cancelled")
    approved_nor = fields.Boolean(string="Approved NOR")
    on_leave = fields.Boolean(string="On Leave")
    visa_company = fields.Selection(
        [
            ('company_a', 'Company A'),
            ('company_b', 'Company B'),
            ('company_c', 'Company C'),
            ('company_d', 'Company D'),
        ],
        string="Visa Company",
        help="Company sponsoring the employee's visa. Drives WPS vs CASH "
             "routing of the salary.",
    )

    # ------------------------------------------------------------------
    # Salary structure
    # ------------------------------------------------------------------
    basic_salary = fields.Monetary(string="Basic Salary", tracking=True)
    house_allowance = fields.Monetary(string="House Allowance")
    transport_allowance = fields.Monetary(string="Travelling Allowance")
    other_allowance = fields.Monetary(string="Other Allowance")
    gross_salary = fields.Monetary(
        string="Total / Gross Salary",
        compute='_compute_gross_salary', store=True, tracking=True,
        help="Basic + House + Travelling + Other allowances.",
    )
    rate_per_day = fields.Monetary(
        string="Rate per Day (30d)",
        compute='_compute_rate_per_day', store=True,
        help="Indicative daily rate (gross / 30). The payslip recomputes the "
             "rate using the actual number of days in its period.",
    )

    # Overtime hourly rates
    ot_rate_regular = fields.Float(string="OT Rate - Regular Days")
    ot_rate_off = fields.Float(string="OT Rate - Off / Holidays")

    # Monthly allowances / benefits
    phone_bill_allowance = fields.Boolean(string="Phone Bill Allowance")
    agreed_monthly_allowance = fields.Monetary(
        string="Agreed Monthly Allowance",
        help="Fixed monthly mobile/phone allowance added to the payslip.",
    )

    # True for a user who may see the employee but not the salary of
    # engineer/office staff (the SSC HR Admin group). Drives the view: the
    # salary fields are hidden, and ssc.employee.read() strips their values.
    hide_salary = fields.Boolean(
        string="Salary Hidden", compute='_compute_hide_salary')

    # ------------------------------------------------------------------
    # Contract dates / status
    # ------------------------------------------------------------------
    joining_date = fields.Date(string="Joining Date", tracking=True)
    submitted_cancellation = fields.Boolean(string="Submitted Cancellation")
    last_day = fields.Date(string="Last Day")
    end_of_service_date = fields.Date(string="End of Service Date")

    # ------------------------------------------------------------------
    # Staff project distribution
    # ------------------------------------------------------------------
    staff_project_ids = fields.One2many(
        'ssc.staff.project', 'employee_id', string="Staff Project Distribution",
    )

    _sql_constraints = [
        ('employee_code_company_uniq',
         'unique(employee_code, company_id)',
         "The Employee ID must be unique per company."),
    ]

    @api.depends('basic_salary', 'house_allowance',
                 'transport_allowance', 'other_allowance')
    def _compute_gross_salary(self):
        for emp in self:
            emp.gross_salary = (
                emp.basic_salary + emp.house_allowance
                + emp.transport_allowance + emp.other_allowance
            )

    @api.depends('gross_salary')
    def _compute_rate_per_day(self):
        for emp in self:
            emp.rate_per_day = emp.gross_salary / 30.0 if emp.gross_salary else 0.0

    # ------------------------------------------------------------------
    # Salary confidentiality for the SSC HR Admin group
    # ------------------------------------------------------------------
    # Money fields hidden from - and stripped for - the restricted group when
    # the employee is engineer/office staff.
    _SALARY_FIELDS = (
        'basic_salary', 'house_allowance', 'transport_allowance',
        'other_allowance', 'gross_salary', 'rate_per_day',
        'ot_rate_regular', 'ot_rate_off', 'agreed_monthly_allowance',
    )

    def _salary_restricted_user(self):
        """True when the current user must not see the salary of engineer/office
        staff.

        The trigger is membership of the SSC Attendance 'HR Admin' group - it
        applies even to a user who is also in the labour attendance/payroll
        groups (that overlap is exactly the case we must catch). Only the Staff
        Payroll groups are exempt, because they legitimately process
        engineer/office pay and payroll generation must read their real salary;
        the superuser and administrators are exempt too.

        The group lives in ssc_attendance (which depends on this module), so it
        is resolved at runtime rather than declared, to avoid a circular
        dependency."""
        env = self.env
        if env.su or env.is_admin():
            return False
        hr_admin = env.ref(
            'ssc_attendance.group_ssc_hr_admin', raise_if_not_found=False)
        if not hr_admin or hr_admin not in env.user.groups_id:
            return False
        staff_payroll_groups = (
            'ssc_payroll.group_staff_payroll',
            'ssc_payroll.group_staff_payroll_manager',
        )
        return not any(env.user.has_group(g) for g in staff_payroll_groups)

    @api.depends_context('uid')
    def _compute_hide_salary(self):
        restricted = self._salary_restricted_user()
        for emp in self:
            emp.hide_salary = bool(restricted and emp.is_engineer_office)

    def read(self, fields=None, load='_classic_read'):
        records = super().read(fields=fields, load=load)
        if not self._salary_restricted_user():
            return records
        # Strip the salary of engineer/office staff from the result. Read with
        # sudo so the flag itself is never masked, and never recurse (sudo is a
        # superuser context, so _salary_restricted_user is False there).
        eng_ids = set(self.sudo().filtered('is_engineer_office').ids)
        for row in records:
            if row.get('id') in eng_ids:
                for fname in self._SALARY_FIELDS:
                    if fname in row:
                        row[fname] = 0.0
        return records

    @api.depends('name', 'employee_code')
    def _compute_display_name(self):
        for emp in self:
            if emp.employee_code:
                emp.display_name = "%s - %s" % (emp.employee_code, emp.name)
            else:
                emp.display_name = emp.name or ""

    # ------------------------------------------------------------------
    # One-way sync onto the Odoo hr.employee, matched by Badge ID (barcode).
    # Existing hr.employees are updated; none are created, and records with no
    # badge or no match are left alone. Called in batch (after the Studio mirror
    # and by the resync/reconcile helpers) rather than from a create/write
    # override, which would fire on every internal write and slow bulk syncs.
    # ------------------------------------------------------------------
    def _sync_to_hr(self):
        HrEmployee = self.env.get('hr.employee')
        if HrEmployee is None:
            return
        HrEmployee = HrEmployee.sudo()
        for emp in self:
            try:
                badge = (emp.attendance_code or '').strip()
                if not badge:
                    continue
                hr = emp.hr_employee_id
                if not hr or hr.barcode != badge:
                    hr = HrEmployee.with_context(active_test=False).search(
                        [('barcode', '=', badge)], limit=1)
                if not hr:
                    continue
                vals = {
                    'name': emp.name,
                    'job_title': emp.job_position or False,
                    'work_phone': emp.phone or False,
                    'birthday': emp.date_of_birth or False,
                }
                changes = {k: v for k, v in vals.items() if hr[k] != v}
                if changes:
                    hr.write(changes)
                if emp.hr_employee_id.id != hr.id:
                    emp.hr_employee_id = hr.id
            except Exception:
                _logger.exception(
                    "ssc_payroll: hr.employee sync failed for ssc.employee id=%s", emp.id)

    # ------------------------------------------------------------------
    # TEMPORARY live bridge with the Studio x_employeeslist master.
    # Driven by an automated action (see hooks) so no Python inherit of the
    # Studio model is needed - that would break databases without Studio.
    # ------------------------------------------------------------------
    @api.model
    def _sync_from_studio(self, studio_records):
        """Mirror the given x_employeeslist records onto ssc.employee."""
        Emp = self.with_context(
            active_test=False, tracking_disable=True,
            mail_create_nolog=True, mail_create_nosubscribe=True)
        synced = Emp.browse()
        for src in studio_records:
            try:
                # Savepoint: one bad record must not abort the transaction.
                with self.env.cr.savepoint():
                    vals = Emp._studio_employee_vals(src)
                    mirror = Emp.search([('studio_ref_id', '=', src.id)], limit=1)
                    if not mirror and vals.get('employee_code'):
                        mirror = Emp.search([('employee_code', '=', vals['employee_code'])], limit=1)
                    if not mirror and vals.get('attendance_code'):
                        mirror = Emp.search([('attendance_code', '=', vals['attendance_code'])], limit=1)
                    if mirror:
                        mirror.write(dict(vals, studio_ref_id=src.id))
                    else:
                        mirror = Emp.create(dict(vals, studio_ref_id=src.id))
                    synced |= mirror
            except Exception:
                _logger.exception(
                    "ssc_payroll: employee mirror failed for x_employeeslist id=%s", src.id)
        # Push the freshly mirrored employees onto their hr.employee once.
        try:
            synced._sync_to_hr()
        except Exception:
            _logger.exception("ssc_payroll: hr sync after Studio mirror failed.")
        return True

    @api.model
    def _studio_employee_vals(self, src):
        company = _get(src, 'x_studio_company') or self.env.company
        visa = _get(src, 'x_studio_visas_company')
        return {
            'name': _get(src, 'x_name') or "Unnamed",
            'active': bool(_get(src, 'x_active', True)),
            'employee_code': _get(src, 'x_studio_employee_id') or False,
            'attendance_code': _get(src, 'x_studio_attendance_id') or False,
            'company_id': company.id,
            'job_position': _get(src, 'x_studio_profession') or False,
            'nationality': _get(src, 'x_studio_nationality') or False,
            'date_of_birth': _get(src, 'x_studio_date_of_birth') or False,
            'phone': _get(src, 'x_studio_phone_no') or False,
            'is_staff': bool(_get(src, 'x_studio_staff')),
            'is_engineer_office': bool(_get(src, 'x_studio_engineeroffice_staff')),
            'overtime_eligible': bool(_get(src, 'x_studio_overtime_contract')),
            'is_cancelled': bool(_get(src, 'x_studio_cancelled')),
            'approved_nor': bool(_get(src, 'x_studio_approved_nor')),
            'on_leave': bool(_get(src, 'x_studio_on_leave')),
            'visa_company': VISA_COMPANY_MAP.get(visa, False),
            'basic_salary': _get(src, 'x_studio_basic_salary') or 0.0,
            'house_allowance': _get(src, 'x_studio_house_allowance') or 0.0,
            'transport_allowance': _get(src, 'x_studio_transportation_allowance') or 0.0,
            'other_allowance': _get(src, 'x_studio_other_allowance') or 0.0,
            'ot_rate_regular': _get(src, 'x_studio_overtime_hrs_rate_on_regular_days') or 0.0,
            'ot_rate_off': _get(src, 'x_studio_overtime_hrs_rate_on_offholidays') or 0.0,
            'phone_bill_allowance': bool(_get(src, 'x_studio_phone_bill_allowance')),
            'agreed_monthly_allowance': _get(src, 'x_studio_agreed_monthly_allowwance') or 0.0,
            'joining_date': _get(src, 'x_studio_joining_date') or False,
            'submitted_cancellation': bool(_get(src, 'x_studio_submitted_cancellation')),
            'last_day': _get(src, 'x_studio_last_day') or False,
            'end_of_service_date': _get(src, 'x_studio_end_of_service') or False,
        }
