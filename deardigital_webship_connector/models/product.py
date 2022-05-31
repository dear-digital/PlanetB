# -*- coding: utf-8 -*-
# Part of Odoo. See LICENSE file for full copyright and licensing details.

from odoo import api, fields, models


class ProductCategory(models.Model):
    _inherit = 'product.category'

    ignored_in_webship = fields.Boolean(string='Ignore in Webship',
                                        help='All products having this category or child of this category will not be considered in process of Webship file transfers.')
