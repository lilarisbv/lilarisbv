import logging
import pprint

from psycopg2 import OperationalError

from odoo import models, fields, tools, api, _
from odoo.fields import Domain
from odoo.addons.base_marketplace.models.exceptions import MarketplaceException
from .misc import convert_bol_datetime_to_utc, log_traceback_for_exception

_logger = logging.getLogger("TeqStars:bol")

FINANCIAL_STATUS = [('pending', 'Pending'), ('authorized', 'Authorized'), ('partially_paid', 'Partially Paid'), ('paid', 'Paid'), ('partially_refunded', 'Partially Refunded'), ('refunded', 'Refunded'), ('voided', 'Voided')]

BOL_FULFILMENT_METHOD = [('FBB', 'Fulfilment by bol.com'), ('FBR', 'Fulfilment by retailer')]


class SaleOrder(models.Model):
    _inherit = 'sale.order'

    bol_fulfilment_method = fields.Selection(BOL_FULFILMENT_METHOD, string='Fulfilment Method (Bol)', help='Specifies whether this shipment has been fulfilled by the retailer (FBR) or fulfilled by bol.com (FBB). Defaults to FBR.')
    bol_commission = fields.Monetary("Bol.com Commission", compute='_compute_bol_commission', store=True)
    bol_commission_percent = fields.Float("Bol.com Commission (%)", compute='_compute_bol_commission', store=True)
    bol_is_skipped_for_import_shipment = fields.Boolean("Is Skipped For Shipment Import (Bol.com)?", help="Technical field to filter orders from shipment import.")

    @api.depends('order_line.bol_commission', 'amount_untaxed')
    def _compute_bol_commission(self):
        if not all(self._ids):
            for order in self:
                order.bol_commission = sum(order.order_line.mapped('bol_commission'))
                order.bol_commission_percent = order.amount_untaxed and order.bol_commission / order.amount_untaxed
        else:
            grouped_order_lines_data = self.env['sale.order.line']._read_group([('order_id', 'in', self.ids), ],['order_id'], ['bol_commission:sum'])
            mapped_data = {order.id: margin for order, margin in grouped_order_lines_data}
            for order in self:
                order.bol_commission = mapped_data.get(order.id, 0.0)
                order.bol_commission_percent = order.amount_untaxed and order.bol_commission / order.amount_untaxed

    def fetch_orders_from_bol(self, mk_instance_id, type="FBR", mk_order_id=False):
        bol_order_list, page = [], 0
        if mk_order_id:
            return mk_instance_id._send_bol_request('retailer/orders/{}'.format(mk_order_id), {})
        while True:
            page += 1
            # Commented below code because Bol.com gives updated order on same date which is passed as latest-change-date not for after onwards.
            # from_date = mk_instance_id.last_order_sync_date if mk_instance_id.last_order_sync_date else fields.Datetime.now() - timedelta(3)
            # params = {'fulfilment-method': type, 'status': mk_instance_id.bol_order_status, 'page': page, 'latest-change-date': from_date.strftime('%Y-%m-%d')}
            params = {'fulfilment-method': type, 'status': mk_instance_id.bol_order_status, 'page': page}
            response = mk_instance_id._send_bol_request('retailer/orders', {}, params=params)
            if not response.get('orders'):
                break
            bol_order_list += response.get('orders')
        return bol_order_list

    def check_product_taxes_validation(self, offer_id, mk_instance_id, billing_customer_id, shipping_customer_id):
        mk_log_id = self.env.context.get('mk_log_id', False)
        queue_line_id = self.env.context.get('queue_line_id', False)
        mk_listing_item_id = self.get_mk_listing_item_for_mk_order(offer_id, mk_instance_id)
        if not mk_listing_item_id:
            return False
        odoo_product_id = mk_listing_item_id.product_id
        fpos = self.env['account.fiscal.position'].with_company(mk_instance_id.company_id)._get_fiscal_position(billing_customer_id, shipping_customer_id)
        taxes = odoo_product_id.taxes_id.filtered(lambda t: t.company_id == mk_instance_id.company_id)
        taxes = fpos.map_tax(taxes)
        if taxes and any([not tax.price_include_override for tax in taxes]):
            log_message = _(
                "IMPORT ORDER: Taxes on product '{}' must have 'Included in Price' set since Bol.com always gives tax included in price. Taxes on products that needs to correct are below \n{} \n\nOR\n\n You can tick 'Managing Excluded Taxes on Odoo Product' in instance >> Products and try again.".format(
                    odoo_product_id.display_name, ',\n'.join([tax.name for tax in taxes.filtered(lambda x: not x.price_include_override)])))
            self.env['mk.log'].create_update_log(mk_log_id=mk_log_id, mk_log_line_dict={'error': [{'log_message': log_message, 'queue_job_line_id': queue_line_id and queue_line_id.id or False}]})
            return False
        return True

    def check_validation_for_import_bol_sale_orders(self, bol_order_line_list, mk_instance_id, order_number, billing_customer_id, shipping_customer_id):
        odoo_product_variant_obj, is_importable = self.env['product.product'], True
        mk_log_id = self.env.context.get('mk_log_id', False)
        queue_line_id = self.env.context.get('queue_line_id', False)
        mk_listing_item_obj, mk_listing_obj = self.env['mk.listing.item'], self.env['mk.listing']
        for bol_order_line_dict in bol_order_line_list:
            offer_id = bol_order_line_dict.get('offer', {}).get('offerId', False)
            if offer_id:
                mk_listing_item_id = self.get_mk_listing_item_for_mk_order(offer_id, mk_instance_id)
                if mk_listing_item_id:
                    if not mk_instance_id.bol_managing_excluded_taxes_on_product:
                        is_importable = self.check_product_taxes_validation(offer_id, mk_instance_id, billing_customer_id, shipping_customer_id)
                        if not is_importable:
                            break
                    continue
                try:
                    offer_response_dict = mk_instance_id._send_bol_request('retailer/offers/{}'.format(offer_id), {})
                except Exception as e:
                    log_message = "Cannot find Offer ID {} in Bol, Order reference {}.".format(offer_id, order_number)
                    self.env['mk.log'].create_update_log(mk_log_id=mk_log_id, mk_log_line_dict={'error': [{'log_message': log_message, 'queue_job_line_id': queue_line_id and queue_line_id.id or False}]})
                    is_importable = False
                    break
                if offer_response_dict:
                    is_importable = mk_listing_obj.create_update_bol_product(offer_response_dict, mk_instance_id, update_product_price=True, is_update_existing_products=True)
                    if not mk_instance_id.bol_managing_excluded_taxes_on_product:
                        is_importable = self.check_product_taxes_validation(offer_id, mk_instance_id, billing_customer_id, shipping_customer_id)
                        if not is_importable:
                            break
                    if not is_importable:
                        break
        return is_importable

    def _set_payment_term_to_bol_customer(self, customer_id, mk_instance_id):
        if mk_instance_id and mk_instance_id.bol_payment_term_id:
            payment_term_id = mk_instance_id.bol_payment_term_id
            if payment_term_id:
                customer_id.write({'property_payment_term_id': payment_term_id.id})

    def create_bol_sale_order(self, bol_order_dict, mk_instance_id, billing_customer_id, shipping_customer_id):
        self._set_payment_term_to_bol_customer(billing_customer_id, mk_instance_id)
        customer = self.new({'partner_id': billing_customer_id.id})
        # customer.onchange_partner_id()
        customer_dict = customer._convert_to_write({name: customer[name] for name in customer._cache})
        fiscal_position_id = self.env['account.fiscal.position'].with_company(mk_instance_id.company_id)._get_fiscal_position(billing_customer_id, shipping_customer_id)
        payment_term_id = customer_dict.get('payment_term_id', False)
        bol_fulfilment_method = bol_order_dict['orderItems'][0].get('fulfilment', {}).get('method') if bol_order_dict.get('orderItems', []) else False
        warehouse_id = mk_instance_id.warehouse_id if bol_fulfilment_method == 'FBR' else mk_instance_id.bol_fbb_warehouse_id
        order_workflow_id = mk_instance_id.bol_fbr_workflow_id if bol_fulfilment_method == 'FBR' else mk_instance_id.bol_fbb_workflow_id

        sale_order_vals = {
            'state': 'draft',
            'partner_id': billing_customer_id.id,
            'partner_invoice_id': billing_customer_id.id,
            'partner_shipping_id': shipping_customer_id.id or billing_customer_id.id,
            'date_order': convert_bol_datetime_to_utc(bol_order_dict.get('orderPlacedDateTime', "")),
            'company_id': mk_instance_id.company_id.id,
            'warehouse_id': warehouse_id.id,
            'fiscal_position_id': fiscal_position_id.id or False,
            'pricelist_id': mk_instance_id.pricelist_id.id or False,
            'team_id': mk_instance_id.team_id.id or False,
        }
        sale_order_vals = self.prepare_sales_order_vals_ts(sale_order_vals, mk_instance_id)

        sale_order_vals.update({
            'mk_id': bol_order_dict.get('orderId'),
            'mk_order_number': bol_order_dict.get('orderId'),
            'bol_fulfilment_method': bol_fulfilment_method,
            'mk_instance_id': mk_instance_id.id,
            'user_id': mk_instance_id.salesperson_user_id.id})

        if bol_order_dict.get('pickupPoint'):
            sale_order_vals.update({'note': "PickUpPoint - {}".format(bol_order_dict.get('pickupPoint'))})

        if mk_instance_id.use_marketplace_sequence:
            order_prefix = mk_instance_id.fbm_order_prefix if bol_fulfilment_method == 'FBB' else mk_instance_id.order_prefix
            order_name = bol_order_dict.get('orderId')
            if order_prefix:
                order_name = "{}{}".format(order_prefix, bol_order_dict.get('orderId'))
            sale_order_vals.update({'name': order_name})

        if order_workflow_id:
            sale_order_vals.update({
                'picking_policy': order_workflow_id.picking_policy,
                'payment_term_id': payment_term_id or False,
                'order_workflow_id': order_workflow_id.id})

        order_id = self.create(sale_order_vals)
        return order_id

    def create_sale_order_line_for_bol(self, order_line_dict, odoo_product_id, order_id, is_delivery=False, is_discount=False):
        sale_order_line_obj = self.env['sale.order.line']
        mk_instance_id = order_id.mk_instance_id
        price = order_line_dict.get('unitPrice')
        if mk_instance_id.bol_managing_excluded_taxes_on_product:
            fpos = self.env['account.fiscal.position'].with_company(mk_instance_id.company_id)._get_fiscal_position(order_id.partner_id, order_id.partner_shipping_id)
            taxes = odoo_product_id.taxes_id.filtered(lambda t: t.company_id == mk_instance_id.company_id)
            taxes = fpos.map_tax(taxes)
            taxes_res = taxes.filtered(lambda x: not x.price_include_override).with_context(force_price_include=True).compute_all(price, order_id.currency_id, 1, product=odoo_product_id, partner=order_id.partner_shipping_id)
            price -= tools.float_round(taxes_res['total_included'] - taxes_res['total_excluded'], precision_digits=self.env['decimal.precision'].precision_get('Product Price'))
        description = order_line_dict.get('product', {}).get('title')
        line_vals = {'name': description if description else odoo_product_id.name, 'product_id': odoo_product_id.id or False, 'order_id': order_id.id, 'company_id': order_id.company_id.id,
                     'product_uom': odoo_product_id.uom_id and odoo_product_id.uom_id.id or False, 'price_unit': price, 'order_qty': order_line_dict.get('quantity', 1) or 1, }
        order_line_data = self.env['sale.order.line'].prepare_sale_order_line_ts(line_vals, mk_instance_id)
        order_line_data.update({'name': description if description else odoo_product_id.name, 'is_delivery': is_delivery, 'is_discount': is_discount, 'mk_id': order_line_dict.get('orderItemId'), 'bol_commission': order_line_dict.get('commission', 0.0) or 0, })
        order_line = sale_order_line_obj.create(order_line_data)
        return order_line

    def create_sale_order_line_bol(self, mk_instance_id, bol_order_dict, order_id):
        bol_order_line_list = bol_order_dict.get('orderItems')
        mk_log_id = self.env.context.get('mk_log_id', False)
        queue_line_id = self.env.context.get('queue_line_id', False)
        if not bol_order_line_list:
            return False
        for order_line_dict in bol_order_line_list:
            mk_listing_item_id = self.get_mk_listing_item_for_mk_order(order_line_dict.get('offer', {}).get('offerId'), mk_instance_id)
            if not mk_listing_item_id:
                log_message = _("IMPORT ORDER: Bol Offer not found for Order ID {}, Offer ID: {} and Name: {}.".format(order_id.mk_id, order_line_dict.get('offerId'), order_line_dict.get('title', '')))
                self.env['mk.log'].create_update_log(mk_log_id=mk_log_id, mk_log_line_dict={'error': [{'log_message': log_message, 'queue_job_line_id': queue_line_id and queue_line_id.id or False}]})
                return False
            odoo_product_id = mk_listing_item_id.product_id
            self.create_sale_order_line_for_bol(order_line_dict, odoo_product_id, order_id)
        return True

    def _get_customers_for_bol_order(self, bol_order_dict, mk_instance_id):
        partner_obj = self.env['res.partner']

        def get_valid_address(bol_order_dict, primary_key, fallback_key):
            return bol_order_dict.get(primary_key, {}) or bol_order_dict.get(fallback_key, {})

        billing_details = get_valid_address(bol_order_dict, 'billingDetails', 'shipmentDetails')
        shipping_details = get_valid_address(bol_order_dict, 'shipmentDetails', 'billingDetails')

        billing_company_contact = False
        shipping_company_contact = False

        if billing_details.get('company'):
            domain = [('is_company', '=', True)]
            if billing_details.get('vatNumber'):
                # domain = expression.AND([('vat', '=ilike', billing_details.get('vatNumber'))])
                domain = Domain.OR([
                    [('vat', '=ilike', billing_details.get('vatNumber'))],
                    [('name', '=ilike', billing_details.get('company'))]
                ])
            else:
                domain = Domain.AND([Domain('name', '=ilike', billing_details.get('company')), domain])

            billing_company_contact = self.env['res.partner'].search(domain, order='vat', limit=1)

            if not billing_company_contact:
                billing_company_contact = self.env['res.partner'].create_update_bol_customers(billing_details, mk_instance_id, type='company')

        if shipping_details.get('company'):
            domain = [('is_company', '=', True)]
            if shipping_details.get('vatNumber'):
                domain = Domain.OR([
                    [('vat', '=ilike', shipping_details.get('vatNumber'))],
                    [('name', '=ilike', shipping_details.get('company'))]
                ])
            else:
                domain = Domain.AND([Domain('name', '=ilike', shipping_details.get('company')), domain])

            shipping_company_contact = self.env['res.partner'].search(domain, order='vat', limit=1)
            if not shipping_company_contact:
                shipping_company_contact = partner_obj.create_update_bol_customers(shipping_details, mk_instance_id, type='company')

        partner_invoice_id = partner_obj.create_update_bol_customers(billing_details, mk_instance_id, type='invoice' if billing_company_contact else 'contact', parent_id=billing_company_contact)
        partner_shipping_id = partner_obj.create_update_bol_customers(shipping_details, mk_instance_id, type='delivery', parent_id=shipping_company_contact or billing_company_contact or partner_invoice_id)
        return partner_invoice_id, partner_shipping_id

    def process_import_order_from_bol_ts(self, bol_order_dict, mk_instance_id):
        mk_log_id = self.env.context.get('mk_log_id', False)
        queue_line_id = self.env.context.get('queue_line_id', False)
        bol_order_id = bol_order_dict.get('orderId', '')

        if not bol_order_id:
            log_message = _("IMPORT ORDER: Order Identification not found in Bol Order.")
            self.env['mk.log'].create_update_log(mk_log_id=mk_log_id, mk_log_line_dict={'error': [{'log_message': log_message, 'queue_job_line_id': queue_line_id and queue_line_id.id or False}]})
            return self

        bol_fulfilment_method = list(set([item_dict.get('fulfilment', {}).get('method') for item_dict in bol_order_dict.get('orderItems')]))
        existing_order_id = self.search([('mk_id', '=', bol_order_id), ('mk_instance_id', '=', mk_instance_id.id), ('bol_fulfilment_method', 'in', bol_fulfilment_method)])
        if existing_order_id:
            log_message = _("IMPORT ORDER: Bol Order {} is already imported.".format(bol_order_id))
            self.env['mk.log'].create_update_log(mk_log_id=mk_log_id, mk_log_line_dict={'success': [{'log_message': log_message, 'queue_job_line_id': queue_line_id and queue_line_id.id or False}]})
            return existing_order_id

        if not self.check_marketplace_order_date(convert_bol_datetime_to_utc(bol_order_dict.get('orderPlacedDateTime', "")), mk_instance_id):
            log_message = "IMPORT ORDER: Bol Order {} is skipped due to order created prior to the date configured on Import Order After in Instance.".format(bol_order_id)
            self.env['mk.log'].create_update_log(mk_log_id=mk_log_id, mk_log_line_dict={'error': [{'log_message': log_message, 'queue_job_line_id': queue_line_id and queue_line_id.id or False}]})
            return self

        customer_details = bol_order_dict.get('billingDetails', {}) or bol_order_dict.get('shipmentDetails', {})
        if not customer_details:
            log_message = _("IMPORT ORDER: Customer not found in Bol Order ID: {}".format(bol_order_id))
            self.env['mk.log'].create_update_log(mk_log_id=mk_log_id, mk_log_line_dict={'error': [{'log_message': log_message, 'queue_job_line_id': queue_line_id and queue_line_id.id or False}]})
            return self

        partner_invoice_id, partner_shipping_id = self._get_customers_for_bol_order(bol_order_dict, mk_instance_id)

        if not partner_invoice_id and not partner_shipping_id:
            log_message = _("IMPORT SHIPMENT: Billing and Shipping Address not found")
            self.env['mk.log'].create_update_log(mk_log_id=mk_log_id, mk_log_line_dict={'error': [{'log_message': log_message, 'queue_job_line_id': queue_line_id and queue_line_id.id or False}]})
            return self

        bol_order_line_list = bol_order_dict.get('orderItems')
        is_importable = self.check_validation_for_import_bol_sale_orders(bol_order_line_list, mk_instance_id, bol_order_id, partner_invoice_id, partner_shipping_id)
        if not is_importable:
            return self

        order_id = self.create_bol_sale_order(bol_order_dict, mk_instance_id, partner_invoice_id, partner_shipping_id)
        if not order_id:
            return self

        if not self.create_sale_order_line_bol(mk_instance_id, bol_order_dict, order_id):
            order_id.unlink()
            return self

        is_fulfilled_order = all([item.get('quantity') == item.get('quantityShipped') for item in bol_order_dict.get('orderItems')])
        order_id.with_context(create_date=convert_bol_datetime_to_utc(bol_order_dict.get('orderPlacedDateTime', '')), is_fulfilled_order=is_fulfilled_order).do_marketplace_workflow_process()
        if order_id:
            log_message = _('IMPORT ORDER: Successfully imported marketplace order {}({})'.format(order_id.name, order_id.mk_id))
            self.env['mk.log'].create_update_log(mk_log_id=mk_log_id, mk_log_line_dict={'success': [{'log_message': log_message, 'queue_job_line_id': queue_line_id and queue_line_id.id or False}]})
        return order_id

    def bol_import_orders(self, mk_instance_id, type="FBR"):
        mk_log_id = self.env['mk.log'].create_update_log(mk_instance_id=mk_instance_id, operation_type='import')
        bol_order_list = self.fetch_orders_from_bol(mk_instance_id, type)
        if bol_order_list:
            batch_size = mk_instance_id.queue_batch_limit or 100
            for bol_orders in tools.split_every(batch_size, bol_order_list):
                line_vals_list = []
                for order_dict in bol_orders:
                    order_id = order_dict.get('orderId', '') or ''
                    create_job_line = False
                    for item in order_dict.get('orderItems'):
                        if self.search_count([('mk_id', '=', order_id), ('mk_instance_id', '=', mk_instance_id.id), ('bol_fulfilment_method', '=', item.get('fulfilmentMethod'))]):
                            _logger.info(_("IMPORT ORDER: Bol Order {} ({}) is already imported.".format(order_id, item.get('fulfilmentMethod'))))
                            continue
                        create_job_line = True
                    if not create_job_line:
                        # Skip queue job line create process if order is already imported
                        continue
                    bol_order_dict = self.fetch_orders_from_bol(mk_instance_id, mk_order_id=order_id)
                    line_vals_list.append({'mk_id': order_id, 'state': 'draft', 'name': order_id.strip(), 'data_to_process': pprint.pformat(bol_order_dict), 'mk_instance_id': mk_instance_id.id, })
                if line_vals_list:
                    queue_id = mk_instance_id.action_create_queue(type='order')
                    for line_vals in line_vals_list:
                        queue_id.action_create_queue_lines(line_vals)
        if not mk_log_id.log_line_ids and not self.env.context.get('log_id', False):
            mk_log_id.unlink()
        mk_instance_id.last_order_sync_date = fields.Datetime.now()
        return True

    def bol_import_order_by_ids(self, mk_instance_id, mk_order_ids=[]):
        sale_order_ids = self
        mk_log_id = self.env['mk.log'].create_update_log(mk_instance_id=mk_instance_id, operation_type='import')
        for mk_id in mk_order_ids:
            try:
                bol_order_dict = self.fetch_orders_from_bol(mk_instance_id, mk_order_id=mk_id)
                bol_operation_type = ['FBR', 'FBB'] if mk_instance_id.bol_operation_type == 'Both' else [mk_instance_id.bol_operation_type]
                for f_type in bol_operation_type:
                    order_dict = bol_order_dict.copy()
                    order_dict.update({'orderItems': [item for item in order_dict.get('orderItems') if item.get('fulfilment', {}).get('method') == f_type]})
                    if order_dict.get('orderItems'):
                        with self.env.cr.savepoint():
                            sale_order_ids |= self.with_context(mk_log_id=mk_log_id).process_import_order_from_bol_ts(order_dict, mk_instance_id)
            except OperationalError as e:
                raise
            except Exception as e:
                log_traceback_for_exception()
                self.env.cr.rollback()
                log_message = "IMPORT ORDER: Error while importing processing Bol Order {}, ERROR: {}".format(mk_id, e)
                if not mk_log_id.exists():
                    mk_log_id = self.env['mk.log'].create_update_log(mk_instance_id=mk_instance_id, operation_type='import')
                self.env['mk.log'].create_update_log(mk_log_id=mk_log_id, mk_log_line_dict={'error': [{'log_message': log_message}]})
                payload = {'title': "Import Failed", 'message': e, 'sticky': False, 'type': 'danger', 'message_is_html': False}
                self.env['bus.bus']._sendone(self.env.user.partner_id, 'marketplace_notification', payload)
                continue
            self.env.cr.commit()
        if not mk_log_id.log_line_ids and not self.env.context.get('log_id', False):
            mk_log_id.unlink()
        if sale_order_ids:
            return mk_instance_id.action_open_model_view(sale_order_ids.ids, 'sale.order', 'Bol.com Order')
        return True

    def bol_update_order_status(self, mk_instance_ids):
        if not isinstance(mk_instance_ids, list):
            mk_instance_ids = [mk_instance_ids]
        for mk_instance_id in mk_instance_ids:
            mk_log_id = self.env['mk.log'].create_update_log(mk_instance_id=mk_instance_id, operation_type='export')
            mk_log_line_dict = self.env.context.get('mk_log_line_dict', {'error': [], 'success': []})
            picking_ids = self.env['stock.picking'].search(
                ['|', ('mk_instance_id', '=', mk_instance_id.id), ('backorder_id.mk_instance_id', '=', mk_instance_id.id), ('updated_in_marketplace', '=', False), ('bol_fulfilment_method', '=', 'FBR'), ('state', '=', 'done'),
                 ('location_dest_id.usage', '=', 'customer'), ('is_marketplace_exception', '=', False)], order='id asc')
            picking_ids.filtered(lambda x: not x.mk_instance_id).write({'mk_instance_id': mk_instance_id.id})
            picking_ids.with_context(mk_log_line_dict=mk_log_line_dict, mk_log_id=mk_log_id).do_bol_update_order_status(manual_process=False)
            self.env['mk.log'].create_update_log(mk_instance_id=mk_instance_id, mk_log_id=mk_log_id, mk_log_line_dict=mk_log_line_dict)
            if not mk_log_id.log_line_ids and not self.env.context.get('log_id', False):
                mk_log_id.unlink()
        return True

    def _get_available_item_for_cancel_in_bol(self):
        item_to_be_cancelled = self.env['sale.order.line']
        for order in self.filtered(lambda x: x.mk_id and x.marketplace == 'bol'):
            order_response = order.mk_instance_id._send_bol_request('retailer/orders/{}'.format(order.mk_id), {})
            order_items_available_to_cancel = [item.get('orderItemId') for item in order_response.get('orderItems') if
                                               item.get('fulfilment', {}).get('method') == order.bol_fulfilment_method and (item.get('quantity') > item.get('quantityShipped') + item.get('quantityCancelled'))]
            if not order_items_available_to_cancel:
                if all([item['cancellationRequest'] or item['quantity'] == item['quantityCancelled'] for item in order_response.get('orderItems')]):
                    with self.pool.cursor() as custom_cr:
                        order.with_env(self.env(cr=custom_cr)).write({'canceled_in_marketplace': True})
                        order.with_env(self.env(cr=custom_cr)).message_post(body=_("Marked order as cancelled in Marketplace since all items are cancelled on Bol.com."))
                raise MarketplaceException(_("We couldn't find any items eligible for cancellation. It appears that the items may have already been shipped or cancelled on bol.com"))
            item_to_be_cancelled |= order.order_line.filtered(lambda x: x.mk_id in order_items_available_to_cancel)
        return item_to_be_cancelled

    def prepare_cancel_wizard_vals(self):
        bol_cancel_item_ids = self.env['bol.cancel.item.line']
        item_to_be_cancelled = self._get_available_item_for_cancel_in_bol()
        for order_line in item_to_be_cancelled:
            bol_cancel_item_ids |= self.env['bol.cancel.item.line'].create({'order_line_id': order_line.id})
        return {'bol_cancel_item_line_ids': [(6, 0, bol_cancel_item_ids.ids)]}

    def cancel_in_bol(self):
        view = self.env.ref('bol.cancel_in_bol_form_view')
        context = dict(self.env.context)
        context.update({'active_model': 'sale.order', 'active_id': self.id, 'active_ids': self.ids})
        wizard_vals = self.prepare_cancel_wizard_vals()
        res_id = self.env['mk.cancel.order'].with_context(context).create(wizard_vals)
        return {
            'name': _('Cancel Order In Bol.com'),
            'type': 'ir.actions.act_window',
            'view_type': 'form',
            'view_mode': 'form',
            'res_model': 'mk.cancel.order',
            'views': [(view.id, 'form')],
            'view_id': view.id,
            'target': 'new',
            'res_id': res_id.id,
            'context': context
        }

    def cron_auto_import_bol_orders(self, mk_instance_id):
        mk_instance_id = self.env['mk.instance'].browse(mk_instance_id)
        if mk_instance_id.state == 'confirmed':
            valid_fulfillment_options = mk_instance_id.bol_get_valid_fulfillment_options()
            for fulfillment_option in valid_fulfillment_options:
                self.bol_import_orders(mk_instance_id, type=fulfillment_option)
        return True

    def cron_auto_update_bol_order_status(self, mk_instance_id):
        mk_instance_id = self.env['mk.instance'].browse(mk_instance_id)
        if mk_instance_id.state == 'confirmed':
            self.bol_update_order_status(mk_instance_id)
        return True

    def bol_open_sale_order_in_marketplace(self):
        raise MarketplaceException(_("Navigation not available for Bol.com marketplace."))


class SaleOrderLine(models.Model):
    _inherit = 'sale.order.line'

    bol_commission = fields.Float('Bol Commission', help="Amount of commission was added by Bol for order line.")
