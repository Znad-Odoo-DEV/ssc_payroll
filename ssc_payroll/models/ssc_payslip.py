# -*- coding: utf-8 -*-
import logging

from odoo import api, fields, models
from odoo.exceptions import UserError

from .ssc_project_chart import render_project_chart
from .utils import studio_get as _get

_logger = logging.getLogger(__name__)


class SscPayslip(models.Model):
    _name = 'ssc.payslip'
    _description = "Payslip"
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'to_date desc, id desc'

    name = fields.Char(string="Description", compute='_compute_name', store=True, readonly=False)
    employee_id = fields.Many2one(
        'ssc.employee', string="Employee", required=True, index=True, tracking=True,
    )
    company_id = fields.Many2one(related='employee_id.company_id', store=True, index=True)
    currency_id = fields.Many2one(related='employee_id.currency_id', store=True)
    employee_code = fields.Char(related='employee_id.employee_code', string="Employee ID", store=True)

    # Source documents
    attendance_sheet_id = fields.Many2one('ssc.attendance.sheet', string="Attendance Sheet")
    staff_attendance_id = fields.Many2one('ssc.staff.attendance', string="Staff Attendance")
    summary_id = fields.Many2one('ssc.attendance.summary', string="Attendance Summary")
    batch_id = fields.Many2one('ssc.salary.batch', string="Batch", index=True, tracking=True)

    # Period
    from_date = fields.Date(string="From Date")
    to_date = fields.Date(string="To Date")
    days = fields.Integer(string="Days in Month")
    month = fields.Selection(
        [
            ('JAN', 'JAN'), ('FEB', 'FEB'), ('MAR', 'MAR'), ('APR', 'APR'),
            ('MAY', 'MAY'), ('JUN', 'JUN'), ('JUL', 'JUL'), ('AUG', 'AUG'),
            ('SEP', 'SEP'), ('OCT', 'OCT'), ('NOV', 'NOV'), ('DEC', 'DEC'),
        ],
        string="Month",
    )
    year = fields.Char(string="Year")

    # Routing
    is_cash = fields.Boolean(string="Cash", help="Paid in cash (no WPS employee ID).")
    is_staff = fields.Boolean(string="Staff")

    # Contract snapshot (frozen at generation so later master changes do not
    # retroactively alter historical payslips).
    designation = fields.Char(string="Designation")
    basic_salary = fields.Monetary(string="Basic Salary", tracking=True)
    house_allowance = fields.Monetary(string="House Allowance")
    transport_allowance = fields.Monetary(string="Travelling Allowance")
    other_allowance = fields.Monetary(string="Other Allowances")
    gross_salary = fields.Monetary(string="Total Gross Salary", tracking=True)
    ot_rate_regular = fields.Float(string="OT Rate - Regular Days")
    ot_rate_off = fields.Float(string="OT Rate - Off Days")

    # Work entries
    total_attendance = fields.Float(string="Total Attendance for This Month", tracking=True)
    overtime_reg = fields.Float(string="Overtime hrs - Regular Days", tracking=True)
    overtime_off = fields.Float(string="Overtime hrs - Off Days", tracking=True)

    # Computed money
    rate_per_day = fields.Monetary(
        string="Rate per Day", compute='_compute_rate_per_day', store=True,
    )
    # Tracked as well: an hours correction is only readable in the chatter next
    # to the money it moved.
    total_salary = fields.Monetary(
        string="Total Salary of this Month", compute='_compute_total_salary',
        store=True, tracking=True,
    )
    overtime_salary = fields.Monetary(
        string="Total Overtime Salary", compute='_compute_overtime_salary',
        store=True, tracking=True,
    )
    # Regular / off overtime priced separately, so analytics can sum each side
    # across employees whose hourly rates differ.
    overtime_reg_amount = fields.Monetary(
        string="Overtime Amount - Regular", compute='_compute_overtime_salary', store=True,
    )
    overtime_off_amount = fields.Monetary(
        string="Overtime Amount - Off", compute='_compute_overtime_salary', store=True,
    )
    salary_adjustment = fields.Monetary(
        string="Salary Adjustment", compute='_compute_salary_adjustment', store=True,
        tracking=True,
    )
    net_amount = fields.Monetary(
        string="Net Amount", compute='_compute_net_amount', store=True, tracking=True,
    )

    # Lines
    project_ids = fields.One2many('ssc.payslip.project', 'payslip_id', string="Projects")
    attachment_ids = fields.One2many('ssc.attachment', 'payslip_id', string="Salary Attachments")
    day_ids = fields.One2many('ssc.payslip.day', 'payslip_id', string="Daily Attendance")

    @api.model
    def _copy_days(self, day_records):
        """Build day commands from a summary/staff daily recordset so the same
        attendance detail can be shown on the payslip."""
        return [(0, 0, {
            'date': d.date,
            'day_name': d.day_name,
            'status': d.status,
            'check_in': d.check_in,
            'check_out': d.check_out,
            'overtime': d.overtime if 'overtime' in d._fields else 0.0,
            'biotime_overtime': d.biotime_overtime if 'biotime_overtime' in d._fields else 0.0,
            'project_id': d.project_id.id or False,
        }) for d in day_records]

    state = fields.Selection(
        [('new', 'New'), ('checked', 'Checked'), ('attached', 'Attached'), ('paid', 'Paid')],
        string="Status", default='new', tracking=True, index=True,
    )
    # Auto-generated summary of the period's deductions/advance, written at
    # generation time. Shown in light purple so it reads as machine-written;
    # sanitize is off because the content is built by us, not entered by a user.
    auto_note = fields.Html(string="Auto Notes", readonly=True, sanitize=False)
    # Free notes typed by the payroll officer (plain black text).
    note = fields.Text(string="Notes & Remarks", tracking=True)
    # Live link to the Studio x_all_payslips record this mirrors, plus the
    # exact Studio money figures. Mirrored payslips must show Studio's own
    # numbers rather than recompute them, so the computes below fall back to
    # these values when studio_ref_id is set; native payslips ignore them.
    studio_ref_id = fields.Integer(string="Studio Ref", index=True, copy=False)
    studio_total_salary = fields.Monetary(string="Total Salary (Studio)")
    studio_overtime_salary = fields.Monetary(string="Overtime Salary (Studio)")
    studio_salary_adjustment = fields.Monetary(string="Salary Adjustment (Studio)")
    studio_net_amount = fields.Monetary(string="Net Amount (Studio)")

    @api.depends('employee_id', 'month', 'year')
    def _compute_name(self):
        for slip in self:
            slip.name = "Pay Slip for %s-%s for %s" % (
                slip.month or '', slip.year or '', slip.employee_id.name or '',
            )

    project_chart = fields.Html(
        string="Worked Days", compute='_compute_project_chart',
        sanitize=False, readonly=True,
    )
    project_chart_allocated = fields.Html(
        string="Including Advanced Days", compute='_compute_project_chart',
        sanitize=False, readonly=True,
    )

    @api.depends('project_ids.total_amount', 'project_ids.share',
                 'project_ids.allocated_total_amount',
                 'project_ids.allocated_share')
    def _compute_project_chart(self):
        for slip in self:
            label = lambda l: l.project_id.display_name
            slip.project_chart = render_project_chart(
                slip.project_ids, label_of=label,
                amount_of=lambda l: l.total_amount,
                share_of=lambda l: l.share,
                currency=slip.currency_id,
            )
            slip.project_chart_allocated = render_project_chart(
                slip.project_ids, label_of=label,
                amount_of=lambda l: l.allocated_total_amount,
                share_of=lambda l: l.allocated_share,
                currency=slip.currency_id,
            )

    @api.depends('gross_salary', 'days')
    def _compute_rate_per_day(self):
        for slip in self:
            slip.rate_per_day = slip.gross_salary / slip.days if slip.days else 0.0

    @api.depends('total_attendance', 'days', 'gross_salary', 'rate_per_day',
                 'studio_ref_id', 'studio_total_salary')
    def _compute_total_salary(self):
        for slip in self:
            if slip.studio_ref_id:
                slip.total_salary = slip.studio_total_salary
            elif slip.days and slip.total_attendance >= slip.days:
                slip.total_salary = slip.gross_salary
            else:
                slip.total_salary = slip.rate_per_day * slip.total_attendance

    @api.depends('overtime_reg', 'ot_rate_regular', 'overtime_off', 'ot_rate_off',
                 'studio_ref_id', 'studio_overtime_salary')
    def _compute_overtime_salary(self):
        for slip in self:
            slip.overtime_reg_amount = slip.overtime_reg * slip.ot_rate_regular
            slip.overtime_off_amount = slip.overtime_off * slip.ot_rate_off
            if slip.studio_ref_id:
                slip.overtime_salary = slip.studio_overtime_salary
            else:
                slip.overtime_salary = (
                    slip.overtime_reg_amount + slip.overtime_off_amount
                )

    @api.depends('attachment_ids.signed_value',
                 'studio_ref_id', 'studio_salary_adjustment')
    def _compute_salary_adjustment(self):
        for slip in self:
            if slip.studio_ref_id:
                slip.salary_adjustment = slip.studio_salary_adjustment
            else:
                slip.salary_adjustment = sum(slip.attachment_ids.mapped('signed_value'))

    @api.depends('total_salary', 'overtime_salary', 'salary_adjustment',
                 'studio_ref_id', 'studio_net_amount')
    def _compute_net_amount(self):
        for slip in self:
            if slip.studio_ref_id:
                slip.net_amount = slip.studio_net_amount
            else:
                slip.net_amount = slip.total_salary + slip.overtime_salary + slip.salary_adjustment

    # ------------------------------------------------------------------
    # Workflow
    # ------------------------------------------------------------------
    def action_check(self):
        self.write({'state': 'checked'})

    def action_attach(self):
        for slip in self:
            slip._attach_pending_attachments()
        self.write({'state': 'attached'})

    def action_set_paid(self):
        self.write({'state': 'paid'})
        self.mapped('attachment_ids').write({'state': 'paid'})

    def action_reset(self):
        self.write({'state': 'new'})

    def _attach_pending_attachments(self):
        """Link new attachments matching this employee/period and mark them
        attached (mirrors the Studio 'attach with salary' behaviour)."""
        self.ensure_one()
        pending = self.env['ssc.attachment'].search([
            ('employee_id', '=', self.employee_id.id),
            ('month', '=', self.month),
            ('year', '=', self.year),
            ('state', '!=', 'paid'),
            ('payslip_id', '=', False),
        ])
        pending.write({'payslip_id': self.id, 'state': 'attached'})

    # ------------------------------------------------------------------
    # Keeping the project lines in step with the payslip.
    #
    # The overtime report and the dashboard read the project lines, not the
    # payslip, so overtime corrected on a payslip has to travel down to its
    # lines - and hours corrected line by line back up to the payslip - or the
    # two would tell different stories for the same employee.
    #
    # Downwards the payslip's figure wins outright: it is what the employee is
    # paid, so the projects share exactly it. Upwards only the edit's delta is
    # carried, because a line edit must never quietly cut overtime the payslip
    # holds outside any project.
    # ------------------------------------------------------------------
    def write(self, vals):
        skip = self.env.context.get('ssc_skip_project_ot_sync')
        push = not skip and ('overtime_reg' in vals or 'overtime_off' in vals)
        pull = not skip and not push and 'project_ids' in vals
        before = self._project_overtime_totals() if pull else {}
        res = super().write(vals)
        if push:
            self._apply_overtime_to_projects()
        elif pull:
            self._pull_overtime_from_projects(before)
        return res

    def _project_overtime_totals(self):
        return {
            slip.id: (sum(slip.project_ids.mapped('total_overtime_reg')),
                      sum(slip.project_ids.mapped('total_overtime_off')))
            for slip in self
        }

    def _apply_overtime_to_projects(self):
        """Spread the payslip's overtime hours over its project lines.

        Each kind keeps the proportions its lines already carry, falling back
        to their share of the total overtime, then of the attendance days,
        then to an even split. The last line absorbs the rounding, so the
        lines always add up to the payslip exactly.
        """
        for slip in self:
            lines = slip.project_ids
            if not lines:
                continue
            updates = {line: {} for line in lines}
            for fname, total in (('total_overtime_reg', slip.overtime_reg),
                                 ('total_overtime_off', slip.overtime_off)):
                weights = self._overtime_weights(lines, fname)
                base = sum(weights)
                spread = 0.0
                for index, line in enumerate(lines):
                    if index == len(lines) - 1:
                        value = round(total - spread, 2)
                    else:
                        value = round(total * weights[index] / base, 2)
                    spread += value
                    if round(line[fname], 2) != value:
                        updates[line][fname] = value
            for line, line_vals in updates.items():
                if line_vals:
                    line.with_context(ssc_skip_project_ot_sync=True).write(line_vals)

    @api.model
    def _overtime_weights(self, lines, fname):
        """How to divide one kind of overtime over ``lines``."""
        for weights in (lines.mapped(fname),
                        [l.total_overtime_reg + l.total_overtime_off for l in lines],
                        lines.mapped('total_attendance')):
            if sum(weights) > 0:
                return weights
        return [1.0] * len(lines)

    def _pull_overtime_from_projects(self, before):
        """Carry hours corrected on a project line up to the payslip.

        Only what the edit changed is moved, never the lines' new total: a
        payslip may legitimately carry overtime no project line accounts for
        (hours worked on a day with no project on it), and that part must
        survive an edit on one of its lines.
        """
        after = self._project_overtime_totals()
        for slip in self:
            old_reg, old_off = before.get(slip.id, (0.0, 0.0))
            new_reg, new_off = after.get(slip.id, (0.0, 0.0))
            vals = {}
            if round(new_reg - old_reg, 2):
                vals['overtime_reg'] = max(
                    0.0, round(slip.overtime_reg + new_reg - old_reg, 2))
            if round(new_off - old_off, 2):
                vals['overtime_off'] = max(
                    0.0, round(slip.overtime_off + new_off - old_off, 2))
            if vals:
                slip.with_context(ssc_skip_project_ot_sync=True).write(vals)

    def unlink(self):
        # Free the salary attachments this payslip claimed so that a payslip
        # regenerated for the same period re-collects them. Paid attachments are
        # left untouched.
        self.mapped('attachment_ids').filtered(
            lambda a: a.state != 'paid').write({'state': 'new', 'payslip_id': False})
        return super().unlink()

    # ------------------------------------------------------------------
    # TEMPORARY live bridge with the Studio x_all_payslips master. The salary
    # figures are copied straight in; the derived totals recompute from them,
    # so no payslip logic is touched.
    # ------------------------------------------------------------------
    @api.model
    def _sync_from_studio(self, studio_records):
        Slip = self.with_context(
            tracking_disable=True, mail_create_nolog=True,
            mail_create_nosubscribe=True)
        for src in studio_records:
            try:
                with self.env.cr.savepoint():
                    vals = Slip._studio_payslip_vals(src)
                    if not vals:
                        continue
                    mirror = Slip.search([('studio_ref_id', '=', src.id)], limit=1)
                    if mirror:
                        mirror.write(vals)
                    else:
                        mirror = Slip.create(dict(vals, studio_ref_id=src.id))
                    Slip._sync_payslip_attachments(mirror, src)
                    Slip._sync_payslip_projects(mirror, src)
            except Exception:
                _logger.exception(
                    "ssc_payroll: payslip mirror failed for x_all_payslips id=%s", src.id)
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
    def _studio_payslip_vals(self, src):
        employee = self._resolve_studio_employee(src)
        if not employee:
            return None
        batch = False
        bl = _get(src, 'x_studio_batch')
        if bl:
            batch = self.env['ssc.salary.batch'].search(
                [('studio_ref_id', '=', bl.id)], limit=1)
        sl = _get(src, 'x_studio_attendance_sheet')
        sheet = self.env['ssc.attendance.sheet'].search(
            [('studio_ref_id', '=', sl.id)], limit=1) if sl else False
        month = _get(src, 'x_studio_month')
        if month not in dict(self._fields['month'].selection):
            month = False
        return {
            'employee_id': employee.id,
            'batch_id': batch.id if batch else False,
            'attendance_sheet_id': sheet.id if sheet else False,
            'from_date': _get(src, 'x_studio_from_date') or False,
            'to_date': _get(src, 'x_studio_to_date') or False,
            'days': _get(src, 'x_studio_days') or 0,
            'month': month,
            'year': _get(src, 'x_studio_year') or False,
            'is_cash': bool(_get(src, 'x_studio_cash')),
            'is_staff': bool(_get(src, 'x_studio_staff_1')),
            'designation': _get(src, 'x_studio_designation') or False,
            'basic_salary': _get(src, 'x_studio_basic_salary') or 0.0,
            'house_allowance': _get(src, 'x_studio_house_allowance') or 0.0,
            'transport_allowance': _get(src, 'x_studio_travelling_allownce') or 0.0,
            'other_allowance': _get(src, 'x_studio_other_allowances') or 0.0,
            'gross_salary': _get(src, 'x_studio_total_gross_salary') or 0.0,
            'ot_rate_regular': _get(src, 'x_studio_overtime_hr_on_reg_days') or 0.0,
            'ot_rate_off': _get(src, 'x_studio_overtime_hr_on_off_days') or 0.0,
            'total_attendance': _get(src, 'x_studio_total_attendance_for_this_month_1') or 0.0,
            'overtime_reg': _get(src, 'x_studio_overtime_reg') or 0.0,
            'overtime_off': _get(src, 'x_studio_overtime_off') or 0.0,
            # Exact Studio money figures the computes fall back to.
            'studio_total_salary': _get(src, 'x_studio_total_salary_of_this_month') or 0.0,
            'studio_overtime_salary': _get(src, 'x_studio_total_overtime_salary_of_this_month') or 0.0,
            'studio_salary_adjustment': _get(src, 'x_studio_salary_adjustment') or 0.0,
            'studio_net_amount': _get(src, 'x_studio_net_amount') or 0.0,
        }

    @api.model
    def _sync_payslip_attachments(self, slip, src):
        """Mirror the payslip's Studio salary attachments as ssc.attachment
        lines linked to it, matched by description. These are display copies:
        a mirrored payslip's net uses Studio's own salary adjustment, so they
        are never summed into it again."""
        # month is required on ssc.attachment, so skip when the payslip has none.
        if not slip.month:
            return
        Attachment = self.env['ssc.attachment']
        AttType = self.env['ssc.attachment.type']
        default_type = self.env.ref('ssc_payroll.attachment_type_salary_addition')
        existing = {a.name: a for a in slip.attachment_ids}
        for line in _get(src, 'x_studio_salary_attachments') or []:
            name = _get(line, 'x_name') or self.env._("Attachment")
            value = _get(line, 'x_studio_value') or 0.0
            studio_type = _get(line, 'x_studio_type_of_attachment')
            tname = _get(studio_type, 'x_name')
            att_type = AttType.search([('name', '=', tname)], limit=1) if tname else False
            if not att_type:
                att_type = default_type
            rec = existing.get(name)
            if rec:
                if rec.value != value or rec.type_id.id != att_type.id:
                    rec.write({'value': value, 'type_id': att_type.id})
            else:
                Attachment.create({
                    'name': name,
                    'employee_id': slip.employee_id.id,
                    'type_id': att_type.id,
                    'month': slip.month or False,
                    'year': slip.year or False,
                    'value': value,
                    'payslip_id': slip.id,
                    'state': 'attached',
                })

    @api.model
    def _sync_payslip_projects(self, slip, src):
        """Mirror the payslip's project distribution, resolving each project by
        name against the shared Studio project list."""
        Project = self.env.get('x_projects_list')
        if Project is None:
            return
        Line = self.env['ssc.payslip.project']
        existing = {l.project_id.id: l for l in slip.project_ids}
        for pl in _get(src, 'x_studio_projectsss_sum') or []:
            pname = _get(pl, 'x_name')
            if not pname:
                continue
            proj = Project.search([('x_name', '=', pname)], limit=1)
            if not proj:
                continue
            att = _get(pl, 'x_studio_total_attendance') or 0.0
            ot = _get(pl, 'x_studio_total_overtime') or 0.0
            vals = {
                'total_attendance': att,
                'total_overtime_reg': ot,
                'total_overtime_off': 0.0,
                'allocated_attendance': att,
            }
            rec = existing.get(proj.id)
            if rec:
                rec.write(vals)
            else:
                Line.create(dict(vals, payslip_id=slip.id, project_id=proj.id))
        # Studio only knows one overtime figure per project, so the lines are
        # re-split along the payslip's own regular/off hours.
        slip._apply_overtime_to_projects()


