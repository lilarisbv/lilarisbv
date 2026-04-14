{
    "name": "Base Marketplace Connector",
    "version": "1.0.2",
    "category": "Extra",
    "summary": "Base app for all the marketplace connector of TeqStars.",

    "depends": ['account', 'delivery', 'sale_management', 'stock'],

    'data': [
        'security/group.xml',
        'security/ir.model.access.csv',

        'report/sale_report_views.xml',

        'wizards/operation_view.xml',
        'wizards/stock_return_views.xml',
        'wizards/marketplace_scope_wizard_views.xml',

        'views/marketplace_listing_item_view.xml',
        'views/marketplace_listing_view.xml',
        'views/product_view.xml',
        'views/marketplace_listing_image_view.xml',
        'views/sale_view.xml',
        'views/pricelist_view.xml',
        'views/account_move_view.xml',
        'views/stock_view.xml',
        'views/log_view.xml',
        'views/res_partner.xml',
        'views/order_workflow_view.xml',

        'data/ir_sequence_data.xml',
        'data/ir_cron.xml',
        'data/dashboard_data.xml',
        'data/data.xml',

        'views/marketplace_queue_job_line_view.xml',
        'views/marketplace_queue_job_view.xml',
        'views/marketplace_instance_view.xml',
        'views/marketplace_menuitems.xml',

    ],

    'images': ['static/description/base_marketplace.jpg'],

    'assets': {
        'web._assets_primary_variables': [
            'base_marketplace/static/src/scss/primary_variables.scss',
        ],
        'web.assets_backend': [
            'base_marketplace/static/src/js/chart_lib/apexcharts.min.js',
            'base_marketplace/static/src/js/**/*',
            'base_marketplace/static/src/css/dashboard.css',
            'base_marketplace/static/src/scss/instance_dashboard.scss',
            'base_marketplace/static/src/scss/kanban_image_view.scss',
            'base_marketplace/static/src/scss/marketplace_scope_wizard.scss',
        ],
    },
    "cloc_exclude": [
        '**/*.css',  # exclude all scss file from the module
        '**/*.xml',  # exclude all XML files from the module
        'static/src/js/chart_lib/**/*',  # exclude all files in a folder hierarchy recursively
        'static/description/**/*',  # exclude all files in a folder hierarchy recursively
    ],
    "author": "TeqStars",
    "website": "http://teqstars.com/r/bSq",
    'support': 'support@teqstars.com',
    'maintainer': 'TeqStars',

    "description": """""",

    'demo': [],
    'license': 'OPL-1',
    'live_test_url': '',
    'auto_install': False,
    'installable': True,
    'application': False,
    "price": "20.00",
    "currency": "EUR",
}
