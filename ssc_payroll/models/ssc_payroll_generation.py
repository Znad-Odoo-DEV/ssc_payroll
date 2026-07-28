# -*- coding: utf-8 -*-
import calendar
from datetime import date

from odoo import api, fields, models
from odoo.exceptions import UserError
from odoo.tools import html_escape

from .ssc_attendance_sheet import ADVANCE_DEDICATION_RATIO, ADVANCE_REVIEW_RATIO
from .utils import MONTH_ABBR, studio_get as _get


class SscAttendanceSheetGeneration(models.Model):
    """Payroll generation from a labour attendance sheet."""
    _inherit = 'ssc.attendance.sheet'

    payslip_ids = fields.One2many('ssc.payslip', 'attendance_sheet_id', string="Payslips")
    payslip_count = fields.Integer(compute='_compute_payslip_count')
    batch_ids = fields.One2many(
        'ssc.salary.batch', 'attendance_sheet_id', string="Salary Batches")

    @api.depends('payslip_ids')
    def _compute_payslip_count(self):
        for sheet in self:
            sheet.payslip_count = len(sheet.payslip_ids)

    # ------------------------------------------------------------------
    def action_generate_payroll(self):
        for sheet in self:
            if sheet.state != 'approved':
                raise UserError(self.env._(
                    "The attendance sheet must be approved before generating payroll."))
            sheet._generate_payroll()
            # The distribution is a pure roll-up of the payslips just created,
            # so there is nothing for the user to trigger by hand.
            sheet.batch_ids.action_compute_project_distribution()
        return True

    def _generate_payroll(self):
        self.ensure_one()
        mon = self.last_date.month
        year = self.last_date.year
        month_abbr = MONTH_ABBR[mon]
        days = calendar.monthrange(year, mon)[1]
        # Rate basis for last-month absence deductions = days in that month.
        if self.last_month_end:
            prev_days = calendar.monthrange(self.last_month_end.year, self.last_month_end.month)[1]
        else:
            prev_days = (calendar.monthrange(year - 1, 12)[1] if mon == 1
                         else calendar.monthrange(year, mon - 1)[1])
        period_from = date(year, mon, 1)
        period_to = date(year, mon, days)

        Payslip = self.env['ssc.payslip']
        batch_cache = {}
        per_map = self._studio_per_emplo_map()
        sick_reports = self._studio_sick_report_data()
        # Same day classification the attendance engine used, so the project
        # overtime split matches the payslip's own reg/off hours.
        off_weekday = int(self.company_id.ssc_weekly_off_day or '4')
        holidays = self._get_public_holidays(self.start_date, self.last_date)

        for summary in self.summary_ids:
            employee = summary.employee_id
            if not summary.total_att_days:
                continue
            # Skip employees who already have a payslip for this sheet.
            if Payslip.search_count([
                ('attendance_sheet_id', '=', self.id),
                ('employee_id', '=', employee.id),
            ]):
                continue

            # Settle the previous-month (second) period natively: last-month
            # absence/penalty deduction and last-month overtime additions, none
            # of which Studio auto-creates. Their notes are written into the
            # payslip note below (_build_payslip_note). The monthly phone-bill
            # allowance is likewise generated here; everything else still comes
            # from the Studio x_attachments_list bridge.
            self._create_period_attachments(summary, month_abbr, year, prev_days)
            self.env['ssc.attachment']._create_phone_bill_for(
                employee, month_abbr, year)
            is_wps = bool(employee.employee_code)
            batch = self._get_or_create_batch(
                batch_cache, is_wps=is_wps, is_staff=False,
                month_abbr=month_abbr, year=year)

            slip = Payslip.create({
                'employee_id': employee.id,
                'attendance_sheet_id': self.id,
                'summary_id': summary.id,
                'batch_id': batch.id,
                'from_date': period_from,
                'to_date': period_to,
                'days': days,
                'month': month_abbr,
                'year': str(year),
                'is_cash': not is_wps,
                'is_staff': False,
                'designation': employee.job_position,
                'basic_salary': employee.basic_salary,
                'house_allowance': employee.house_allowance,
                'transport_allowance': employee.transport_allowance,
                'other_allowance': employee.other_allowance,
                'gross_salary': employee.gross_salary,
                'ot_rate_regular': employee.ot_rate_regular,
                'ot_rate_off': employee.ot_rate_off,
                'total_attendance': summary.total_att_days,
                'overtime_reg': summary.reg_ot_this_month,
                'overtime_off': summary.off_ot_this_month,
                'auto_note': self._build_payslip_note(summary),
                'project_ids': self._build_labour_projects(
                    per_map, employee, holidays, off_weekday, summary),
                'day_ids': Payslip._copy_days(summary.day_ids),
            })
            slip._attach_pending_attachments()

    # ------------------------------------------------------------------
    def _create_period_attachments(self, summary, month_abbr, year, prev_days):
        """Create the additions/deductions that settle the previous-month
        portion of the cycle plus the fixed monthly allowance."""
        employee = summary.employee_id
        Attachment = self.env['ssc.attachment']
        add_type = self.env.ref('ssc_payroll.attachment_type_salary_addition')
        ded_type = self.env.ref('ssc_payroll.attachment_type_salary_deduction')
        base = {'month': month_abbr, 'year': str(year), 'employee_id': employee.id}

        # The two overtime additions share the generic "Salary Addition" type,
        # so they can only be told apart by their description. They have no
        # Studio counterpart, hence no risk of the same fact arriving twice.
        def _exists_named(name):
            return Attachment.search_count([
                ('employee_id', '=', employee.id),
                ('month', '=', month_abbr), ('year', '=', str(year)),
                ('name', '=', name), ('state', '!=', 'paid'),
            ])

        deduct_days = summary.absence_last_month + summary.penalty_last_month
        if deduct_days > 0 and prev_days:
            rate = employee.gross_salary / prev_days
            parts = []
            if summary.absence_last_month:
                parts.append("%s absence day(s): %s" % (
                    summary.absence_last_month, summary.absence_last_dates or ''))
            if summary.penalty_last_month:
                parts.append("holiday penalty %g day(s): %s" % (
                    summary.penalty_last_month, summary.penalty_last_detail or ''))
            name = "Deduction for last month - " + "; ".join(parts)
            if not _exists_named(name):
                Attachment.create(dict(base, name=name, type_id=ded_type.id,
                                       value=deduct_days * rate))

        if summary.reg_ot_last_month > 0:
            name = "Addition for %g OT hour(s) on regular days in last month: %s" % (
                summary.reg_ot_last_month, summary.reg_ot_last_detail or '')
            if not _exists_named(name):
                Attachment.create(dict(base, name=name, type_id=add_type.id,
                                       value=summary.reg_ot_last_month * employee.ot_rate_regular))

        if summary.off_ot_last_month > 0:
            name = "Addition for %g OT hour(s) on off days in last month: %s" % (
                summary.off_ot_last_month, summary.off_ot_last_detail or '')
            if not _exists_named(name):
                Attachment.create(dict(base, name=name, type_id=add_type.id,
                                       value=summary.off_ot_last_month * employee.ot_rate_off))

    def _create_sick_leave_attachments(self, employee, sick_reports, month_abbr, year):
        """Create the sick-leave (and optional medical-bill) reimbursement
        additions for the employee's approved sick leaves falling in this
        payslip's month. Sick days are already paid via attendance; this is the
        separate reimbursement, mirroring the Studio behaviour."""
        Attachment = self.env['ssc.attachment']
        sick_type = self.env.ref('ssc_payroll.attachment_type_sick_leave')
        medical_type = self.env.ref('ssc_payroll.attachment_type_medical_bill')
        emp_ids = {v for v in (
            employee.employee_code, employee.attendance_code, employee.name) if v}

        for rep in sick_reports:
            if not (rep['idset'] & emp_ids):
                continue
            if MONTH_ABBR[rep['month']] != month_abbr or str(rep['year']) != str(year):
                continue
            # The day of the report separates several sick leaves inside the
            # same month, which the month alone would collapse into one.
            day = rep['last_day']
            base = {'month': month_abbr, 'year': str(year),
                    'employee_id': employee.id, 'day': day}
            rate = employee.gross_salary / rep['month_days'] if rep['month_days'] else 0.0

            if not Attachment._exists_for_period(
                    employee, month_abbr, year, sick_type.id, day=day):
                Attachment.create(dict(
                    base,
                    name="Sick Leave reimbursement for %s for %s" % (rep['name'], day),
                    type_id=sick_type.id, value=rep['days'] * rate))

            if rep['approval'] == 'status1' and rep['medical_amount']:
                if not Attachment._exists_for_period(
                        employee, month_abbr, year, medical_type.id, day=day):
                    Attachment.create(dict(
                        base,
                        name="Medical Bill reimbursement for %s for %s" % (rep['name'], day),
                        type_id=medical_type.id, value=rep['medical_amount']))

    def _build_payslip_note(self, summary):
        """Human-readable summary of every deduction applied to the payslip.

        Current-month deductions reduce the base (total_att_days); last-month
        deductions are settled through the salary attachments. Both are listed
        here for transparency, including the public holiday name/reason."""
        lines = []
        if summary.absence_this_month:
            lines.append("Absence this month: %s day(s)%s" % (
                summary.absence_this_month,
                (" - " + summary.absence_this_dates) if summary.absence_this_dates else ''))
        if summary.penalty_this_month:
            lines.append("Public holiday penalty this month: %g day(s) - %s" % (
                summary.penalty_this_month, summary.penalty_this_detail or ''))
        if summary.absence_last_month:
            lines.append("Absence last month (via attachment): %s day(s)%s" % (
                summary.absence_last_month,
                (" - " + summary.absence_last_dates) if summary.absence_last_dates else ''))
        if summary.penalty_last_month:
            lines.append("Public holiday penalty last month (via attachment): %g day(s) - %s" % (
                summary.penalty_last_month, summary.penalty_last_detail or ''))
        if not summary.has_advance:
            lines.append("No advance paid (NOR approved / on leave / "
                         "submitted cancellation).")
        elif summary.advance_granted:
            lines.append("Advance paid: %s day(s) after the period." %
                         summary.advance_days)
        else:
            lines.append(
                "Advance rejected: %s day(s) withheld (attendance %.0f%% of "
                "the period, below %g%%)." % (
                    summary.advance_days, summary.attendance_ratio,
                    ADVANCE_REVIEW_RATIO))
        if not lines:
            return False
        # Rendered in light purple (see auto_note) so it reads as machine-written.
        body = "<br/>".join(html_escape(line) for line in lines)
        return '<div style="color:#8f5fa6;">%s</div>' % body

    @api.model
    def _allocate_advance_days(self, worked_days, advance_days, period_days):
        """Spread the advanced days over the projects the employee worked on.

        Those days carry no daily record yet, so they cannot be observed; they
        are attributed from where the employee actually was. Someone who spent
        more than 60% of the period on one project counts as dedicated to it
        and carries the whole advance there. Otherwise the days follow the same
        proportions as the days that were worked.

        Returns {project_id: days}; empty when there is nothing to attribute.
        """
        total_worked = sum(worked_days.values())
        if not (advance_days and total_worked):
            return {}
        if period_days:
            pid, days = max(worked_days.items(), key=lambda kv: kv[1])
            if 100.0 * days / period_days > ADVANCE_DEDICATION_RATIO:
                return {pid: float(advance_days)}
        return {
            pid: advance_days * days / total_worked
            for pid, days in worked_days.items()
        }

    def _build_labour_projects(self, per_map, employee, holidays, off_weekday,
                               summary):
        """Aggregate present days and overtime per project from the Studio
        daily attendance lines within the sheet period. Overtime is split the
        same way the attendance engine splits it: hours worked on a public
        holiday or on the weekly off day are off-day overtime."""
        per_emplo = self._studio_find_per_emplo(per_map, employee)
        per_lines = self._studio_period_lines(per_emplo, self.start_date, self.last_date)
        totals = {}
        for day, line in per_lines.items():
            if _get(line, 'x_name') != 'Present':
                continue
            project = _get(line, 'x_studio_project')
            if not project:
                continue
            bucket = totals.setdefault(
                project.id, {'att': 0, 'reg': 0.0, 'off': 0.0})
            bucket['att'] += 1
            overtime = _get(line, 'x_studio_overtime') or 0.0
            if overtime and employee.overtime_eligible:
                is_off = day in holidays or day.weekday() == off_weekday
                bucket['off' if is_off else 'reg'] += overtime
        allocated = self._allocate_advance_days(
            {pid: v['att'] for pid, v in totals.items()},
            summary.advance_days if summary.advance_granted else 0,
            summary.period_days,
        )
        return [
            (0, 0, {
                'project_id': pid,
                'total_attendance': vals['att'],
                'total_overtime_reg': vals['reg'],
                'total_overtime_off': vals['off'],
                'allocated_attendance': vals['att'] + allocated.get(pid, 0.0),
            })
            for pid, vals in totals.items()
        ]

    def _get_or_create_batch(self, cache, is_wps, is_staff, month_abbr, year):
        company = self.company_id
        code = company.ssc_company_code or (company.name or '')[:3].upper()
        abbr = company.ssc_company_abbr or code
        pay_type = 'WPS' if is_wps else 'CASH'
        staff_tag = 'Staff-' if is_staff else ''
        name = "%s%s/%s/%s/%s%s/Salary Batch" % (
            year, month_abbr, code, pay_type, staff_tag, abbr)

        if name in cache:
            return cache[name]
        Batch = self.env['ssc.salary.batch']
        batch = Batch.search([('name', '=', name), ('company_id', '=', company.id)], limit=1)
        if not batch:
            batch = Batch.create({
                'name': name,
                'company_id': company.id,
                'attendance_sheet_id': self.id,
                'submitted_by': self.submitted_by.id or self.env.uid,
                'batch_type': 'wps' if is_wps else 'cash',
                'is_staff': is_staff,
            })
        cache[name] = batch
        return batch

    def action_view_payslips(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': self.env._("Payslips"),
            'res_model': 'ssc.payslip',
            'view_mode': 'list,form',
            'domain': [('attendance_sheet_id', '=', self.id)],
            'context': {'default_attendance_sheet_id': self.id},
        }


