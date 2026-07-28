# -*- coding: utf-8 -*-
import logging
import re
import unicodedata

_logger = logging.getLogger(__name__)


def _clean_barcode(value):
    """hr.employee.barcode must be accent-free alphanumeric and <= 18 chars, so
    'RW-14' becomes 'RW14'. Returns False when nothing usable is left."""
    if not value:
        return False
    text = unicodedata.normalize('NFKD', str(value)).encode('ascii', 'ignore').decode()
    text = re.sub(r'[^A-Za-z0-9]', '', text)[:18]
    return text or False

# The SSC groups that should get full payroll access. Referenced by XML id at
# runtime because this module deliberately does not depend on
# studio_customization, so they cannot be referenced from XML/CSV.
PAYROLL_ACCESS_GROUP_XMLIDS = ('__ssc__.odoo_admin', '_ssc_manager.group')

AUTOMATION_NAME = "SSC Payroll: mirror employee to ssc.employee"
AUTOMATION_NAME_ADVANCE = "SSC Payroll: mirror advance to ssc.advance"
AUTOMATION_NAME_LOAN = "SSC Payroll: mirror staff loan to ssc.staff.loan"
AUTOMATION_NAME_LEAVE = "SSC Payroll: mirror leave expense to ssc.leave.expense"
AUTOMATION_NAME_FINE = "SSC Payroll: mirror fine to ssc.fine"
AUTOMATION_NAME_EOS = "SSC Payroll: mirror end of service to ssc.end.of.service"
AUTOMATION_NAME_HOLD = "SSC Payroll: mirror on-hold amount to ssc.on.hold"
AUTOMATION_NAME_SHEET = "SSC Payroll: mirror attendance sheet to ssc.attendance.sheet"
AUTOMATION_NAME_BATCH = "SSC Payroll: mirror salary batch to ssc.salary.batch"
AUTOMATION_NAME_PAYSLIP = "SSC Payroll: mirror payslip to ssc.payslip"


def _ensure_studio_automation(env, name, studio_model, code):
    """Create an on-create/write automated action that mirrors a Studio model
    into a native one, unless it already exists. Using an automation avoids a
    Python _inherit of the Studio model, which breaks databases without Studio."""
    if studio_model not in env or 'base.automation' not in env:
        return
    Automation = env['base.automation']
    if Automation.search_count([('name', '=', name)]):
        return
    model = env['ir.model'].search([('model', '=', studio_model)], limit=1)
    if not model:
        return
    Automation.create({
        'name': name,
        'model_id': model.id,
        'trigger': 'on_create_or_write',
        'active': True,
        'action_server_ids': [(0, 0, {
            'name': name,
            'model_id': model.id,
            'state': 'code',
            'code': code,
        })],
    })
    _logger.info("ssc_payroll: automated action %r created.", name)


def _get(record, field_name, default=False):
    """Safely read a Studio field that may not exist on this database."""
    if field_name in record._fields:
        return record[field_name]
    return default


def ensure_studio_employee_automation(env):
    """Create the automated action that mirrors x_employeeslist onto
    ssc.employee on every create/write. This replaces a Python _inherit of the
    Studio model, which would break any database without Studio."""
    if 'x_employeeslist' not in env or 'base.automation' not in env:
        return
    Automation = env['base.automation']
    if Automation.search_count([('name', '=', AUTOMATION_NAME)]):
        return
    model = env['ir.model'].search([('model', '=', 'x_employeeslist')], limit=1)
    if not model:
        return
    Automation.create({
        'name': AUTOMATION_NAME,
        'model_id': model.id,
        'trigger': 'on_create_or_write',
        'active': True,
        'action_server_ids': [(0, 0, {
            'name': AUTOMATION_NAME,
            'model_id': model.id,
            'state': 'code',
            'code': "env['ssc.employee']._sync_from_studio(records)",
        })],
    })
    _logger.info("ssc_payroll: employee mirror automated action created.")


def resync_studio_employees(env):
    """Link + refresh every ssc.employee from the Studio master."""
    if 'x_employeeslist' not in env:
        return
    src = env['x_employeeslist'].with_context(active_test=False).search([])
    env['ssc.employee']._sync_from_studio(src)
    _logger.info("ssc_payroll: %s employees synced from x_employeeslist.", len(src))


