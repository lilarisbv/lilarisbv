{
    'name': 'Customer Support Ticket',
    'version': '19.0.2.0.0',
    'category': 'Services',
    'summary': 'Müşteri destek talepleri için bilet yönetimi modülü.',
    'description': """
        Müşteri Destek Ticket Modülü
        ============================
        Bu modül, müşterilerin destek taleplerini oluşturmasını, 
        takip etmesini ve yönetmesini sağlar.
    """,
    'author': 'NametechBV',
    'license': 'LGPL-3',
    'depends': [
        'base',
        'mail',
    ],
    'data': [
        'security/ir.model.access.csv',
        'views/ticket_views.xml',
        'data/ir_cron.xml',
        'wizard/ticket_wizard_view.xml',
        'views/email_templates.xml',
        'views/help_views.xml',
    ],
    'installable': True,
    'application': True,
    'auto_install': False,
    'icon': '/customer_ticket/static/description/icon.png',
}