class SscStaffAttendanceGeneration(models.Model):
    """Staff payslip generation from a staff attendance sheet."""
    _inherit = 'ssc.staff.attendance'

    payslip_ids = fields.One2many(
        'ssc.payslip', 'staff_attendance_id', string="Payslips")
    payslip_count = fields.Integer(compute='_compute_staff_payslip_count')

    @api.depends('payslip_ids')
    def _compute_staff_payslip_count(self):
        for rec in self:
            rec.payslip_count = len(rec.payslip_ids)

    def action_generate_payslips(self):
        for rec in self:
            if rec.state != 'approved':
                raise UserError(self.env._(
                    "Approve the staff attendance before generating payslips."))
            rec._generate_staff_payslips()
            rec.payslip_ids.batch_id.action_compute_project_distribution()
        return True

    def _generate_staff_payslips(self):
        self.ensure_one()
        mon = int(self.month)
        year = self.year
        month_abbr = MONTH_ABBR[mon]
        days = self._days_in_month()
        period_from = date(year, mon, 1)
        period_to = date(year, mon, days)

        Payslip = self.env['ssc.payslip']
        batch_cache = {}

        for line in self.line_ids:
            employee = line.employee_id
            if Payslip.search_count([
                ('staff_attendance_id', '=', self.id),
                ('employee_id', '=', employee.id),
            ]):
                continue

            # Phone-bill allowance is generated natively (Studio never creates
            # it); every other attachment comes from the Studio bridge.
            self.env['ssc.attachment']._create_phone_bill_for(
                employee, month_abbr, year)
            is_wps = bool(employee.employee_code)
            batch = self._get_or_create_staff_batch(
                batch_cache, is_wps=is_wps, month_abbr=month_abbr, year=year)

            slip = Payslip.create({
                'employee_id': employee.id,
                'staff_attendance_id': self.id,
                'batch_id': batch.id,
                'from_date': period_from,
                'to_date': period_to,
                'days': days,
                'month': month_abbr,
                'year': str(year),
                'is_cash': not is_wps,
                'is_staff': True,
                'designation': employee.job_position,
                'basic_salary': line.basic_salary,
                'house_allowance': line.house_allowance,
                'transport_allowance': line.transport_allowance,
                'other_allowance': line.other_allowance,
                'gross_salary': line.gross_salary,
                'total_attendance': line.total_attended_days,
                # Staff carry no daily project record: their days are split
                # across projects by their agreed percentages. The staff sheet
                # tracks no overtime, so there are no hours to spread.
                # The staff sheet has no advance to attribute, so the allocated
                # view of a staff payslip is the same as the worked one.
                'project_ids': [
                    (0, 0, {
                        'project_id': d.project_id.id,
                        'total_attendance': (d.percentage / 100.0) * line.total_attended_days,
                        'allocated_attendance': (d.percentage / 100.0) * line.total_attended_days,
                    })
                    for d in line.distribution_ids
                ],
                'day_ids': Payslip._copy_days(line.day_ids),
            })
            slip._attach_pending_attachments()
            # Native last-month absence deduction, kept in sync with the line's
            # last_month_absence (see ssc.staff.attendance.line).
            line._sync_last_month_absence_deduction()

    def _get_or_create_staff_batch(self, cache, is_wps, month_abbr, year):
        company = self.company_id
        code = company.ssc_company_code or (company.name or '')[:3].upper()
        abbr = company.ssc_company_abbr or code
        pay_type = 'WPS' if is_wps else 'CASH'
        name = "%s%s/%s/%s/Staff-%s/Salary Batch" % (
            year, month_abbr, code, pay_type, abbr)
        if name in cache:
            return cache[name]
        Batch = self.env['ssc.salary.batch']
        batch = Batch.search([('name', '=', name), ('company_id', '=', company.id)], limit=1)
        if not batch:
            batch = Batch.create({
                'name': name,
                'company_id': company.id,
                'batch_type': 'wps' if is_wps else 'cash',
                'is_staff': True,
            })
        cache[name] = batch
        return batch

    def action_view_payslips(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': self.env._("Payslips"),
            'res_model': 'ssc.payslip',
            'view_mode': 'list,form',
            'domain': [('staff_attendance_id', '=', self.id)],
            'context': {'default_staff_attendance_id': self.id},
        }