def ensure_studio_advance_automation(env):
    """Mirror x_advance_salaries onto ssc.advance on every create/write."""
    _ensure_studio_automation(
        env, AUTOMATION_NAME_ADVANCE, 'x_advance_salaries',
        "env['ssc.advance']._sync_from_studio(records)")


def resync_studio_advances(env):
    """Link + refresh every ssc.advance from the Studio master."""
    if 'x_advance_salaries' not in env:
        return
    src = env['x_advance_salaries'].with_context(active_test=False).search([])
    env['ssc.advance']._sync_from_studio(src)
    _logger.info("ssc_payroll: %s advances synced from x_advance_salaries.", len(src))


def ensure_studio_loan_automation(env):
    """Mirror x_staff_loan onto ssc.staff.loan on every create/write."""
    _ensure_studio_automation(
        env, AUTOMATION_NAME_LOAN, 'x_staff_loan',
        "env['ssc.staff.loan']._sync_from_studio(records)")


def resync_studio_loans(env):
    """Link + refresh every ssc.staff.loan from the Studio master."""
    if 'x_staff_loan' not in env:
        return
    src = env['x_staff_loan'].with_context(active_test=False).search([])
    env['ssc.staff.loan']._sync_from_studio(src)
    _logger.info("ssc_payroll: %s staff loans synced from x_staff_loan.", len(src))


def ensure_studio_leave_automation(env):
    """Mirror x_leave_expenses onto ssc.leave.expense on every create/write."""
    _ensure_studio_automation(
        env, AUTOMATION_NAME_LEAVE, 'x_leave_expenses',
        "env['ssc.leave.expense']._sync_from_studio(records)")


def resync_studio_leaves(env):
    """Link + refresh every ssc.leave.expense from the Studio master."""
    if 'x_leave_expenses' not in env:
        return
    src = env['x_leave_expenses'].with_context(active_test=False).search([])
    env['ssc.leave.expense']._sync_from_studio(src)
    _logger.info("ssc_payroll: %s leave expenses synced from x_leave_expenses.", len(src))


def ensure_studio_fine_automation(env):
    """Mirror x_fines_deductions onto ssc.fine on every create/write."""
    _ensure_studio_automation(
        env, AUTOMATION_NAME_FINE, 'x_fines_deductions',
        "env['ssc.fine']._sync_from_studio(records)")


def resync_studio_fines(env):
    """Link + refresh every ssc.fine from the Studio master."""
    if 'x_fines_deductions' not in env:
        return
    src = env['x_fines_deductions'].with_context(active_test=False).search([])
    env['ssc.fine']._sync_from_studio(src)
    _logger.info("ssc_payroll: %s fines synced from x_fines_deductions.", len(src))


def ensure_studio_eos_automation(env):
    """Mirror x_end_of_service onto ssc.end.of.service on every create/write."""
    _ensure_studio_automation(
        env, AUTOMATION_NAME_EOS, 'x_end_of_service',
        "env['ssc.end.of.service']._sync_from_studio(records)")


def resync_studio_eos(env):
    """Link + refresh every ssc.end.of.service from the Studio master."""
    if 'x_end_of_service' not in env:
        return
    src = env['x_end_of_service'].with_context(active_test=False).search([])
    env['ssc.end.of.service']._sync_from_studio(src)
    _logger.info("ssc_payroll: %s end-of-service records synced from x_end_of_service.", len(src))


def ensure_studio_hold_automation(env):
    """Mirror x_on_hold_amounts onto ssc.on.hold on every create/write."""
    _ensure_studio_automation(
        env, AUTOMATION_NAME_HOLD, 'x_on_hold_amounts',
        "env['ssc.on.hold']._sync_from_studio(records)")


def resync_studio_holds(env):
    """Link + refresh every ssc.on.hold from the Studio master."""
    if 'x_on_hold_amounts' not in env:
        return
    src = env['x_on_hold_amounts'].with_context(active_test=False).search([])
    env['ssc.on.hold']._sync_from_studio(src)
    _logger.info("ssc_payroll: %s on-hold amounts synced from x_on_hold_amounts.", len(src))


def ensure_studio_sheet_automation(env):
    """Mirror x_attendance_per_month onto ssc.attendance.sheet on every
    create/write."""
    _ensure_studio_automation(
        env, AUTOMATION_NAME_SHEET, 'x_attendance_per_month',
        "env['ssc.attendance.sheet']._sync_from_studio(records)")


