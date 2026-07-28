# -*- coding: utf-8 -*-
import calendar
from datetime import date, timedelta

from odoo import api, fields, models
from odoo.exceptions import UserError

from .utils import studio_get as _get

# Pipeline status of an on-going construction project on x_projects_list.
ONGOING_PROJECT_STATE = 'status3'
PROJECT_STATE_FIELD = 'x_studio_selection_field_841_1ifp8eo32'


class SscStaffAttendance(models.Model):
    _name = 'ssc.staff.attendance'
    _description = "Staff Attendance (Monthly)"
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'year desc, month desc, id desc'

    name = fields.Char(string="Reference", compute='_compute_name', store=True, readonly=False)
    company_id = fields.Many2one(
        'res.company', string="Company", required=True, tracking=True,
        default=lambda self: self.env.company,
    )
    month = fields.Selection(
        [(str(i), calendar.month_name[i]) for i in range(1, 13)],
        string="Month", required=True, tracking=True,
    )
    year = fields.Integer(string="Year", required=True, tracking=True,
                          default=lambda self: fields.Date.context_today(self).year)
    state = fields.Selection(
        [('created', 'Created'), ('generated', 'Generated'),
         ('submitted', 'Submitted'), ('approved', 'Approved')],
        string="Status", default='created', tracking=True, index=True,
    )
    # Periods the daily attendance was pulled for (set by the Generate wizard).
    # Current period (base) + previous-month period (whose absences are settled
    # as a deduction attachment).
    start_date = fields.Date(string="From Date", readonly=True)
    last_date = fields.Date(string="To Date", readonly=True)
    last_month_start = fields.Date(string="Last Month From", readonly=True)
    last_month_end = fields.Date(string="Last Month To", readonly=True)
    line_ids = fields.One2many('ssc.staff.attendance.line', 'staff_attendance_id', string="Employees")

    @api.depends('company_id', 'month', 'year')
    def _compute_name(self):
        for rec in self:
            code = rec.company_id.ssc_company_code or (rec.company_id.name or '')[:3].upper()
            month_name = calendar.month_name[int(rec.month)] if rec.month else ''
            rec.name = "%s Staff Attendance - %s - %s" % (code, month_name, rec.year or '')

    def _days_in_month(self):
        self.ensure_one()
        return calendar.monthrange(self.year, int(self.month))[1]

    def _get_ongoing_projects(self):
        """On-going construction projects, used when a staff member has no
        explicit project distribution."""
        Project = self.env['x_projects_list']
        if PROJECT_STATE_FIELD not in Project._fields:
            return Project.browse()
        return Project.search([
            (PROJECT_STATE_FIELD, '=', ONGOING_PROJECT_STATE),
            ('x_active', '=', True),
        ])

    def _prev_month(self):
        """(month, year) of the month before the sheet's month."""
        self.ensure_one()
        m, y = int(self.month), self.year
        return (12, y - 1) if m == 1 else (m - 1, y)

    def _period_start(self):
        self.ensure_one()
        return self.start_date or date(self.year, int(self.month), 1)

    def _period_end(self):
        self.ensure_one()
        # Default: the first 20 days of the month; editable in the wizard.
        return self.last_date or date(self.year, int(self.month),
                                      min(20, self._days_in_month()))

    def _last_month_start(self):
        self.ensure_one()
        pm, py = self._prev_month()
        return self.last_month_start or date(py, pm, 21)

    def _last_month_end(self):
        self.ensure_one()
        pm, py = self._prev_month()
        return self.last_month_end or date(py, pm, calendar.monthrange(py, pm)[1])

    def action_generate(self):
        """Open the period wizard; the actual generation runs from there so the
        user can choose which ranges the daily attendance is pulled for."""
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': self.env._("Select Attendance Period"),
            'res_model': 'ssc.staff.attendance.generate.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {
                'default_staff_attendance_id': self.id,
                'default_start_date': self._period_start(),
                'default_last_date': self._period_end(),
                'default_last_month_start': self._last_month_start(),
                'default_last_month_end': self._last_month_end(),
            },
        }

    def action_submit(self):
        for rec in self:
            pending = rec.line_ids.filtered(lambda l: l.advance_state == 'pending')
            if pending:
                raise UserError(self.env._(
                    "Approve or reject the advance for every employee before "
                    "submitting:\n\n%s",
                    "\n".join("- %s" % l.employee_id.display_name for l in pending)))
        self.write({'state': 'submitted'})

    def action_approve(self):
        self.write({'state': 'approved'})

    def action_reset(self):
        self.write({'state': 'created'})

    def _get_staff_employees(self):
        """Staff/engineers of the company. TEMPORARY: sourced from the Studio
        x_employeeslist (Engineer/Office staff, not arbab, not cancelled),
        mapped onto ssc.employee. Falls back to the is_engineer_office flag."""
        self.ensure_one()
        Emp = self.env['ssc.employee']
        Studio = self.env.get('x_employeeslist')
        if Studio is None:
            return Emp.search([('is_engineer_office', '=', True),
                               ('company_id', '=', self.company_id.id)])
        src = Studio.with_context(active_test=False).search([
            ('x_studio_company', '=', self.company_id.id),
            ('x_studio_engineeroffice_staff', '=', True),
            ('x_studio_arbab', '!=', True),
            ('x_studio_cancelled', '!=', True),
        ])
        result = Emp.browse()
        for s in src:
            for local, studio in (('employee_code', 'x_studio_employee_id'),
                                  ('attendance_code', 'x_studio_attendance_id'),
                                  ('name', 'x_name')):
                val = _get(s, studio)
                if val:
                    emp = Emp.with_context(active_test=False).search(
                        [(local, '=', val)], limit=1)
                    if emp:
                        result |= emp
                        break
        return result

    def _generate_lines(self, start_date=None, last_date=None,
                        last_month_start=None, last_month_end=None):
        self.ensure_one()
        start_date = start_date or self._period_start()
        last_date = last_date or self._period_end()
        last_month_start = last_month_start or self._last_month_start()
        last_month_end = last_month_end or self._last_month_end()
        self.line_ids.unlink()
        employees = self._get_staff_employees()
        ongoing = self._get_ongoing_projects()
        # Days remaining after the period to complete the salary month, paid in
        # advance once approved. Dynamic: 20th of a 31-day month advances 11.
        advance = max(self._days_in_month() - last_date.day, 0)

        vals_list = []
        for emp in employees:
            day_cmds, attended, absence = self._pull_attendance(emp, start_date, last_date)
            # Previous-month absences settle as a deduction attachment; its days
            # are shown in the table alongside the current period.
            lm_day_cmds, _, last_absence = self._pull_attendance(
                emp, last_month_start, last_month_end)
            distribution = self._build_distribution(emp, ongoing)
            vals_list.append((0, 0, {
                'employee_id': emp.id,
                'last_month_absence': last_absence,
                'attended_days': attended,
                'advance_days': advance,
                'total_absence_days': absence,
                'advance_state': 'pending',
                'distribution_ids': distribution,
                'day_ids': lm_day_cmds + day_cmds,
            }))
        self.line_ids = vals_list

    def _pull_attendance(self, employee, start_date, last_date):
        """Snapshot the employee's daily biometric attendance from the
        ssc_attendance module for the period. Returns (day commands, attended
        days, absence days). Attended/absence exclude off days (Friday); those
        are shown in the table but counted as neither.

        Read dynamically so this module keeps no hard dependency on
        ssc_attendance.
        """
        self.ensure_one()
        Line = self.env.get('ssc.attendance.line')
        if Line is None:
            return [], 0, 0

        # Match the biometric line to our employee: its employee_id points at
        # the Studio master our ssc.employee mirrors, with the badge as backup.
        match = []
        if employee.studio_ref_id:
            match.append(('employee_id', '=', employee.studio_ref_id))
        if employee.attendance_code:
            match.append(('attendance_id', '=', employee.attendance_code))
        if not match:
            return [], 0, 0
        if len(match) == 2:
            match = ['|'] + match
        # Read a little past the period so a Friday at the edge can still see
        # its Thursday / Saturday / Sunday neighbours.
        lines = Line.search(
            [('date', '>=', start_date - timedelta(days=2)),
             ('date', '<=', last_date + timedelta(days=2))] + match,
            order='date')

        # Per-day status, used to test the Friday bridge below.
        by_date = {}
        for l in lines:
            if not l.date:
                continue
            if l.attendance_type == 'Off Day':
                by_date[l.date] = 'off'
            elif l.absent:
                by_date[l.date] = 'absent'
            else:
                by_date[l.date] = 'present'

        def _present(d):
            return by_date.get(d) == 'present'

        # Staff are monthly-salaried, so only an absence on a counted day
        # reduces the paid attendance. The weekly off day (Friday) is paid ONLY
        # when it is bridged: Thursday (the day before) attended AND either
        # Saturday or Sunday attended; otherwise the Friday counts as absence.
        day_cmds, absence = [], 0
        for l in lines:
            if not l.date or not (start_date <= l.date <= last_date):
                continue  # neighbour-only line, outside the period
            off = l.attendance_type == 'Off Day'
            if off:
                bridged = _present(l.date - timedelta(days=1)) and (
                    _present(l.date + timedelta(days=1))
                    or _present(l.date + timedelta(days=2)))
                status = 'Off Day' if bridged else 'Absent'
                if not bridged:
                    absence += 1
            else:
                status = 'Absent' if l.absent else 'Present'
                if l.absent:
                    absence += 1
            day_cmds.append((0, 0, {
                'date': l.date,
                'day_name': l.date.strftime('%A'),
                'status': status,
                'check_in': l.first_punch,
                'check_out': l.last_punch,
                'project_id': l.project_id.id or False,
                'is_off_day': off,
            }))
        period_days = (last_date - start_date).days + 1
        attended = max(period_days - absence, 0)
        return day_cmds, attended, absence

    def _build_distribution(self, employee, ongoing_projects):
        """Explicit staff distribution if present, otherwise an equal split
        over all on-going construction projects."""
        commands = []
        if employee.staff_project_ids:
            for sp in employee.staff_project_ids:
                commands.append((0, 0, {
                    'project_id': sp.project_id.id,
                    'percentage': sp.percentage,
                }))
        elif ongoing_projects:
            share = round(100.0 / len(ongoing_projects), 2)
            for project in ongoing_projects:
                commands.append((0, 0, {
                    'project_id': project.id,
                    'percentage': share,
                }))
        return commands