class SscPayslipDay(models.Model):
    _name = 'ssc.payslip.day'
    _description = "Payslip Daily Attendance"
    _order = 'payslip_id, date'

    payslip_id = fields.Many2one(
        'ssc.payslip', string="Payslip", required=True, ondelete='cascade', index=True)
    date = fields.Date(string="Date")
    day_name = fields.Char(string="Day")
    # Two attendance views of the same day. ``status`` and ``overtime`` are the
    # adjusted figures the salary is computed from (from x_attendance_per_emplo);
    # ``biotime_status`` / ``check_in`` / ``check_out`` are the raw biometric
    # record. Showing both side by side makes the manual corrections visible.
    status = fields.Char(string="Status")
    overtime = fields.Float(string="Overtime")
    check_in = fields.Datetime(string="Check In")
    check_out = fields.Datetime(string="Check Out")
    biotime_status = fields.Char(
        string="BioTime Attendance", compute='_compute_biotime_status', store=True,
        help="Raw biometric attendance: Present when the day carries a punch, "
             "otherwise Absent.")
    biotime_overtime = fields.Float(
        string="BioTime Overtime",
        help="Overtime computed from the raw biometric punches (before the "
             "attendance-per-employee adjustment).")
    project_id = fields.Many2one('x_projects_list', string="Project")

    @api.depends('check_in')
    def _compute_biotime_status(self):
        for rec in self:
            rec.biotime_status = 'Present' if rec.check_in else 'Absent'


