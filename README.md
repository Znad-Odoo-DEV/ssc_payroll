# SSC Payroll

Native Odoo 18 payroll module for multi-company construction groups: labour and
staff payroll driven by daily attendance, with per-project cost distribution.

It replaces a legacy custom payroll app, and still reads the custom models
(`x_*`) left in the database while the migration is in progress — see
[Legacy bridge](#legacy-bridge).

## Features

- **Employee master** (`ssc.employee`) — salary structure, allowances, overtime
  rates, visa company, WPS vs cash routing, salary visibility restricted by group.
- **Attendance sheets** — labour sheets and staff attendance, with the period
  cycle (e.g. the 21st to the 20th) configurable per company.
- **Payslips** — attendance days, overtime split into regular-day and off-day
  hours priced at their own rates, salary attachments, and the resulting net.
- **Project distribution** — every payslip spreads its days and overtime over
  the projects the employee actually worked on, including a share of the
  advanced days, with a share-per-project chart.
- **Salary batches** — WPS/cash batches per company and period, with an approval
  flow and a project roll-up of the whole batch.
- **Salary attachments** — additions and deductions (sick leave, medical bills,
  phone bills, absence settlements) typed and settled on the payslip.
- **Employee expenses** — advances, staff loans, fines and end-of-service, each
  with its own instalment plan and outstanding balance.
- **Public holidays** per company, used by the attendance and overtime rules.
- **Dashboards** — an OWL payroll dashboard with period KPIs (net payroll,
  overtime hours and amounts, cost per project, top employees) and drill-downs.
- **Reports** — payslip and salary batch PDFs.
- **Security** — six groups (attendance / payroll / approvals, each split
  between labour and staff) plus multi-company record rules.

## Requirements

- Odoo 18
- `base`, `mail`, `hr`

## Installation

1. Copy `ssc_payroll` into your addons path.
2. Update the apps list and install **SSC Payroll**.

## Configuration

- **Settings → Companies**: set the payroll code, the WPS abbreviation and the
  payroll cycle start day for each company.
- **Users**: grant the payroll/attendance groups (labour or staff, officer or
  manager) — nothing is visible without one of them.
- **Configuration → Attachment Types**: review the shipped types before the
  first payroll run.
- `ssc.employee.visa_company` ships with placeholder companies
  (`Company A`…`Company D`); adapt the selection and `VISA_COMPANY_MAP` in
  `models/ssc_employee.py` to your group.

## Legacy bridge

The module deliberately does **not** declare a dependency on the database's
auto-generated customisation module: on Odoo.sh it is generated per database
and the dependency graph never resolves it. The links to the legacy custom
models (`x_projects_list`, `x_employeeslist`, `x_attachments_list`, …) resolve
at runtime when those models exist, and are skipped when they do not — so the
module installs on a plain database.

A scheduled action mirrors legacy salary attachments into `ssc.attachment`
every 10 minutes; disable it once the legacy side is retired.

## License

LGPL-3. See [LICENSE](LICENSE).
