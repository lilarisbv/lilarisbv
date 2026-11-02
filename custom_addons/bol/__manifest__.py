# -*- coding: utf-8 -*-
{
    "name": "Bol.com Connector | Bol.com Odoo Integration | Bol.com Marketplace Integration",
    "version": "19.0.1.1.4",
    "category": "eCommerce",
    'summary': """TeqStars Bol.com Integration for Odoo helps businesses manage Bol.com marketplace operations directly from Odoo. This Bol.com connector supports product synchronization, order import, inventory management, shipment updates, and marketplace workflow automation. The Bol.com integration simplifies daily eCommerce operations, and reduces manual marketplace management. This app is also part of the TeqStars marketplace connector solution for Odoo users.
                  Sell on Bol.com from Odoo — Connect with bol.com, the Odoo Bol.com Connector and Bol.com Integration for Odoo, with offer sync, order import and stock updates, built on TeqStars Base Marketplace, the shared core behind every TeqStars marketplace connector.""",

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
    'assets': {
        'web.assets_backend': [
            'bol/static/src/js/onboarding/**/*',
        ],
    },
    "cloc_exclude": [
        '**/*.xml',  # exclude all XML files from the module
        'static/description/**/*',  # exclude all files in a folder hierarchy recursively
    ],

    'images': ['static/description/bol_banner.png'],

    "author": "TeqStars",
    "website": "https://teqstars.com/r/bSq",
    'support': 'support@teqstars.com',
    'maintainer': 'TeqStars',

    "description": """""",

    'demo': [],
    'live_test_url': 'https://teqstars.com/r/Azo',
    'license': 'OPL-1',
    'auto_install': False,
    'installable': True,
    'application': True,
    'qweb': [],
    "price": "479.99",
    "currency": "EUR",
}
