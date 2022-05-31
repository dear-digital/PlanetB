# -*- coding: utf-8 -*-
{
    'name': "Dear Digital Webship Connector",

    'summary': """
        Importing and Exporting the orders from FTP server and connect to Webship.
    """,

    'description': """
    Task id:
       To export new purchase and sales orders to an FTP server,
       from which they will retrieve the orders. Also to import the current status of orders. 
       (from the FTP server).
    """,

    'author': "Odoo PS",

    'version': '15.0.1.0',

    'depends': ['base', 'sale_management', 'edi_document_sync'],

    'data': [
        'data/export_sale_data.xml',

        'views/product_category_views.xml',
        'views/sale_order_views.xml',
    ],
}
