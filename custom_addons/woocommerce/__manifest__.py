{
    "name": "Odoo WooCommerce Connector",
    "version": "19.0.1.0.6",
    "category": "eCommerce",
    'summary': """Integrate & Manage WooCommerce Operations from Odoo by using WooCommerce Integration. connect odoo to woocommerce, integrates Odoo with Woocommerce, Woocommerce Odoo Connector, woo commerce, wocommerce, odoo woocommerce connector, odoo woocommerce, odoo woo commerce, odoo and woocommerce, Woocommerce store in Odoo, WooCommerce odoo bridge, odoo WooCommerce bridge, app connector
               odoo woocommerce by teqstars odoo woocommerce teqstars woocommerce teqstars woo odoo teqstars woocommerce odoo teqstars apps woo teqstars woocommerce teqstars odoo woo teqstars wooCommerce Order Import wooCommerce Stock Sync wooCommerce Product Sync odoo wooCommerce integration wooCommerce odoo integration ecommerce integrations ecommerce connector.
               Sync your WooCommerce store with Odoo — the Odoo WooCommerce Connector and WooCommerce Integration for Odoo, with product sync, order import and stock updates, built on TeqStars Base Marketplace, the shared core behind every TeqStars marketplace connector.""",

    "depends": ['base_marketplace'],

    'data': [
        'security/ir.model.access.csv',

        'data/order_status_data.xml',
        'data/data.xml',

        'views/product_category.xml',
        'views/product_tags.xml',
        'views/marketplace_instance_view.xml',
        'views/payment_gateway_view.xml',
        'views/marketplace_listing_view.xml',
        'views/marketplace_listing_item_view.xml',
        'views/marketplace_queue_job_view.xml',
        'views/sale_order_view.xml',
        'views/stock_view.xml',
        'views/product_attributes.xml',
        'views/marketplace_listing_image_view.xml',
        'views/woocommerce_financial_workflow_config.xml',
        'views/log_view.xml',
        'views/menuitems.xml',

        'wizards/operation_view.xml',
        'wizards/cancel_order_in_marketplace_view.xml',
    ],

    'images': ['static/description/woo_offer_animation.gif'],

    'assets': {
        'web.assets_backend': [
            'woocommerce/static/src/js/onboarding/**/*',
        ],
    },

    "cloc_exclude": [
        "woocommerce/*",  # exclude all files in a folder hierarchy recursively
        'static/description/**/*',  # exclude all files in a folder hierarchy recursively
    ],

    "author": "TeqStars",
    "website": "http://teqstars.com/r/bSq",
    'support': 'support@teqstars.com',
    'maintainer': 'TeqStars',

    "description": """
Odoo WooCommerce Integration
============================
- Connect one or more WooCommerce stores to Odoo from a single backend
- Import products, categories, tags, attributes and variants from WooCommerce to Odoo
- Export products with images, prices and stock from Odoo to WooCommerce
- Import customer orders automatically and process them in Odoo
- Sync customers, billing and shipping addresses with Odoo contacts
- Sync stock levels between Odoo and WooCommerce so inventory stays accurate
- Map WooCommerce payment gateways to Odoo payment methods
- Map WooCommerce order statuses to Odoo sale order workflow
- Real-time updates through WooCommerce webhooks
- Queue jobs for large data sync to avoid timeouts
- Custom field mapping to sync extra fields you need
- Cancel and Refund WooCommerce orders directly from Odoo
    """,

    'demo': [],
    'license': 'OPL-1',
    'live_test_url': '',
    'auto_install': False,
    'installable': True,
    'application': True,
    'qweb': [],
    "price": "164.99",
    "currency": "EUR",
}
