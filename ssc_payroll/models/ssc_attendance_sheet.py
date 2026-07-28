# -*- coding: utf-8 -*-
import calendar
import logging
from datetime import date, timedelta

from dateutil.relativedelta import relativedelta

from odoo import api, fields, models
from odoo.exceptions import UserError

from .utils import (
    MONTH_ABBR, ordinal as _ordinal,
    STUDIO_MONTH_FIELDS, SICK_LEAVE_APPROVED, studio_get as _get,
)

_logger = logging.getLogger(__name__)

# Day statuses that count as an attended (paid) day.
ATTENDED_STATUSES = ('present', 'sick_leave')

# Attendance percentage of the current period below which the advance is not
# granted automatically and has to be approved or rejected line by line.
ADVANCE_REVIEW_RATIO = 50.0

# Share of the period on a single project above which an employee counts as
# dedicated to it, so the advanced days are all charged there instead of being
# spread over the projects they merely passed through.
ADVANCE_DEDICATION_RATIO = 60.0


class SscAttendanceSheet(models.Model):
    _name = 'ssc.attendance.sheet'
    _description = "Monthly Attendance Sheet (Labour)"
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'last_date desc, id desc'

    name = fields.Char(string="Reference", compute='_compute_name', store=True, readonly=False, tracking=True)
    company_id = fields.Many2one(
        'res.company', string="Company", required=True, tracking=True,
        default=lambda self: self.env.company,
    )
    start_date = fields.Date(string="Start Date", required=True, tracking=True)
    last_date = fields.Date(string="Last Date", required=True, tracking=True)
    # Previous-month period. Absence / overtime computed over this range are
    # settled on the current payslip as salary attachments (not paid via base).
    last_month_start = fields.Date(string="Last Month Start", tracking=True)
    last_month_end = fields.Date(string="Last Month End", tracking=True)
    state = fields.Selection(
        [
            ('new', 'New'),
            ('generated', 'Generated'),
            ('to_approve', 'Submitted for Approval'),
            ('approved', 'Approved'),
            ('reject', 'Rejected'),
        ],
        string="Status", default='new', tracking=True, index=True,
    )
    summary_ids = fields.One2many(
        'ssc.attendance.summary', 'sheet_id', string="Employees",
    )
    submitted_by = fields.Many2one('res.users', string="Submitted by", readonly=True, tracking=True)
    approved_by = fields.Many2one('res.users', string="Approved by", readonly=True, tracking=True)
    approval_date = fields.Date(string="Approval Date", readonly=True, tracking=True)
    summary_count = fields.Integer(compute='_compute_summary_count')
    # Live link to the Studio x_attendance_per_month record this mirrors.
    # TEMPORARY: imports the historical Studio sheets as-is, without going
    # through the generation engine, until Studio is retired.
    studio_ref_id = fields.Integer(string="Studio Ref", index=True, copy=False)

    @api.depends('summary_ids')
    def _compute_summary_count(self):
        for sheet in self:
            sheet.summary_count = len(sheet.summary_ids)

    @api.depends('company_id', 'start_date', 'last_date')
    def _compute_name(self):
        for sheet in self:
            if not (sheet.start_date and sheet.last_date):
                sheet.name = sheet.name or "New Attendance Sheet"
                continue
            code = sheet.company_id.ssc_company_code or (sheet.company_id.name or '')[:3].upper()
            s, l = sheet.start_date, sheet.last_date
            sheet.name = "%s/%s%s%s to %s%s%s/%s/Attendance Sheet" % (
                code,
                s.day, _ordinal(s.day), MONTH_ABBR[s.month],
                l.day, _ordinal(l.day), MONTH_ABBR[l.month],
                l.year,
            )

    # ------------------------------------------------------------------
    # Constraints
    # ------------------------------------------------------------------
    @api.constrains('start_date', 'last_date', 'last_month_start', 'last_month_end')
    def _check_dates(self):
        for sheet in self:
            if sheet.start_date and sheet.last_date and sheet.start_date > sheet.last_date:
                raise UserError(self.env._("Start Date must be before Last Date."))
            if sheet.last_month_start and sheet.last_month_end:
                if sheet.last_month_start > sheet.last_month_end:
                    raise UserError(self.env._(
                        "Last Month Start must be before Last Month End."))
                # The two periods must not overlap (a day would be settled twice).
                if (sheet.start_date and sheet.last_date
                        and sheet.last_month_start <= sheet.last_date
                        and sheet.last_month_end >= sheet.start_date):
                    raise UserError(self.env._(
                        "The current-month and last-month periods must not overlap."))

    # ------------------------------------------------------------------
    # Workflow
    # ------------------------------------------------------------------
    def action_generate(self):
        for sheet in self:
            if not (sheet.start_date and sheet.last_date):
                raise UserError(self.env._("Please set the Start and Last dates first."))
            sheet._generate_summaries()
            sheet.state = 'generated'
        return True

    def action_submit(self):
        for sheet in self:
            sheet._check_advance_decisions()
        self.write({'state': 'to_approve', 'submitted_by': self.env.uid})

    def _check_advance_decisions(self):
        """Every line flagged for review must carry an explicit decision before
        the sheet can move on: an undecided line would silently advance days to
        an employee who barely attended."""
        self.ensure_one()
        pending = self.summary_ids.filtered(
            lambda s: s.needs_review and s.advance_state == 'pending')
        if not pending:
            return
        raise UserError(self.env._(
            "These employees attended less than %(ratio)g%% of the period. "
            "Approve or reject their advance before submitting:\n\n%(names)s",
            ratio=ADVANCE_REVIEW_RATIO,
            names="\n".join(
                "- %s (%.0f%% attendance: %s of %s days)" % (
                    s.employee_id.display_name, s.attendance_ratio,
                    s.attended_days, s.period_days)
                for s in pending),
        ))

    def action_approve(self):
        self.write({
            'state': 'approved',
            'approved_by': self.env.uid,
            'approval_date': fields.Date.context_today(self),
        })

    def action_reject(self):
        self.write({'state': 'reject'})

    def action_reset_to_new(self):
        self.write({'state': 'new'})

    # ------------------------------------------------------------------
    # Calculation engine
    # ------------------------------------------------------------------
    def _get_employees(self):
        """Labour employees of the sheet's company: everybody except the
        Engineer/Office staff (who are paid through the staff attendance sheet)
        and cancelled employees. The Studio "Staff" flag does NOT exclude an
        employee here, and neither do approved_nor / on_leave /
        submitted_cancellation: those only withhold the advance."""
        self.ensure_one()
        return self.env['ssc.employee'].search([
            ('is_engineer_office', '=', False),
            ('is_cancelled', '=', False),
            ('company_id', '=', self.company_id.id),
        ])

    def _period_span(self):
        """Overall [start, last] covering both the current and last-month
        ranges, used to prefetch Studio data in one pass."""
        self.ensure_one()
        starts, ends = [self.start_date], [self.last_date]
        if self.last_month_start and self.last_month_end:
            # Include the last month's main period (1st -> day before the
            # last-month period) so we can tell whether the advance was given.
            starts.append(self.last_month_start.replace(day=1))
            ends.append(self.last_month_end)
        return min(starts), max(ends)

    def _get_public_holidays(self, span_start, span_last):
        """Map {public-holiday date: name} in the span for this company.
        A holiday with no company applies to every company."""
        self.ensure_one()
        holidays = self.env['ssc.public.holiday'].search([
            ('date', '>=', span_start), ('date', '<=', span_last),
            '|', ('company_id', '=', False), ('company_id', '=', self.company_id.id),
        ])
        result = {}
        for h in holidays:
            result.setdefault(h.date, h.name)
        return result

    def _advance_days(self, employee, month_end):
        """Number of days paid in advance: everything after the period up to
        the end of its month. An employee joining inside that window is only
        advanced from their joining date on."""
        self.ensure_one()
        first = self.last_date + timedelta(days=1)
        if employee.joining_date and employee.joining_date > first:
            first = employee.joining_date
        return max((month_end - first).days + 1, 0)

    def _generate_summaries(self):
        self.ensure_one()
        self.summary_ids.unlink()

        employees = self._get_employees()
        off_weekday = int(self.company_id.ssc_weekly_off_day or '4')
        span_start, span_last = self._period_span()
        holidays = self._get_public_holidays(span_start, span_last)
        # The days between the end of the period and the end of its month are
        # paid in advance, so their count follows the calendar rather than a
        # fixed number: 01->20 JUN advances 10 days (June has 30), while
        # 01->20 MAY advances 11 (May has 31).
        month_days = calendar.monthrange(self.last_date.year, self.last_date.month)[1]
        month_end = self.last_date.replace(day=month_days)

        # TEMPORARY: source daily attendance from the Studio models. Prefetch
        # the per-employee attendance cards and approved sick leaves once.
        per_map = self._studio_per_emplo_map()
        sick_ranges = self._studio_sick_ranges(span_start, span_last)

        vals_list = []
        for emp in employees:
            # Skip employees who joined after the advanced days end.
            if emp.joining_date and emp.joining_date > month_end:
                continue
            per_emplo = self._studio_find_per_emplo(per_map, emp)
            per_lines = self._studio_period_lines(per_emplo, span_start, span_last)
            sick_dates = self._studio_employee_sick_dates(
                emp, sick_ranges, span_start, span_last)
            day_map = self._studio_day_map(per_lines, sick_dates)

            this = self._compute_range(
                emp, day_map, self.start_date, self.last_date, holidays, off_weekday)
            last = self._compute_range(
                emp, day_map, self.last_month_start, self.last_month_end,
                holidays, off_weekday)

            # Display-only daily snapshot: the Studio status/overtime/project the
            # salary is computed from, plus the biometric check-in/out.
            punches = self._biometric_punches(emp, span_start, span_last)
            day_cmds = self._build_summary_days(per_lines, punches)

            # The advance is withheld from employees who are leaving or away.
            # Cancelled employees never reach here: they are out of the sheet.
            # Sick days are excluded from the base too: they are reimbursed
            # through their own attachment, so paying them here would double.
            this_deduct = this['absence'] + this['penalty'] + this['sick']
            attended = max(this['period_days'] - this_deduct, 0)
            has_advance = not (
                emp.approved_nor or emp.on_leave or emp.submitted_cancellation
            )
            if not has_advance:
                # No advance was given, so there is nothing to reconcile.
                last = self._empty_range()

            vals_list.append((0, 0, {
                'employee_id': emp.id,
                'attended_days': attended,
                'period_days': this['period_days'],
                'advance_days': self._advance_days(emp, month_end),
                'has_advance': bool(has_advance),
                'absence_this_month': this['absence'],
                'penalty_this_month': this['penalty'],
                'absence_this_dates': self._fmt_dates(this['absent_dates']),
                'penalty_this_detail': this['penalty_detail'],
                'reg_ot_this_month': this['reg_ot'],
                'off_ot_this_month': this['off_ot'],
                'absence_last_month': last['absence'],
                'penalty_last_month': last['penalty'],
                'reg_ot_last_month': last['reg_ot'],
                'off_ot_last_month': last['off_ot'],
                'absence_last_dates': self._fmt_dates(last['absent_dates']),
                'penalty_last_detail': last['penalty_detail'],
                'reg_ot_last_detail': self._fmt_ot(last['reg_ot_detail']),
                'off_ot_last_detail': self._fmt_ot(last['off_ot_detail']),
                'day_ids': day_cmds,
            }))
        self.summary_ids = vals_list

    def _biometric_punches(self, employee, start, last):
        """{date: (check_in, check_out)} from the ssc_attendance biometric
        module for the employee over the span. Read dynamically so this module
        keeps no hard dependency on ssc_attendance."""
        Line = self.env.get('ssc.attendance.line')
        if Line is None:
            return {}
        match = []
        if employee.studio_ref_id:
            match.append(('employee_id', '=', employee.studio_ref_id))
        if employee.attendance_code:
            match.append(('attendance_id', '=', employee.attendance_code))
        if not match:
            return {}
        if len(match) == 2:
            match = ['|'] + match
        lines = Line.search([('date', '>=', start), ('date', '<=', last)] + match)
        return {l.date: (l.first_punch, l.last_punch, l.total_ot) for l in lines}

    def _build_summary_days(self, per_lines, punches):
        """Build the daily-attendance commands, merging the Studio daily lines
        (status / overtime / project) with the biometric punch times."""
        cmds = []
        for d in sorted(set(per_lines) | set(punches)):
            line = per_lines.get(d)
            name = _get(line, 'x_name') if line else False
            status = name if name in ('Present', 'Sick Leave') else 'Absent'
            project = _get(line, 'x_studio_project') if line else False
            check_in, check_out, bio_ot = punches.get(d) or (False, False, 0.0)
            cmds.append((0, 0, {
                'date': d,
                'day_name': d.strftime('%A'),
                'status': status,
                'overtime': (_get(line, 'x_studio_overtime') or 0.0) if line else 0.0,
                'project_id': project.id if project else False,
                'check_in': check_in,
                'check_out': check_out,
                'biotime_overtime': bio_ot or 0.0,
            }))
        return cmds

    @staticmethod
    def _attended(day_map, holidays, d):
        """A day counts as attendance for the off-day bridge if it is a public
        holiday or has a Present/Sick-Leave record."""
        if d in holidays:
            return True
        entry = day_map.get(d)
        return bool(entry) and entry[0] in ATTENDED_STATUSES

    @staticmethod
    def _empty_range():
        return {'period_days': 0, 'absence': 0, 'penalty': 0.0, 'sick': 0,
                'reg_ot': 0.0, 'off_ot': 0.0, 'absent_dates': [], 'sick_dates': [],
                'reg_ot_detail': [], 'off_ot_detail': [], 'penalty_detail': ''}

    @staticmethod
    def _fmt_dates(dates):
        return ", ".join(str(d) for d in dates)

    @staticmethod
    def _fmt_ot(pairs):
        return ", ".join("%s (%gh)" % (d, h) for d, h in pairs)

    # ------------------------------------------------------------------
    # TEMPORARY Studio attendance source (until ssc_attendance is wired)
    # ------------------------------------------------------------------
    def _studio_per_emplo_map(self):
        """Map identifiers -> x_attendance_per_emplo record for the company."""
        self.ensure_one()
        result = {}
        Model = self.env.get('x_attendance_per_emplo')
        if Model is None:
            return result
        recs = Model.with_context(active_test=False).search(
            [('x_studio_company', '=', self.company_id.id)])
        for rec in recs:
            att, code = _get(rec, 'x_studio_attendance_id'), _get(rec, 'x_studio_employee_id')
            if att:
                result.setdefault(('att', att), rec)
            if code:
                result.setdefault(('code', code), rec)
        return result

    def _studio_find_per_emplo(self, per_map, employee):
        return (per_map.get(('att', employee.attendance_code))
                or per_map.get(('code', employee.employee_code)))

    def _studio_period_lines(self, per_emplo, start, last):
        """Return {date: studio daily line} across the period, reading the
        Studio monthly fields (which store one list of daily lines per month)."""
        lines_by_date = {}
        if not per_emplo:
            return lines_by_date
        cur = date(start.year, start.month, 1)
        while cur <= last:
            field = STUDIO_MONTH_FIELDS.get(cur.year, {}).get(cur.month)
            if field and field in per_emplo._fields:
                for line in per_emplo[field]:
                    d = _get(line, 'x_studio_date')
                    if d and start <= d <= last:
                        lines_by_date[d] = line
            cur += relativedelta(months=1)
        return lines_by_date

    def _studio_sick_report_data(self):
        """Parsed approved sick-leave reports. Each entry:
        {idset, from, to, days, last_day, month, year, month_days,
         approval, medical_amount, name}. Robust to whichever model
        x_sick_leave_reports.x_studio_employee points at."""
        self.ensure_one()
        out = []
        Model = self.env.get('x_sick_leave_reports')
        if Model is None:
            return out
        recs = Model.search(
            [('x_studio_selection_field_632_1ii4lpujj', '=', SICK_LEAVE_APPROVED)])
        for r in recs:
            if _get(r, 'x_studio_for') == 'value_2':
                f = _get(r, 'x_studio_from_date')
                t = _get(r, 'x_studio_to_date')
            else:
                f = t = _get(r, 'x_studio_date') or _get(r, 'x_studio_from_date')
            if not (f and t):
                continue
            emp_rec = _get(r, 'x_studio_employee')
            idset = {v for v in (
                _get(emp_rec, 'x_studio_employee_id'),
                _get(emp_rec, 'x_studio_attendance_id'),
                _get(emp_rec, 'x_name'),
            ) if v}
            if not idset:
                continue
            out.append({
                'idset': idset, 'from': f, 'to': t, 'days': (t - f).days + 1,
                'last_day': t, 'month': t.month, 'year': t.year,
                'month_days': calendar.monthrange(t.year, t.month)[1],
                'approval': _get(r, 'x_studio_approval'),
                'medical_amount': _get(r, 'x_studio_medical_bill_total_amount') or 0.0,
                'name': _get(r, 'x_name') or '',
            })
        return out

    def _studio_sick_ranges(self, span_start, span_last):
        """Approved sick-leave ranges overlapping the span, each as
        (idset, from_date, to_date), reused for the attendance overlay."""
        return [
            (rep['idset'], rep['from'], rep['to'])
            for rep in self._studio_sick_report_data()
            if not (rep['to'] < span_start or rep['from'] > span_last)
        ]

    def _studio_employee_sick_dates(self, employee, sick_ranges, start, last):
        emp_ids = {v for v in (
            employee.employee_code, employee.attendance_code, employee.name) if v}
        dates = set()
        if not emp_ids:
            return dates
        for idset, f, t in sick_ranges:
            if idset & emp_ids:
                d, end = max(f, start), min(t, last)
                while d <= end:
                    dates.add(d)
                    d += timedelta(days=1)
        return dates

    def _studio_day_map(self, per_lines, sick_dates):
        """Return {date: (status, overtime)} with status in
        ('present', 'sick_leave', 'absent'). Approved sick leaves override the
        daily line. Dates with no record are simply absent from the map."""
        day_map = {}
        for d, line in per_lines.items():
            name = _get(line, 'x_name')
            if name == 'Present':
                status = 'present'
            elif name == 'Sick Leave':
                status = 'sick_leave'
            else:
                status = 'absent'
            day_map[d] = (status, _get(line, 'x_studio_overtime') or 0.0)
        for d in sick_dates:
            ot = day_map[d][1] if d in day_map else 0.0
            day_map[d] = ('sick_leave', ot)
        return day_map

    def _compute_range(self, employee, day_map, r_start, r_last, holidays, off_weekday):
        """Compute attendance figures over the [r_start, r_last] range.

        Absence model: only working days (not the weekly off day, not a public
        holiday) with no Present/Sick-Leave record count as absence; the weekly
        off day and holidays are never absence. Any overtime worked on the
        weekly off day OR on a public holiday is counted as "off" overtime.
        A separate holiday penalty is added for absence around holidays.
        """
        res = self._empty_range()
        if not (r_start and r_last) or r_start > r_last:
            return res

        eff_start = r_start
        if employee.joining_date:
            if employee.joining_date > r_last:
                return res
            if employee.joining_date > r_start:
                eff_start = employee.joining_date

        total_days = 0
        current = eff_start
        while current <= r_last:
            total_days += 1
            entry = day_map.get(current)
            status = entry[0] if entry else None
            overtime = entry[1] if entry else 0.0
            is_holiday = current in holidays
            is_off = current.weekday() == off_weekday

            if is_holiday:
                # Public holiday: never absence here (penalty handled apart);
                # any worked hours are "off" overtime.
                if status in ATTENDED_STATUSES and employee.overtime_eligible and overtime:
                    res['off_ot'] += overtime
                    res['off_ot_detail'].append((current, overtime))
            elif is_off:
                # Weekly off day is paid only if the employee worked it, or is
                # bridged by attendance on BOTH the day before and the day
                # after; otherwise it counts as absence.
                if status in ATTENDED_STATUSES:
                    if employee.overtime_eligible and overtime:
                        res['off_ot'] += overtime
                        res['off_ot_detail'].append((current, overtime))
                elif not (self._attended(day_map, holidays, current - timedelta(days=1))
                          and self._attended(day_map, holidays, current + timedelta(days=1))):
                    res['absence'] += 1
                    res['absent_dates'].append(current)
            else:
                # Working day.
                if status == 'sick_leave':
                    # Neutral: the day is paid through the sick-leave attachment,
                    # so it counts as neither base attendance nor absence.
                    res['sick'] += 1
                    res['sick_dates'].append(current)
                elif status in ATTENDED_STATUSES:
                    if employee.overtime_eligible and overtime:
                        res['reg_ot'] += overtime
                        res['reg_ot_detail'].append((current, overtime))
                else:
                    res['absence'] += 1
                    res['absent_dates'].append(current)

            current += timedelta(days=1)

        res['period_days'] = total_days
        penalty, detail = self._holiday_penalty(
            day_map, holidays, eff_start, r_last, off_weekday)
        res['penalty'] = penalty
        res['penalty_detail'] = detail
        return res

    def _holiday_penalty(self, day_map, holidays, r_start, r_last, off_weekday):
        """Extra deduction (in days) for absence adjacent to a public holiday.

        For each consecutive holiday block of length L:
          * short block (< 4 days): absence the day before OR after -> the whole
            block is deducted;
          * long block (>= 4 days): absence one side -> half the block; both
            sides -> the whole block.
        If the adjacent day is the weekly off day, it is not treated as absence.
        """
        block_dates = sorted(d for d in holidays if r_start <= d <= r_last)
        if not block_dates:
            return 0.0, ''

        blocks, group = [], [block_dates[0]]
        for d in block_dates[1:]:
            if (d - group[-1]).days == 1:
                group.append(d)
            else:
                blocks.append(group)
                group = [d]
        blocks.append(group)

        def _absent_working(d):
            if d.weekday() == off_weekday or d in holidays:
                return False
            entry = day_map.get(d)
            return not (entry and entry[0] in ATTENDED_STATUSES)

        penalty, detail = 0.0, []
        for group in blocks:
            length = len(group)
            before = group[0] - timedelta(days=1)
            after = group[-1] + timedelta(days=1)
            ab, aa = _absent_working(before), _absent_working(after)
            if length >= 4:
                add = length if (ab and aa) else (length / 2.0 if (ab or aa) else 0.0)
            else:
                add = length if (ab or aa) else 0.0
            if add:
                penalty += add
                name = holidays.get(group[0]) or "Public Holiday"
                reason = "before & after" if (ab and aa) else ("before" if ab else "after")
                detail.append("%s (%s..%s): -%g day(s) [absence %s]" % (
                    name, group[0], group[-1], add, reason))
        return penalty, "; ".join(detail)

    # ------------------------------------------------------------------
    # TEMPORARY snapshot bridge with the Studio x_attendance_per_month master.
    # This imports the already-computed Studio sheets and summary lines as-is;
    # it does NOT run the generation engine, so none of the attendance logic
    # above is touched. Driven by an automated action.
    # ------------------------------------------------------------------
    @api.model
    def _sync_from_studio(self, studio_records):
        Sheet = self.with_context(
            tracking_disable=True, mail_create_nolog=True,
            mail_create_nosubscribe=True)
        for src in studio_records:
            try:
                with self.env.cr.savepoint():
                    vals = Sheet._studio_sheet_vals(src)
                    if not vals:
                        continue
                    mirror = Sheet.search([('studio_ref_id', '=', src.id)], limit=1)
                    if mirror:
                        mirror.write(vals)
                    else:
                        mirror = Sheet.create(dict(vals, studio_ref_id=src.id))
                    Sheet._sync_summaries(mirror, src)
            except Exception:
                _logger.exception(
                    "ssc_payroll: attendance sheet mirror failed for "
                    "x_attendance_per_month id=%s", src.id)
        return True

    @api.model
    def _studio_sheet_vals(self, src):
        """Map an x_attendance_per_month header onto ssc.attendance.sheet.
        Returns None without the company and the period dates it requires."""
        company = _get(src, 'x_studio_company')
        start = _get(src, 'x_studio_start_date')
        last = _get(src, 'x_studio_last_date')
        if not (company and start and last):
            return None
        if _get(src, 'x_studio_submitted') or _get(src, 'x_studio_approval_date_1'):
            state = 'approved'
        elif _get(src, 'x_studio_submit'):
            state = 'to_approve'
        else:
            state = 'generated'
        return {
            'company_id': company.id,
            'start_date': start,
            'last_date': last,
            'approval_date': _get(src, 'x_studio_approval_date_1') or False,
            'state': state,
        }

    @api.model
    def _sync_summaries(self, sheet, src):
        """Mirror the Studio monthly-attendance lines onto the sheet's summary,
        matched by their Studio ref. The advanced-day machinery is bypassed:
        the Studio total already bakes it in, so it is stored straight into
        attended_days with no advance and no review."""
        Summary = self.env['ssc.attendance.summary']
        existing = {s.studio_ref_id: s for s in sheet.summary_ids if s.studio_ref_id}
        for line in _get(src, 'x_studio_monthly_attendance') or []:
            employee = self._resolve_line_employee(line)
            if not employee:
                continue
            vals = {
                'sheet_id': sheet.id,
                'employee_id': employee.id,
                'attended_days': _get(line, 'x_studio_total_att_days') or 0,
                'advance_days': 0,
                'has_advance': False,
                'absence_this_month': _get(line, 'x_studio_total_absence_days_this_month') or 0,
                'absence_last_month': _get(line, 'x_studio_total_absence_days_last_month') or 0,
                'reg_ot_this_month': _get(line, 'x_studio_total_reg_ot') or 0.0,
                'reg_ot_last_month': _get(line, 'x_studio_total_reg_ot_last_month') or 0.0,
                'off_ot_this_month': _get(line, 'x_studio_total_off_ot') or 0.0,
                'off_ot_last_month': _get(line, 'x_studio_total_off_ot_last_month') or 0.0,
            }
            rec = existing.get(line.id)
            if rec:
                rec.write(vals)
            else:
                Summary.create(dict(vals, studio_ref_id=line.id))

    @api.model
    def _resolve_line_employee(self, line):
        Emp = self.env['ssc.employee'].with_context(active_test=False)
        for studio_field, local_field in (
                ('x_studio_employee_id', 'employee_code'),
                ('x_studio_attendance_id', 'attendance_code')):
            val = _get(line, studio_field)
            if val:
                emp = Emp.search([(local_field, '=', val)], limit=1)
                if emp:
                    return emp
        studio_emp = _get(line, 'x_studio_employee')
        if studio_emp:
            return self.env['ssc.attachment']._resolve_studio_employee(studio_emp)
        return Emp.browse()