def resync_studio_sheets(env):
    """Link + refresh every ssc.attendance.sheet from the Studio master."""
    if 'x_attendance_per_month' not in env:
        return
    src = env['x_attendance_per_month'].with_context(active_test=False).search([])
    env['ssc.attendance.sheet']._sync_from_studio(src)
    _logger.info("ssc_payroll: %s attendance sheets synced from x_attendance_per_month.", len(src))


def ensure_studio_batch_automation(env):
    """Mirror x_salary_batches onto ssc.salary.batch on every create/write."""
    _ensure_studio_automation(
        env, AUTOMATION_NAME_BATCH, 'x_salary_batches',
        "env['ssc.salary.batch']._sync_from_studio(records)")


def resync_studio_batches(env):
    """Link + refresh every ssc.salary.batch from the Studio master."""
    if 'x_salary_batches' not in env:
        return
    src = env['x_salary_batches'].with_context(active_test=False).search([])
    env['ssc.salary.batch']._sync_from_studio(src)
    _logger.info("ssc_payroll: %s salary batches synced from x_salary_batches.", len(src))


def ensure_studio_payslip_automation(env):
    """Mirror x_all_payslips onto ssc.payslip on every create/write."""
    _ensure_studio_automation(
        env, AUTOMATION_NAME_PAYSLIP, 'x_all_payslips',
        "env['ssc.payslip']._sync_from_studio(records)")


def resync_studio_payslips(env):
    """Link + refresh every ssc.payslip from the Studio master."""
    if 'x_all_payslips' not in env:
        return
    src = env['x_all_payslips'].with_context(active_test=False).search([])
    env['ssc.payslip']._sync_from_studio(src)
    _logger.info("ssc_payroll: %s payslips synced from x_all_payslips.", len(src))


def full_studio_resync(env):
    """Idempotent re-sync of every Studio-mirrored model. Each step is a
    create-or-update keyed on studio_ref_id, so re-running never duplicates."""
    steps = (
        resync_studio_employees, resync_studio_advances, resync_studio_loans,
        resync_studio_leaves, resync_studio_fines, resync_studio_eos,
        resync_studio_holds, resync_studio_sheets, resync_studio_batches,
        resync_studio_payslips,
    )
    for fn in steps:
        try:
            fn(env)
        except Exception:
            _logger.exception("ssc_payroll: re-sync step %s failed.", fn.__name__)
    try:
        env['ssc.attachment']._cron_sync_studio_attachments()
    except Exception:
        _logger.exception("ssc_payroll: attachment import failed.")


def resync_ssc_employees_to_hr(env):
    """Push every ssc.employee onto its matching hr.employee (by Badge ID)."""
    if 'hr.employee' not in env:
        return
    employees = env['ssc.employee'].with_context(active_test=False).search([])
    employees._sync_to_hr()
    _logger.info("ssc_payroll: synced %s employees to hr.employee.", len(employees))


def reconcile_hr_to_ssc(env):
    """Make hr.employee mirror ssc.employee exactly, keyed on Badge (barcode):
    archive every hr.employee that is a duplicate badge or whose badge is absent
    from ssc.employee, then push the ssc details onto the survivors."""
    if 'hr.employee' not in env:
        return
    Hr = env['hr.employee'].sudo().with_context(active_test=False)
    Ssc = env['ssc.employee'].with_context(active_test=False)

    ssc_badges = {
        (e.attendance_code or '').strip()
        for e in Ssc.search([]) if (e.attendance_code or '').strip()
    }

    # Classify: keep the first hr.employee per ssc badge; everything else
    # (no badge, badge not in ssc, or a repeated badge) is removed.
    seen, remove = set(), Hr.browse()
    for hr in Hr.search([]):
        badge = (hr.barcode or '').strip()
        if badge and badge in ssc_badges and badge not in seen:
            seen.add(badge)
        else:
            remove |= hr

    # Safety guard: if the Badge match would wipe out most of hr (e.g. the
    # hr.employees have no barcode, so nothing matches), the match is unreliable
    # -> archive NOTHING and just log. Archiving is only done when the vast
    # majority of hr.employees still match an ssc badge.
    total = len(Hr.search([]))
    if total and len(remove) > total * 0.3:
        _logger.warning(
            "ssc_payroll: hr reconcile SKIPPED archiving - it would archive %s of "
            "%s hr.employees (Badge match looks broken; check attendance_code vs "
            "hr.barcode).", len(remove), total)
    elif remove:
        # Archive rather than hard-delete: deleting cascades onto hr.attendance
        # history and is slow; archiving is reversible and drops them from the
        # active list so hr still mirrors ssc.employee.
        remove.write({'active': False})
        _logger.info(
            "ssc_payroll: hr reconcile - archived %s; kept %s matched.",
            len(remove), len(seen))

    # Push the ssc details onto the matched hr.employees.
    Ssc.search([])._sync_to_hr()


