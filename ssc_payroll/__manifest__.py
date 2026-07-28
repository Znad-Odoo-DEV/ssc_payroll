# -*- coding: utf-8 -*-
{
    'name': 'SSC Payroll',
    'version': '18.0.1.53.0',
    'summary': 'Construction payroll: employees, attendance, payslips, salary '
               'batches and employee expenses (native rewrite of a legacy custom app)',
    'description': """
SSC Payroll
===========
Native Odoo 18 payroll module for multi-company construction groups,
replacing a legacy custom app.

Foundation:
    * Company payroll configuration (code, abbreviation, period start day)
    * Employee master (``ssc.employee``) with salary, allowances and OT rates
    * Staff project distribution
    * Public holidays
    * Salary attachment types
    * Security groups and multi-company record rules
""",
    'author': 'Ibrahim Elzenad',
    'category': 'Human Resources/Payroll',
    'license': 'LGPL-3',
    # NOTE: we intentionally do NOT declare a dependency on the database's
    # auto-generated customisation module. On Odoo.sh it is generated per
    # database and cannot be used as a declared dependency (the graph reports it
    # as unmet and the install never completes). The links to the custom models
    # ``x_projects_list`` and ``x_employeeslist`` still resolve, because those
    # models live in the database registry and are available by the time
    # relations are set up.
    'depends': ['base', 'mail', 'hr'],
    'data': [
        'security/ssc_payroll_groups.xml',
        'security/ir.model.access.csv',
        'security/ssc_payroll_record_rules.xml',
        'data/ssc_attachment_type_data.xml',
        'data/ssc_sequence_data.xml',
        'data/ssc_cron_data.xml',
        'views/res_company_views.xml',
        'views/ssc_employee_views.xml',
        'views/ssc_public_holiday_views.xml',
        'views/ssc_attachment_type_views.xml',
        'views/ssc_attendance_sheet_views.xml',
        'views/ssc_staff_attendance_views.xml',
        'views/ssc_attachment_views.xml',
        'views/ssc_payslip_views.xml',
        'views/ssc_salary_batch_views.xml',
        'views/ssc_expenses_views.xml',
        'views/ssc_dashboard_views.xml',
        'report/ssc_payslip_report.xml',
        'report/ssc_salary_batch_report.xml',
        'views/ssc_payroll_menus.xml',
    ],
    'assets': {
        'web.assets_backend': [
            'ssc_payroll/static/src/scss/dashboard.scss',
            'ssc_payroll/static/src/js/dashboard.js',
            'ssc_payroll/static/src/xml/dashboard.xml',
        ],
    },
    'post_init_hook': 'post_init_hook',
    'application': True,
    'installable': True,
}