class SscPayslipProject(models.Model):
    _name = 'ssc.payslip.project'
    _description = "Payslip Project Distribution"
    _order = 'payslip_id, id'

    payslip_id = fields.Many2one(
        'ssc.payslip', string="Payslip", required=True, ondelete='cascade', index=True,
    )
    project_id = fields.Many2one('x_projects_list', string="Project", required=True)
    currency_id = fields.Many2one(related='payslip_id.currency_id', store=True)
    # Stored copies of the payslip period/owner, so this line can be listed,
    # filtered and grouped on its own (the overtime drill-down report).
    employee_id = fields.Many2one(related='payslip_id.employee_id', store=True, index=True)
    company_id = fields.Many2one(related='payslip_id.company_id', store=True, index=True)
    month = fields.Selection(related='payslip_id.month', store=True)
    year = fields.Char(related='payslip_id.year', store=True)
    total_attendance = fields.Float(string="Total Attendance")
    # Overtime hours are split by the kind of day they were worked on, because
    # each kind is paid at its own hourly rate.
    total_overtime_reg = fields.Float(string="Overtime hrs - Regular Days")
    total_overtime_off = fields.Float(string="Overtime hrs - Off Days")
    total_overtime = fields.Float(
        string="Total Overtime", compute='_compute_total_overtime', store=True,
    )

    # Money this project carries for this payslip.
    salary_amount = fields.Monetary(
        string="Salary", compute='_compute_amounts', store=True,
        help="Attendance days on this project at the payslip's daily rate.",
    )
    # The advanced days carry no daily record, so they are attributed to the
    # projects the employee was actually on rather than observed there.
    allocated_attendance = fields.Float(
        string="Allocated Days",
        help="Attendance days plus this project's share of the advanced days.",
    )
    allocated_salary_amount = fields.Monetary(
        string="Allocated Salary", compute='_compute_amounts', store=True)
    allocated_total_amount = fields.Monetary(
        string="Allocated Total", compute='_compute_amounts', store=True)
    allocated_share = fields.Float(
        string="Allocated Share %", compute='_compute_share', store=True)
    ot_reg_amount = fields.Monetary(
        string="OT Regular", compute='_compute_amounts', store=True)
    ot_off_amount = fields.Monetary(
        string="OT Off", compute='_compute_amounts', store=True)
    total_overtime_amount = fields.Monetary(
        string="Total Overtime", compute='_compute_amounts', store=True,
        help="What both kinds of overtime cost together on this project.")
    total_amount = fields.Monetary(
        string="Total", compute='_compute_amounts', store=True)
    share = fields.Float(
        string="Share %", compute='_compute_share', store=True,
        help="This project's share of the payslip's project cost.",
    )

    @api.depends('total_overtime_reg', 'total_overtime_off')
    def _compute_total_overtime(self):
        for rec in self:
            rec.total_overtime = rec.total_overtime_reg + rec.total_overtime_off

    @api.depends('total_attendance', 'allocated_attendance',
                 'total_overtime_reg', 'total_overtime_off',
                 'payslip_id.rate_per_day', 'payslip_id.ot_rate_regular',
                 'payslip_id.ot_rate_off')
    def _compute_amounts(self):
        for rec in self:
            slip = rec.payslip_id
            rec.ot_reg_amount = rec.total_overtime_reg * slip.ot_rate_regular
            rec.ot_off_amount = rec.total_overtime_off * slip.ot_rate_off
            overtime = rec.ot_reg_amount + rec.ot_off_amount
            rec.total_overtime_amount = overtime

            rec.salary_amount = rec.total_attendance * slip.rate_per_day
            rec.total_amount = rec.salary_amount + overtime

            rec.allocated_salary_amount = rec.allocated_attendance * slip.rate_per_day
            rec.allocated_total_amount = rec.allocated_salary_amount + overtime

    @api.depends('total_amount', 'allocated_total_amount',
                 'payslip_id.project_ids.total_amount',
                 'payslip_id.project_ids.allocated_total_amount')
    def _compute_share(self):
        for rec in self:
            lines = rec.payslip_id.project_ids
            total = sum(lines.mapped('total_amount'))
            rec.share = 100.0 * rec.total_amount / total if total else 0.0
            allocated = sum(lines.mapped('allocated_total_amount'))
            rec.allocated_share = (
                100.0 * rec.allocated_total_amount / allocated if allocated else 0.0)
