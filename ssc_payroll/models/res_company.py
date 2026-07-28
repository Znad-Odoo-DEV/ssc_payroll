# -*- coding: utf-8 -*-
from odoo import fields, models


class ResCompany(models.Model):
    _inherit = 'res.company'

    # Short code used in batch / document naming, e.g. ACM, BLD, MEP.
    ssc_company_code = fields.Char(
        string="Payroll Code",
        help="Short company code used in salary batch and document names "
             "(e.g. ACM, BLD, MEP).",
    )
    # Abbreviation used in the WPS part of the batch name, e.g. ACMELLC, BLDCO.
    ssc_company_abbr = fields.Char(
        string="Payroll Abbreviation",
        help="Company abbreviation used in WPS batch names "
             "(e.g. ACMELLC, BLDCO, MEPCO).",
    )
    # Labour payroll cycle start day. The cycle runs from this day of the
    # previous month to (start_day - 1) of the current month (default 21 -> 20).
    ssc_payroll_start_day = fields.Integer(
        string="Payroll Start Day",
        default=21,
        help="Day of month the labour payroll cycle starts on. "
             "The cycle runs from this day of the previous month to the day "
             "before it in the current month (e.g. 21 -> 21st to the 20th).",
    )
    # Weekly off / overtime day. Overtime worked on this day is paid at the
    # "off days" rate; a missing record on this day is bridged from the
    # surrounding days instead of counting as absence.
    ssc_weekly_off_day = fields.Selection(
        [
            ('0', 'Monday'), ('1', 'Tuesday'), ('2', 'Wednesday'),
            ('3', 'Thursday'), ('4', 'Friday'), ('5', 'Saturday'),
            ('6', 'Sunday'),
        ],
        string="Weekly Off Day", default='4',
        help="Weekly rest day treated as an overtime/off day in attendance.",
    )
