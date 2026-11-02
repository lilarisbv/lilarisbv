import base64
import datetime
import json
import logging
import time
from uuid import uuid4

import requests
from dateutil.relativedelta import relativedelta

from odoo import models, fields, api, _
from odoo.addons.base_marketplace.models.exceptions import MarketplaceException
from odoo.addons.base_marketplace.models.misc import check_go_live
from odoo.modules.module import get_resource_from_path
from odoo.tools.misc import file_path

_logger = logging.getLogger("TeqStars:bol")
_logger_connection = logging.getLogger("TeqStars:bol.connection")

ACCOUNT_STATE = [('not_confirmed', 'Not Confirmed'), ('confirmed', 'Confirmed')]
ORDER_FILTER_STATUS = [('OPEN', 'OPEN'), ('SHIPPED', 'SHIPPED'), ('ALL', 'ALL')]

BOL_ACCEPT_V10 = 'application/vnd.retailer.v10+json'
BOL_ACCEPT_V10_CSV = 'application/vnd.retailer.v10+csv'
BOL_OFFERS_ACCEPT_V11 = 'application/vnd.retailer.v11+json'

# Bol push notification subscriptions (webhooks). One webhook record per resource.
# OFFER_FOR_SALE / OFFER_NOT_FOR_SALE excluded: webhook delivery not supported for these
# (Bol only offers GCP Pub/Sub / AWS SQS for them).
BOL_WEBHOOK_EVENT_CODES = ['ORDER', 'SHIPMENT', 'PROCESS_STATUS']
BOL_CONDITION_CATEGORY = [('NEW', 'New'), ('SECONDHAND', 'Second hand'), ('REFURBISHED', 'Refurbished')]
BOL_CONDITION_STATE = [('AS_NEW', 'As new'), ('GOOD', 'Good'), ('MODERATE', 'Moderate'), ('REASONABLE', 'Reasonable')]
BOL_CONDITION_GRADE = [('A', 'A - near-mint'), ('B', 'B - light scratches'), ('C', 'C - low cosmetic quality')]
BOL_FULFILMENT_SCHEDULE = [('MY_DELIVERY_PROMISE', 'My delivery promise'), ('SHIPPING_VIA_BOL', 'Shipping via bol'), ('BOL_DELIVERY_PROMISE', 'Bol delivery promise')]
BOL_ULTIMATE_ORDER_TIME = [(f'{h:02d}:00', f'{h:02d}:00') for h in range(12, 24)]
# Languages the Product Content API accepts, per selling country. bol.com has no released v11 for content, so those calls keep the v10 media type.
BOL_CONTENT_LANGUAGE_BY_COUNTRY = {'NL': 'nl', 'BE': 'nl-BE'}
BOL_DELIVERY_CODE_TO_V11 = {
    '24uurs-12': ('BOL_DELIVERY_PROMISE', 0, 1, '12:00'),
    '24uurs-13': ('BOL_DELIVERY_PROMISE', 0, 1, '13:00'),
    '24uurs-14': ('BOL_DELIVERY_PROMISE', 0, 1, '14:00'),
    '24uurs-15': ('BOL_DELIVERY_PROMISE', 0, 1, '15:00'),
    '24uurs-16': ('BOL_DELIVERY_PROMISE', 0, 1, '16:00'),
    '24uurs-17': ('BOL_DELIVERY_PROMISE', 0, 1, '17:00'),
    '24uurs-18': ('BOL_DELIVERY_PROMISE', 0, 1, '18:00'),
    '24uurs-19': ('BOL_DELIVERY_PROMISE', 0, 1, '19:00'),
    '24uurs-20': ('BOL_DELIVERY_PROMISE', 0, 1, '20:00'),
    '24uurs-21': ('BOL_DELIVERY_PROMISE', 0, 1, '21:00'),
    '24uurs-22': ('BOL_DELIVERY_PROMISE', 0, 1, '22:00'),
    '24uurs-23': ('BOL_DELIVERY_PROMISE', 0, 1, '23:00'),
    '1-2d': ('BOL_DELIVERY_PROMISE', 1, 2, None),
    '2-3d': ('BOL_DELIVERY_PROMISE', 2, 3, None),
    '3-5d': ('BOL_DELIVERY_PROMISE', 3, 5, None),
    '4-8d': ('BOL_DELIVERY_PROMISE', 4, 8, None),
    '1-8d': ('BOL_DELIVERY_PROMISE', 1, 8, None),
    'VVB': ('SHIPPING_VIA_BOL', None, None, None),
    'MijnLeverbelofte': ('MY_DELIVERY_PROMISE', None, None, None),
}


