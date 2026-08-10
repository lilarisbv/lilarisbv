import time
import pprint
import logging
from datetime import timedelta
import pytz
from odoo import models, fields, tools, _
from .misc import convert_woocommerce_datetime_to_utc, log_traceback_for_exception
from odoo.exceptions import ValidationError
from psycopg2 import OperationalError

_logger = logging.getLogger("Teqstars:WooCommerce")

FULFILLMENT_STATUS = [('pending', 'Pending'),
                      ('processing', 'Processing'),
                      ('on-hold', 'On-Hold'),
                      ('completed', 'Completed'),
                      ('cancelled', 'Cancelled'),
                      ('refunded', 'Refunded'),
                      ('failed', 'Failed'),
                      ('trash', 'Trash')]


class SaleOrder(models.Model):
    _inherit = 'sale.order'

    woo_coupon_code = fields.Char("WooCommerce Coupon", copy=False)
    woo_status_ts = fields.Selection(FULFILLMENT_STATUS, copy=False, string="Order Status", help="The order's status:\n\n"
                                                                                                 "Pending payment: Order received, no payment initiated. Awaiting payment (unpaid).\n"
                                                                                                 "Failed: Payment failed or was declined (unpaid) or requires authentication (SCA).\n"
                                                                                                 "Processing: Payment received (paid) and stock has been reduced; order is awaiting fulfillment. All product orders require processing, except those that only contain products which are both Virtual and Downloadable.\n"
                                                                                                 "Completed: Order fulfilled and complete – requires no further action.\n"
                                                                                                 "On hold: Awaiting payment – stock is reduced, but you need to confirm payment.\n"
                                                                                                 "Canceled: Canceled by an admin or the customer – stock is increased.\n"
                                                                                                 "Refunded: Refunded by an admin.", default='pending')
    force_refunded = fields.Boolean("Force Refunded", copy=False, default=False)

    def woo_check_validation_for_import_sale_orders(self, order_line_list, mk_instance_id, order_dict):
        odoo_product_variant_obj, is_importable, order_number = self.env['product.product'], True, order_dict.get('number', '')
        mk_log_id = self.env.context.get('mk_log_id', False)
        queue_line_id = self.env.context.get('queue_line_id', False)
        mk_listing_item_obj, mk_listing_obj = self.env['mk.listing.item'], self.env['mk.listing']

        # validation for Financial workflow
        financial_workflow_config_id = self.validate_woocommerce_financial_workflow(order_dict, mk_instance_id)
        if not financial_workflow_config_id:
            return False, financial_workflow_config_id

        for order_line_dict in order_line_list:
            product_id = order_line_dict.get('product_id', False)
            if product_id == 0:
                log_message = "IMPORT ORDER: Cannot find WooCommerce Listing Item {} in WooCommerce, Order reference {}.".format(order_line_dict.get("name"), order_number)
                self.env['mk.log'].create_update_log(
                    mk_log_id=mk_log_id, operation_type='import',
                    mk_log_line_dict={'error': [{'log_message': log_message, 'queue_job_line_id': queue_line_id and queue_line_id.id or False}]})
                is_importable = False
                break
            variant_id = order_line_dict.get('variation_id', False)
            if not variant_id:
                variant_id = product_id
                order_line_dict.update({'variation_id': product_id})
            if variant_id:
                variant = mk_listing_item_obj.search([('mk_id', '=', variant_id), ('mk_instance_id', '=', mk_instance_id.id)])
                if variant:
                    continue
                if product_id:
                    try:
                        mk_listing_obj.woocommerce_import_listings(mk_instance_id, mk_listing_id=str(product_id), update_product_price=True,woo_update_image=True, update_existing_product=True)
                    except Exception as e:
                        log_message = "IMPORT ORDER: Cannot find WooCommerce Listing Item ID {} in WooCommerce, Order reference {}.".format(variant_id, order_number)
                        self.env['mk.log'].create_update_log(
                            mk_log_id=mk_log_id, operation_type='import',
                            mk_log_line_dict={'error': [{'log_message': log_message, 'queue_job_line_id': queue_line_id and queue_line_id.id or False}]})
                        is_importable = False
                        break
                sku = order_line_dict.get('sku', '')
                odoo_product_variant_id = sku and odoo_product_variant_obj.search([('default_code', '=ilike', sku)], limit=1) or False
                if not odoo_product_variant_id:
                    log_message = "IMPORT ORDER: Marketplace Item SKU {} Not found for Order {}".format(sku, order_number)
                    self.env['mk.log'].create_update_log(
                        mk_log_id=mk_log_id, operation_type='import',
                        mk_log_line_dict={'error': [{'log_message': log_message, 'queue_job_line_id': queue_line_id and queue_line_id.id or False}]})
                    is_importable = False
                    break
        return is_importable, financial_workflow_config_id

    def validate_woocommerce_financial_workflow(self, order_dict, mk_instance_id):
        mk_log_id = self.env.context.get('mk_log_id', False)
        queue_line_id = self.env.context.get('queue_line_id', False)
        payment_gateway_code = order_dict.get('payment_method', 'Untitled') or 'Untitled'
        not_found = False

        if order_dict.get("transaction_id") or (order_dict.get("status") == "processing" and order_dict.get("date_paid") and order_dict.get("payment_method") != "cod"):
            financial_state = "paid"
        else:
            financial_state = "unpaid"

        payment_gateway_id = self.env['woo.payment.gateway.ts'].search([('code', '=', payment_gateway_code), ('mk_instance_id', '=', mk_instance_id.id)], limit=1)
        if not payment_gateway_id:
            payment_gateway_id = self.env['woo.payment.gateway.ts'].create(
                {'name': order_dict.get('payment_method_title', 'Untitled') or 'Untitled', 'code': payment_gateway_code, 'mk_instance_id': mk_instance_id.id})

        financial_workflow_config_id = self.env['woo.order.workflow.config'].search(
            ['|', ('order_status', '=', financial_state), ('order_status', '=', 'any'), ('mk_instance_id', '=', mk_instance_id.id), ('payment_gateway_id', '=', payment_gateway_id.id)],
            limit=1)
        marketplace_workflow_id = financial_workflow_config_id.order_workflow_id or False

        if not marketplace_workflow_id:
            log_message = "IMPORT ORDER: Financial Workflow Configuration not found for WooCommerce Order {}. Please configure the order workflow under the Workflow tab with Payment " \
                          "Gateway {} and Financial Status {} in Instance Configuration (Marketplaces > Configuration > Instance).".format(
                order_dict.get('id'), payment_gateway_id.name, financial_state)
            self.env['mk.log'].create_update_log(mk_instance_id=mk_instance_id, operation_type='import', mk_log_id=mk_log_id,
                                                 mk_log_line_dict={
                                                     'error': [{'log_message': log_message, 'woocommerce_payment_gateway_id': payment_gateway_id.id, 'order_status': financial_state, 'queue_job_line_id': queue_line_id and queue_line_id.id or False}]})
            not_found = True
        return False if not_found else financial_workflow_config_id

    def _get_coupon_data(self, order_dict):
        coupon_line_list = order_dict.get('coupon_lines')
        coupon_code_list = [coupon_line.get('code') for coupon_line in coupon_line_list if coupon_line.get('code', False)]
        return ', '.join(coupon_code_list) if coupon_line_list else ''

    def create_woocommerce_sale_order(self, order_dict, mk_instance_id, customer_id, billing_customer_id, shipping_customer_id, pricelist_id, fiscal_position_id,
                                      payment_term_id, financial_workflow_config_id):
        coupon_code = self._get_coupon_data(order_dict)
        if financial_workflow_config_id:
            payment_term_id = financial_workflow_config_id.payment_term_id
            if payment_term_id:
                customer_id.write({'property_payment_term_id': payment_term_id.id})

        sale_order_vals = {
            'state': 'draft',
            'partner_id': customer_id.id,
            'partner_invoice_id': billing_customer_id.ids[0] if billing_customer_id else customer_id.id,
            'partner_shipping_id': shipping_customer_id.ids[0] if shipping_customer_id else customer_id.id,
            'date_order': convert_woocommerce_datetime_to_utc(order_dict.get("date_created", "")),
            'expected_date': convert_woocommerce_datetime_to_utc(order_dict.get("date_created", "")),
            'company_id': mk_instance_id.company_id.id,
            'warehouse_id': mk_instance_id.warehouse_id.id,
            'fiscal_position_id': fiscal_position_id.id or False,
            'pricelist_id': pricelist_id or mk_instance_id.pricelist_id.id or False,
            'team_id': mk_instance_id.team_id.id or False,
        }

        if mk_instance_id.use_marketplace_sequence:
            order_prefix = mk_instance_id.order_prefix
            order_name = order_dict.get("number", '')
            if order_prefix:
                order_name = "{}{}".format(order_prefix, order_name)
            sale_order_vals.update({'name': order_name})

        sale_order_vals = self.prepare_sales_order_vals_ts(sale_order_vals, mk_instance_id)

        sale_order_vals.update({'note': order_dict.get('note'),
                                'mk_id': order_dict.get('id'),
                                'mk_order_number': order_dict.get('number'),
                                'woo_status_ts': order_dict.get('status'),
                                'mk_instance_id': mk_instance_id.id,
                                'woo_coupon_code': coupon_code,
                                'user_id': mk_instance_id.salesperson_user_id.id or self.env.user.id})

        if financial_workflow_config_id:
            marketplace_workflow_id = financial_workflow_config_id.order_workflow_id
            sale_order_vals.update({
                'picking_policy': marketplace_workflow_id.picking_policy,
                'payment_term_id': payment_term_id.id,
                'order_workflow_id': marketplace_workflow_id.id})

        order_id = self.create(sale_order_vals)
        return order_id

    def get_woocommerce_order_line_price(self, order_line_dict, tax_included):
        price = float(order_line_dict.get('subtotal', 0.0))
        quantity = order_line_dict.get('quantity', 1) or 1
        if tax_included:
            subtotal_tax = float(order_line_dict.get('subtotal_tax', 0.0))
            return (price + subtotal_tax) / quantity
        return price / quantity

    def create_woocommerce_sale_order_line_ts(self, order_currency, order_line_dict, tax_ids, odoo_product_id, order_id, is_delivery=False, description='', is_discount=False,
                                              tax_included=False):
        sale_order_line_obj = self.env['sale.order.line']

        price = self.get_woocommerce_order_line_price(order_line_dict, tax_included)
        quantity = order_line_dict.get('quantity', 1) or 1
        rate_id = [tax_line.get('id') for tax_line in order_line_dict.get('taxes', []) if tax_line and tax_line.get('id')]

        order_currency_id = self.env['res.currency'].search([('name', '=', order_currency)], limit=1)
        if order_currency_id:
            price = order_currency_id._convert(float(price), order_id.currency_id, order_id.company_id, fields.Date.today())
        line_description = description if description else (order_line_dict.get('name') or odoo_product_id.name)

        line_vals = {
            'name': line_description,
            'product_id': odoo_product_id.id or False,
            'order_id': order_id.id,
            'company_id': order_id.company_id.id,
            'product_uom': odoo_product_id.uom_id and odoo_product_id.uom_id.id or False,
            'price_unit': price,
            'order_qty': quantity
        }

        order_line_data = sale_order_line_obj.prepare_sale_order_line_ts(line_vals, self.mk_instance_id)

        order_line_data.update({
            'name': line_description,
            'is_delivery': is_delivery,
            'is_discount': is_discount,
            'mk_id': order_line_dict.get('id'),
            'woocommerce_rate_id': rate_id and rate_id[0] or 0
        })

        if order_id and order_id.mk_instance_id.tax_system == 'according_to_marketplace':
            order_line_data.update({'tax_ids': [(6, 0, tax_ids.ids)]})

        if order_id and order_id.mk_instance_id.tax_system == 'marketplace_with_fiscal_position':
            order_line_data.update({'tax_ids': self.fiscal_position_id.map_tax(tax_ids)})

        order_line = sale_order_line_obj.create(order_line_data)
        return order_line

    def get_woocommerce_delivery_method(self, carrier_name, mk_instance_id):
        carrier_obj = self.env['delivery.carrier']
        carrier_id = carrier_obj.search(['|', ('name', '=', carrier_name), ('woocommerce_code', '=', carrier_name)], limit=1)
        if not carrier_id:
            carrier_id = carrier_obj.search(['|', ('name', 'ilike', carrier_name), ('woocommerce_code', 'ilike', carrier_name)], limit=1)
        if not carrier_id:
            carrier_id = carrier_obj.create({'name': carrier_name, 'woocommerce_code': carrier_name, 'product_id': mk_instance_id.delivery_product_id.id})
        return carrier_id

    def create_woocommerce_fees_line(self, mk_instance_id, order_dict, order_id):
        for fees_dict in order_dict.get('fee_lines', []):
            tax_ids = False
            if mk_instance_id.tax_system != 'default':
                tax_ids = self.prepare_woocommerce_tax(order_dict, fees_dict, mk_instance_id)
            tax_included = order_dict.get('prices_include_tax', False)
            order_line_dict = {'subtotal': float(fees_dict.get('total')), 'name': fees_dict.get('name'), 'id': fees_dict.get('id'), 'subtotal_tax': float(fees_dict.get('total_tax', 0.0))}
            self.create_woocommerce_sale_order_line_ts(order_dict.get('currency'), order_line_dict, tax_ids, mk_instance_id.woocommerce_fees_product_id, order_id, is_delivery=False, tax_included=tax_included)
        return True

    def create_woocommerce_shipping_line(self, mk_instance_id, order_dict, order_id):
        tax_included = order_dict.get('prices_include_tax', False)
        for shipping_dict in order_dict.get('shipping_lines', []):
            tax_ids = False
            if mk_instance_id.tax_system != 'default':
                tax_ids = self.prepare_woocommerce_tax(order_dict, shipping_dict, mk_instance_id)
            carrier_name = shipping_dict.get('method_title', 'WooCommerce Delivery Method')
            carrier_id = self.get_woocommerce_delivery_method(carrier_name, mk_instance_id)
            order_id.write({'carrier_id': carrier_id.id})
            shipping_product = carrier_id.product_id
            order_line_dict = {'subtotal': float(shipping_dict.get('total')), 'id': shipping_dict.get('id'), 'subtotal_tax': float(shipping_dict.get('total_tax', 0.0))}
            self.create_woocommerce_sale_order_line_ts(order_dict.get('currency'), order_line_dict, tax_ids, shipping_product, order_id, is_delivery=True, description=carrier_name,
                                                       tax_included=tax_included)

    def prepare_woocommerce_tax(self, order_dict, order_line_dict, mk_instance_id):
        tax_ids = self.env['account.tax']
        taxable = order_dict.get('tax_lines', [])
        taxes_included = order_dict.get('prices_include_tax', False)
        if taxable:
            tax_line_list = []
            for tax_dict in order_line_dict.get('taxes', []):
                if tax_dict.get('total', 0.0):
                    tax_detail_dict = next((item for item in taxable if item["rate_id"] == tax_dict.get('id')), None)
                    tax_detail_dict and tax_line_list.append({'rate': tax_detail_dict.get('rate_percent'), 'title': tax_detail_dict.get('label')})
                    tax_ids += self.get_odoo_tax(mk_instance_id, tax_line_list, taxes_included)
        return tax_ids

    def create_sale_order_line_woocommerce(self, mk_instance_id, order_dict, order_id):
        order_line_list = order_dict.get('line_items')
        mk_log_id = self.env.context.get('mk_log_id', False)
        queue_line_id = self.env.context.get('queue_line_id', False)
        tax_included = order_dict.get('prices_include_tax', False)
        for order_line_dict in order_line_list:
            product_variant_id = self.get_mk_listing_item_for_mk_order(order_line_dict.get('variation_id'), mk_instance_id)
            if not product_variant_id and order_line_dict.get('variation_id'):
                log_message = "IMPORT ORDER: WooCommerce Variant not found for WooCommerce Order ID {}, Variant ID: {} and Name: {}.".format(order_id.mk_id,
                                                                                                                                             order_line_dict.get('variant_id'),
                                                                                                                                             order_line_dict.get('name', ''))
                self.env['mk.log'].create_update_log(mk_log_id=mk_log_id, operation_type='import',
                                                     mk_log_line_dict={'error': [{'log_message': log_message, 'queue_job_line_id': queue_line_id and queue_line_id.id or False}]})
                return False
            odoo_product_id = product_variant_id.product_id

            tax_ids = False
            if mk_instance_id.tax_system != 'default':
                tax_ids = self.prepare_woocommerce_tax(order_dict, order_line_dict, mk_instance_id)

            order_line = self.create_woocommerce_sale_order_line_ts(order_dict.get('currency'), order_line_dict, tax_ids, odoo_product_id, order_id, tax_included=tax_included)
            total_discounts = float(order_line_dict.get('subtotal', 0.0)) - float(order_line_dict.get('total', 0.0)) or 0
            if tax_included:
                total_tax_discount = float(order_line_dict.get("subtotal_tax", 0.0)) - float(order_line_dict.get("total_tax", 0.0)) or 0
                total_discounts = total_tax_discount + total_discounts
            if float(total_discounts) > 0.0:
                if not mk_instance_id.discount_product_id:
                    log_message = "IMPORT ORDER: WooCommerce Discount Product not found for WooCommerce Order ID {}, Please set Product in Discount Product field in Order tab of Instance " \
                                  "configuration.".format(order_id.mk_id)
                    self.env['mk.log'].create_update_log(mk_log_id=mk_log_id, operation_type='import',
                                                         mk_log_line_dict={'error': [{'log_message': log_message, 'queue_job_line_id': queue_line_id and queue_line_id.id or False}]})
                    return False
                discount_desc = "Discount: {}".format(order_line.product_id.name)
                discount_order_line = self.create_woocommerce_sale_order_line_ts(order_dict.get('currency'), {'subtotal': float(total_discounts) * -1}, tax_ids,
                                                                                 mk_instance_id.discount_product_id, order_id, is_discount=True, description=discount_desc,
                                                                                 tax_included=tax_included)
                order_line and order_line.write({'woocommerce_discount_amount': total_discounts, 'related_disc_sale_line_id': discount_order_line.id})
        self.create_woocommerce_shipping_line(mk_instance_id, order_dict, order_id)
        self.create_woocommerce_fees_line(mk_instance_id, order_dict, order_id)
        return True

    def update_existing_order_from_woocommerce(self, order_id, order_dict, mk_instance_id):
        if not order_id or not order_dict:
            return False
        mk_log_id = self.env.context.get('mk_log_id', False)
        queue_line_id = self.env.context.get('queue_line_id', False)
        order_name = order_dict.get('number', '')
        if order_id:
            if order_dict.get('status') in ['cancelled', 'refunded'] and order_id.state != 'cancel':
                order_id.cancel_woocommerce_order_in_odoo(order_dict.get('status'))

            if order_dict.get('status') == 'completed':
                order_id.with_context(operation_type='import', mk_log_id=mk_log_id).auto_validate_woocommerce_delivery_order()
            order_id.write({'woo_status_ts': order_dict.get('status'),
                                     'updated_in_marketplace': True if order_dict.get('status', '') == 'completed' else False})
            log_message = "IMPORT ORDER: WooCommerce Order {}({}) is already imported.".format(order_name, order_dict.get('id'))
            self.env['mk.log'].create_update_log(mk_log_id=mk_log_id, operation_type='import',
                                                 mk_log_line_dict={'success': [{'log_message': log_message, 'queue_job_line_id': queue_line_id and queue_line_id.id or False}]})
        return order_id

    def _get_customers_for_woocommerce_order(self, order_dict, mk_instance_id):
        """
        Create or update customers in Odoo based on Woocommerce order information.
        :param order_dict: Dictionary containing Woocommerce order information.
        :param mk_instance_id: ID of the Woocommerce instance in Odoo.
        :return: all browsable - customer_id, billing_customer_id, shipping_customer_id.
        """
        partner_obj = self.env['res.partner']

        company_customer_id = False
        ship_company_customer_id = False
        billing_customer_dict = order_dict.get('billing', {})
        shipping_customer_dict = order_dict.get('shipping', {})

        mk_log_id = self.env.context.get('mk_log_id', False)
        queue_line_id = self.env.context.get('queue_line_id', False)
        order_name = order_dict.get('number', '')
        if mk_instance_id.is_create_company_contact_woo:
            billing_company = billing_customer_dict.get('company', False)
            shipping_company = shipping_customer_dict.get('company', False)

            if billing_company:
                company_customer_id = partner_obj.create_update_woocommerce_customers(billing_customer_dict, mk_instance_id)
                ship_company_customer_id = company_customer_id
                if billing_company != shipping_company:  # Create the shipping address company when both company is not same.
                    ship_company_customer_id = partner_obj.create_update_woocommerce_customers(shipping_customer_dict, mk_instance_id)

            if not billing_company and shipping_company:  # Create the shipping company when there is no billing company.
                ship_company_customer_id = partner_obj.create_update_woocommerce_customers(shipping_customer_dict, mk_instance_id)
                company_customer_id = ship_company_customer_id
        customer_id = partner_obj.create_update_woocommerce_customers(billing_customer_dict, mk_instance_id, parent_id=company_customer_id)
        if not customer_id:
            log_message = "IMPORT ORDER: Customer not found in WooCommerce Order No: {}".format(order_name)
            self.env['mk.log'].create_update_log(mk_log_id=mk_log_id, operation_type='import',
                                                 mk_log_line_dict={'error': [{'log_message': log_message, 'queue_job_line_id': queue_line_id and queue_line_id.id or False}]})
            return False
        if not billing_customer_dict:
            billing_customer_id = customer_id
        else:
            billing_customer_id = partner_obj.create_update_woocommerce_customers(billing_customer_dict, mk_instance_id, type='invoice',
                                                                                  parent_id=company_customer_id or customer_id)
        if not shipping_customer_dict.get('first_name') and not shipping_customer_dict.get('last_name') and not shipping_customer_dict.get(
                'address_1') and not shipping_customer_dict.get(
            'city') and not shipping_customer_dict.get('state'):
            shipping_customer_id = customer_id
        else:
            shipping_customer_id = partner_obj.create_update_woocommerce_customers(shipping_customer_dict, mk_instance_id, type='delivery',
                                                                                   parent_id=ship_company_customer_id or customer_id)
        return customer_id, billing_customer_id, shipping_customer_id

    def process_import_order_from_woocommerce_ts(self, order_dict, mk_instance_id):
        mk_log_id = self.env.context.get('mk_log_id', False)
        queue_line_id = self.env.context.get('queue_line_id', False)
        order_name = order_dict.get('number', '')

        if not self.check_marketplace_order_date(convert_woocommerce_datetime_to_utc(order_dict.get("date_created", "")), mk_instance_id):
            log_message = "IMPORT ORDER: WooCommerce Order {} is skipped due to order created prior to the date configured on Import Order After in Instance.".format(order_name)
            self.env['mk.log'].create_update_log(mk_log_id=mk_log_id, mk_log_line_dict={'success': [{'log_message': log_message, 'queue_job_line_id': queue_line_id and queue_line_id.id or False}]})
            return True

        #Validation and update order line vals for variation
        order_line_list = order_dict.get('line_items')
        is_importable, financial_workflow_config_id = self.woo_check_validation_for_import_sale_orders(order_line_list, mk_instance_id, order_dict)
        if not is_importable:
            return False

        #Checked for the existing order
        existing_order_id = self.search([('mk_id', '=', order_dict.get('id')), ('mk_instance_id', '=', mk_instance_id.id)])
        if existing_order_id:
            return self.update_existing_order_from_woocommerce(existing_order_id, order_dict, mk_instance_id)

        customer_id, billing_customer_id, shipping_customer_id = self.with_context(mk_log_id=mk_log_id, queue_line_id=queue_line_id)._get_customers_for_woocommerce_order(
            order_dict, mk_instance_id)

        customer = self.new({'partner_id': customer_id.id})
        # customer.onchange_partner_id() #todo
        customer_dict = customer._convert_to_write({name: customer[name] for name in customer._cache})
        pricelist_id = customer_dict.get('pricelist_id', False)
        fiscal_position_id = self.env['account.fiscal.position'].with_company(mk_instance_id.company_id)._get_fiscal_position(billing_customer_id, shipping_customer_id)
        payment_term_id = customer_dict.get('payment_term_id', False)

        order_id = self.create_woocommerce_sale_order(order_dict, mk_instance_id, customer_id, billing_customer_id, shipping_customer_id, pricelist_id, fiscal_position_id,
                                                      payment_term_id, financial_workflow_config_id)
        if not order_id:
            return False

        if not order_id.create_sale_order_line_woocommerce(mk_instance_id, order_dict, order_id):
            order_id.unlink()
            return False

        order_id.with_context(create_date=convert_woocommerce_datetime_to_utc(order_dict.get("date_created", "")), order_dict=order_dict).do_marketplace_workflow_process()
        if order_id:
            if order_id.woo_status_ts == 'completed' and not order_id.invoice_ids:
                order_id.process_invoice(order_id.order_workflow_id)
            log_message = 'IMPORT ORDER: Successfully imported marketplace order {}({})'.format(order_id.name, order_id.mk_id)
            self.env['mk.log'].create_update_log(mk_log_id=mk_log_id, operation_type='import',
                                                 mk_log_line_dict={'success': [{'log_message': log_message, 'queue_job_line_id': queue_line_id and queue_line_id.id or False}]})

        # meta field import with orders
        if 'meta_data' in order_dict:
            custom_field_mapping_ids = mk_instance_id.custom_field_mapping_ids.filtered(
                lambda meta: meta.woo_operation == 'import_order')

            # order metafield
            for custom_field_mapping in custom_field_mapping_ids.filtered(
                    lambda meta: meta.model_id.model == order_id._name):
                mk_instance_id.woocommerce_custom_field_mapping(custom_field_mapping,
                                                                order_dict.get('meta_data'), "import", order_id)
            # customer metafield
            for custom_field_mapping in custom_field_mapping_ids.filtered(
                    lambda meta: meta.model_id.model == customer_id._name):
                mk_instance_id.woocommerce_custom_field_mapping(custom_field_mapping,
                                                                order_dict.get('meta_data'), "import", customer_id)
            # Delivery orders metafield
            stock_pickings = order_id.picking_ids
            for custom_field_mapping in custom_field_mapping_ids.filtered(
                    lambda meta: meta.model_id.model == stock_pickings._name):
                mk_instance_id.woocommerce_custom_field_mapping(custom_field_mapping,
                                                                order_dict.get('meta_data'), "import", stock_pickings)
        return order_id

    def woocommerce_import_orders(self, mk_instance_ids, from_date=False, to_date=False, mk_order_id=False):
        res_id_list, action = [], False
        if not isinstance(mk_instance_ids, list):
            mk_instance_ids = [mk_instance_ids]
        for mk_instance_id in mk_instance_ids:
            mk_log_id = self.env['mk.log'].create_update_log(mk_instance_id=mk_instance_id, operation_type='import')
            if not from_date:
                from_date = mk_instance_id.last_order_sync_date if mk_instance_id.last_order_sync_date else fields.Datetime.now() - timedelta(3)
                from_date = from_date - timedelta(hours=1)
            if not to_date:
                to_date = fields.Datetime.now()
            from_date = mk_instance_id.woo_convert_date_onstore_tz(from_date)
            to_date = mk_instance_id.woo_convert_date_onstore_tz(to_date)
            woo_order_status_ids = mk_instance_id.woo_order_status_ids

            if mk_order_id:
                order_list = []
                for order in ''.join(mk_order_id.split()).split(','):
                    order_response, status_code = mk_instance_id.fetch_data_from_woocommerce('get', 'orders/{}'.format(order))
                    order_list += order_response
                woo_order_list = order_list
            else:
                params = {'modified_after': from_date.isoformat(), 'modified_before': to_date.isoformat(), 'status': ','.join(woo_order_status_ids.mapped('status'))}
                woo_order_list, status_code = mk_instance_id.fetch_data_from_woocommerce('get', 'orders', params=params)

            if status_code not in [200, 201]:
                return False
            if mk_order_id and woo_order_list:
                for woo_order in woo_order_list:
                    try:
                        with self.env.cr.savepoint():
                            order_id = self.with_context(mk_log_id=mk_log_id).process_import_order_from_woocommerce_ts(woo_order, mk_instance_id)
                            if not isinstance(order_id,bool):
                                res_id_list.append(order_id.id)
                    except OperationalError as e:
                        raise
                    except Exception as e:
                        log_traceback_for_exception()
                        self.env.cr.rollback()
                        log_message = "IMPORT ORDER: Error while importing processing WooCommerce Order {}, ERROR: {}".format(mk_order_id, e)
                        if not mk_log_id.exists():
                            mk_log_id = self.env['mk.log'].create_update_log(mk_instance_id=mk_instance_id, operation_type='import')
                        self.env['mk.log'].create_update_log(mk_log_id=mk_log_id, mk_log_line_dict={'error': [{'log_message': log_message}]})
                        continue
                if not mk_log_id.log_line_ids and not self.env.context.get('log_id', False):
                    mk_log_id.unlink()
                self.env.cr.commit()
                if res_id_list:
                    action = {'res_model': 'sale.order', 'type': 'ir.actions.act_window', 'target': 'current'}
                    if len(res_id_list) == 1:
                        action.update({'view_mode': 'form', 'res_id': res_id_list[0]})
                    else:
                        action.update({'name': _('WooCommerce Orders'), 'domain': [('id', 'in', res_id_list)], 'view_mode': 'list,form'})
                    return action
                return True
            if woo_order_list:
                batch_size = mk_instance_id.queue_batch_limit or 100
                for woo_orders in tools.split_every(batch_size, woo_order_list):
                    queue_id = mk_instance_id.action_create_queue(type='order')
                    for order in woo_orders:
                        # Check is order cancelled and order not found in Odoo to skip order queue line creation.
                        if order.get('status') in ['cancelled', 'refunded']:
                            if not self.search([('mk_id', '=', order.get('id')), ('mk_instance_id', '=', mk_instance_id.id)]):
                                continue

                        woo_order_dict = order
                        name = woo_order_dict.get('number', '') or ''
                        line_vals = {
                            'mk_id': woo_order_dict.get('id') or '',
                            'state': 'draft',
                            'name': name.strip(),
                            'data_to_process': pprint.pformat(woo_order_dict),
                            'mk_instance_id': mk_instance_id.id,
                        }
                        queue_id.action_create_queue_lines(line_vals)
                        res_id_list.append(queue_id.id)
            if not mk_log_id.log_line_ids and not self.env.context.get('log_id', False):
                mk_log_id.unlink()
            mk_instance_id.last_order_sync_date = pytz.timezone(mk_instance_id.woo_store_tz).localize(to_date).astimezone(pytz.timezone("UTC")).replace(tzinfo=None)
        if res_id_list:
            action = {'res_model': 'mk.queue.job', 'type': 'ir.actions.act_window', 'target': 'current'}
            if len(res_id_list) == 1:
                action.update({'view_mode': 'form', 'res_id': res_id_list[0]})
            else:
                action.update({'name': _('WooCommerce Order Queue'), 'domain': [('id', 'in', res_id_list)], 'view_mode': 'list,form'})
        return action

    def get_woocommerce_sale_orders(self, mk_instance_id):
        order_ids = self.search([('mk_id', '!=', False),
                                 ('updated_in_marketplace', '=', False),
                                 ('woo_status_ts', '!=', 'completed'),
                                 ('delivery_status', '=', 'full'),
                                 ('state', 'in', ['sale', 'done']),
                                 ('mk_instance_id', '=', mk_instance_id.id)], order='date_order')
        return order_ids

    def woocommerce_get_order_fulfillment_status(self):
        return True if self.woo_status_ts == 'completed' else False

    def _post_process_update_order_status(self, order_update_response):
        mk_log_id = self.env.context.get('mk_log_id', False)
        error_list = []
        for response_line in order_update_response[0].get('update'):
            if response_line.get('error'):
                log_message = 'UPDATE ORDER STATUS: Error while processing WooCommerce Order ID {}. ERROR: {}'.format(response_line.get('id', 'Undefined'), response_line.get('error'))
                mk_log_id and self.env['mk.log'].create_update_log(mk_log_id=mk_log_id, operation_type='export', mk_log_line_dict={'error': [{'log_message': log_message}]})
                error_list.append(log_message)
                continue
            order_id = self.search([('mk_id', '=', response_line.get('id'))])
            picking_ids = order_id.picking_ids.filtered(lambda x: not x.updated_in_marketplace and x.state == 'done' and x.location_dest_id.usage == 'customer' and x.no_of_retry_count < 3)
            order_id.write({'updated_in_marketplace': True, 'woo_status_ts': response_line.get('status', 'completed')})
            picking_ids.write({'updated_in_marketplace': True})
            order_id.message_post(body="Order is fulfilled in WooCommerce.")
            for picking_id in picking_ids:
                picking_id.message_post(body="Order is fulfilled in WooCommerce.")
            log_message = 'UPDATE ORDER STATUS: Successfully updated WooCommerce order {}'.format(order_id.name)
            mk_log_id and self.env['mk.log'].create_update_log(mk_log_id=mk_log_id, operation_type='export', mk_log_line_dict={'success': [{'log_message': log_message}]})
        return error_list

    def update_custom_fields_vals(self, order_id, picking_ids):
        mk_instance_id = order_id.mk_instance_id
        custom_data_vals = {}
        custom_field_mapping_ids = mk_instance_id.custom_field_mapping_ids.filtered(
            lambda meta: meta.woo_operation == 'update_order_status')

        for custom_field_mapping in custom_field_mapping_ids.filtered(
                lambda meta: meta.model_id.model == order_id._name):
            custom_data_vals = mk_instance_id.woocommerce_custom_field_mapping(custom_field_mapping,
                                                                               custom_data_vals, "export", order_id)

        for custom_field_mapping in custom_field_mapping_ids.filtered(
                lambda meta: meta.model_id.model == picking_ids._name):
            custom_data_vals = mk_instance_id.woocommerce_custom_field_mapping(custom_field_mapping,
                                                                               custom_data_vals, "export", picking_ids)
        return custom_data_vals

    def woocommerce_update_order_status(self, mk_instance_ids):
        if not isinstance(mk_instance_ids, list):
            mk_instance_ids = [mk_instance_ids]
        for mk_instance_id in mk_instance_ids:
            mk_log_id = self.env['mk.log'].create_update_log(mk_instance_id=mk_instance_id, operation_type='export')
            mk_log_line_dict = self.env.context.get('mk_log_line_dict', {'error': [], 'success': []})
            remaining_order_ids = self.get_woocommerce_sale_orders(mk_instance_id)
            update_order_list = []
            for order_id in remaining_order_ids:
                picking_ids = order_id.picking_ids.filtered(
                    lambda x: not x.updated_in_marketplace and x.state == 'done' and x.location_dest_id.usage == 'customer' and x.no_of_retry_count < 3)
                if not picking_ids:
                    continue
                data_to_export = self.update_custom_fields_vals(order_id, picking_ids)
                data_to_export.update({'id': order_id.mk_id, 'status': 'completed'})
                update_order_list.append(data_to_export)
            for update_order_list_batch in tools.split_every(100, update_order_list, piece_maker=list):
                order_update_response, status_code = mk_instance_id.fetch_data_from_woocommerce('post', 'orders/batch', data={'update': update_order_list_batch})
                woo_order_ids = [order_dict['id'] for order_dict in update_order_list_batch]
                if status_code in [200, 201]:
                    self.with_context(mk_log_id=mk_log_id)._post_process_update_order_status(order_update_response)
                else:
                    mk_log_line_dict['error'].append(
                        {'log_message': 'UPDATE ORDER STATUS: Error while processing WooCommerce Order IDs {}. ERROR: {}'.format(', '.join(woo_order_ids), ', '.join(order_update_response))})
                self.env['mk.log'].create_update_log(mk_instance_id=mk_instance_id, operation_type='export', mk_log_id=mk_log_id, mk_log_line_dict=mk_log_line_dict)
                self.env.cr.commit()
            if not mk_log_id.log_line_ids and not self.env.context.get('log_id', False):
                mk_log_id.unlink()
        return True

    def cron_auto_import_woocommerce_orders(self, mk_instance_id):
        mk_instance_id = self.env['mk.instance'].browse(mk_instance_id)
        if mk_instance_id.state == 'confirmed':
            self.woocommerce_import_orders(mk_instance_id)
        return True

    def cron_auto_update_woocommerce_order_status(self, mk_instance_id):
        mk_instance_id = self.env['mk.instance'].browse(mk_instance_id)
        if mk_instance_id.state == 'confirmed':
            self.woocommerce_update_order_status(mk_instance_id)
        return True

    def process_update_order_status_woocommerce(self, order_id, mk_instance_id):
        picking_ids = order_id.picking_ids.filtered(lambda x: not x.updated_in_marketplace and x.state == 'done' and x.location_dest_id.usage == 'customer' and x.no_of_retry_count < 3)
        if not picking_ids or order_id.delivery_status != 'full':
            return False
        try:
            data_to_export = self.update_custom_fields_vals(order_id, picking_ids)
            data_to_export.update({'id': order_id.mk_id, 'status': 'completed'})
            order_update_response, status_code = mk_instance_id.fetch_data_from_woocommerce('post', 'orders/batch', data={'update': [data_to_export]})
            if status_code in [200, 201]:
                error_list = self._post_process_update_order_status(order_update_response)
                if error_list:
                    raise ValidationError(_([order_dict.get('error') for order_dict in order_update_response[0].get('update') if order_dict.get('error')]))
        except Exception as e:
            raise ValidationError(_('UPDATE ORDER STATUS: Error while processing WooCommerce Order {}. ERROR: {}'.format(order_id.name, e)))
        return True

    def woocommerce_open_sale_order_in_marketplace(self):
        marketplace_url = self.mk_instance_id.woo_shop_url + '/wp-admin/post.php?post=' + self.mk_id + '&action=edit'
        return marketplace_url

    def refund_in_woocommerce(self):
        cancel_order_wizard_obj = self.env['mk.cancel.order']
        view = self.env.ref('woocommerce.refund_in_woocommerce_form_view')
        context = dict(self.env.context)
        context.update({'active_model': 'sale.order', 'active_id': self.id, 'active_ids': self.ids})
        wizard_vals = cancel_order_wizard_obj.woocommerce_refund_wizard_default_get(self)
        res_id = cancel_order_wizard_obj.create(wizard_vals)
        return {
            'name': _('Refund Order In WooCommerce'),
            'type': 'ir.actions.act_window',
            # 'view_type': 'form',
            'view_mode': 'form',
            'res_model': 'mk.cancel.order',
            'views': [(view.id, 'form')],
            'view_id': view.id,
            'target': 'new',
            'res_id': res_id.id,
            'context': context
        }

    def cancel_in_woocommerce(self):
        cancel_order_wizard_obj = self.env['mk.cancel.order']
        view = self.env.ref('woocommerce.cancel_in_woocommerce_form_view')
        context = dict(self.env.context)
        context.update({'active_model': 'sale.order', 'active_id': self.id, 'active_ids': self.ids})
        res_id = cancel_order_wizard_obj.create({})
        return {
            'name': _('Cancel Order In WooCommerce'),
            'type': 'ir.actions.act_window',
            #'view_mode': 'form',
            'res_model': 'mk.cancel.order',
            'views': [(view.id, 'form')],
            'view_id': view.id,
            'target': 'new',
            'res_id': res_id.id,
            'context': context
        }

    def cancel_woocommerce_order_in_odoo(self, woo_status):
        cancel_message = _("The order was {} on WooCommerce.".format(woo_status))
        # filter completed picking orders related to the sale order
        picking_ids = self.picking_ids.filtered(lambda x: x.state == 'done')
        # Post cancel message to each completed picking order
        for picking_id in picking_ids:
            picking_id.message_post(body=cancel_message)
        picking_ids and picking_ids.write({'cancel_in_marketplace': True})  # Mark picking orders as canceled in the marketplace
        if picking_ids:
            return False
        # Perform order cancellation in Odoo, disabling cancellation warning
        ctx = self.env.context.copy()
        ctx.update({'active_id': self.id, 'active_ids': self.ids, 'active_model': self._name, 'disable_cancel_warning': True})
        self.with_context(ctx).action_cancel()
        self.message_post(body=cancel_message)
        self.write({'canceled_in_marketplace': True})  # Mark the sale order as canceled in the marketplace
        return True

    def auto_validate_woocommerce_delivery_order(self):
        """
        Auto validate WooCommerce delivery orders.
        """
        # Get the context variables.
        mk_log_id = self.env.context.get('mk_log_id', False)
        queue_line_id = self.env.context.get('queue_line_id', False)

        # Get the pickings for the fulfillment.
        picking_ids = self.picking_ids.filtered(lambda p: p.location_dest_id.usage == "customer" and p.state in ['confirmed', 'assigned'])
        for picking_id in picking_ids:

            # Quality check validation.
            if hasattr(picking_id, "quality_check_todo"):
                if getattr(picking_id, "quality_check_todo") or getattr(picking_id, "check_ids"):
                    log_message = _(
                        'UPDATE ORDER STATUS IN ODOO: Cannot auto validate WooCommerce Order {}, ERROR: {} {}. Please check quality manually.'.format(
                            self.name, picking_id.name, 'Failed Due To Open Quality Checks For Delivery Order'))
                    self.env['mk.log'].create_update_log(mk_log_id=mk_log_id, mk_log_line_dict={'error': [
                        {'log_message': log_message,
                         'queue_job_line_id': queue_line_id and queue_line_id.id or False}]})
                    continue

            picking_id.move_ids._action_assign()
            for move in picking_id.move_ids:
                move._set_quantity_done(move.product_uom_qty)

            # Validate the picking.
            res = picking_id.with_context(skip_sms=True, skip_sanity_check=True).button_validate()
            if isinstance(res, dict):
                if res.get('res_model', False):
                    record = self.env[res.get('res_model')].with_context(res.get("context")).create({})
                    record.process()

            # Update the picking if it is done.
            if picking_id.state == "done":
                picking_vals = {'updated_in_marketplace': True, 'is_marketplace_exception': False, 'exception_message': False}
                picking_id.write(picking_vals)
        return True


class SaleOrderLine(models.Model):
    _inherit = 'sale.order.line'

    woocommerce_discount_amount = fields.Float("WooCommerce Discount Amount", copy=False)
    woocommerce_rate_id = fields.Integer("WooCommerce Rate ID", copy=False)