class SscStaffAttendanceLine(models.Model):
    _name = 'ssc.staff.attendance.line'
    _description = "Staff Attendance Line"
    _order = 'staff_attendance_id, employee_id'

    staff_attendance_id = fields.Many2one(
        'ssc.staff.attendance', string="Staff Attendance",
        required=True, ondelete='cascade', index=True,
    )
    employee_id = fields.Many2one('ssc.employee', string="Employee", required=True, index=True)
    employee_code = fields.Char(related='employee_id.employee_code', string="Employee ID", store=True)
    job_position = fields.Char(related='employee_id.job_position', string="Position", store=True)
    company_id = fields.Many2one(related='staff_attendance_id.company_id', store=True)
    currency_id = fields.Many2one(related='employee_id.currency_id', store=True)

    # Salary snapshot (kept editable to allow per-month overrides).
    basic_salary = fields.Monetary(related='employee_id.basic_salary', readonly=False)
    house_allowance = fields.Monetary(related='employee_id.house_allowance', readonly=False)
    transport_allowance = fields.Monetary(related='employee_id.transport_allowance', readonly=False)
    other_allowance = fields.Monetary(related='employee_id.other_allowance', readonly=False)
    gross_salary = fields.Monetary(related='employee_id.gross_salary')

    # Days present inside the selected period, and the days that complete the
    # month (paid in advance once approved).
    attended_days = fields.Integer(string="Attended Days (Period)")
    advance_days = fields.Integer(string="Advance Days")
    total_absence_days = fields.Integer(string="Total Absence Days")
    # Absences during the previous-month period, deducted as an attachment.
    last_month_absence = fields.Integer(string="Absence (Last Month)")
    advance_state = fields.Selection(
        [('pending', 'Pending'), ('approved', 'Approved'), ('rejected', 'Rejected')],
        string="Advance Decision", default='pending', copy=False,
    )
    advance_granted = fields.Boolean(
        string="Advance Granted", compute='_compute_advance_granted', store=True)
    total_attended_days = fields.Integer(
        string="Total Attended Days", compute='_compute_total_attended', store=True)

    distribution_ids = fields.One2many(
        'ssc.staff.attendance.distribution', 'line_id', string="Projects Distribution",
    )
    day_ids = fields.One2many(
        'ssc.staff.attendance.day', 'line_id', string="Daily Attendance",
    )

    @api.depends('advance_state')
    def _compute_advance_granted(self):
        for rec in self:
            rec.advance_granted = rec.advance_state == 'approved'

    @api.depends('attended_days', 'advance_days', 'advance_granted')
    def _compute_total_attended(self):
        for rec in self:
            rec.total_attended_days = rec.attended_days + (
                rec.advance_days if rec.advance_granted else 0)

    def action_approve_advance(self):
        self.write({'advance_state': 'approved'})
        return True

    def action_reject_advance(self):
        self.write({'advance_state': 'rejected'})
        return True

    def _sync_last_month_absence_deduction(self):
        """Keep the last-month absence deduction attachment in step with
        ``last_month_absence``: value = days x (gross / days-in-previous-month),
        so June (30) divides by 30 and July (31) by 31.

        The attachment is created once a payslip exists and updated in place
        afterwards - fixing the day count never needs a delete/recreate. A paid
        deduction is never touched."""
        self.ensure_one()
        Att = self.env['ssc.attachment']
        att = Att.search([('staff_line_id', '=', self.id)], limit=1)
        if att and att.state == 'paid':
            return
        sa = self.staff_attendance_id
        pm, py = sa._prev_month()
        prev_days = calendar.monthrange(py, pm)[1]
        days = self.last_month_absence or 0
        value = days * (self.gross_salary / prev_days) if (days and prev_days) else 0.0
        name = self.env._("Deduction for last month - %s absence day(s)", days)
        if att:
            att.write({'value': value, 'name': name})
            return
        if days <= 0:
            return
        payslip = self.env['ssc.payslip'].search([
            ('staff_attendance_id', '=', sa.id),
            ('employee_id', '=', self.employee_id.id),
        ], limit=1)
        if not payslip:
            return
        Att.create({
            'name': name,
            'employee_id': self.employee_id.id,
            'type_id': self.env.ref('ssc_payroll.attachment_type_salary_deduction').id,
            'month': payslip.month,
            'year': payslip.year,
            'value': value,
            'state': 'attached',
            'payslip_id': payslip.id,
            'staff_line_id': self.id,
        })

    def write(self, vals):
        res = super().write(vals)
        if 'last_month_absence' in vals:
            for line in self:
                line._sync_last_month_absence_deduction()
        return res