# Studio x_employeeslist -> hr.employee. "Safe" fields carry no unique/format
# constraint and go in the main write. "Risky" ones (unique per company, or
# validated like the image) are written one-by-one afterwards, so a single
# duplicate/corrupt value only skips that field, never the whole employee.
_STUDIO_HR_SAFE_MAP = {
    'job_title': 'x_studio_profession',
    'birthday': 'x_studio_date_of_birth',
    'work_phone': 'x_studio_phone_no',
}


def _studio_hr_base_vals(src):
    vals = {'name': _get(src, 'x_name') or 'Unnamed', 'active': True}
    for hr_field, studio_field in _STUDIO_HR_SAFE_MAP.items():
        value = _get(src, studio_field)
        if value:
            vals[hr_field] = value
    return vals


def _studio_hr_risky_vals(src):
    # company_id is here too: changing it re-checks the (registration_number,
    # company_id) unique constraint, which can clash on the target company.
    vals = {}
    company = _get(src, 'x_studio_company')
    if company:
        vals['company_id'] = company.id
    badge = _clean_barcode(_get(src, 'x_studio_attendance_id'))
    if badge:
        vals['barcode'] = badge
    for hr_field, studio_field in (
            ('registration_number', 'x_studio_employee_id'),
            ('identification_id', 'x_studio_eid_no'),
            ('passport_id', 'x_studio_passport')):
        value = _get(src, studio_field)
        if value:
            vals[hr_field] = value
    avatar = _get(src, 'x_avatar_image')
    if avatar:
        vals['image_1920'] = avatar
    return vals


def sync_studio_employees_to_hr(env):
    """Create/update an hr.employee for every Studio x_employeeslist record,
    matched by Badge (barcode) -> Employee ID (registration_number) -> name, so
    no hr.employee is duplicated. Missing ones are created, matched ones
    refreshed. Unique/validated fields are written best-effort."""
    Studio = env.get('x_employeeslist')
    if Studio is None:
        return
    Hr = env['hr.employee'].sudo().with_context(active_test=False)
    created = updated = 0
    for src in Studio.with_context(active_test=False).search([]):
        hr = None
        try:
            with env.cr.savepoint():
                badge = _clean_barcode(_get(src, 'x_studio_attendance_id'))
                reg = (_get(src, 'x_studio_employee_id') or '').strip()
                name = (_get(src, 'x_name') or '').strip()
                hr = Hr.browse()
                if badge:
                    hr = Hr.search([('barcode', '=', badge)], limit=1)
                if not hr and reg:
                    hr = Hr.search([('registration_number', '=', reg)], limit=1)
                if not hr and name:
                    hr = Hr.search([('name', '=', name)], limit=1)
                base = _studio_hr_base_vals(src)
                if hr:
                    hr.write(base)
                    updated += 1
                else:
                    hr = Hr.create(base)
                    created += 1
        except Exception:
            _logger.exception(
                "ssc_payroll: studio -> hr base failed for x_employeeslist id=%s", src.id)
            continue
        # Unique / validated fields: set each on its own savepoint so a duplicate
        # barcode/registration or a corrupt avatar only skips that one field.
        for field, value in _studio_hr_risky_vals(src).items():
            try:
                with env.cr.savepoint():
                    hr.write({field: value})
            except Exception:
                _logger.warning(
                    "ssc_payroll: skipped %s for hr id=%s (x_employeeslist id=%s).",
                    field, hr.id, src.id)
        # Commit each employee so progress survives a lock timeout / restart and
        # locks are held only briefly (the writes touch ir.attachment for images).
        env.cr.commit()
    _logger.info(
        "ssc_payroll: studio -> hr.employee - created %s, updated %s.", created, updated)


