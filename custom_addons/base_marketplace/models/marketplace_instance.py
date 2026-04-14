import json
import babel
import random
import base64
import datetime
from lxml import etree
from odoo.tools import date_utils
from odoo.tools import html_escape
from odoo.tools import float_round
from datetime import date, timedelta
from odoo import models, fields, api, _
from babel.dates import get_quarter_names
from odoo.tools.misc import format_date, get_lang
from dateutil.relativedelta import relativedelta
from odoo.tools import DEFAULT_SERVER_DATE_FORMAT
from odoo.addons.base_marketplace.models.exceptions import MarketplaceException
from babel.dates import format_datetime, format_date as babel_format_date


class MkInstance(models.Model):
    _name = "mk.instance"
    _order = 'sequence, name'
    _inherit = ['portal.mixin', 'mail.thread', 'mail.activity.mixin', 'analytic.mixin']
    _description = 'Marketplace Instance'

    filter_date = {'date_from': '', 'date_to': '', 'filter': 'this_month'}

    def _get_kanban_graph(self):
        self.dashboard_graph = json.dumps(self.get_bar_graph_datas())

    def _get_mk_kanban_badge_color(self):
        default_code = "#7C7BAD"
        # Hook type method that will get default kanban badge color according to marketplace type.
        if hasattr(self, '%s_mk_kanban_badge_color' % self.marketplace):
            default_code = getattr(self, '%s_mk_kanban_badge_color' % self.marketplace)
        return default_code

    @api.onchange('marketplace')
    def _get_mk_default_api_limit(self):
        api_limit = 250
        # Hook type method that will get default api limit according to marketplace type.
        if hasattr(self, '%s_mk_default_api_limit' % self.marketplace):
            api_limit = getattr(self, '%s_mk_default_api_limit' % self.marketplace)()
        self.api_limit = api_limit

    def _get_mk_kanban_counts(self):
        for mk_instance_id in self:
            mk_instance_id.mk_listing_count = len(mk_instance_id.mk_listing_ids)
            mk_instance_id.mk_order_count = self.env['sale.order'].search_count([('mk_instance_id', '=', mk_instance_id.id)])
            mk_instance_id.mk_queue_count = len(mk_instance_id.mk_queue_ids.filtered(lambda x: x.state != 'processed'))

    def _kanban_dashboard_graph(self):
        for mk_instance_id in self:
            chart_data = mk_instance_id.get_bar_graph_datas()
            mk_instance_id.kanban_dashboard_graph = json.dumps(chart_data)
            mk_instance_id.is_sample_data = chart_data[0].get('is_sample_data', False)

    def _get_discount_product(self):
        # Hook type method that will get default discount according to marketplace type.
        discount_product = False
        if hasattr(self, '_get_%s_discount_product' % self.marketplace):
            discount_product = getattr(self, '_get_%s_discount_product' % self.marketplace)
        return discount_product

    def _get_delivery_product(self):
        # Hook type method that will get default discount according to marketplace type.
        delivery_product = False
        if hasattr(self, '_get_%s_delivery_product' % self.marketplace):
            delivery_product = getattr(self, '_get_%s_delivery_product' % self.marketplace)
        return delivery_product

    def _get_default_warehouse(self):
        company_id = self.company_id if self.company_id else self.env.company
        warehouse_id = self.env['stock.warehouse'].search([('company_id', '=', company_id.id)], limit=1)
        return warehouse_id.id if warehouse_id else False

    @api.model
    def _lang_get(self):
        return self.env['res.lang'].get_installed()

    # TODO: Is environment needed?
    name = fields.Char(string='Name', required=True, help="Name of your marketplace instance.")
    color = fields.Integer('Color Index')
    sequence = fields.Integer(default=1, help="The sequence field is used to define Instance Sequence.")
    marketplace = fields.Selection(selection=[], string='Marketplace', default='')
    state = fields.Selection(selection=[('draft', 'Draft'), ('confirmed', 'Confirmed'), ('error', 'Error')], default='draft')
    company_id = fields.Many2one('res.company', 'Company', default=lambda self: self.env.company, ondelete="restrict",)
    country_id = fields.Many2one('res.country', string='Country', default=lambda self: self.env.company.country_id)
    warehouse_id = fields.Many2one('stock.warehouse', string='Warehouse', required=True, default=_get_default_warehouse, domain="[('company_id', '=', company_id)]", check_company=True,
                                   ondelete="restrict")
    api_limit = fields.Integer("API Record Count", help="Record limit while perform api calling.")
    kanban_badge_color = fields.Char(default=_get_mk_kanban_badge_color)
    log_level = fields.Selection([('all', 'All'), ('success', 'Success'), ('error', 'Error')], string="Log Level", default="error")
    company_currency_id = fields.Many2one('res.currency', related='company_id.currency_id', string="Company Currency")
    show_in_systray = fields.Boolean("Show in Systray Menu?", copy=False)
    queue_batch_limit = fields.Integer("Queue Batch Limit", default=100, help="Odoo will create a batch with defined limit.")
    image = fields.Binary("Marketplace Image", attachment=True, help="This field holds the image used as photo for the marketplace, limited to 1024x1024px.")
    image_medium = fields.Binary("Medium-sized photo", related="image", store=True,
                                 help="Medium-sized photo of the marketplace. It is automatically resized as a 128x128px image, with aspect ratio preserved. ")
    image_small = fields.Binary("Small-sized photo", related="image", store=True,
                                help="Small-sized photo of the marketplace. It is automatically resized as a 64x64px image, with aspect ratio preserved. ")
    lang = fields.Selection(_lang_get, string='Language', default=lambda self: self.env.lang, help="Instance language.")

    # Product Fields
    is_create_products = fields.Boolean("Create Odoo Products?", help="If Odoo products not found while Sync create Odoo products.")
    is_update_odoo_product_category = fields.Boolean("Update Category in Odoo Products?", help="Update Odoo Products Category.")
    is_export_product_sale_price = fields.Boolean("Export Odoo Product's Sale Price?", help="Directly exporting the product's sale price instead of the price from the pricelist")
    is_sync_images = fields.Boolean("Sync Listing Images?", help="If true then Images will be sync at the time of Import Listing.")
    sync_product_with = fields.Selection([('barcode', 'Barcode'), ('sku', 'SKU'), ('barcode_or_sku', 'Barcode or SKU')], string="Sync Product With", default="barcode_or_sku")
    last_listing_import_date = fields.Datetime("Last Listing Imported On", copy=False)
    last_listing_price_update_date = fields.Datetime("Last Listing Price Updated On", copy=False)

    # Stock Fields
    stock_field_id = fields.Many2one('ir.model.fields', string='Stock Based On', help="At the time of Export/Update inventory this field is used.",
                                     default=lambda self: self.env['ir.model.fields'].search([('model_id.model', '=', 'product.product'), ('name', '=', 'qty_available')]))
    last_stock_update_date = fields.Datetime("Last Stock Exported On", copy=False, help="Date were stock updated to marketplace.")
    last_stock_import_date = fields.Datetime("Last Stock Imported On", copy=False, help="Date were stock imported from marketplace.")
    is_validate_adjustment = fields.Boolean("Validate Inventory Adjustment?", help="If true then validate Inventory adjustment at the time of Import Stock Operation.")

    # Order Fields
    use_marketplace_sequence = fields.Boolean("Use Marketplace Order Sequence?", default=True)
    order_prefix = fields.Char(string='Order Prefix', help="Order name will be set with given Prefix while importing Order.")
    fbm_order_prefix = fields.Char(string='Order Prefix (Fulfilment by Marketplace)', help="Order name will be set with given Prefix while importing fulfillment by marketplace orders.")
    team_id = fields.Many2one('crm.team', string='Sales Team', default=lambda self: self.env['crm.team'].search([], limit=1), help='Sales Team used for imported order.')
    discount_product_id = fields.Many2one('product.product', string='Discount Product', domain=[('type', '=', 'service')], ondelete="restrict",
                                          help="Discount product used in sale order line.")
    delivery_product_id = fields.Many2one('product.product', string='Delivery Product', domain=[('type', '=', 'service')], ondelete="restrict",
                                          help="Delivery product used in sale order line.")
    last_order_sync_date = fields.Datetime("Last Order Imported On", copy=False)
    import_order_after_date = fields.Datetime("Import Order After", copy=False)
    pricelist_id = fields.Many2one('product.pricelist', string='Pricelist', domain="[('company_id', '=', company_id)]", check_company=True, ondelete="restrict")
    tax_system = fields.Selection([('default', "Odoo's Default Tax Behaviour (Taxes will be taken from Odoo Product)"), ('according_to_marketplace', "Follow Marketplace Tax (no fiscal position mappings)"), ('marketplace_with_fiscal_position', 'Follow Marketplace Tax and applies fiscal position mappings')], default='according_to_marketplace',
                                  help="""1. Odoo Default Tax Behaviour – Taxes are applied using Odoo’s configured tax rates and fiscal position mappings.\n
                                  2. Follow Marketplace Tax – The system creates a new tax in Odoo matching the marketplace rate if none exists, without applying fiscal position mappings.\n
                                  3. Follow Marketplace Tax & Apply Fiscal Position – The system creates a new tax in Odoo matching the marketplace rate if none exists and then applies fiscal position mappings.""")
    tax_account_id = fields.Many2one('account.account', string='Tax Account', help="Account that will be set while creating tax.")
    tax_refund_account_id = fields.Many2one('account.account', string='Tax Account on Credit Notes', help="Account that will be set while creating tax.")
    tax_rounding = fields.Integer(string="Tax Rounding", default=2)
    salesperson_user_id = fields.Many2one('res.users', string='Salesperson', domain="[('share', '=', False)]", help="Selected sales person will be used to process order.")
    use_marketplace_currency = fields.Boolean("Use Marketplace Order Currency?", default=True,
                                              help="If it's true, the order will be imported with the currency that is available in the marketplace. Otherwise, it will use the company's currency.")
    is_create_single_invoice = fields.Boolean("Create Single Invoice?", default=True, help="Create invoice only when all products are ready to be invoiced.")

    # Customer Fields
    account_receivable_id = fields.Many2one('account.account', string='Receivable Account', domain="[('account_type', '=', 'asset_receivable')]",
                                            company_dependent=True, help="While creating Customer set this field in Account Receivable instead of default.")
    last_customer_import_date = fields.Datetime("Last Customers Imported On", copy=False)

    # Scheduled actions
    cron_ids = fields.One2many("ir.cron", "mk_instance_id", "Automated Actions", context={'active_test': False}, groups="base.group_system")

    # Emails & Notifications
    notification_ids = fields.One2many("mk.notification", "mk_instance_id", "Marketplace Notification")

    # Dashboard Fields
    mk_listing_ids = fields.One2many('mk.listing', 'mk_instance_id', string="Listing")
    mk_listing_count = fields.Integer("Listing Count", compute='_get_mk_kanban_counts')
    mk_order_ids = fields.One2many('sale.order', 'mk_instance_id', string="Orders")
    mk_order_count = fields.Integer("Order Count", compute='_get_mk_kanban_counts')
    mk_invoice_ids = fields.One2many('account.move', 'mk_instance_id', string="Invoices")
    mk_shipment_ids = fields.One2many('stock.picking', 'mk_instance_id', string="Shipments")
    mk_queue_ids = fields.One2many('mk.queue.job', 'mk_instance_id', string="Queue Job")
    mk_queue_count = fields.Integer("Queue Count", compute='_get_mk_kanban_counts')
    mk_customer_ids = fields.Many2many("res.partner", "mk_instance_res_partner_rel", "partner_id", "marketplace_id", string="Customers")

    mk_log_ids = fields.One2many('mk.log', 'mk_instance_id', string="Logs")

    # Kanban bar graph
    kanban_dashboard_graph = fields.Text(compute='_kanban_dashboard_graph')

    # Activity
    mk_activity_type_id = fields.Many2one('mail.activity.type', string='Activity', domain="[('res_model', '=', False)]")
    activity_date_deadline_range = fields.Integer(string='Due Date In')
    activity_date_deadline_range_type = fields.Selection([('days', 'Days'), ('weeks', 'Weeks'), ('months', 'Months'), ], string='Due type', default='days')
    activity_user_ids = fields.Many2many('res.users', string='Responsible')

    is_sample_data = fields.Boolean("Is Sample Data", compute='_kanban_dashboard_graph')

    def get_all_marketplace(self):
        marketplace_list = [marketplace[0] for marketplace in self.env['mk.instance'].fields_get()['marketplace']['selection'] if marketplace]
        return marketplace_list and marketplace_list or []

    @api.onchange('marketplace')
    def _onchange_marketplace(self):
        default_code, image = "#7C7BAD", False
        # Hook type method that will get default kanban badge color according to marketplace type.
        if hasattr(self, '%s_mk_kanban_badge_color' % self.marketplace):
            default_code = getattr(self, '%s_mk_kanban_badge_color' % self.marketplace)()
        self.kanban_badge_color = default_code
        if hasattr(self, '%s_mk_kanban_image' % self.marketplace):
            image_path = getattr(self, '%s_mk_kanban_image' % self.marketplace)()
            image = base64.b64encode(open(image_path, 'rb').read())
        if not self.delivery_product_id and hasattr(self, '_get_%s_delivery_product' % self.marketplace):
            self.delivery_product_id = getattr(self, '_get_%s_delivery_product' % self.marketplace)()
        if not self.discount_product_id and hasattr(self, '_get_%s_discount_product' % self.marketplace):
            self.discount_product_id = getattr(self, '_get_%s_discount_product' % self.marketplace)()
        self.image = image

    def _update_default_products_in_instance(self):
        if not self.delivery_product_id and hasattr(self, '_get_%s_delivery_product' % self.marketplace):
            self.delivery_product_id = getattr(self, '_get_%s_delivery_product' % self.marketplace)()
        if not self.discount_product_id and hasattr(self, '_get_%s_discount_product' % self.marketplace):
            self.discount_product_id = getattr(self, '_get_%s_discount_product' % self.marketplace)()

    @api.model_create_multi
    def create(self, vals):
        res = super(MkInstance, self).create(vals)
        self.env['ir.cron'].setup_schedule_actions(res)
        res._update_default_products_in_instance()
        return res

    def write(self, vals):
        res = super(MkInstance, self).write(vals)
        for instance in self:
            instance._update_default_products_in_instance()
        return res

    @api.depends('name', 'marketplace')
    def _compute_display_name(self):
        for record in self:
            record.display_name = "[{}] {}".format(dict(record._fields['marketplace'].selection).get(record.marketplace), record.name or '')

    def confirm_connection(self):
        """
        Task: T4565 - Marketplace Access Rights Check
        This hook type method is used establishes a connection to the marketplace, sets the pricelist, and imports location data based on the marketplace type.
        Returns:
            Call the marketplace method. Based on that establishes a connection to the marketplace, sets the pricelist, and imports location data.
        Raises:
            Exception: Raise users error if exception occurs.
        """
        if hasattr(self, '%s_confirm_connection' % self.marketplace):
            return getattr(self, '%s_confirm_connection' % self.marketplace)()
        else:
            raise MarketplaceException(_("Something went wrong! Please contact your integration provider."))

    def action_confirm(self):
        # This hook method verifies the required marketplace access scopes. If access is granted, it connects to the marketplace, sets the price list, and imports location data based on the marketplace type.
        self.ensure_one()
        if hasattr(self, '%s_action_confirm' % self.marketplace):
            return getattr(self, '%s_action_confirm' % self.marketplace)()
        else:
            raise MarketplaceException(_("Something went wrong! Please contact your integration provider."))

    def reset_to_draft(self):
        self.write({'state': 'draft'})

    def action_test_connection(self):
        """
        Task: T4565 - Marketplace Access Rights Check
        This hook method checks the connection to the marketplace.If connection established to the marketplace then show rainbow man effect.
        Returns:
            success_msg (dictionary): Returns rainbow man effect if successfully connected to marketplace.
        Raises:
            Exception: Raise user error if exception occurs.
        """
        if hasattr(self, '%s_test_connection' % self.marketplace):
            success_msg = getattr(self, '%s_test_connection' % self.marketplace)()
            if not success_msg:
                raise MarketplaceException(_("Test Connection Failed: Invalid Credentials."))
            return success_msg
        else:
            raise MarketplaceException(_("Test Connection is not supported for this Marketplace."))

    def _get_required_access_scopes(self):
        """
        Task: T4565 - Marketplace Access Rights Check
        This hook method is used to returns predefined set of required scopes based on the marketplace type.
        Returns:
            set: Call the marketplace method.Based on give predefined set of required scopes.
        Raises:
            Exception: Raise user error if exception occurs.
        """
        if hasattr(self, '%s_required_access_scopes' % self.marketplace):
            return getattr(self, '%s_required_access_scopes' % self.marketplace)()
        else:
            raise MarketplaceException(_("Something went wrong! Please contact your integration provider."))

    def _get_granted_access_scopes(self):
        """
        Task: T4565 - Marketplace Access Rights Check
        This hook method retrieves the granted scopes from the marketplace app and returns them.
        Returns:
            set: Call the marketplace method.Based on give granted scopes from the marketplace app.
        Raises:
            Exception: Raise user error if exception occurs.
        """
        if hasattr(self, '%s_granted_access_scopes' % self.marketplace):
            return getattr(self, '%s_granted_access_scopes' % self.marketplace)()
        else:
            raise MarketplaceException(_("Something went wrong! Please contact your integration provider."))

    def get_marketplace_operation_wizard(self):
        if hasattr(self, '%s_marketplace_operation_wizard' % self.marketplace):
            return getattr(self, '%s_marketplace_operation_wizard' % self.marketplace)()
        else:
            raise MarketplaceException(_("Something went wrong! Please contact your integration provider."))

    def get_marketplace_import_operation_wizard(self):
        if hasattr(self, '%s_marketplace_import_operation_wizard' % self.marketplace):
            return getattr(self, '%s_marketplace_import_operation_wizard' % self.marketplace)()
        else:
            return self.env.ref('base_marketplace.action_marketplace_import_operation').sudo().read()[0]

    def get_marketplace_export_operation_wizard(self):
        if hasattr(self, '%s_marketplace_export_operation_wizard' % self.marketplace):
            return getattr(self, '%s_marketplace_export_operation_wizard' % self.marketplace)()
        else:
            return self.env.ref('base_marketplace.action_marketplace_export_operation').sudo().read()[0]

    def is_order_create_notification_message(self, count, marketplace):
        # Dynamic method for get notification title and message
        title = _('{marketplace} Orders Import'.format(marketplace=marketplace))
        message = {'error': '{count} {marketplace} order(s) facing issue for {instance} Instance'.format(count=count, marketplace=marketplace, instance=self.name), 'success': _(
            '{count} {marketplace} order(s) imported successfully for {instance} Instance.'.format(count=count, marketplace=marketplace, instance=self.name))}
        return title, message

    def is_product_import_notification_message(self, count, marketplace):
        # Dynamic method for get notification title and message
        title = _('{marketplace} Product Import'.format(marketplace=marketplace))
        queue_id = self.env.context.get('queue_id', False)
        if queue_id:
            queue_link = '<a href="/web#id={}&model=mk.queue.job&view_type=form">{}</a>'.format(queue_id.id, html_escape(queue_id.name))
            message = {'error': _('{count} {marketplace} product(s) encountered failures during the processing of <b>{queue}</b> for {instance} Instance'.format(count=count, marketplace=marketplace, instance=self.name, queue=queue_link)),
                       'success': _('{count} {marketplace} product(s) imported successfully from <b>{queue}</b> for {instance} Instance.'.format(count=count, marketplace=marketplace, instance=self.name, queue=queue_link))}
            return title, message
        message = {'error': _('{count} {marketplace} product(s) encountered failures during the processing for {instance} Instance'.format(count=count, marketplace=marketplace, instance=self.name)),
                   'success': _('{count} {marketplace} product(s) imported successfully for {instance} Instance.'.format(count=count, marketplace=marketplace, instance=self.name))}
        return title, message

    def get_smart_notification_message(self, notify_field, count, marketplace):
        # Hook type method that will get notification title and message according to `notify_field`
        title, message = 'No Title', 'Nothing to display'
        if hasattr(self, '%s_notification_message' % notify_field):
            title, message = getattr(self, '%s_notification_message' % notify_field)(count, marketplace)
        return title, message

    def send_smart_notification(self, notify_field, notify_type, count):
        """ Method to send Smart Notification to Users that is configured in Marketplace Notification Tab.
        :param notify_field: order_create, product_create
        :param notify_type: success, error, all.
        :param count: count
        :return: True
        exp. : self.send_smart_notification('is_order_create', 'success', 5)
        """
        notification_ids = self.notification_ids
        for notification_id in notification_ids:
            if hasattr(notification_id, notify_field) and count > 0:
                notify = getattr(notification_id, notify_field)
                if notify_type == 'error' and notification_id.type not in ['error', 'all']:
                    continue
                if notify_type == 'success' and notification_id.type not in ['success', 'all']:
                    continue
                if notify:
                    marketplace = notification_id.mk_instance_id.marketplace
                    marketplace_name = dict(self._fields['marketplace'].selection).get(marketplace) or ''
                    title, message = self.get_smart_notification_message(notify_field, count, marketplace_name)
                    if title and message:
                        queue_id = self.env.context.get('queue_id', False)
                        message = message.get('success') if notify_type == 'success' else message.get('error')
                        payload = {'title': title, 'message': message, 'sticky': notification_id.is_sticky, 'type': 'success' if notify_type == 'success' else 'danger', 'message_is_html': True if queue_id else False}
                        self.env['bus.bus']._sendone(notification_id.user_id.partner_id, 'marketplace_notification', payload)

    def action_create_queue(self, type):
        self.ensure_one()
        queue_obj = self.env['mk.queue.job']

        queue_id = queue_obj.create({'type': type,
                                     'mk_instance_id': self.id,
                                     'update_existing_product': self.env.context.get('update_existing_product'),
                                     'update_product_price': self.env.context.get('update_product_price')})
        if self.env.context.get('active_model', '') == 'mk.instance':
            message = '{} Queue <b><a href="/web#id={}&model=mk.queue.job&view_type=form">{}</a></b> created successfully.'.format(type.title(), queue_id.id, html_escape(queue_id.name))
            self.env['bus.bus']._sendone(self.env.user.partner_id, 'marketplace_notification', {'title': 'Queue Created', 'message_is_html': True, 'message': message, 'sticky': False, 'type': 'info'})
        return queue_id

    def _graph_title_and_key(self):
        return ['Untaxed Total', _('Untaxed Total')]

    def _get_bar_graph_select_query(self):
        """
        Returns a tuple containing the base SELECT SQL query used to gather
        the bar graph's data as its first element, and the arguments dictionary
        for it as its second.
        """
        return ('''
            SELECT SUM(amount_untaxed) AS total, MIN(date_order) AS aggr_date
            FROM sale_order
            WHERE mk_instance_id = %(mk_instance_id)s
            AND state not in ('cancel')
            AND date_order >= %(start_date)s
            AND date_order < %(end_date)s
        ''', {'mk_instance_id': self.id})

    def get_bar_graph_datas(self):
        """
        Retrieves data for the bar graph, including historical and current week sales data,
        formatted for visualization.
        Returns:
            list: A list containing the bar graph data.
        """
        data = self._initialize_data()
        query, query_args = self._build_weekly_sales_query()
        self.env.cr.execute(query, query_args)
        query_results = self.env.cr.dictfetchall()
        self._populate_data_with_query_results(data, query_results)
        return self._finalize_graph_data(data, query_results)

    def _initialize_data(self):
        """
        Initializes the data list with labels for each week.
        Returns:
            list: A list of dictionaries containing the initial data setup.
        """
        data = []
        today = fields.Datetime.now(self)
        day_of_week = int(format_datetime(today, 'e', locale=self.env.context.get('lang') or 'en_US'))
        first_day_of_week = today + timedelta(days=-day_of_week + 1)

        for i in range(-5, 1):
            if i == 0:
                label = _('This Week')
            else:
                start_week = first_day_of_week + timedelta(days=i * 7)
                end_week = start_week + timedelta(days=6)
                if start_week.month == end_week.month:
                    label = f"{start_week.day}-{end_week.day} {babel_format_date(end_week, 'MMM', locale=get_lang(self.env).code)}"
                else:
                    label = f"{babel_format_date(start_week, 'd MMM', locale=get_lang(self.env).code)}-{babel_format_date(end_week, 'd MMM', locale=get_lang(self.env).code)}"
            data.append({'label': label, 'value': 0.0, 'type': 'past' if i < 0 else 'future'})
        return data

    def _build_weekly_sales_query(self):
        """
        Constructs the SQL query to retrieve weekly sales data.
        Returns:
            tuple: A tuple containing the query string and the query arguments.
        """
        select_sql_clause, query_args = self._get_bar_graph_select_query()
        queries = []
        today = fields.Datetime.now(self)
        day_of_week = int(format_datetime(today, 'e', locale=self.env.context.get('lang') or 'en_US'))
        o_start_date = today + timedelta(days=-day_of_week + 1)

        for i in range(-5, 1):
            start_date = o_start_date + timedelta(days=i * 7)
            next_date = start_date + timedelta(days=7)
            clause_with_dates = select_sql_clause.replace('%(start_date)s', f'%(start_date_{i})s').replace('%(end_date)s', f'%(end_date_{i})s')
            if i == -5:
                queries.append(f"({clause_with_dates})")
            else:
                queries.append(f"UNION ALL ({clause_with_dates})")
            query_args[f'start_date_{i}'] = start_date.strftime(DEFAULT_SERVER_DATE_FORMAT)
            query_args[f'end_date_{i}'] = next_date.strftime(DEFAULT_SERVER_DATE_FORMAT)

        query = " ".join(queries)
        return query, query_args

    def _populate_data_with_query_results(self, data, query_results):
        """
        Populates the data list with the results from the query.
        Args:
            data (list): The data list to populate.
            query_results (list): The results from the SQL query.
        """
        for index in range(len(query_results)):
            if query_results[index].get('aggr_date') is not None:
                data[index]['value'] = query_results[index].get('total')

    def _finalize_graph_data(self, data, query_results):
        """
        Finalizes the graph data by checking for sample data and setting the graph title and key.
        Args:
            data (list): The data list to finalize.
            query_results (list): The results from the SQL query.
        Returns:
            list: The final graph data.
        """
        is_sample_data = True
        for index in range(len(query_results)):
            if query_results[index].get('total') not in [None, 0.0]:
                is_sample_data = False
                data[index]['value'] = query_results[index].get('total')

        graph_title, graph_key = self._graph_title_and_key()

        if is_sample_data:
            for index in range(len(query_results)):
                data[index]['type'] = 'o_sample_data'
                # we use unrealistic values for the sample data
                data[index]['value'] = random.randint(0, 20)
                graph_key = _('Sample data')

        return [{'values': data, 'title': graph_title, 'key': graph_key, 'is_sample_data': is_sample_data}]

    def action_marketplace_open_instance_view(self):
        form_id = self.sudo().env.ref('base_marketplace.marketplace_instance_form_view')
        action = {
            'name': _('Marketplace Instance'),
            'view_id': False,
            'res_model': 'mk.instance',
            'context': self.env.context,
            'view_mode': 'form',
            'res_id': self.id,
            'views': [(form_id.id, 'form')],
            'type': 'ir.actions.act_window',
        }
        return action

    def redirect_to_general_dashboard(self):
        if self.env.user.has_group('base_marketplace.group_base_marketplace_manager'):
            return self.sudo().env.ref('base_marketplace.backend_mk_general_dashboard').read()[0]
        return self.sudo().env.ref('base_marketplace.action_marketplace_dashboard').read()[0]

    def redirect_to_sales_report(self):
        action = self.env['sale.report'].redirect_to_mk_sale_report()
        action['display_name'] = _("{} Sales Analysis".format(self.name))
        action['domain'] = [('mk_instance_id', '=', self.id)]
        return action

    def _get_margin_value(self, value, previous_value=0.0):
        margin = 0.0
        if (value != previous_value) and (value != 0.0 and previous_value != 0.0):
            margin = float_round((float(value-previous_value) / previous_value or 1) * 100, precision_digits=2)
        return margin

    def _get_top_10_performing_products(self, dashboard_data_list, sales_domain):
        report_product_lines = self.env['sale.report'].read_group(domain=sales_domain + [('product_type', '!=', 'service')],
                                                                  fields=['product_tmpl_id', 'product_uom_qty', 'price_total'],
                                                                  groupby=['product_tmpl_id'], orderby='price_total desc', limit=10)

        for product_line in report_product_lines:
            product_tmpl_id = self.env['product.template'].browse(product_line['product_tmpl_id'][0])
            dashboard_data_list['best_sellers'].append({'id': product_tmpl_id.id,
                                                        'name': product_tmpl_id.name,
                                                        'qty': product_line['product_uom_qty'],
                                                        'sales': product_line['price_total']})
        return dashboard_data_list

    def _get_top_10_customers(self, total_orders, dashboard_data_list, sales_domain):
        report_customer_lines = self.env['sale.report'].read_group(domain=sales_domain + [('partner_id', '!=', False)],
            fields=['product_uom_qty', 'price_total'],
            groupby=['partner_id'], orderby='price_total desc', limit=10)
        for customer_line in report_customer_lines:
            order_count = self.env['sale.order'].search_count([('partner_id', '=', customer_line['partner_id'][0]), ('id', 'in', total_orders.ids)])
            dashboard_data_list['top_customers'].append({'id': customer_line['partner_id'][0],
                                                         'name': customer_line['partner_id'][1],
                                                         'count': order_count,
                                                         'sales': customer_line['price_total']})

    def _get_data_for_instance_wise_selling(self, is_general_dashboard, dashboard_data_list, sales_domain, date_from, date_to):
        mk_type_dict = {}
        date_date_from = fields.Date.from_string(date_from)
        date_date_to = fields.Date.from_string(date_to)
        if not is_general_dashboard:
            sale_graph_data = self._compute_sale_graph(date_date_from, date_date_to, sales_domain)
            dashboard_data_list['sale_graph'] = {'series': [{'name': 'Total Amount', 'data': sale_graph_data[1]}], 'categories': sale_graph_data[0]}
        else:
            series_data_list, bar_categories, bar_data = [], [], []
            for mk_instance_id in self:
                instance_name = mk_instance_id.name
                sale_graph_data = self._compute_sale_graph(date_date_from, date_date_to, [('state', 'in', ['sale', 'done']), ('mk_instance_id', '=', mk_instance_id.id)])
                series_data_list.append({'name': instance_name, 'data': sale_graph_data[1]})
                instance_bar_total_selling = round(sum(sale_graph_data[1]), 2)
                if instance_bar_total_selling:
                    bar_data.append({'name': instance_name, 'data': [instance_bar_total_selling]})
                    bar_categories.append(instance_name)

            dashboard_data_list['sale_graph'] = {'series': series_data_list, 'categories': sale_graph_data[0]}
            dashboard_data_list['bar_graph'] = {'series': bar_data, 'categories': bar_categories}

            # Marketplace Type wise selling
            mk_type_data = self.env['sale.report'].read_group(domain=sales_domain,
                fields=['marketplace_type', 'price_total'],
                groupby=['marketplace_type'], orderby='price_total desc', limit=5)
            [mk_type_dict.update({dict(self._fields['marketplace'].selection).get(mk_type_line['marketplace_type']): mk_type_line['price_total']}) for mk_type_line in mk_type_data]
            dashboard_data_list['mk_revenue_pieChart'] = {'series': list(mk_type_dict.values()), 'labels': list(mk_type_dict.keys())}
        return dashboard_data_list

    def _fetch_country_sales_data_for_dashboard(self, dashboard_data_list, sales_domain):
        country_lines = self.env['sale.report'].read_group(domain=sales_domain, fields=['country_id', 'price_total'], groupby=['country_id'], orderby='price_total desc', limit=5)
        country_dict = {country_line['country_id'][1]: country_line['price_total'] for country_line in country_lines if country_line.get('country_id')}
        dashboard_data_list['country_graph'] = {'series': list(country_dict.values()), 'labels': list(country_dict.keys())}
        return dashboard_data_list

    def _get_top_5_performing_category_for_dashboard(self, dashboard_data_list, sales_domain):
        category_lines = self.env['sale.report'].read_group(domain=sales_domain, fields=['categ_id', 'price_total'], groupby=['categ_id'], orderby='price_total desc', limit=5)
        category_dict = {category_line.get('categ_id', False) and category_line['categ_id'][1] or 'None': category_line['price_total'] for category_line in category_lines}
        dashboard_data_list['category_graph'] = {'series': list(category_dict.values()), 'labels': list(category_dict.keys())}
        return dashboard_data_list

    def _fetch_tiles_summary_for_dashboard(self, is_general_dashboard, sales_domain, total_orders, dashboard_data_list, date_from, date_to):
        total_sales = self.env['sale.report'].read_group(domain=sales_domain, fields=['price_total'], groupby=['mk_instance_id'])
        total_sales = total_sales[0].get('price_total') if total_sales else 0
        if not is_general_dashboard:
            to_ship_domain = [('mk_instance_id', 'in', self.ids)]
        else:
            to_ship_domain = [('mk_instance_id', '!=', False), ('mk_instance_id.state', '=', 'confirmed')]

        to_ship_count = self.env['stock.picking'].search_count(to_ship_domain + [('state', 'not in', ['cancel', 'done']), ('create_date', '>=', date_from), ('create_date', '<=', date_to)])
        dashboard_data_list['summary']['total_orders'] = len(total_orders)
        dashboard_data_list['summary']['pending_shipments'] = to_ship_count
        dashboard_data_list['summary']['total_sales'] = total_sales
        dashboard_data_list['summary']['avg_order_value'] = total_sales / (len(total_orders) or 1)
        return dashboard_data_list

    def get_previous_date_range(self, start_date, end_date, interval):
        if interval in ['this_month', 'last_month']:
            previous_start_date = start_date - relativedelta(months=1)
            previous_end_date = end_date - relativedelta(months=1)
        elif interval in ['this_year', 'last_year']:
            previous_start_date = start_date - relativedelta(years=1)
            previous_end_date = end_date - relativedelta(years=1)
        elif interval == ['this_quarter', 'last_quarter']:
            previous_start_date = start_date - relativedelta(months=3)
            previous_end_date = end_date - relativedelta(months=3)
        else:
            previous_start_date = start_date - timedelta(days=1)
            previous_end_date = previous_start_date - (end_date - start_date)
        return previous_start_date, previous_end_date

    def _fetch_kpi_tiles_summary_for_dashboard(self, is_general_dashboard, dashboard_data_list, dates_ranges, interval):
        date_from, date_to = self.get_previous_date_range(dates_ranges.get('date_from'), dates_ranges.get('date_to'), interval)
        total_orders = self.env['sale.order'].search([('date_order', '>=', date_from), ('date_order', '<=', date_to), ('state', 'in', ['sale']),
                                                      ('mk_instance_id', 'in', self.ids)], order="date_order")
        sales_domain = [('state', 'in', ['sale', 'done']), ('order_reference', 'in', [f'sale.order,{order.id}' for order in total_orders]), ('date', '>=', date_from), ('date', '<=', date_to)]
        if not is_general_dashboard:
            total_sales = self.env['sale.report'].read_group(domain=sales_domain, fields=['price_total'], groupby=['mk_instance_id'])
            total_sales = total_sales[0].get('price_total') if total_sales else 0
            to_ship_domain = [('mk_instance_id', 'in', self.ids)]
        else:
            total_sales = total_orders.mapped('amount_total')
            total_sales = sum(total_sales) if total_sales else 0
            to_ship_domain = [('mk_instance_id', '!=', False), ('mk_instance_id.state', '=', 'confirmed')]

        to_ship_count = self.env['stock.picking'].search_count(to_ship_domain + [('state', 'not in', ['cancel', 'done']), ('create_date', '>=', date_from), ('create_date', '<=', date_to)])
        kpi_total_orders = self._get_margin_value(dashboard_data_list['summary']['total_orders'], len(total_orders))
        kpi_pending_shipments = self._get_margin_value(dashboard_data_list['summary']['pending_shipments'], to_ship_count)
        kpi_total_sales = self._get_margin_value(dashboard_data_list['summary']['total_sales'], total_sales)
        kpi_avg_order_value = self._get_margin_value(dashboard_data_list['summary']['avg_order_value'], total_sales / (len(total_orders) or 1))
        dashboard_data_list['summary']['kpi_total_orders'] = round(kpi_total_orders, 2)
        dashboard_data_list['summary']['kpi_pending_shipments'] = round(kpi_pending_shipments, 2)
        dashboard_data_list['summary']['kpi_total_sales'] = round(kpi_total_sales, 2)
        dashboard_data_list['summary']['kpi_avg_order_value'] = round(kpi_avg_order_value, 2)
        return dashboard_data_list

    def get_mk_dashboard_data(self, date_from, date_to, dates_ranges, date_filter, is_general_dashboard=True):
        dashboard_data_list = dict(currency_id=self.env.user.company_id.currency_id.id, is_general_dashboard=is_general_dashboard, sale_graph=[], best_sellers=[], top_customers=[], category_graph=[], country_graph=[], summary=dict(total_orders=0, total_sales=0, pending_shipments=0, avg_order_value=0))

        total_orders = self.env['sale.order'].search([('date_order', '>=', date_from), ('date_order', '<=', date_to), ('state', 'in', ['sale', 'done']),
                                                      ('mk_instance_id', 'in', self.ids)], order="date_order")
        if not date_from or not date_to or not self:
            return dashboard_data_list

        sales_domain = [('state', 'in', ['sale', 'done']), ('order_reference', 'in', [f'sale.order,{order.id}' for order in total_orders]), ('date', '>=', date_from), ('date', '<=', date_to)]

        # Product-based computation
        self._get_top_10_performing_products(dashboard_data_list, sales_domain)

        # Customer-based computation
        self._get_top_10_customers(total_orders, dashboard_data_list, sales_domain)

        # Sale Graph
        self._get_data_for_instance_wise_selling(is_general_dashboard, dashboard_data_list, sales_domain, date_from, date_to)

        # Country wise selling
        self._fetch_country_sales_data_for_dashboard(dashboard_data_list, sales_domain)

        # Category wise selling
        self._get_top_5_performing_category_for_dashboard(dashboard_data_list, sales_domain)

        # Tiles Summery
        self._fetch_tiles_summary_for_dashboard(is_general_dashboard, sales_domain, total_orders, dashboard_data_list, date_from, date_to)

        # KPI for Tiles Summery
        if not dates_ranges:
            dates_ranges = {'date_from': fields.Date.from_string(date_from), 'date_to': fields.Date.from_string(date_to)}
        self._fetch_kpi_tiles_summary_for_dashboard(is_general_dashboard, dashboard_data_list, dates_ranges, date_filter)
        return dashboard_data_list

    def _compute_sale_graph(self, date_from, date_to, sales_domain, previous=False):
        days_between = (date_to - date_from).days
        date_list = [(date_from + timedelta(days=x)) for x in range(0, days_between + 1)]

        daily_sales = self.env['sale.report'].read_group(domain=sales_domain, fields=['date', 'price_subtotal'], groupby=['date:day'])

        daily_sales_dict = {p['date:day']: p['price_subtotal'] for p in daily_sales}

        sales_graph = [{
            '0': fields.Date.to_string(d) if not previous else fields.Date.to_string(d + timedelta(days=days_between)),
            '1': daily_sales_dict.get(babel.dates.format_date(d, format='dd MMM yyyy', locale=self.env.context.get('lang') or 'en_US'), 0)
        } for d in date_list]
        date_range = [item.get('0') for item in sales_graph]
        sale_amount = [item.get('1') for item in sales_graph]
        if len(date_range) == 1:
            next_date = fields.Date.to_string(fields.Date.from_string(date_range[0]) + timedelta(1))
            date_range = date_range + [next_date]
            sale_amount = sale_amount + [0.0]
        return [date_range, sale_amount]

    @api.model
    def _get_mk_dashboard_dates_ranges(self):
        today = fields.Date.context_today(self)

        is_account_present = hasattr(self.env.company, 'compute_fiscalyear_dates')
        this_year = {'date_from': date(today.year, 1, 1), 'date_to': date(today.year, 12, 31)}
        last_year = {'date_from': date(today.year - 1, 1, 1), 'date_to': date(today.year - 1, 12, 31)}

        this_year_dates = self.env.company.compute_fiscalyear_dates(today) if is_account_present else this_year
        last_year_dates = self.env.company.compute_fiscalyear_dates(today - relativedelta(years=1)) if is_account_present else last_year

        this_quarter_from, this_quarter_to = date_utils.get_quarter(today)
        last_quarter_from, last_quarter_to = date_utils.get_quarter(today - relativedelta(months=3))

        this_month_from, this_month_to = date_utils.get_month(today)
        last_month_from, last_month_to = date_utils.get_month(today - relativedelta(months=1))
        return {
            'this_year': {'date_from': this_year_dates['date_from'], 'date_to': this_year_dates['date_to']},
            'last_year': {'date_from': last_year_dates['date_from'], 'date_to': last_year_dates['date_to']},
            'this_quarter': {'date_from': this_quarter_from, 'date_to': this_quarter_to},
            'last_quarter': {'date_from': last_quarter_from, 'date_to': last_quarter_to},
            'this_month': {'date_from': this_month_from, 'date_to': this_month_to},
            'last_month': {'date_from': last_month_from, 'date_to': last_month_to},
        }

    def check_instance_pricelist(self, currency_id):
        if self.pricelist_id:
            instance_currency_id = self.pricelist_id.currency_id
            if instance_currency_id != currency_id:
                raise MarketplaceException(_("Pricelist's currency and currency get from Marketplace is not same. Marketplace Currency: {}".format(currency_id.name)))
        return True

    def create_pricelist(self, currency_id):
        pricelist_vals = {'name': "{}: {}".format(self.marketplace.title(), self.name),
                          'currency_id': currency_id.id,
                          'company_id': self.company_id.id}
        pricelist_id = self.env['product.pricelist'].create(pricelist_vals)
        return pricelist_id

    def set_pricelist(self, currency_name):
        currency_obj = self.env['res.currency']
        currency_id = currency_obj.search([('name', '=', currency_name)])
        if not currency_id:
            currency_id = currency_obj.search([('name', '=', currency_name), ('active', '=', False)])
            if currency_id:
                currency_id.write({'active': True})
        if not self.check_instance_pricelist(currency_id):
            raise MarketplaceException(_("Set Pricelist currency {} is not match with {} Store Currency {}".format(self.pricelist_id.currency_id.name, self.marketplace.title(), currency_name)))
        if not self.pricelist_id:
            pricelist_id = self.create_pricelist(currency_id)
            if not pricelist_id:
                raise MarketplaceException(_("Please set pricelist manually with currency: {}".format(currency_id.name)))
            self.pricelist_id = pricelist_id.id
        return True

    def get_fields_for_hide(self):
        marketplace_list = self.env['mk.instance'].get_all_marketplace()
        field_dict = {}
        for marketplace in marketplace_list:
            if hasattr(self, '%s_hide_fields' % marketplace):
                field_list = getattr(self, '%s_hide_fields' % marketplace)()
                field_dict.update({marketplace: field_list})
        return field_dict

    def get_page_for_hide(self):
        marketplace_list = self.env['mk.instance'].get_all_marketplace()
        page_dict = {}
        for marketplace in marketplace_list:
            if hasattr(self, '%s_hide_page' % marketplace):
                page_list = getattr(self, '%s_hide_page' % marketplace)()
                page_dict.update({marketplace: page_list})
        return page_dict

    def get_instance_fields_for_hide(self):
        marketplace_list = self.env['mk.instance'].get_all_marketplace()
        instance_field_dict = {}
        for marketplace in marketplace_list:
            if hasattr(self, '%s_hide_instance_field' % marketplace):
                instance_field_list = getattr(self, '%s_hide_instance_field' % marketplace)()
                instance_field_dict.update({marketplace: instance_field_list})
        return instance_field_dict

    @api.model
    def get_view(self, view_id=None, view_type='form', **options):
        """
        Customizes the view to conditionally hide fields and pages based on the marketplace.
        """
        # Fetch the original view
        ret_val = super(MkInstance, self).get_view(view_id=view_id, view_type=view_type, **options)
        doc = etree.XML(ret_val['arch'])

        if view_type == 'form':
            # Apply invisibility to pages and fields in the form view
            self._apply_invisible_to_pages(doc)
            self._apply_invisible_to_instance_fields(doc)

        # Update the view architecture with the modified XML
        ret_val['arch'] = etree.tostring(doc, encoding='unicode')
        return ret_val

    def _apply_invisible_to_pages(self, doc):
        """
        Applies conditional invisibility to pages in the form view based on marketplace settings.
        Args:
            doc: The XML document (form view) to modify.
        """
        # Get the pages to hide based on marketplace
        need_to_hide_page_list = self.get_page_for_hide()

        for marketplace, page_list in need_to_hide_page_list.items():
            for page in page_list:
                for node in doc.xpath("//page[@name='%s']" % page):
                    self._set_invisibility(node, marketplace)

    def _apply_invisible_to_instance_fields(self, doc):
        """
        Applies conditional invisibility to instance-specific fields in the form view.
        Args:
            doc: The XML document (form view) to modify.
        """
        # Get the fields to hide based on marketplace
        need_to_hide_instance_fields_list = self.get_instance_fields_for_hide()

        for marketplace, field_list in need_to_hide_instance_fields_list.items():
            for field in field_list:
                for node in doc.xpath("//div[@name='%s']" % field):
                    self._set_invisibility(node, marketplace)

    def _set_invisibility(self, node, marketplace):
        """
        Sets the 'invisible' attribute for a given node based on marketplace conditions.
        Args:
            node: The XML node (either a page or a field) to modify.
            marketplace (str): The marketplace condition for setting invisibility.
        """
        # Get the existing 'invisible' attribute
        existing_invisible = node.get("invisible", "")

        # Define the new condition based on the marketplace
        new_condition = f"marketplace == '{marketplace}'"

        # Combine the existing condition with the new condition, if applicable
        if existing_invisible:
            combined_condition = f"{existing_invisible} or {new_condition}"
            node.set("invisible", combined_condition)
        else:
            node.set("invisible", new_condition)

    def create_update_schedule_actions(self):
        self.env['ir.cron'].setup_schedule_actions(self)

    def action_open_model_view(self, res_id_list, model_name, action_name):
        if not res_id_list:
            return False
        action = {'res_model': model_name, 'type': 'ir.actions.act_window', 'target': 'current'}
        if len(res_id_list) == 1:
            action.update({'view_mode': 'form', 'res_id': res_id_list[0]})
        else:
            action.update({'name': _(action_name), 'domain': [('id', 'in', res_id_list)], 'view_mode': 'list,form'})
        return action

    def get_options(self, previous_options=None):
        options = {'sections_source_id': 'mk_instance_dashboard',
                   'report_id': 'mk_instance_dashboard'}
        if (previous_options or {}).get('_running_export_test'):
            options['_running_export_test'] = True
        self._init_options_date(options, previous_options)
        return options

    @api.model
    def _get_dates_previous_period(self, options, period_vals, tax_period=False):
        period_type = period_vals['period_type']
        mode = period_vals['mode']
        date_from = fields.Date.from_string(period_vals['date_from'])
        date_to = date_from - datetime.timedelta(days=1)

        if tax_period:
            date_from, date_to = self.env.company._get_tax_closing_period_boundaries(date_to)
            return self._get_dates_period(date_from, date_to, mode)
        if period_type in ('fiscalyear', 'today'):
            company_fiscalyear_dates = self.env.company.compute_fiscalyear_dates(date_to)
            return self._get_dates_period(company_fiscalyear_dates['date_from'], company_fiscalyear_dates['date_to'], mode)
        if period_type in ('month', 'custom'):
            return self._get_dates_period(*date_utils.get_month(date_to), mode, period_type='month')
        if period_type == 'quarter':
            return self._get_dates_period(*date_utils.get_quarter(date_to), mode, period_type='quarter')
        if period_type == 'year':
            return self._get_dates_period(*date_utils.get_fiscal_year(date_to), mode, period_type='year')
        return None

    def _init_options_date(self, options, previous_options):
        """ Initialize the 'date' options key.

        :param options:             The current report options to build.
        :param previous_options:    The previous options coming from another report.
        """
        date = previous_options.get('date', {})
        period_date_to = date.get('date_to')
        period_date_from = date.get('date_from')
        mode = date.get('mode')
        date_filter = date.get('filter', 'custom')

        default_filter = 'this_year'
        options_mode = 'range'
        date_from = date_to = period_type = False

        if mode == 'single' and options_mode == 'range':
            # 'single' date mode to 'range'.
            if date_filter:
                date_to = fields.Date.from_string(period_date_to or period_date_from)
                date_from = self.env.company.compute_fiscalyear_dates(date_to)['date_from']
                options_filter = 'custom'
            else:
                options_filter = default_filter
        elif mode == 'range' and options_mode == 'single':
            # 'range' date mode to 'single'.
            if date_filter == 'custom':
                date_to = fields.Date.from_string(period_date_to or period_date_from)
                date_from = date_utils.get_month(date_to)[0]
                options_filter = 'custom'
            elif date_filter:
                options_filter = date_filter
            else:
                options_filter = default_filter
        elif (mode is None or mode == options_mode) and date:
            # Same date mode.
            if date_filter == 'custom':
                if options_mode == 'range':
                    date_from = fields.Date.from_string(period_date_from)
                    date_to = fields.Date.from_string(period_date_to)
                else:
                    date_to = fields.Date.from_string(period_date_to or period_date_from)
                    date_from = date_utils.get_month(date_to)[0]

                options_filter = 'custom'
            else:
                options_filter = date_filter
        else:
            options_filter = default_filter

        if not date_from or not date_to:
            if options_filter == 'today':
                date_to = fields.Date.context_today(self)
                date_from = self.env.company.compute_fiscalyear_dates(date_to)['date_from']
                period_type = 'today'
            elif 'month' in options_filter:
                date_from, date_to = date_utils.get_month(fields.Date.context_today(self))
                period_type = 'month'
            elif 'quarter' in options_filter:
                date_from, date_to = date_utils.get_quarter(fields.Date.context_today(self))
                period_type = 'quarter'
            elif 'year' in options_filter:
                company_fiscalyear_dates = self.env.company.compute_fiscalyear_dates(fields.Date.context_today(self))
                date_from = company_fiscalyear_dates['date_from']
                date_to = company_fiscalyear_dates['date_to']
            elif 'tax_period' in options_filter:
                if 'custom' in options_filter:
                    base_date = fields.Date.from_string(period_date_to)
                else:
                    base_date = fields.Date.context_today(self)

                date_from, date_to = self.env.company._get_tax_closing_period_boundaries(base_date, self)
                period_type = 'tax_period'

        options['date'] = self._get_dates_period(
            date_from,
            date_to,
            options_mode,
            period_type=period_type,
        )

        if any(option in options_filter for option in ['previous', 'next']):
            new_period = date.get('period', -1 if 'previous' in options_filter else 1)
            options['date'] = self._get_shifted_dates_period(options, options['date'], new_period, tax_period='tax_period' in options_filter)
            # This line is useful for the export and tax closing so that the period is set in the options.
            options['date']['period'] = new_period

        options['date']['filter'] = options_filter

    @api.model
    def _get_dates_period(self, date_from, date_to, mode, period_type=None):
        def match(dt_from, dt_to):
            return (dt_from, dt_to) == (date_from, date_to)

        def get_quarter_name(date_to, date_from):
            date_to_quarter_string = format_date(self.env, fields.Date.to_string(date_to), date_format='MMM yyyy')
            date_from_quarter_string = format_date(self.env, fields.Date.to_string(date_from), date_format='MMM')
            return f"{date_from_quarter_string} - {date_to_quarter_string}"

        string = None
        # If no date_from or not date_to, we are unable to determine a period
        if not period_type or period_type == 'custom':
            date = date_to or date_from
            company_fiscalyear_dates = self.env.company.compute_fiscalyear_dates(date)
            if match(company_fiscalyear_dates['date_from'], company_fiscalyear_dates['date_to']):
                period_type = 'fiscalyear'
                if company_fiscalyear_dates.get('record'):
                    string = company_fiscalyear_dates['record'].name
            elif match(*date_utils.get_month(date)):
                period_type = 'month'
            elif match(*date_utils.get_quarter(date)):
                period_type = 'quarter'
            elif match(*date_utils.get_fiscal_year(date)):
                period_type = 'year'
            elif match(date_utils.get_month(date)[0], fields.Date.today()):
                period_type = 'today'
            else:
                period_type = 'custom'
        elif period_type == 'fiscalyear':
            date = date_to or date_from
            company_fiscalyear_dates = self.env.company.compute_fiscalyear_dates(date)
            record = company_fiscalyear_dates.get('record')
            string = record and record.name
        elif period_type == 'tax_period':
            day, month = self.env.company._get_tax_closing_start_date_attributes(self)
            months_per_period = self.env.company._get_tax_periodicity_months_delay(self)
            # We need to format ourselves the date and not switch the period type to the actual period because we do not want to write the actual period in the options but keep tax_period
            if day == 1 and month == 1 and months_per_period in (1, 3, 12):
                match months_per_period:
                    case 1:
                        string = format_date(self.env, fields.Date.to_string(date_to), date_format='MMM yyyy')
                    case 3:
                        string = get_quarter_name(date_to, date_from)
                    case 12:
                        string = date_to.strftime('%Y')
            else:
                dt_from_str = format_date(self.env, fields.Date.to_string(date_from))
                dt_to_str = format_date(self.env, fields.Date.to_string(date_to))
                string = '%s - %s' % (dt_from_str, dt_to_str)

        if not string:
            fy_day = self.env.company.fiscalyear_last_day
            fy_month = int(self.env.company.fiscalyear_last_month)
            if mode == 'single':
                string = _('As of %s', format_date(self.env, date_to))
            elif period_type == 'year' or (
                    period_type == 'fiscalyear' and (date_from, date_to) == date_utils.get_fiscal_year(date_to)):
                string = date_to.strftime('%Y')
            elif period_type == 'fiscalyear' and (date_from, date_to) == date_utils.get_fiscal_year(date_to, day=fy_day, month=fy_month):
                string = '%s - %s' % (date_to.year - 1, date_to.year)
            elif period_type == 'month':
                string = format_date(self.env, fields.Date.to_string(date_to), date_format='MMM yyyy')
            elif period_type == 'quarter':
                string = get_quarter_name(date_to, date_from)
            else:
                dt_from_str = format_date(self.env, fields.Date.to_string(date_from))
                dt_to_str = format_date(self.env, fields.Date.to_string(date_to))
                string = _('From %(date_from)s\nto  %(date_to)s', date_from=dt_from_str, date_to=dt_to_str)

        return {
            'string': string,
            'period_type': period_type,
            'currency_table_period_key': f"{date_from if mode == 'range' else 'None'}_{date_to}",
            'mode': mode,
            'date_from': date_from and fields.Date.to_string(date_from) or False,
            'date_to': fields.Date.to_string(date_to),
        }

    @api.model
    def _get_shifted_dates_period(self, options, period_vals, periods, tax_period=False):
        '''Shift the period.
        :param period_vals: A dictionary generated by the _get_dates_period method.
        :param periods:     The number of periods we want to move either in the future or the past
        :return:            A dictionary containing:
            * date_from * date_to * string * period_type *
        '''
        period_type = period_vals['period_type']
        mode = period_vals['mode']
        date_from = fields.Date.from_string(period_vals['date_from'])
        date_to = fields.Date.from_string(period_vals['date_to'])
        if period_type == 'month':
            date_to = date_from + relativedelta(months=periods)
        elif period_type == 'quarter':
            date_to = date_from + relativedelta(months=3 * periods)
        elif period_type == 'year':
            date_to = date_from + relativedelta(years=periods)
        elif period_type in {'custom', 'today'}:
            date_to = date_from + relativedelta(days=periods)

        if tax_period or 'tax_period' in period_type:
            month_per_period = self.env.company._get_tax_periodicity_months_delay(self)
            date_from, date_to = self.env.company._get_tax_closing_period_boundaries(date_from + relativedelta(months=month_per_period * periods), self)
            return self._get_dates_period(date_from, date_to, mode, period_type='tax_period')
        if period_type in ('fiscalyear', 'today'):
            # Don't pass the period_type to _get_dates_period to be able to retrieve the account.fiscal.year record if
            # necessary.
            company_fiscalyear_dates = {}
            # This loop is needed because a fiscal year can be a month, quarter, etc
            for _ in range(abs(periods)):
                date_to = (date_from if periods < 0 else date_to) + relativedelta(days=periods)
                company_fiscalyear_dates = self.env.company.compute_fiscalyear_dates(date_to)
                if periods < 0:
                    date_from = company_fiscalyear_dates['date_from']
                else:
                    date_to = company_fiscalyear_dates['date_to']

            return self._get_dates_period(company_fiscalyear_dates['date_from'], company_fiscalyear_dates['date_to'], mode)
        if period_type in ('month', 'custom'):
            return self._get_dates_period(*date_utils.get_month(date_to), mode, period_type='month')
        if period_type == 'quarter':
            return self._get_dates_period(*date_utils.get_quarter(date_to), mode, period_type='quarter')
        if period_type == 'year':
            return self._get_dates_period(*date_utils.get_fiscal_year(date_to), mode, period_type='year')
        return None

    def _is_module_installed(self, module_name):
        """
        Check if a specific module is installed.
        Args:
            module_name: The name of the module to check.
        Returns:
            bool: True if the module is installed, False otherwise.
        """
        return bool(self.env['ir.module.module'].sudo().search([('name', '=', module_name), ('state', '=', 'installed')]))
