# -*- coding: utf-8 -*-
{
    "name": "Bol.com Integration",
    "version": "1.0.3",
    "category": "eCommerce",
    'summary': 'Integrate & Manage Bol.com Operations from Odoo by using Odoo Bol.com Integration or Bol.com Odoo Integration or Bol Integration. Odoo BOL.com integration, BOL.com integration with Odoo, Odoo integration for BOL.com, Bol.com Connector, Bol.com Connector Odoo, We also provide modules like shipping and marketplace dhl integration express integration fedex integration ups integration gls integration usps integration stamps.com integration shipstation integration easyship integration amazon integration sendcloud integration woocommerce integration shopify integration',

    "depends": ['base_marketplace', 'base_address_extended'],

    'data': [
        'data/bol.transporter.code.csv',
        'data/bol.transporter.tracking.csv',
        'security/ir.model.access.csv',

        'views/marketplace_listing_view.xml',

        'views/delivery_carrier_view.xml',
        'views/sale_order_view.xml',
        'views/stock_view.xml',
        'views/stock_warehouse_view.xml',
        'views/pricelist_view.xml',

        'wizards/operation_view.xml',
        'wizards/cancel_order_in_marketplace_view.xml',

        'views/marketplace_instance_view.xml',
        'views/bol_process_status_view.xml',
        'views/bol_return_view.xml',
        'views/bol_transporter_code_view.xml',
        'views/bol_invoice_request.xml',
        'views/bol_menuitem.xml',

        'data/data.xml',

    ],
    "cloc_exclude": [
        '**/*.xml',  # exclude all XML files from the module
        'static/description/**/*',  # exclude all files in a folder hierarchy recursively
    ],

    'images': ['static/description/bol_banner.jpg'],

    "author": "TeqStars",
    "website": "http://teqstars.com/r/bSq",
    'support': 'support@teqstars.com',
    'maintainer': 'TeqStars',

    "description": """""",

    'demo': [],
    'license': 'OPL-1',
    'auto_install': False,
    'installable': True,
    'application': True,
    'qweb': [],
    "price": "479.99",
    "currency": "EUR",
}
