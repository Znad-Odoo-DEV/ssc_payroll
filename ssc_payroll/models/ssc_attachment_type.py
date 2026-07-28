# -*- coding: utf-8 -*-
from odoo import api, fields, models


class SscAttachmentType(models.Model):
    _name = 'ssc.attachment.type'
    _description = "Salary Attachment Type"
    _order = 'name'

    name = fields.Char(string="Name", required=True)
    kind = fields.Selection(
        [('addition', 'Addition'), ('deduction', 'Deduction')],
        string="Kind", required=True, default='addition',
        help="Whether amounts of this type add to or subtract from the salary.",
    )
    # +1 for additions, -1 for deductions. Kept as a stored field so payslip
    # adjustment = sum(factor * value) stays a simple, readable computation.
    factor = fields.Integer(
        string="Factor", compute='_compute_factor', store=True,
        help="Sign applied to the value in the payslip adjustment "
             "(+1 for additions, -1 for deductions).",
    )
    active = fields.Boolean(string="Active", default=True)

    @api.depends('kind')
    def _compute_factor(self):
        for rec in self:
            rec.factor = 1 if rec.kind == 'addition' else -1
