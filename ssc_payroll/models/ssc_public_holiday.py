# -*- coding: utf-8 -*-
from odoo import fields, models


class SscPublicHoliday(models.Model):
    _name = 'ssc.public.holiday'
    _description = "Payroll Public Holiday"
    _order = 'date desc'

    name = fields.Char(string="Name", required=True)
    date = fields.Date(string="Date", required=True, index=True)
    # Empty company means the holiday applies to every company.
    company_id = fields.Many2one(
        'res.company', string="Company",
        help="Leave empty to apply the holiday to all companies.",
    )
    note = fields.Char(string="Note")

    _sql_constraints = [
        ('date_company_uniq',
         'unique(date, company_id)',
         "A public holiday already exists for this date and company."),
    ]