def _studio_hr_keepers(env):
    """Set of hr.employee ids that a Studio x_employeeslist record maps to
    (Badge -> Employee ID -> name). These are the records to keep."""
    Studio = env.get('x_employeeslist')
    Hr = env['hr.employee'].sudo().with_context(active_test=False)
    keepers = set()
    if Studio is None:
        return keepers
    for src in Studio.with_context(active_test=False).search([]):
        badge = _clean_barcode(_get(src, 'x_studio_attendance_id'))
        reg = (_get(src, 'x_studio_employee_id') or '').strip()
        name = (_get(src, 'x_name') or '').strip()
        hr = Hr.browse()
        if badge:
            hr = Hr.search([('barcode', '=', badge)], limit=1)
        if not hr and reg:
            hr = Hr.search([('registration_number', '=', reg)], limit=1)
        if not hr and name:
            hr = Hr.search([('name', '=', name)], limit=1)
        if hr:
            keepers.add(hr.id)
    return keepers


def dedupe_hr_from_studio(env):
    """Archive every ACTIVE hr.employee that is not the one a Studio
    x_employeeslist record maps to - i.e. the old duplicates / orphans - so hr
    keeps exactly the imported set. User-linked employees are never touched, and
    the run aborts if it would archive the majority (a sign the match is off).
    Archiving (not deleting) preserves each employee's attendance history and is
    reversible."""
    if 'hr.employee' not in env or env.get('x_employeeslist') is None:
        return
    Hr = env['hr.employee'].sudo()
    keepers = _studio_hr_keepers(env)
    active = Hr.search([('active', '=', True)])
    remove = active.filtered(lambda e: e.id not in keepers and not e.user_id)
    if active and len(remove) > len(active) * 0.6:
        _logger.warning(
            "ssc_payroll: hr dedupe SKIPPED - it would archive %s of %s active "
            "employees (Studio match looks off).", len(remove), len(active))
        return
    if remove:
        remove.write({'active': False})
    _logger.info(
        "ssc_payroll: hr dedupe - archived %s duplicate/orphan(s); kept %s from Studio.",
        len(remove), len(keepers))


def grant_payroll_access(env):
    """Give the designated SSC groups full payroll access by having them imply
    the four Manager groups, which together cover every functional area.
    Idempotent, and a no-op when the manager groups are absent (e.g. on a
    database without those Studio groups)."""
    manager_xmlids = (
        'ssc_payroll.group_labour_attendance_manager',
        'ssc_payroll.group_staff_attendance_manager',
        'ssc_payroll.group_labour_payroll_manager',
        'ssc_payroll.group_staff_payroll_manager',
    )
    managers = env['res.groups'].browse()
    for mx in manager_xmlids:
        grp = env.ref(mx, raise_if_not_found=False)
        if grp:
            managers |= grp
    if not managers:
        return
    for xmlid in PAYROLL_ACCESS_GROUP_XMLIDS:
        group = env.ref(xmlid, raise_if_not_found=False)
        if not group:
            _logger.info("ssc_payroll: group %s not found; access grant skipped.", xmlid)
            continue
        group.write({'implied_ids': [(4, m.id) for m in managers]})
        # Propagate to the group's current members, since implied_ids only adds
        # the manager groups to users assigned afterwards.
        users = group.with_context(active_test=False).users
        if users:
            managers.write({'users': [(4, uid) for uid in users.ids]})
        _logger.info("ssc_payroll: granted full payroll access to %s.", xmlid)


def grant_hr_admin_employee_access(env):
    """Give the SSC Attendance 'SSC HR Admin' group read/write on the employee
    master (and read on the staff project distribution its form shows).

    The group lives in ssc_attendance, which already depends on ssc_payroll, so
    a static CSV rule here would be a circular dependency. The access lines are
    therefore created idempotently at install/upgrade. A no-op when the group
    is absent. Salary figures of engineer/office staff are still stripped for
    this group by ssc.employee.read()."""
    group = env.ref('ssc_attendance.group_ssc_hr_admin', raise_if_not_found=False)
    if not group:
        _logger.info(
            "ssc_payroll: SSC HR Admin group absent; employee access skipped.")
        return
    Access = env['ir.model.access']
    specs = [
        ('ssc_payroll_hr_admin_employee', 'ssc.employee',
         {'perm_read': True, 'perm_write': True,
          'perm_create': False, 'perm_unlink': False}),
        ('ssc_payroll_hr_admin_staff_project', 'ssc.staff.project',
         {'perm_read': True, 'perm_write': False,
          'perm_create': False, 'perm_unlink': False}),
    ]
    for name, model_name, perms in specs:
        model = env['ir.model'].search([('model', '=', model_name)], limit=1)
        if not model:
            continue
        access = Access.search([
            ('name', '=', name),
            ('group_id', '=', group.id),
            ('model_id', '=', model.id)], limit=1)
        if access:
            access.write(perms)
        else:
            Access.create(dict(perms, name=name,
                               model_id=model.id, group_id=group.id))
    _logger.info("ssc_payroll: granted SSC HR Admin access to the employee master.")