class SscAttendanceSummary(models.Model):
    _name = 'ssc.attendance.summary'
    _description = "Attendance Sheet Employee Summary"
    _order = 'sheet_id, employee_id'

    sheet_id = fields.Many2one(
        'ssc.attendance.sheet', string="Attendance Sheet",
        required=True, ondelete='cascade', index=True,
    )
    employee_id = fields.Many2one(
        'ssc.employee', string="Employee", required=True, index=True,
    )
    # Live link to the Studio monthly-attendance line this mirrors.
    studio_ref_id = fields.Integer(string="Studio Ref", index=True, copy=False)
    day_ids = fields.One2many(
        'ssc.attendance.summary.day', 'summary_id', string="Daily Attendance")
    employee_code = fields.Char(related='employee_id.employee_code', string="Employee ID", store=True)
    attendance_code = fields.Char(related='employee_id.attendance_code', string="Attendance ID", store=True)
    company_id = fields.Many2one(related='sheet_id.company_id', store=True)

    # Days actually attended inside the current period, and the days between
    # the end of the period and the end of its month that may be advanced.
    attended_days = fields.Integer(string="Attended Days (Period)")
    period_days = fields.Integer(string="Period Days")
    advance_days = fields.Integer(string="Advance Days")
    total_att_days = fields.Integer(
        string="Total Attendance Days",
        compute='_compute_total_att_days', store=True,
    )
    has_advance = fields.Boolean(
        string="Advance Eligible",
        help="False when the employee is NOR approved, on leave or has "
             "submitted their cancellation.",
    )
    attendance_ratio = fields.Float(
        string="Attendance %", compute='_compute_attendance_ratio', store=True,
        help="Attended days over the days of the current period.",
    )
    needs_review = fields.Boolean(
        string="Needs Review", compute='_compute_needs_review', store=True,
        help="Advance-eligible employees who attended less than half of the "
             "period. Their advance must be approved or rejected by hand.",
    )
    advance_state = fields.Selection(
        [
            ('pending', 'Pending'),
            ('approved', 'Approved'),
            ('rejected', 'Rejected'),
        ],
        string="Advance Decision", default='pending', copy=False,
    )
    advance_granted = fields.Boolean(
        string="Advance Granted", compute='_compute_advance_granted', store=True,
    )
    # True while a flagged line still awaits a decision; used to float the
    # undecided lines to the top of the sheet.
    is_pending_review = fields.Boolean(
        string="Pending Review", compute='_compute_is_pending_review', store=True,
    )
    absence_this_month = fields.Integer(string="Absence (This Month)")
    penalty_this_month = fields.Float(string="Holiday Penalty (This Month)")
    absence_this_dates = fields.Char(string="Absence Dates (This Month)")
    penalty_this_detail = fields.Char(string="Holiday Penalty Detail (This Month)")
    absence_last_month = fields.Integer(string="Absence (Last Month)")
    reg_ot_this_month = fields.Float(string="Reg OT (This Month)")
    reg_ot_last_month = fields.Float(string="Reg OT (Last Month)")
    off_ot_this_month = fields.Float(string="Off OT (This Month)")
    off_ot_last_month = fields.Float(string="Off OT (Last Month)")
    total_ot = fields.Float(string="Total OT", compute='_compute_total_ot', store=True)
    # Holiday penalty days for the last month (fractional allowed).
    penalty_last_month = fields.Float(string="Holiday Penalty (Last Month)")
    # Specific dates behind the last-month figures, used in attachment texts.
    absence_last_dates = fields.Char(string="Absence Dates (Last Month)")
    penalty_last_detail = fields.Char(string="Holiday Penalty Detail (Last Month)")
    reg_ot_last_detail = fields.Char(string="Reg OT Detail (Last Month)")
    off_ot_last_detail = fields.Char(string="Off OT Detail (Last Month)")

    @api.depends('reg_ot_this_month', 'reg_ot_last_month',
                 'off_ot_this_month', 'off_ot_last_month')
    def _compute_total_ot(self):
        for rec in self:
            rec.total_ot = (
                rec.reg_ot_this_month + rec.reg_ot_last_month
                + rec.off_ot_this_month + rec.off_ot_last_month
            )

    @api.depends('attended_days', 'period_days')
    def _compute_attendance_ratio(self):
        for rec in self:
            rec.attendance_ratio = (
                100.0 * rec.attended_days / rec.period_days
                if rec.period_days else 0.0
            )

    @api.depends('has_advance', 'attendance_ratio')
    def _compute_needs_review(self):
        for rec in self:
            rec.needs_review = bool(
                rec.has_advance and rec.attendance_ratio < ADVANCE_REVIEW_RATIO)

    @api.depends('needs_review', 'advance_state')
    def _compute_is_pending_review(self):
        for rec in self:
            rec.is_pending_review = rec.needs_review and rec.advance_state == 'pending'

    @api.depends('has_advance', 'needs_review', 'advance_state')
    def _compute_advance_granted(self):
        """Eligible employees keep their advance automatically; the ones under
        the attendance threshold only get it once approved by hand."""
        for rec in self:
            if not rec.has_advance:
                rec.advance_granted = False
            elif rec.needs_review:
                rec.advance_granted = rec.advance_state == 'approved'
            else:
                rec.advance_granted = True

    @api.depends('attended_days', 'advance_days', 'advance_granted')
    def _compute_total_att_days(self):
        for rec in self:
            rec.total_att_days = rec.attended_days + (
                rec.advance_days if rec.advance_granted else 0)

    def action_approve_advance(self):
        self.write({'advance_state': 'approved'})
        return True

    def action_reject_advance(self):
        self.write({'advance_state': 'rejected'})
        return True