class SscStaffAttendanceDistribution(models.Model):
    _name = 'ssc.staff.attendance.distribution'
    _description = "Staff Attendance Project Distribution"
    _order = 'line_id, id'

    line_id = fields.Many2one(
        'ssc.staff.attendance.line', string="Staff Line",
        required=True, ondelete='cascade', index=True,
    )
    project_id = fields.Many2one('x_projects_list', string="Project", required=True)
    percentage = fields.Float(string="Percentage")


class SscStaffAttendanceDay(models.Model):
    _name = 'ssc.staff.attendance.day'
    _description = "Staff Daily Attendance"
    _order = 'line_id, date'

    line_id = fields.Many2one(
        'ssc.staff.attendance.line', string="Staff Line",
        required=True, ondelete='cascade', index=True,
    )
    date = fields.Date(string="Date")
    day_name = fields.Char(string="Day")
    status = fields.Char(string="Status")
    check_in = fields.Datetime(string="Check In")
    check_out = fields.Datetime(string="Check Out")
    project_id = fields.Many2one('x_projects_list', string="Project")
    is_off_day = fields.Boolean(string="Off Day")


class SscStaffAttendanceGenerateWizard(models.TransientModel):
    _name = 'ssc.staff.attendance.generate.wizard'
    _description = "Generate Staff Attendance"

    staff_attendance_id = fields.Many2one(
        'ssc.staff.attendance', string="Staff Attendance", required=True, ondelete='cascade')
    start_date = fields.Date(string="From Date", required=True)
    last_date = fields.Date(string="To Date", required=True)
    # Not DB-required: the column is added to an existing transient table that
    # may already hold rows, so a NOT NULL constraint would fail to apply on
    # upgrade. The view marks them required and action_generate validates them.
    last_month_start = fields.Date(string="Last Month From")
    last_month_end = fields.Date(string="Last Month To")

    def action_generate(self):
        self.ensure_one()
        if not (self.last_month_start and self.last_month_end):
            raise UserError(self.env._("Set both Last Month dates."))
        if self.start_date > self.last_date:
            raise UserError(self.env._("From Date must be before To Date."))
        if self.last_month_start > self.last_month_end:
            raise UserError(self.env._("Last Month From must be before Last Month To."))
        sheet = self.staff_attendance_id
        sheet._generate_lines(self.start_date, self.last_date,
                              self.last_month_start, self.last_month_end)
        sheet.write({
            'start_date': self.start_date,
            'last_date': self.last_date,
            'last_month_start': self.last_month_start,
            'last_month_end': self.last_month_end,
            'state': 'generated',
        })
        return {'type': 'ir.actions.act_window_close'}