class MkInstance(models.Model):
    _inherit = "mk.instance"

    marketplace = fields.Selection(selection_add=[('bol', "Bol.com")], ondelete={'bol': 'set default'}, string='Marketplace')

    bol_client_id = fields.Char("Client ID", copy=False)
    bol_client_secret = fields.Char("Client Secret", copy=False)
    bol_access_token = fields.Char(string='Access Token', help='A rotatable unique token to access data from Bol', copy=False)
    bol_expires_in = fields.Char(string="Token Expired In", copy=False)
    bol_last_token_generated_on = fields.Datetime(string='Last Token Generated on', help='Use to automatic rotatable token if expired', copy=False)

    bol_operation_type = fields.Selection([('FBR', 'FBR'), ('FBB', 'FBB'), ('Both', 'FBR & FBB')], default="FBR", string="Use Operation For")
    bol_import_return = fields.Selection([('handled', 'Handled'), ('unhandled', 'Unhandled')], default="unhandled", string="Import Return for Status")

    bol_fbb_warehouse_id = fields.Many2one('stock.warehouse', string='FBB Warehouse', help="FBB order will go for into this warehouse.", ondelete='restrict', copy=False)

    bol_fbr_workflow_id = fields.Many2one("order.workflow.config.ts", "FBR Workflow", ondelete='restrict', copy=False)
    bol_fbb_workflow_id = fields.Many2one("order.workflow.config.ts", "FBB Workflow", ondelete='restrict', copy=False)

    bol_fbb_last_shipment_page_number = fields.Integer("FBB Shipment Page", copy=False,
                                                       help="Technical to manage pagination while importing shipped order/shipments. You can adjust it to zero if you wish to import shipped order from beginning (Past 90 days).")
    bol_fbr_last_shipment_page_number = fields.Integer("FBR Shipment Page", copy=False,
                                                       help="Technical to manage pagination while importing shipped order/shipments. You can adjust it to zero if you wish to import shipped order from beginning (Past 90 days).")
    bol_order_status = fields.Selection(ORDER_FILTER_STATUS, default='OPEN', string="Bol Order Status",
                                        help="To filter on order status. You can filter on either all orders independent from their status, open orders (excluding shipped and cancelled orders), and shipped orders.")

    bol_last_return_sync_on = fields.Datetime("Last Return Imported On", copy=False)
    bol_managing_excluded_taxes_on_product = fields.Boolean("Managing Excluded Taxes on Product?")
    bol_payment_term_id = fields.Many2one('account.payment.term', string='Payment Terms', copy=False, ondelete='restrict')
    bol_export_invoice = fields.Boolean("Export Invoices to Bol.com?", default=False, help="Export invoices to Bol.com when an invoice request is received from the customer.")
    bol_invoice_report_id = fields.Many2one(comodel_name='ir.actions.report', string='Invoice Report', domain=[('model', '=', 'account.move'), ('report_type', '=', 'qweb-pdf')],
                                            help="Choose the invoice report template that will be used to generate the PDF sent to Bol.com. This report defines the layout and content of the exported invoice.")
    bol_export_invoice_delay = fields.Integer(string='Invoice Export Delay (Days)',
                                              help="Define the number of days to wait after the invoice date before exporting the invoice to Bol.com. This delay helps prevent sending invoices too early, such as when an order is cancelled or modified shortly after invoicing.",
                                              default=1)
    bol_default_condition_category = fields.Selection(BOL_CONDITION_CATEGORY, string='Default Condition Category', default='NEW',
                                                      help='Seeded into bol_condition_category when a listing is created from a product.')
    bol_default_condition_state = fields.Selection(BOL_CONDITION_STATE, string='Default Condition State',
                                                   help='Required only when default category is SECONDHAND.')
    bol_default_condition_grade = fields.Selection(BOL_CONDITION_GRADE, string='Default Refurbished Grade',
                                                   help='Required only when default category is REFURBISHED.')
    bol_default_condition_margin = fields.Boolean('Default Margin Adjustment', help='Refurbished only. If true, VAT was paid earlier (0% tax rate).')
    bol_default_fulfilment_schedule = fields.Selection(BOL_FULFILMENT_SCHEDULE,
                                                       string='Default Fulfilment Schedule', default='BOL_DELIVERY_PROMISE',
                                                       help='Seeded into bol_fulfilment_schedule. Only used when fulfilment method is FBR.')
    bol_default_delivery_min_days = fields.Integer('Default Min Days To Customer', default=2,
                                                   help='Seeded into bol_delivery_min_days. Required for BOL_DELIVERY_PROMISE schedule.')
    bol_default_delivery_max_days = fields.Integer('Default Max Days To Customer', default=3, help='Seeded into bol_delivery_max_days.')
    bol_default_ultimate_order_time = fields.Selection(BOL_ULTIMATE_ORDER_TIME,
                                                       string='Default Ultimate Order Time',
                                                       help='Seeded into bol_ultimate_order_time. Required when min=0 and max=1 (24h promise).')

    # === Bol Subscriptions (push notifications / webhooks) ===
    bol_webhook_url = fields.Char("Bol Webhook URL", copy=False)
    bol_webhook_uuid = fields.Char("Webhook UUID", readonly=True, copy=False)
    bol_webhook_ids = fields.One2many("bol.webhook.ts", "mk_instance_id", "Bol Webhooks")
    bol_last_webhook_received = fields.Datetime("Last Webhook Received", copy=False)

    def bol_hide_instance_field(self):
        return ['last_customer_import_date', 'last_listing_import_date', 'is_update_odoo_product_category', 'is_sync_images', 'api_limit', 'discount_product_id', 'use_marketplace_currency',
                'tax_system', 'environment','tax_account_id','tax_refund_account_id','tax_rounding']

    def bol_hide_page(self):
        return ['description', 'product_images']

    @api.onchange('marketplace')
    def _onchange_bol_get_default_tax_system(self):
        if self.marketplace == 'bol':
            self.tax_system = 'default'

    def _get_bol_delivery_product(self):
        return self.env.ref('bol.bol_delivery', raise_if_not_found=False) or False

    def bol_mk_kanban_badge_color(self):
        return "#1000A4"

    def bol_mk_kanban_image(self):
        return file_path('bol/static/description/bol_instance_icon.png')

    def bol_basic_auth(self):
        credentials = "%s:%s" % (self.bol_client_id, self.bol_client_secret)
        return base64.b64encode(credentials.encode('utf-8')).decode('utf-8').replace("\n", "")

    def bol_get_valid_fulfillment_options(self):
        self.ensure_one()
        valid_fulfillment_options = [self.bol_operation_type]
        if self.bol_operation_type == 'Both':
            valid_fulfillment_options = ['FBR', 'FBB']
        return valid_fulfillment_options

    @check_go_live()
    @api.model
    def _send_bol_request(self, request_url, request_data, params={}, files=[], method='GET', accept='application/vnd.retailer.v10+json', max_retries=3, _attempt=1):
        headers = {'Accept': accept}
        if accept.endswith('+json'):
            headers.update({'Content-Type': accept})
        if self.env.context.get('force_content_type_for_invoice'):
            headers.pop('Content-Type', None)
        if self.bol_access_token:
            validated = self.validate_bol_token_hash()
            if not validated:
                self.with_context(token_expired=True).bol_generate_new_token()
        if not self.bol_access_token:
            self.with_context(token_expired=True).bol_generate_new_token()
        if self.bol_access_token:
            headers.update({'Authorization': 'Bearer %s' % self.bol_access_token, })
        req = None
        data = json.dumps(request_data) if request_data else False
        api_endpoint = 'https://api.bol.com/'
        api_url = api_endpoint + request_url
        try:
            _logger_connection.info('%s %s', method, api_url)
            if not data:
                req = requests.request(method, api_url, params=params, headers=headers, files=files)
            else:
                req = requests.request(method, api_url, data=data, params=params, headers=headers, files=files)
            if not self.env.context.get('from_queue'):
                req.raise_for_status()
            response_text = req.text
        except requests.HTTPError as e:
            if e.response.status_code == 429 and _attempt <= max_retries:
                wait = self._bol_retry_wait(e.response)
                _logger_connection.info('429 rate limited on %s, retrying in %ss (attempt %d/%d)', api_url, wait, _attempt, max_retries)
                time.sleep(wait)
                return self._send_bol_request(request_url, request_data, params=params, files=files, method=method, accept=accept,
                                              max_retries=max_retries, _attempt=_attempt + 1)
            elif e.response.status_code == 404 and self.env.context.get('skip_raise'):
                return True
            elif e.response.status_code == 400:
                error_dict = e.response.json()
                error_messages = []
                for violation in error_dict.get('violations', []):
                    error_messages.append(f"{violation.get('name')}: {violation.get('reason')}")
                raise MarketplaceException("\n".join(error_messages))
            else:
                error_dict = e.response.json()
                error_detail = error_dict.get('detail')
                raise MarketplaceException(error_detail, f"{e.response.status_code} - {e.response.reason}")
        except Exception as e:
            raise MarketplaceException("%s" % req.text, additional_context={'show_traceback': True})
        _logger_connection.info('--> %d %s %db', req.status_code, req.reason, len(req.text))
        if req.status_code == 204 or not response_text:
            return True
        response = json.loads(response_text) if accept.endswith('+json') else response_text
        if isinstance(response, dict):
            # A process-status read wants the FAILURE answer itself (bol.process.status tells it apart from a network error).
            if response.get('status') == 'FAILURE' and not self.env.context.get('bol_keep_failed_process_status'):
                raise MarketplaceException(_("{}: {}".format(response.get('eventType'), response.get('errorMessage'))))
        return response

    @staticmethod
    def _bol_retry_wait(response, default=60):
        """Seconds to wait before retrying a Bol 429.

        Honors the ``Retry-After`` header when Bol sends it, capped at 60s so a long header
        value cannot pin a worker for minutes. Without a usable header, waits ``default``.
        """
        retry_after = response.headers.get('Retry-After')
        if retry_after:
            try:
                return max(0.0, min(float(retry_after), 60))
            except (TypeError, ValueError):
                pass
        return default

    def validate_bol_token_hash(self):
        if self.bol_last_token_generated_on and self.bol_access_token and self.bol_expires_in is not None:
            delta = datetime.datetime.now() - self.bol_last_token_generated_on
            # duration = (delta.microseconds + (delta.seconds + delta.days * 24 * 3600) * 10 ** 6) / 10 ** 6
            if delta.total_seconds() > float(self.bol_expires_in):
                return False
            return True
        return False

    def bol_generate_new_token(self):
        self.ensure_one()
        if not self.env.context.get('token_expired', False):
            validated = self.validate_bol_token_hash()
            if validated:
                return True
        self.bol_access_token = False
        headers = {'Authorization': "Basic %s" % self.bol_basic_auth(), 'Accept': 'application/json', 'Content-Type': 'application/x-www-form-urlencoded'}
        try:
            req = requests.request("POST", "https://login.bol.com/token", data={'grant_type': 'client_credentials'}, headers=headers)
            req.raise_for_status()
            response_text = req.text
        except requests.HTTPError as e:
            raise MarketplaceException(_("Bol API request failed with code: {}, msg: {}, content: {}".format(e.response.status_code, e.response.reason, e.response.content)))
        except Exception as e:
            raise MarketplaceException(_("%s" % e), additional_context={'show_traceback': True})
        response = json.loads(response_text)
        if response:
            self.bol_access_token = response.get('access_token')
            self.bol_expires_in = response.get('expires_in')
            self.bol_last_token_generated_on = datetime.datetime.now()
        return True

    def bol_confirm_connection(self):
        if not self.bol_access_token:
            self.with_context(token_expired=True).bol_generate_new_token()
        self.set_pricelist('EUR')
        self.write({'state': 'confirmed'})
        self._bol_register_default_webhooks()

    def _bol_register_default_webhooks(self):
        """Subscribe to Process Status, which keeps catalog job results current without the customer
        having to switch anything on by hand. Offer For Sale / Offer Not For Sale are not registered
        here: Bol does not support webhook delivery for those two (Pub/Sub / SQS only).

        Order and Shipment stay the customer's own choice, so confirming an instance never changes those two
        notifications an existing shop already relies on.

        Bol.com only accepts a secure address, and a subscription that cannot be created must never stop an instance
        from being confirmed, so every registration is attempted on its own and a failure is only logged. When the
        base URL itself is not https, the customer is warned with a notification and an activity instead, the same
        way the eBay connector reminds about a refresh token about to expire.
        """
        webhook_obj = self.env['bol.webhook.ts']
        for mk_instance_id in self.filtered(lambda instance: instance.marketplace == 'bol'):
            if not mk_instance_id.bol_webhook_url:
                mk_instance_id.bol_webhook_url = mk_instance_id._get_bol_webhook_url()
            if not (mk_instance_id.bol_webhook_url or '').startswith('https://'):
                _logger.info("BOL WEBHOOK: Skipped automatic registration for %s because the webhook URL is not secure (https).", mk_instance_id.name)
                mk_instance_id._bol_notify_https_required()
                continue
            mk_instance_id._bol_mark_webhook_https_activities_done()
            # A row ticked Active only says what Odoo believes: bol.com may have dropped the subscription, or
            # never had it. What bol.com actually holds for this URL decides whether it has to be registered again.
            try:
                live_subscriptions = webhook_obj._bol_get_live_subscriptions(mk_instance_id)
            except MarketplaceException as error:
                _logger.warning("BOL WEBHOOK: Could not fetch live subscriptions for %s | Error: %s", mk_instance_id.name, error)
                continue
            if live_subscriptions is False:
                _logger.warning("BOL WEBHOOK: Could not fetch live subscriptions for %s. " "Skipping default webhook reconciliation.", mk_instance_id.name, )
                continue
            live_resources = set()
            for subscription in live_subscriptions:
                if isinstance(subscription, dict) and subscription.get('subscriptionType') == 'WEBHOOK' and subscription.get('eventDestination') == mk_instance_id.bol_webhook_url:
                    live_resources.update(subscription.get('resources') or [])
            for webhook_event in BOL_WEBHOOK_EVENT_CODES:
                # Process Status is taken care of here. Order and Shipment stay the customer's own choice,
                # so an existing shop keeps exactly the notifications it already had.
                if webhook_event != 'PROCESS_STATUS':
                    continue
                webhook_id = mk_instance_id.bol_webhook_ids.filtered(lambda webhook: webhook.webhook_event == webhook_event)
                if webhook_id.active_webhook and webhook_event in live_resources:
                    continue
                try:
                    # A token refresh commits, and a commit drops the savepoint below, so a valid token is made first.
                    mk_instance_id.bol_generate_new_token()
                    with self.env.cr.savepoint():
                        if webhook_id:
                            webhook_id.create_webhook_in_bol(mk_instance_id)
                            if not webhook_id.active_webhook:
                                webhook_id.with_context(skip_create=True).write({'active_webhook': True})
                        else:
                            webhook_obj.create({'mk_instance_id': mk_instance_id.id, 'webhook_event': webhook_event, 'active_webhook': True})
                except Exception as error:
                    _logger.warning("BOL WEBHOOK: Could not register %s for %s | Error: %s", webhook_event, mk_instance_id.name, error)
        return True

    def _bol_get_webhook_https_activities(self):
        self.ensure_one()
        model_id = self.env['ir.model']._get('mk.instance').id
        return self.env['mail.activity'].search([
            ('res_id', '=', self.id),
            ('res_model_id', '=', model_id),
            ('summary', 'ilike', 'Bol.com Webhook HTTPS Required'),
        ])

    def _bol_notify_https_required(self):
        """Warn the customer the base URL is not https: a toast now, and an activity so the message survives past
        it even if nobody is watching the screen at confirm time. Mirrors the eBay refresh-token reminder pattern
        (ebay/models/marketplace_instance.py): create once, dedupe by summary, mark done once the instance registers
        cleanly again. Uses this instance's own Activity Configuration (Notification & Alerts tab) when set, and
        falls back to a To-Do assigned to the salesperson/creator/confirming user otherwise.
        """
        self.ensure_one()
        if self._bol_get_webhook_https_activities():
            return False
        note = _("The Odoo base URL (%s) is not secure (https). Bol.com requires an HTTPS "
                 "webhook callback URL, so the Process Status subscription could not be "
                 "created.\n\n"
                 "Until this is fixed, catalog job results won't reach Odoo automatically.\n\n"
                 "To fix this, open the Webhook tab and add the Process Status webhook "
                 "manually.") % (
                     self.env['ir.config_parameter'].sudo().get_param('web.base.url'))
        activity_type_id = self.mk_activity_type_id or self.env.ref('mail.mail_activity_data_todo', raise_if_not_found=False)
        if not activity_type_id:
            activity_type_id = self.env['mail.activity.type'].sudo().search([], limit=1)
        date_deadline = fields.Date.context_today(self)
        if self.mk_activity_type_id and self.activity_date_deadline_range_type:
            date_deadline += relativedelta(**{self.activity_date_deadline_range_type: self.activity_date_deadline_range})
        responsible_users = self.activity_user_ids or (self.salesperson_user_id or self.create_uid or self.env.user)
        # Administration always gets a copy too, so a blank/misconfigured Responsible field never leaves nobody informed.
        admin_user = self.env.ref('base.user_admin', raise_if_not_found=False)
        if admin_user:
            responsible_users |= admin_user
        model_id = self.env['ir.model']._get('mk.instance').id
        for user_id in responsible_users:
            if activity_type_id:
                self.env['mail.activity'].sudo().create({
                    'activity_type_id': activity_type_id.id,
                    'summary': _('Bol.com Webhook HTTPS Required'),
                    'note': note,
                    'res_id': self.id,
                    'res_model_id': model_id,
                    'date_deadline': date_deadline,
                    'user_id': user_id.id,
                })
            self.env['bus.bus']._sendone(user_id.partner_id, 'marketplace_notification',
                                         {'title': _("Bol.com webhooks not created"), 'message': note, 'sticky': True, 'type': 'warning'})
        return True

    def _bol_mark_webhook_https_activities_done(self):
        self.ensure_one()
        activities = self._bol_get_webhook_https_activities()
        if activities:
            activities.action_done()
        return True

    def bol_action_confirm(self):
        """
        Task: T4565 - Marketplace Access Rights Check
        This method get requested and granted access scopes.if any missing scope present, then opens the permission wizard otherwise establishes a connection to the bol.
        """
        self.confirm_connection()

    def _toggle_bol_invoice_crons(self):
        self.ensure_one()
        cron_obj = self.env['ir.cron'].sudo().with_context(active_test=False)
        # Block activation in staging/test
        is_neutralized = self.env['ir.config_parameter'].sudo().get_param('database.is_neutralized')
        active = bool(self.bol_export_invoice and not is_neutralized)
        invoice_methods = ['cron_auto_import_invoice_request', 'cron_auto_export_bol_invoice']
        codes = [f"model.{m}({self.id})" for m in invoice_methods]
        export_invoice_cron_ids = cron_obj.search([('mk_instance_id', '=', self.id), ('code', 'in', codes)])
        if export_invoice_cron_ids:
            export_invoice_cron_ids.write({'active': active})
        return True

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('marketplace') == 'bol':
                # Task: T9113 - New Bol instances put the database token in their webhook URL; existing ones keep the database name.
                vals['use_db_token_in_url'] = True
        instances = super().create(vals_list)
        for instance in instances.filtered(lambda i: i.marketplace == 'bol'):
            if not instance.bol_webhook_uuid:
                instance.bol_webhook_uuid = str(uuid4())
            instance.bol_webhook_url = instance._get_bol_webhook_url()
        return instances

    def write(self, vals):
        res = super().write(vals)
        if 'bol_export_invoice' in vals:
            for rec in self.filtered(lambda x: x.marketplace == 'bol'):
                rec._toggle_bol_invoice_crons()
        return res

    def reset_to_draft(self):
        res = super(MkInstance, self).reset_to_draft()
        for instance in self.filtered(lambda i: i.marketplace == 'bol'):
            instance.bol_webhook_ids.filtered(lambda x: x.active_webhook).bol_delete_webhook()
        return res

    def bol_test_connection(self):
        """
        If connection established to the bol then show rainbow man effect.
        Returns:
            effect (dictionary): Returns rainbow man effect if successfully connected to shopify.
        Raises:
            Exception: Returns False if exception occurs.
        """
        try:
            self.with_context(token_expired=True).bol_generate_new_token()
            return {
                'effect': {
                    'fadeout': 'slow',
                    'message': "Successfully Connected.",
                    'type': 'rainbow_man',
                }
            }
        except Exception:
            return False

    def bol_marketplace_operation_wizard(self):
        action = self.env.ref('base_marketplace.action_marketplace_operation').read()[0]
        action['views'] = [(self.env.ref('bol.bol_mk_operation_form_view').id, 'form')]
        return action

    # ------------------------------------------------------------------
    # Bol Subscriptions (webhooks) — mirrors the Shopify connector conventions
    # ------------------------------------------------------------------
    def _get_bol_webhook_url(self):
        self.ensure_one()
        odoo_url = self.env['ir.config_parameter'].sudo().get_param('web.base.url')
        # Task: T9113 - Database token for new instances, database name for existing ones (see use_db_token_in_url in base_marketplace).
        instance_url = "{}/{}".format(self._get_marketplace_url_db_segment(), self.id)
        instance_url = self.bol_webhook_uuid and "{}/{}".format(instance_url, self.bol_webhook_uuid or "") or instance_url
        return odoo_url + '/bol/webhook/notification/' + instance_url

    def _bol_log_webhook(self, message, error=False):
        self.ensure_one()
        key = 'error' if error else 'success'
        self.env['mk.log'].create_update_log(mk_instance_id=self, operation_type='webhook',
                                             mk_log_line_dict={key: [{'log_message': "WEBHOOK: %s" % message}]})
        return True

    def fetch_bol_webhook(self):
        self.ensure_one()
        if not self.bol_webhook_uuid:
            self.bol_webhook_uuid = str(uuid4())
            self.bol_webhook_url = self._get_bol_webhook_url()
        self.env['bol.webhook.ts'].fetch_all_webhook_from_bol(self)
        return True

    def delete_bol_webhook(self):
        self.ensure_one()
        if self.bol_webhook_ids.unlink() is False:
            raise MarketplaceException(_("Unable to delete one or more Bol.com webhooks. Try again."))
        return True

    def action_bol_rotate_webhook_uuid(self):
        for mk_instance_id in self.filtered(lambda i: i.marketplace == 'bol'):
            active_webhook_ids = mk_instance_id.bol_webhook_ids.filtered(lambda x: x.active_webhook)
            # Drop the old subscriptions first. Do not rotate the UUID
            # if any old subscription could not be deleted.
            for webhook_id in active_webhook_ids:
                if webhook_id.bol_delete_webhook() is False:
                    raise MarketplaceException(_("Unable to delete the existing Bol.com webhook '%s'. The webhook UUID was not rotated.") % webhook_id.webhook_event)
            # All old subscriptions were deleted successfully.
            mk_instance_id.bol_webhook_uuid = str(uuid4())
            mk_instance_id.bol_webhook_url = mk_instance_id._get_bol_webhook_url()
            # Re-register the active webhooks on the new URL.
            for webhook_id in active_webhook_ids:
                webhook_id.create_webhook_in_bol(mk_instance_id)
                webhook_id.with_context(skip_create=True).write({'active_webhook': True})
        return True

    def action_bol_use_db_token_in_webhook_url(self):
        """
        Task: T9113 - Switch the Bol webhook URL from the database name to the database token.
            Confirmed instances rotate the webhook secret so active webhooks are re-registered with the new URL.
            Draft instances only update the URL; their webhooks use it when activated.
        Returns:
            dict: A notification action that reloads the form.
        """
        self.ensure_one()
        self.use_db_token_in_url = True
        if self.state == 'confirmed':
            self.action_bol_rotate_webhook_uuid()
            message = _("The webhook URL now uses the database token instead of the database name. "
                        "Active webhooks were registered again on Bol.com with the new URL.")
        else:
            self.bol_webhook_url = self._get_bol_webhook_url()
            message = _("The webhook URL now uses the database token instead of the database name. "
                        "Webhooks will use it when they are activated.")
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _("Webhook URL Regenerated"),
                'message': message,
                'type': 'success',
                'sticky': False,
                'next': {'type': 'ir.actions.client', 'tag': 'soft_reload'},
            },
        }

    def bol_setup_schedule_actions(self, mk_instance_id):
        cron_obj = self.env['ir.cron'].sudo()
        bol_cron_ids = cron_obj.search([('mk_instance_id', '=', self.id), '|', ('active', '=', True), ('active', '=', False)])
        cron_list = [{'cron_name': 'Bol [{}] : Import FBR & FBB Order'.format(mk_instance_id.name), 'method_name': 'cron_auto_import_bol_orders', 'model_name': 'sale.order', 'interval_type': 'minutes', 'interval_number': 15},
                     {'cron_name': 'Bol [{}] : Export Order Status/Tracking Information to Bol'.format(mk_instance_id.name), 'method_name': 'cron_auto_update_bol_order_status', 'model_name': 'sale.order', 'interval_type': 'minutes',
                      'interval_number': 25},
                     {'cron_name': 'Bol [{}] : Import LVB/FBB Product\'s Stock'.format(mk_instance_id.name), 'method_name': 'cron_auto_import_fbb_bol_stock', 'model_name': 'mk.listing', 'interval_type': 'days', 'interval_number': 1},
                     {'cron_name': 'Bol [{}] : Export Product\'s Stock to Bol'.format(mk_instance_id.name), 'method_name': 'cron_auto_export_bol_stock', 'model_name': 'mk.listing', 'interval_type': 'minutes', 'interval_number': 25},
                     {'cron_name': 'Bol [{}] : Export Product\'s Price to Bol'.format(mk_instance_id.name), 'method_name': 'cron_auto_export_bol_price', 'model_name': 'mk.listing', 'interval_type': 'days', 'interval_number': 1},
                     {'cron_name': 'Bol [{}] : Import FBB Shipments For Open Order'.format(mk_instance_id.name), 'method_name': 'cron_auto_import_fbb_shipments', 'model_name': 'stock.picking', 'interval_type': 'hours', 'interval_number': 1},
                     {'cron_name': 'Bol [{}] : Import FBR Shipments For Open Order'.format(mk_instance_id.name), 'method_name': 'cron_auto_import_fbr_shipments', 'model_name': 'stock.picking', 'interval_type': 'hours', 'interval_number': 1},
                     {'cron_name': 'Bol [{}] : Import Returns'.format(mk_instance_id.name), 'method_name': 'cron_import_returns', 'model_name': 'bol.return', 'interval_type': 'hours', 'interval_number': 1},
                     {'cron_name': 'Bol [{}] : Import Invoice Request'.format(mk_instance_id.name), 'method_name': 'cron_auto_import_invoice_request', 'model_name': 'bol.invoice.request', 'interval_type': 'hours','interval_number': 1},
                     {'cron_name': 'Bol [{}] : Export Invoice to Bol'.format(mk_instance_id.name), 'method_name': 'cron_auto_export_bol_invoice', 'model_name': 'bol.invoice.request', 'interval_type': 'days','interval_number': 1},]
        for cron_dict in cron_list:
            bol_cron_ids -= cron_obj.create_marketplace_cron(mk_instance_id, cron_dict['cron_name'], method_name=cron_dict['method_name'], model_name=cron_dict['model_name'], interval_type=cron_dict['interval_type'],
                                                             interval_number=cron_dict['interval_number'])
        if bol_cron_ids:
            bol_cron_ids.unlink()
        return True

    # ---------------------------------------------------------------
    # Quick Onboarding hooks (Setup Wizard) — mirrors Shopify pattern
    # ---------------------------------------------------------------

    def bol_shown_in_onboarding_picker(self):
        return True

    def bol_get_onboarding_steps(self):
        return [
            {
                'title': _('Connect Your Store'),
                'icon': 'fa-plug',
                'template': 'bol.MarketplaceOnboarding_bol_step_1',

                # New fields to match reference styling
                'card_heading': _('Generate your bol.com credentials'),
                'card_intro': _('Before connecting Odoo, you must have an active <b>bol.com seller account</b> and API credentials generated from the seller portal.'),

                'substeps_title': _('Create your API key (4 steps)'),
                'substeps': [
                    _('Log in to your <b>bol.com seller account</b>.'),
                    _('Navigate to <b>Settings → API</b> in the seller portal.'),
                    _('Generate new <b>API credentials</b> (Client ID and Client Secret).'),
                    _('<b>Copy and keep</b> these credentials ready for the next step.'),
                ],

                'screen_help_title': _('What you need on this screen'),
                'screen_help': [
                    _('<b>Instance Name</b> — Enter a recognizable name for this connection (e.g., Bol NL).'),
                    _('<b>API Key</b> — Paste the <b>Client ID</b> obtained from the bol.com seller portal.'),
                    _('<b>API Secret</b> — Paste the <b>Client Secret</b>. This will be stored securely.'),
                ],

                'doc_url': 'https://docs.teqstars.com/19.0/applications/bol/setup/create_instance.html#create-a-bol-com-instance-in-odoo',
                'doc_label': _('Read: Account setup & API credentials'),
            },
            {
                'title': _('Order Workflow'),
                'icon': 'fa-cogs',
                'template': 'bol.MarketplaceOnboarding_bol_step_2',

                'card_heading': _('Configure your order workflow'),
                'card_intro': _(
                    'Select how <b>bol.com orders</b> are fulfilled — '
                    '<b>FBR</b> (you ship), <b>FBB</b> (bol.com ships), or <b>Both</b>. '
                    'Each fulfillment type requires its own <b>Odoo workflow</b>.'
                ),
                'tip': _(
                    '<b>Tip:</b> Choose <b>FBR</b> if you manage your own warehouse. '
                    'Choose <b>FBB</b> if bol.com handles storage and delivery.'
                ),

                'substeps_title': _('Configure your workflow'),
                'substeps': [
                    _('Select your <b>fulfillment method</b>: <b>FBR</b>, <b>FBB</b>, or <b>Both</b>.'),
                    _('<b>FBR (Fulfilled by Retailer)</b> — You handle <b>storage</b>, <b>packing</b>, and <b>shipping</b>.'),
                    _('<b>FBB (Fulfilled by bol.com)</b> — bol.com manages <b>storage</b>, <b>packing</b>, and <b>delivery</b>.'),
                    _('Create or select the appropriate <b>workflow(s)</b> based on your selection.'),
                    _('The workflow automates <b>order confirmation</b>, <b>invoice creation</b>, <b>validation</b>, and <b>payment registration</b>.'),
                ],

                'screen_help_title': _('What you need on this screen'),
                'screen_help': [
                    _('<b>Operation Type</b> — Choose <b>FBR</b>, <b>FBB</b>, or <b>Both</b>.'),
                    _('<b>FBR Workflow</b> — Required for <b>merchant-fulfilled orders</b>.'),
                    _('<b>FBB Workflow</b> — Required for <b>bol.com fulfilled orders</b>.'),
                    _('<b>FBR & FBB</b> — If both are selected, configure <b>both workflows</b>. Each applies based on the order type.'),
                ],

                'doc_url': 'https://docs.teqstars.com/19.0/applications/bol/setup/create_instance.html#workflow-configurations',
                'doc_label': _('Read More: Configure Workflow'),
            },
            {
                'title': _('Product Settings'),
                'icon': 'fa-cubes',
                'template': 'bol.MarketplaceOnboarding_bol_step_3',
                'card_heading': _('Configure product settings'),
                'card_intro': _(
                    'Control how <b>products</b> are matched, created, and stored when imported from bol.com. '
                    'The <b>sync method</b> and <b>default condition</b> affect every imported product.'
                ),
                'tip': _(
                    '<b>Tip:</b> Enable <b>Create products if missing</b> so new bol.com offers are never skipped.'
                ),

                'substeps_title': _('Configure product synchronization'),
                'substeps': [
                    _('Choose the <b>sync method</b> — match products using <b>Barcode</b>, <b>SKU</b>, or <b>both</b>.'),
                    _('Enable <b>automatic product creation</b> to create products when no match is found.'),
                    _('Set default values like <b>condition</b>, <b>delivery settings</b>, and <b>warehouse</b> for consistent imports.'),
                ],

                'screen_help_title': _('What you need on this screen'),
                'screen_help': [
                    _('<b>Sync products by</b> — Choose <b>Barcode</b>, <b>SKU</b>, or <b>both</b>.'),
                    _('<b>Create products if missing</b> — Automatically create products when no match is found.'),
                    _('<b>Condition</b> — Default condition (e.g., <b>New</b>) applied to imported products.'),
                    _('<b>Delivery</b> — Configure <b>fulfillment schedule</b> and <b>delivery time</b> (min/max days).'),
                    _('<b>Warehouse</b> — Used for <b>FBR</b> fulfillment (your warehouse).'),
                    _('<b>FBB Warehouse</b> — Used when <b>FBB</b> is enabled (bol.com managed stock).'),
                ],

                'doc_url': 'https://docs.teqstars.com/19.0/applications/bol/setup/create_instance.html#product-configurations',
                'doc_label': _('Read More: Product Configuration'),
            },
            {
                'title': _('Review & Confirm'),
                'description': _('Review your setup before saving'),
                'icon': 'fa-check-circle',
                'template': 'bol.MarketplaceOnboarding_bol_step_4',

                'card_heading': _('Review & confirm your setup'),
                'card_intro': _(
                    'Carefully review all configuration details below. Once you click <b>Save</b>, '
                    'the integration is <b>activated</b> and Odoo will start syncing data with <b>bol.com</b>.'
                ),

                'substeps_title': _('Verify your configuration'),
                'substeps': [
                    _('Review all settings, including <b>API credentials</b>, <b>workflows</b>, and <b>product configuration</b>.'),
                    _('Ensure all required fields are <b>correct</b> and aligned with your business setup.'),
                    _('Click <b>Save</b> to <b>activate</b> the integration.'),
                ],

                'screen_help_title': _('What you can do on this screen'),
                'screen_help': [
                    _('<b>Review Details</b> — Verify all sections for accuracy.'),
                    _('<b>Go Back</b> — Return to previous steps to make changes.'),
                    _('<b>Confirm Setup</b> — Click <b>Save</b> to activate the integration.'),
                ],

                'doc_url': 'https://docs.teqstars.com/19.0/applications/bol/setup.html',
                'doc_label': _('Read More:Setup Guide'),
            }
        ]

    def bol_get_onboarding_values(self):
        op_pairs = self._fields['bol_operation_type']._description_selection(self.env)
        cat_pairs = self._fields['bol_default_condition_category']._description_selection(self.env)
        state_pairs = self._fields['bol_default_condition_state']._description_selection(self.env)
        grade_pairs = self._fields['bol_default_condition_grade']._description_selection(self.env)
        sched_pairs = self._fields['bol_default_fulfilment_schedule']._description_selection(self.env)
        uot_pairs = self._fields['bol_default_ultimate_order_time']._description_selection(self.env)
        sync_pairs = self._fields['sync_product_with']._description_selection(self.env)

        Workflow = self.env['order.workflow.config.ts'].sudo()
        Pricelist = self.env['product.pricelist'].sudo()
        Warehouse = self.env['stock.warehouse'].sudo()

        company = self.company_id or self.env.company
        default_wh_id = self.warehouse_id.id or Warehouse.search(
            [('company_id', '=', company.id)], limit=1, order='id'
        ).id

        return {
            # Step 1
            'name': self.name or '',
            'bol_client_id': self.bol_client_id or '',
            'bol_client_secret': self.bol_client_secret or '',
            # Step 2
            'bol_operation_type': self.bol_operation_type or 'FBR',
            'bol_operation_type_options': [{'value': k, 'label': v} for k, v in op_pairs],
            'bol_fbr_workflow_name': self.bol_fbr_workflow_id.name or False,
            'bol_fbb_workflow_name': self.bol_fbb_workflow_id.name or False,
            'workflow_options': [
                {'id': r.id, 'name': r.display_name}
                for r in Workflow.search([], order='name')
            ],
            # Step 3 condition
            'bol_default_condition_category': self.bol_default_condition_category or 'NEW',
            'bol_default_condition_category_options': [{'value': k, 'label': v} for k, v in cat_pairs],
            'bol_default_condition_state': self.bol_default_condition_state or False,
            'bol_default_condition_state_options': [{'value': k, 'label': v} for k, v in state_pairs],
            'bol_default_condition_grade': self.bol_default_condition_grade or False,
            'bol_default_condition_grade_options': [{'value': k, 'label': v} for k, v in grade_pairs],
            'bol_default_condition_margin': bool(self.bol_default_condition_margin),
            # Step 3 delivery
            'bol_default_fulfilment_schedule': self.bol_default_fulfilment_schedule or 'BOL_DELIVERY_PROMISE',
            'bol_default_fulfilment_schedule_options': [{'value': k, 'label': v} for k, v in sched_pairs],
            'bol_default_delivery_min_days': self.bol_default_delivery_min_days or 2,
            'bol_default_delivery_max_days': self.bol_default_delivery_max_days or 3,
            'bol_default_ultimate_order_time': self.bol_default_ultimate_order_time or False,
            'bol_default_ultimate_order_time_options': [{'value': k, 'label': v} for k, v in uot_pairs],
            # Step 3 resources
            # 'pricelist_id': self.pricelist_id.id or False,
            'pricelist_options': [
                {'id': r.id, 'name': r.display_name}
                for r in Pricelist.search([], order='name')
            ],
            'warehouse_id': default_wh_id or False,
            'warehouse_options': [
                {'id': r.id, 'name': r.display_name}
                for r in Warehouse.search([('company_id', '=', company.id)], order='name')
            ],
            'bol_fbb_warehouse_id': self.bol_fbb_warehouse_id.id or False,
            # Step 4
            'sync_product_with': self.sync_product_with or 'barcode_or_sku',
            'sync_product_with_options': [{'value': k, 'label': v} for k, v in sync_pairs],
        }

    def bol_validate_onboarding_step_1(self, data):
        name = (data.get('name') or '').strip()
        if not name:
            return {'valid': False, 'error': _('Instance name is required.')}
        cid = (data.get('bol_client_id') or '').strip()
        if not cid:
            return {'valid': False, 'error': _('Bol Client ID is required.')}
        if not (data.get('bol_client_secret') or '').strip():
            return {'valid': False, 'error': _('Bol Client Secret is required.')}
        return {'valid': True}

    def _bol_resolve_workflow_id(self, data, key_id, key_name):
        Workflow = self.env['order.workflow.config.ts'].sudo()
        wf_id = data.get(key_id)
        if wf_id:
            return int(wf_id)
        name = (data.get(key_name) or '').strip()
        if not name:
            return False
        wf = Workflow.search([('name', '=', name)], limit=1)
        if not wf:
            wf = Workflow.create({
                'name': name,
                'company_id': self.env.company.id,
            })
        data[key_id] = wf.id
        return wf.id

    def bol_validate_onboarding_step_2(self, data):
        op = data.get('bol_operation_type')
        valid_op = dict(self._fields['bol_operation_type']._description_selection(self.env))
        if op not in valid_op:
            return {'valid': False, 'error': _('Please select a valid Operation Type.')}
        if op in ('FBR', 'Both'):
            if not self._bol_resolve_workflow_id(data, 'bol_fbr_workflow_id', 'bol_fbr_workflow_name'):
                return {'valid': False, 'error': _('FBR Workflow is required for FBR operations.')}
        if op in ('FBB', 'Both'):
            if not self._bol_resolve_workflow_id(data, 'bol_fbb_workflow_id', 'bol_fbb_workflow_name'):
                return {'valid': False, 'error': _('FBB Workflow is required for FBB operations.')}
        return {'valid': True}

    def bol_validate_onboarding_step_3(self, data):
        cat = data.get('bol_default_condition_category')
        valid_cat = dict(self._fields['bol_default_condition_category']._description_selection(self.env))
        if cat not in valid_cat:
            return {'valid': False, 'error': _('Please select a valid Condition Category.')}
        if cat == 'SECONDHAND':
            st = data.get('bol_default_condition_state')
            if st not in dict(self._fields['bol_default_condition_state']._description_selection(self.env)):
                return {'valid': False, 'error': _('Condition State is required for Second Hand.')}
        if cat == 'REFURBISHED':
            gr = data.get('bol_default_condition_grade')
            if gr not in dict(self._fields['bol_default_condition_grade']._description_selection(self.env)):
                return {'valid': False, 'error': _('Refurbished Grade is required for Refurbished.')}

        sched = data.get('bol_default_fulfilment_schedule')
        valid_sched = dict(self._fields['bol_default_fulfilment_schedule']._description_selection(self.env))
        if sched not in valid_sched:
            return {'valid': False, 'error': _('Please select a valid Fulfilment Schedule.')}
        if sched == 'BOL_DELIVERY_PROMISE':
            try:
                mn = int(data.get('bol_default_delivery_min_days') or 0)
                mx = int(data.get('bol_default_delivery_max_days') or 0)
            except (TypeError, ValueError):
                return {'valid': False, 'error': _('Delivery min/max days must be integers.')}
            if mn < 0:
                return {'valid': False, 'error': _('Min days must be >= 0.')}
            if mx < mn:
                return {'valid': False, 'error': _('Max days must be >= Min days.')}
            if mn == 0 and mx == 1:
                uot = data.get('bol_default_ultimate_order_time')
                if uot not in dict(self._fields['bol_default_ultimate_order_time']._description_selection(self.env)):
                    return {'valid': False, 'error': _('Ultimate Order Time is required when Min=0 and Max=1.')}

        if not data.get('warehouse_id'):
            return {'valid': False, 'error': _('Warehouse is required.')}
        op = data.get('bol_operation_type') or self.bol_operation_type
        if op in ('FBB', 'Both') and not data.get('bol_fbb_warehouse_id'):
            return {'valid': False, 'error': _('FBB Warehouse is required for FBB operations.')}
        sync = data.get('sync_product_with')
        valid_sync = dict(self._fields['sync_product_with']._description_selection(self.env))
        if sync not in valid_sync:
            return {'valid': False, 'error': _('Please select a valid product sync method.')}
        return {'valid': True}

    def bol_save_onboarding_data(self, values):
        vals = {}
        # Step 1
        if 'name' in values:
            vals['name'] = (values.get('name') or '').strip()
        if 'bol_client_id' in values:
            cid = (values.get('bol_client_id') or '').strip()
            if cid:
                vals['bol_client_id'] = cid
        if 'bol_client_secret' in values:
            sec = (values.get('bol_client_secret') or '').strip()
            if sec:
                vals['bol_client_secret'] = sec
        # Step 2
        if 'bol_operation_type' in values:
            vals['bol_operation_type'] = values['bol_operation_type']
        op_for_save = values.get('bol_operation_type') or self.bol_operation_type
        if op_for_save in ('FBR', 'Both'):
            vals['bol_fbr_workflow_id'] = self._bol_resolve_workflow_id(
                values, 'bol_fbr_workflow_id', 'bol_fbr_workflow_name'
            ) or False
        if op_for_save in ('FBB', 'Both'):
            vals['bol_fbb_workflow_id'] = self._bol_resolve_workflow_id(
                values, 'bol_fbb_workflow_id', 'bol_fbb_workflow_name'
            ) or False
        # Step 3 condition
        for k in ('bol_default_condition_category',
                  'bol_default_condition_state',
                  'bol_default_condition_grade'):
            if k in values:
                vals[k] = values[k] or False
        if 'bol_default_condition_margin' in values:
            vals['bol_default_condition_margin'] = bool(values.get('bol_default_condition_margin'))
        cat = vals.get('bol_default_condition_category', self.bol_default_condition_category)
        if cat != 'SECONDHAND':
            vals['bol_default_condition_state'] = False
        if cat != 'REFURBISHED':
            vals['bol_default_condition_grade'] = False
            vals['bol_default_condition_margin'] = False
        # Step 3 delivery
        if 'bol_default_fulfilment_schedule' in values:
            vals['bol_default_fulfilment_schedule'] = values['bol_default_fulfilment_schedule']
        for k in ('bol_default_delivery_min_days', 'bol_default_delivery_max_days'):
            if k in values:
                try:
                    vals[k] = int(values[k])
                except (TypeError, ValueError):
                    pass
        if 'bol_default_ultimate_order_time' in values:
            vals['bol_default_ultimate_order_time'] = values['bol_default_ultimate_order_time'] or False
        # Step 3 resources
        if 'warehouse_id' in values:
            vals['warehouse_id'] = int(values['warehouse_id']) if values['warehouse_id'] else False
        if 'bol_fbb_warehouse_id' in values:
            vals['bol_fbb_warehouse_id'] = int(values['bol_fbb_warehouse_id']) if values['bol_fbb_warehouse_id'] else False
        # Step 4
        if 'sync_product_with' in values:
            vals['sync_product_with'] = values['sync_product_with']

        if 'is_create_products' in values:
            vals['is_create_products'] = bool(values.get('is_create_products'))
        # The panel has no tax step and the instance is created without the form, so the marketplace onchange that sets the bol.com tax system never runs.
        vals['tax_system'] = 'default'
        if vals:
            self.write(vals)

        confirm_action = self.action_confirm()
        if isinstance(confirm_action, dict) and confirm_action.get('type'):
            return {'action': confirm_action}
        return {}

    def _get_bol_automatic_jobs_note(self):
        return _(
            "<ul>"
            "<li>We create <strong>variant-level</strong> pricelist rules in Odoo while importing <strong>products/listings</strong> from the marketplace.</li>"
            "<li><strong>Export/Update Product's Price in marketplace</strong> supports all pricelist rule levels - <strong>Variant, Product, Category and Global</strong> - with priority handled by Odoo's standard pricelist function.</li>"
            "<li>The system will no longer update the product prices in the marketplace if the price is set to zero.</li>"
            "</ul>"
        )
