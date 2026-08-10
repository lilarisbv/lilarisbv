import pytz
import logging
from datetime import datetime
from odoo.addons.woocommerce.woocommerce import API
from urllib.parse import urlparse
from odoo import models, fields, api, _
from odoo.tools import DEFAULT_SERVER_DATETIME_FORMAT, file_path
from odoo.exceptions import ValidationError, AccessError, UserError
from odoo.addons.base.models.res_partner import _tz_get
from odoo.addons.base_marketplace.models.misc import check_go_live

_logger = logging.getLogger("Teqstars:WooCommerce")


class MkInstance(models.Model):
    _inherit = "mk.instance"

    def _get_mk_kanban_counts(self):
        super(MkInstance, self)._get_mk_kanban_counts()
        for mk_instance_id in self:
            mk_instance_id.woo_product_category_count = self.env['woo.product.category.ts'].search_count([('mk_instance_id', '=', mk_instance_id.id)])
            mk_instance_id.woo_product_tag_count = self.env['woo.product.tags.ts'].search_count([('mk_instance_id', '=', mk_instance_id.id)])

    def _get_default_woo_status(self):
        status_id = self.env.ref('woocommerce.processing_status', raise_if_not_found=False)
        return [(6, 0, [status_id.id])] if status_id else False

    def _get_woocommerce_discount_product(self):
        return self.env.ref('woocommerce.woocommerce_discount', raise_if_not_found=False) or False

    def _get_woocommerce_delivery_product(self):
        return self.env.ref('woocommerce.woocommerce_delivery', raise_if_not_found=False) or False

    def woocommerce_mk_default_api_limit(self):
        return 100

    def woocommerce_shown_in_onboarding_picker(self):
        return True

    def _validate_woocommerce_shop_url(self, shop_url):
        """Validate and normalize a WooCommerce shop URL.

        Returns:
            tuple: (error_message_or_None, normalized_url)
        """
        shop_url = (shop_url or '').strip()
        if not shop_url:
            return None, shop_url
        if shop_url.endswith('/'):
            shop_url = shop_url[:-1]
        if not urlparse(shop_url).scheme:
            return _("URL must include http or https!"), shop_url
        return None, shop_url

    marketplace = fields.Selection(selection_add=[('woocommerce', "WooCommerce")], string='Marketplace')
    woo_consumer_key = fields.Char("Consumer key", copy=False)
    woo_consumer_secret = fields.Char("Consumer Secret", copy=False)
    woo_shop_url = fields.Char("Shop URL", copy=False, help="Exp. https://teqstars.mywoocommerce.com")

    woo_weight_uom_id = fields.Many2one("uom.uom", string="Weight Unit", domain=[('name','in',['kg','g','oz','lb'])],
                                        help="This controls what unit you will define weights in.")
    woo_order_status_ids = fields.Many2many('woo.order.status', 'marketplace_woo_order_status_rel', 'mk_instance_id', 'status_id', "Order Status",
                                            default=_get_default_woo_status, help="Filter orders by their fulfillment status at the time of Import Orders.")
    woo_order_workflow_config_ids = fields.One2many("woo.order.workflow.config", "mk_instance_id", "Order Workflow Configuration")

    woocommerce_fees_product_id = fields.Many2one('product.product', string='Fees Product', domain=[('type', '=', 'service')],
                                                  help="WooCommerce Fees order line will be imported with this product (Only Service type product).")

    # Webhook
    woo_webhook_url = fields.Char("Webhook URL", copy=False)
    woo_webhook_ids = fields.One2many("woocommerce.webhook.ts", "mk_instance_id", "Webhooks")

    # Dashboard fields
    woo_product_category_count = fields.Integer("WooCommerce Category Count", compute='_get_mk_kanban_counts')
    woo_product_tag_count = fields.Integer("WooCommerce Tag Count", compute='_get_mk_kanban_counts')

    # Meta Fields
    custom_field_mapping_ids = fields.One2many("woocommerce.custom.field.mapping", "mk_instance_id")

    # Customer Fields.
    is_create_company_contact_woo = fields.Boolean("Create Company Contact?", default=False, help="It will create company contact if found company while creating Customer.")

    woo_store_tz = fields.Selection(_tz_get, string="Timezone", default=lambda self: self.env.user.tz or 'UTC')

    def woo_convert_date_onstore_tz(self, woo_datetime):
        store_datetime = pytz.utc.localize(woo_datetime).astimezone(pytz.timezone(self.woo_store_tz))
        return fields.Datetime.from_string(store_datetime.strftime(DEFAULT_SERVER_DATETIME_FORMAT))

    @api.model_create_multi
    def create(self, vals_list):
        for instance in vals_list:
            if instance.get('marketplace', '') == 'woocommerce':
                # Onboarding creates the draft row with only marketplace set; URL is filled in the wizard.
                error, normalized_url = self._validate_woocommerce_shop_url(instance.get('woo_shop_url'))
                if error:
                    raise ValidationError(error)
                if normalized_url:
                    instance['woo_shop_url'] = normalized_url
        res = super(MkInstance, self).create(vals_list)
        for instance in vals_list:
            # Create Webhook URL
            if instance.get('marketplace', '') == 'woocommerce':
                odoo_url = self.env['ir.config_parameter'].get_param('web.base.url')
                instance_url = "{}/{}".format(self.env.cr.dbname, res.id)
                res.woo_webhook_url = odoo_url + '/woocommerce/webhook/notification/' + instance_url
                if not res.woocommerce_fees_product_id:
                    fees_product = self.env.ref('woocommerce.woocommerce_fees', False)
                    if fees_product:
                        res.woocommerce_fees_product_id = fees_product
        return res

    def write(self, vals):
        for record in self:
            if vals.get('marketplace') == 'woocommerce' or record.marketplace == 'woocommerce':
                if vals.get('api_limit', False) and vals.get('api_limit') > 100:
                    raise ValidationError(_("You cannot set API Fetch record limit more than 100."))
                if 'woo_shop_url' in vals:
                    error, normalized_url = self._validate_woocommerce_shop_url(vals.get('woo_shop_url'))
                    if error:
                        raise ValidationError(error)
                    vals['woo_shop_url'] = normalized_url
        res = super(MkInstance, self).write(vals)
        for rec in self:
            if rec.marketplace == 'woocommerce' and rec.woo_shop_url and not urlparse(rec.woo_shop_url).scheme:
                raise ValidationError(_("URL must include http or https protocol!"))
        return res

    @api.onchange('marketplace')
    def _onchange_woocommerce_get_default_sync_product_with(self):
        if self.marketplace == 'woocommerce':
            self.sync_product_with = 'sku'

    @api.onchange('woocommerce_fees_product_id', 'marketplace')
    def onchange_tip_product_id(self):
        if not self.woocommerce_fees_product_id and self.marketplace == 'woocommerce':
            self.woocommerce_fees_product_id = self.env.ref('woocommerce.woocommerce_fees', False) or False

    def woocommerce_mk_kanban_badge_color(self):
        return "##7F54B3"

    def woocommerce_mk_kanban_image(self):
        return file_path('woocommerce/static/description/img/woocommerce_logo.png')

    @check_go_live()
    def connection_to_woocommerce(self):
        woocommerce = API(url=self.woo_shop_url,
                          consumer_key=self.woo_consumer_key,
                          consumer_secret=self.woo_consumer_secret,
                          version="wc/v3")
        return woocommerce

    @check_go_live()
    def fetch_data_from_woocommerce(self, method, url, params={}, **kwargs):
        try:
            response_list, api_limit, page_url, page = [], self.api_limit or 100, False, 1
            woocommerce = self.connection_to_woocommerce()
            if params:
                params.update({"per_page": api_limit, "page": page})
            else:
                params = {"per_page": api_limit, "page": page}
            while 1:
                if page_url:
                    page_wise_response = eval('woocommerce.' + method + '(page_url)')
                else:
                    page_wise_response = eval('woocommerce.' + method + '(url, params=params, **kwargs)')
                status = page_wise_response.status_code
                total_pages = page_wise_response.headers.get('X-WP-TotalPages', 1) or 1
                if page_wise_response.status_code in [504, 403, 401]:
                    page_wise_response.raise_for_status()
                page_wise_response = page_wise_response.json()
                if isinstance(page_wise_response, dict):
                    page_wise_response = [page_wise_response]
                response_list += page_wise_response
                if not page_wise_response or int(total_pages) == page:
                    break
                page += 1
                params.update({'page': page})
            return response_list, status
        except Exception as e:
            return e, 400

    def woocommerce_confirm_connection(self):
        try:
            shop_currency_list, status_code = self.fetch_data_from_woocommerce('get', 'settings/general/woocommerce_currency')
            if status_code in [200, 201]:
                self.set_pricelist(shop_currency_list[0].get('value'))
            elif status_code in [400, 401]:
                raise ValidationError(shop_currency_list)
            shop_weight_uom_list, status_code = self.fetch_data_from_woocommerce('get', 'settings/products/woocommerce_weight_unit')
            if status_code in [200, 201]:
                self.set_woo_weight_unit(shop_weight_uom_list[0])
                self.env['woo.payment.gateway.ts'].set_woo_payment_gateways(self)
            elif status_code in [400, 401]:
                raise ValidationError(shop_currency_list)
            self.write({'state': 'confirmed'})
        except Exception as e:
            raise AccessError(e)

    def woocommerce_required_access_scopes(self):
        """
        Task: T5058 - Marketplace Access Rights Check
        This method returns set of predefined required access scopes for woocommerce.
        Returns:
            set: Return set of predefined required access scopes for woocommerce.
        """
        required_scopes = {
            'read',
            'write',
        }
        return set(required_scopes)

    def woocommerce_granted_access_scopes(self):
        """
        Task: T5058 - Marketplace Access Rights Check
        This method retrieves the granted scopes from the woocommerce app and returns them.
        Returns:
            set: Return set of granted scopes.
        Raises:
            Exception: Raise user error if exception occurs.
        """
        try:
            granted_scopes = []
            response_data, read_status = self.fetch_data_from_woocommerce('get', 'settings/general/woocommerce_currency')
            if read_status == 200 and isinstance(response_data, list):
                for data in response_data:
                    if data.get('id') == 'woocommerce_currency':
                        granted_scopes.append('read')
                        currency_id = data.get('id')
                        currency_value = data.get('value')
                        if currency_id and currency_value:
                            response, write_status = self.fetch_data_from_woocommerce('put', f'settings/general/{currency_id}', data={'value': currency_value})
                            if write_status in [200, 201]:
                                granted_scopes.append('write')
                return set(granted_scopes)
        except Exception:
            raise UserError("Something went wrong while retrieves the granted scopes from the Woocommerce")

    def woocommerce_action_confirm(self):
        """
        Task: T5058 - Marketplace Access Rights Check
        This method get requested and granted access scopes.if any missing scope present, then opens the permission wizard otherwise establishes a connection to the woocommerce.
        Returns:
            action (dictionary): Return the access scope permission wizard, if find any missing scope.
        Raises:
            Exception: Raise an access error if exception occurs.
        """
        try:
            required_access_scopes = self.woocommerce_required_access_scopes()
            granted_access_scopes = self.woocommerce_granted_access_scopes()
            missing_scope = required_access_scopes - granted_access_scopes
            # If any missing scope present, then opens the permission wizard otherwise establishes a connection to the woocommerce.
            if missing_scope:
                action = self.env.ref('base_marketplace.action_marketplace_scope_wizard').sudo().read()[0]
                action['context'] = {'mk_instance_id': self.id, 'default_mk_instance_name': self.marketplace}
                return action
            else:
                self.confirm_connection()
        except Exception as e:
            raise AccessError(e)

    def woocommerce_test_connection(self):
        """
        Task: T5058 - Marketplace Access Rights Check
        If connection established to the woocommerce then show rainbow man effect.
        Returns:
            effect (dictionary): Returns rainbow man effect if successfully connected to woocommerce.
        Raises:
            Exception: Returns False if exception occurs.
        """
        try:
            shop_currency_list, status_code = self.fetch_data_from_woocommerce('get','settings/general/woocommerce_currency')
            if status_code in [200, 201]:
                return {
                    'effect': {
                        'fadeout': 'slow',
                        'message': "Successfully Connected.",
                        'type': 'rainbow_man',
                    }
                }
        except Exception:
            return False

    def woocommerce_marketplace_operation_wizard(self):
        action = self.env.ref('base_marketplace.action_marketplace_operation').read()[0]
        action['views'] = [(self.env.ref('woocommerce.woocommerce_mk_operation_form_view').id, 'form')]
        return action

    def set_woo_weight_unit(self, shop_weight_uom_dict):
        weight_name = shop_weight_uom_dict.get('value')
        mk_uom_id = False
        if weight_name == 'lbs':
            mk_uom_id = self.env.ref('uom.product_uom_lb')
        elif weight_name == 'kg':
            mk_uom_id = self.env.ref('uom.product_uom_kgm')
        elif weight_name == 'oz':
            mk_uom_id = self.env.ref('uom.product_uom_oz')
        elif weight_name == 'g':
            mk_uom_id = self.env.ref('uom.product_uom_gram')
        if mk_uom_id:
            self.woo_weight_uom_id = mk_uom_id
        return True

    def reset_to_draft(self):
        res = super(MkInstance, self).reset_to_draft()
        if self.marketplace == 'woocommerce':
            self.connection_to_woocommerce()
            for webhook in self.woo_webhook_ids:
                webhook.woocommerce_delete_webhook()
        return res

    def fetch_woo_webhook(self):
        self.env['woocommerce.webhook.ts'].fetch_all_webhook_from_woo(self)
        return True

    def woocommerce_setup_schedule_actions(self, mk_instance_id):
        cron_obj = self.env['ir.cron']
        woocommerce_cron_ids = self.env['ir.cron'].search([('mk_instance_id', '=', self.id), '|', ('active', '=', True), ('active', '=', False)])
        cron_list = [{'cron_name': 'WooCommerce [{}] : Import Order'.format(mk_instance_id.name), 'method_name': 'cron_auto_import_woocommerce_orders', 'model_name': 'sale.order', 'interval_type': 'minutes', 'interval_number': 15},
                     {'cron_name': 'WooCommerce [{}] : Export Order Status/Tracking Information'.format(mk_instance_id.name), 'method_name': 'cron_auto_update_woocommerce_order_status', 'model_name': 'sale.order', 'interval_type': 'minutes',
                      'interval_number': 25},
                     {'cron_name': 'WooCommerce [{}] : Export Product\'s Inventory'.format(mk_instance_id.name), 'method_name': 'cron_auto_export_woocommerce_stock', 'model_name': 'mk.listing', 'interval_type': 'minutes', 'interval_number': 30},
                     {'cron_name': 'WooCommerce [{}] : Import Product\'s Inventory'.format(mk_instance_id.name), 'method_name': 'cron_auto_import_woocommerce_stock', 'model_name': 'mk.listing', 'interval_type': 'days', 'interval_number': 1},
                     {'cron_name': 'WooCommerce [{}] : Export Product\'s Price'.format(mk_instance_id.name), 'method_name': 'cron_auto_update_woocommerce_product_price', 'model_name': 'mk.listing', 'interval_type': 'days', 'interval_number': 1}]

        for cron_dict in cron_list:
            woocommerce_cron_ids -= cron_obj.create_marketplace_cron(mk_instance_id,
                                                                     cron_dict['cron_name'], method_name=cron_dict['method_name'], model_name=cron_dict['model_name'], interval_type=cron_dict['interval_type'],
                                                                     interval_number=cron_dict['interval_number'])
        if woocommerce_cron_ids:
            woocommerce_cron_ids.unlink()
        return True

    def woocommerce_hide_instance_field(self):
        return ['sync_product_with', 'is_sync_images', 'fbm_order_prefix', 'use_marketplace_currency', 'environment']

    def woocommerce_custom_field_mapping(self, custom_field_mapping, woocommerce_vals, operation_type, record):
        if operation_type == "import":
            for custom_value in woocommerce_vals:
                if custom_value.get('key') == custom_field_mapping.woo_custom_key:
                    self.write_imported_custom_fields_in_odoo_record(custom_field_mapping, custom_value.get('value'), record)
        elif operation_type == 'export':
            if isinstance(record, (list, tuple)):
                rec_val = [getattr(rec, custom_field_mapping.field_id.name) for rec in record]
                value_to_export = ','.join(rec_val)
            else:
                value_to_export = getattr(record, custom_field_mapping.field_id.name)
            if 'meta_data' in woocommerce_vals:
                woocommerce_vals['meta_data'].append({'key': custom_field_mapping.woo_custom_key, 'value': value_to_export})
            else:
                woocommerce_vals.setdefault('meta_data', []).append({'key': custom_field_mapping.woo_custom_key, 'value': value_to_export})
            return woocommerce_vals
        return True

    def write_imported_custom_fields_in_odoo_record(self, custom_field_mapping, custom_value, record):
        try:
            if custom_field_mapping.field_id.ttype in ['datetime', 'date']:
                match_format = custom_field_mapping.date_format + ' ' + custom_field_mapping.time_format if custom_field_mapping.time_format else custom_field_mapping.date_format
                custom_value = datetime.strptime(custom_value, match_format)
            field = record._fields[custom_field_mapping.field_id.name]
            record_value = field.convert_to_cache(custom_value, record)
            record.write({custom_field_mapping.field_id.name: record_value})
        except Exception as error:
            self.env['mk.log'].create_update_log(mk_instance_id=self, operation_type='import', mk_log_line_dict={'error': [{'log_message': error}]})
        return True

    def action_create_queue(self, type):
        res = super(MkInstance, self).action_create_queue(type)
        res.write({'woo_update_image': self.env.context.get('woo_update_image')})
        return res

    # ------------------------------------------------------------------
    # Quick Onboarding
    # ------------------------------------------------------------------
    def woocommerce_get_onboarding_steps(self):
        return [
            {
                'title': _('Connect Your Store'),
                'icon': 'fa-plug',
                'template': 'woocommerce.MarketplaceOnboarding_woocommerce_step_1',
                'woo_card_heading': _('Connect your WooCommerce store'),
                'woo_card_intro': _('Enter your <b>https://</b> store URL and the REST API <b>Consumer Key / Secret</b> from <b>WooCommerce → Settings → Advanced → REST API</b> (create a <b>Read/Write</b> key).'),
                'woo_screen_help_title': _('What you need on this screen'),
                'woo_screen_help': [
                    _('<b>Shop URL</b> + <b>Consumer Key / Secret</b> — Your store URL and REST API credentials.'),
                    _('<b>Import order after</b> — Import orders after this specific date(Optional), if left empty then import orders of last 3 days.'),
                ],
                'woo_doc_url': 'https://woocommerce.com/document/woocommerce-rest-api/#generate-api-keys',
                'woo_doc_label': _('How to generate API keys'),
            },
            {
                'title': _('Product Settings'),
                'icon': 'fa-cubes',
                'template': 'woocommerce.MarketplaceOnboarding_woocommerce_step_2',
                'woo_card_heading': _('Product & stock settings'),
                'woo_card_intro': _('Products sync by <b>SKU</b> (WooCommerce supports SKU only). Decide whether to <b>create missing products</b> and which <b>stock quantity</b> is exported.'),
                'woo_tip': _('<b>Tip:</b> <b>Free To Use Quantity</b> excludes reserved stock, so it helps avoid overselling.'),
                'woo_screen_help_title': _('What you need on this screen'),
                'woo_screen_help': [
                    _('<b>Sync products by</b> — SKU (fixed) WooCommerce identifies products by SKU only.'),
                    _('<b>Create Odoo products if missing</b> — If no product is matched by the <b>SKU</b> then creates Odoo product.'),
                    _('<b>Export stock based on</b> — On Hand, Forecasted, or Free To Use.'),
                ],
            },
            {
                'title': _('Order Configuration'),
                'icon': 'fa-shopping-cart',
                'template': 'woocommerce.MarketplaceOnboarding_woocommerce_step_3',
                'woo_card_heading': _('Tax & order import'),
                'woo_card_intro': _('Pick the <b>tax behaviour</b> for imported orders (and tax accounts if you follow the marketplace tax), and which <b>order statuses</b> to import.'),
                'woo_tip': _('<b>Tip:</b> Keep <b>Odoo\'s Default Tax Behaviour</b> if your products already carry the right taxes.'),
                'woo_screen_help_title': _('What you need on this screen'),
                'woo_screen_help': [
                    _('<b>Tax behaviour</b> — Taxes to be applied on the order while importing from WooCommerce.'),
                    {'text': _('<b>Tax Account</b> — Which account should be used to configure while creating tax on orders.'), 'tax_only': True},
                    {'text': _('<b>Tax Account on Credit Notes</b> — Which account should be used to configure while creating tax on Credit Notes.'), 'tax_only': True},
                    _('<b>Order statuses</b> — Choose the WooCommerce statuses to import (e.g. <i>Processing</i>), orders in other statuses are skipped while importing order of specific date.'),
                ],
            },
            {
                'title': _('Review & Confirm'),
                'description': _('Review your setup before saving'),
                'icon': 'fa-check-circle',
                'template': 'woocommerce.MarketplaceOnboarding_woocommerce_step_4',
                'woo_card_heading': _('Review & confirm'),
                'woo_card_intro': _('Check the summary, then click <b>Save</b> — Odoo connects to your store, verifies the credentials and activates the integration.'),
            }
        ]

    def _woocommerce_last_order_sync_for_onboarding_input(self):
        """Format stored UTC datetime for HTML ``datetime-local`` in the user timezone."""
        if not self.last_order_sync_date:
            return ''
        dt = fields.Datetime.context_timestamp(self, self.last_order_sync_date)
        return dt.strftime('%Y-%m-%dT%H:%M')

    def _woocommerce_parse_onboarding_last_order_sync(self, raw):
        if not raw:
            return False
        s = str(raw).strip().replace('T', ' ')
        if len(s) == 16:
            s += ':00'
        try:
            return fields.Datetime.to_datetime(s)
        except Exception:
            return False

    def _woocommerce_get_stock_field_options(self):
        """Allowed stock-source fields for inventory export (mirrors the radio domain on the instance form)."""
        recs = self.env['ir.model.fields'].sudo().search_read(
            [('model_id.model', '=', 'product.product'), ('name', 'in', ['qty_available', 'virtual_available', 'free_qty'])],
            ['id', 'field_description', 'name'],
            order='name',
        )
        return [{'id': r['id'], 'name': r['field_description'] or r['name']} for r in recs]

    def woocommerce_get_onboarding_values(self):
        tax_pairs = self._fields['tax_system']._description_selection(self.env)
        tax_sel = dict(tax_pairs)
        sync_sel = dict(self._fields['sync_product_with']._description_selection(self.env))
        tax_account_options = self.get_onboarding_tax_account_options() if self.id else self.get_onboarding_tax_account_options_default_company()
        stock_field_options = self._woocommerce_get_stock_field_options()
        # Default to qty_available when not yet set (matches base_marketplace stock_field_id default).
        if self.id and self.stock_field_id:
            stock_field_id = self.stock_field_id.id
        else:
            qty_field = self.env['ir.model.fields'].sudo().search(
                [('model_id.model', '=', 'product.product'), ('name', '=', 'qty_available')], limit=1)
            stock_field_id = qty_field.id or False
        return {
            'name': self.name or '',
            'last_order_sync_date': self._woocommerce_last_order_sync_for_onboarding_input() if self.id else '',
            'woo_shop_url': self.woo_shop_url or '',
            'woo_consumer_key': self.woo_consumer_key or '',
            'woo_consumer_secret': self.woo_consumer_secret or '',
            'sync_product_with': 'sku',
            'sync_product_with_label': sync_sel.get('sku', 'SKU'),
            'stock_field_id': stock_field_id,
            'stock_field_options': stock_field_options,
            'is_create_products': self.is_create_products,
            'tax_system': self.tax_system or 'according_to_marketplace',
            'tax_system_label': tax_sel.get(self.tax_system, ''),
            'tax_system_options': [{'value': k, 'label': v} for k, v in tax_pairs],
            'tax_account_id': self.tax_account_id.id if self.tax_account_id else False,
            'tax_refund_account_id': self.tax_refund_account_id.id if self.tax_refund_account_id else False,
            'tax_account_options': tax_account_options,
            'fulfillment_status_ids': self._woocommerce_default_status_ids_for_onboarding(),
            'fulfillment_status_options': self._woocommerce_get_status_options(),
        }

    def _woocommerce_get_status_options(self):
        recs = self.env['woo.order.status'].sudo().search_read([], ['id', 'name'], order='name')
        return [{'id': r['id'], 'name': r['name']} for r in recs]

    def _woocommerce_default_status_ids_for_onboarding(self):
        if self.id and self.woo_order_status_ids:
            return self.woo_order_status_ids.ids
        default = self.env.ref('woocommerce.processing_status', raise_if_not_found=False)
        return [default.id] if default else []

    def woocommerce_validate_onboarding_step_1(self, data):
        name = (data.get('name') or '').strip()
        if not name:
            return {'valid': False, 'error': _('Instance name is required.')}
        shop_url = (data.get('woo_shop_url') or '').strip()
        if not shop_url:
            return {'valid': False, 'error': _('Shop URL is required.')}
        url_error, _normalized = self._validate_woocommerce_shop_url(shop_url)
        if url_error:
            return {'valid': False, 'error': url_error}
        if not (data.get('woo_consumer_key') or '').strip():
            return {'valid': False, 'error': _('Consumer Key is required.')}
        if not (data.get('woo_consumer_secret') or '').strip():
            return {'valid': False, 'error': _('Consumer Secret is required.')}
        return {'valid': True}

    def woocommerce_validate_onboarding_step_2(self, data):
        return {'valid': True}

    def woocommerce_validate_onboarding_step_3(self, data):
        tax_system = data.get('tax_system')
        valid_tax = dict(self._fields['tax_system']._description_selection(self.env))
        if tax_system not in valid_tax:
            return {'valid': False, 'error': _('Please select a valid tax configuration.')}
        if tax_system != 'default':
            tax_acc = data.get('tax_account_id')
            tax_ref = data.get('tax_refund_account_id')
            if not tax_acc or not tax_ref:
                return {
                    'valid': False,
                    'error': _('Please select Tax Account and Tax Account on Credit Notes for this tax configuration.'),
                }
            company = self.company_id or self.env.company
            Account = self.env['account.account'].with_company(company).sudo()
            domain = self._mk_onboarding_tax_account_domain(company)
            if (
                Account.search_count(domain + [('id', '=', int(tax_acc))]) != 1
                or Account.search_count(domain + [('id', '=', int(tax_ref))]) != 1
            ):
                return {'valid': False, 'error': _('One or more selected tax accounts are not valid for this company.')}
        status_ids = data.get('fulfillment_status_ids') or []
        if not isinstance(status_ids, list) or not status_ids:
            return {'valid': False, 'error': _('Please select at least one order status to import.')}
        existing = self.env['woo.order.status'].sudo().search_count([('id', 'in', status_ids)])
        if existing != len(set(status_ids)):
            return {'valid': False, 'error': _('One or more order statuses are invalid.')}
        return {'valid': True}

    def woocommerce_save_onboarding_data(self, values):
        vals = {}
        if 'name' in values:
            vals['name'] = (values.get('name') or '').strip()
        if 'last_order_sync_date' in values:
            vals['last_order_sync_date'] = self._woocommerce_parse_onboarding_last_order_sync(
                values.get('last_order_sync_date')
            )
        if 'woo_shop_url' in values:
            vals['woo_shop_url'] = (values.get('woo_shop_url') or '').strip()
        new_key = values.get('woo_consumer_key')
        if new_key:
            vals['woo_consumer_key'] = new_key
        new_secret = values.get('woo_consumer_secret')
        if new_secret:
            vals['woo_consumer_secret'] = new_secret
        vals['sync_product_with'] = 'sku'  # WooCommerce identifies products by SKU only.
        if 'stock_field_id' in values:
            vals['stock_field_id'] = int(values['stock_field_id']) if values.get('stock_field_id') else False
        if 'is_create_products' in values:
            vals['is_create_products'] = bool(values.get('is_create_products'))
        if 'fulfillment_status_ids' in values:
            ids = [int(x) for x in (values.get('fulfillment_status_ids') or [])]
            vals['woo_order_status_ids'] = [(6, 0, ids)]
        if 'tax_system' in values:
            vals['tax_system'] = values['tax_system']
        ts = vals.get('tax_system', self.tax_system)
        if ts == 'default':
            vals['tax_account_id'] = False
            vals['tax_refund_account_id'] = False
        else:
            if 'tax_account_id' in values:
                vals['tax_account_id'] = values['tax_account_id'] or False
            if 'tax_refund_account_id' in values:
                vals['tax_refund_account_id'] = values['tax_refund_account_id'] or False
        if vals:
            self.write(vals)
        # Same as Confirm on the instance form: verify scopes / connect, then confirm or open scope wizard.
        confirm_action = self.action_confirm()
        if isinstance(confirm_action, dict) and confirm_action.get('type'):
            return {'action': confirm_action}
        return {}

    def _get_woocommerce_automatic_jobs_note(self):
        return _(
            "<ul>"
            "<li>We create <strong>variant-level</strong> pricelist rules in Odoo while importing <strong>products/listings</strong> from the marketplace.</li>"
            "<li><strong>Export/Update Product's Price in marketplace</strong> supports all pricelist rule levels - <strong>Variant, Product, Category and Global</strong> - with priority handled by Odoo's standard pricelist function.</li>"
            "<li>The system will no longer update the product prices in the marketplace if the price is set to zero.</li>"
            "</ul>"
        )
