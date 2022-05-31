# -*- coding: utf-8 -*-
# Part of Odoo. See LICENSE file for full copyright and licensing details.

from odoo import api, fields, models


class SaleOrder(models.Model):
    _inherit = 'sale.order'

    # Field Declarations
    webship_status = fields.Char('Webship Status')
    webship_reference = fields.Char('Webship Reference')
    webship_track_and_trace = fields.Char('Webship Track and Trace')
   