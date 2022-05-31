# -*- coding: utf-8 -*-
# Part of Odoo. See LICENSE file for full copyright and licensing details.

{
    'name': 'EDI Document Synchonizaton',
    'version': '15.0.1.0.',
    'category': 'Tools',
    'description': """
Allows you to configure edi document exchange configurations
""",
    'depends': ['base', 'mail'],
    'data': [
        'security/ir.model.access.csv',
        'views/edi_config_view.xml',
    ],
    'demo': [],
    'installable': True,
}