class SscAttendanceSummaryDay(models.Model):
    _name = 'ssc.attendance.summary.day'
    _description = "Labour Daily Attendance"
    _order = 'summary_id, date'

    summary_id = fields.Many2one(
        'ssc.attendance.summary', string="Summary",
        required=True, ondelete='cascade', index=True,
    )
    date = fields.Date(string="Date")
    day_name = fields.Char(string="Day")
    # Present / Sick Leave / Absent, from the Studio daily line (drives salary).
    status = fields.Char(string="Status")
    overtime = fields.Float(string="Overtime")
    project_id = fields.Many2one('x_projects_list', string="Project")
    # Punch times from the biometric ssc_attendance module (display only).
    check_in = fields.Datetime(string="Check In")
    check_out = fields.Datetime(string="Check Out")
    # Overtime computed from the raw punches (ssc_attendance total_ot), shown
    # next to the adjusted overtime so the two sources can be compared.
    biotime_overtime = fields.Float(string="BioTime Overtime")
    # Raw biometric attendance: Present when the day carries a punch, else
    # Absent - shown beside the adjusted status the salary is computed from.
    biotime_status = fields.Char(
        string="BioTime Attendance", compute='_compute_biotime_status', store=True)

    @api.depends('check_in')
    def _compute_biotime_status(self):
        for rec in self:
            rec.biotime_status = 'Present' if rec.check_in else 'Absent'