def _as_percentage(value):
    """Studio stores the staff share as a fraction (0.33); the new model uses
    0-100. Convert fractions, leave already-percentage values untouched."""
    if not value:
        return 0.0
    return round(value * 100.0, 2) if value <= 1 else value


def post_init_hook(env):
    """Import employee master data (and staff project distribution) from the
    Studio ``x_employeeslist`` / ``x_attendance_per_emplo`` models.

    The migration is best-effort: any failure is logged but never propagated,
    so a data issue can never block the module installation. It is also
    idempotent and a no-op when the Studio models are absent.
    """
    # Grant access first, independently of the Studio data import, so it runs
    # even on a database without the Studio models.
    try:
        grant_payroll_access(env)
    except Exception:  # pragma: no cover
        _logger.exception("ssc_payroll: granting payroll access failed.")

    try:
        grant_hr_admin_employee_access(env)
    except Exception:  # pragma: no cover
        _logger.exception("ssc_payroll: granting SSC HR Admin access failed.")

    try:
        if 'x_employeeslist' not in env:
            _logger.info("ssc_payroll: x_employeeslist not found, skipping migration.")
            return
        resync_studio_employees(env)
        ensure_studio_employee_automation(env)
        _migrate_staff_projects(env)
        resync_studio_advances(env)
        ensure_studio_advance_automation(env)
        resync_studio_loans(env)
        ensure_studio_loan_automation(env)
        resync_studio_leaves(env)
        ensure_studio_leave_automation(env)
        resync_studio_fines(env)
        ensure_studio_fine_automation(env)
        resync_studio_eos(env)
        ensure_studio_eos_automation(env)
        resync_studio_holds(env)
        ensure_studio_hold_automation(env)
        resync_studio_sheets(env)
        ensure_studio_sheet_automation(env)
        # Batches before payslips: the payslip mirror links to its batch.
        resync_studio_batches(env)
        ensure_studio_batch_automation(env)
        resync_studio_payslips(env)
        ensure_studio_payslip_automation(env)
        resync_ssc_employees_to_hr(env)
    except Exception:  # pragma: no cover - migration must never break install
        _logger.exception(
            "ssc_payroll: master-data migration failed; the module is still "
            "installed. Re-run the migration manually once the data is fixed.")


def _migrate_staff_projects(env):
    """Rebuild staff project distribution from ``x_attendance_per_emplo``."""
    if 'x_attendance_per_emplo' not in env:
        return

    Employee = env['ssc.employee']
    StaffProject = env['ssc.staff.project']

    wrappers = env['x_attendance_per_emplo'].with_context(active_test=False).search(
        [('x_studio_staff', '=', True)]
    )
    created = 0
    for wrapper in wrappers:
        try:
            lines = _get(wrapper, 'x_studio_staff_project')
            if not lines:
                continue

            acc = _get(wrapper, 'x_studio_employee_acc')
            code = _get(acc, 'x_studio_employee_id') if acc else _get(wrapper, 'x_studio_employee_id')
            name = _get(acc, 'x_name') if acc else _get(wrapper, 'x_name')

            domain = [('employee_code', '=', code)] if code else [('name', '=', name)]
            employee = Employee.with_context(active_test=False).search(domain, limit=1)
            if not employee or employee.staff_project_ids:
                continue

            for line in lines:
                project = _get(line, 'x_studio_project')
                if not project:
                    continue
                StaffProject.create({
                    'employee_id': employee.id,
                    'project_id': project.id,
                    'percentage': _as_percentage(_get(line, 'x_studio_p') or 0.0),
                })
                created += 1
        except Exception:
            _logger.exception(
                "ssc_payroll: skipped staff projects for wrapper id=%s.", wrapper.id)

    _logger.info("ssc_payroll: created %s staff project distribution lines.", created)
