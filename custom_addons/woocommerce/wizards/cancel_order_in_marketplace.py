from datetime import datetime
from odoo.tools import float_compare
from odoo.exceptions import UserError
from odoo import models, api, fields, _


class MKCancelOrder(models.TransientModel):
    _inherit = "mk.cancel.order"

    @api.depends('woocommerce_refund_payment_lines.price_subtotal', 'woocommerce_refund_payment_lines.refund_price_unit', 'woocommerce_shipping_amount')
    def _woocommerce_amount_all(self):
        for record in self:
            woocommerce_amount_subtotal_to_refund, woocommerce_tax_amount, order_id = 0.0, 0.0, False
            for line in record.woocommerce_refund_payment_lines:
                order_id = line.order_line_id.order_id
                tax_1 = (line.order_line_id.price_tax - abs(line.order_line_id.related_disc_sale_line_id.price_tax)) - line.tax_refunded
                tax_2 = round((line.order_line_id.price_tax - abs(line.order_line_id.related_disc_sale_line_id.price_tax)), 2) / line.order_line_id.product_uom_qty * line.to_refund_qty
                woocommerce_tax_amount += min(tax_1, tax_2)
                line_subtotal = line.order_line_id.price_subtotal - abs(line.order_line_id.related_disc_sale_line_id.price_subtotal)
                woo_unit_price = round(line_subtotal, 2) / line.order_line_id.product_uom_qty
                actual_refund_price = woo_unit_price * line.to_refund_qty
                available_refund = line_subtotal - line.total_refunded
                available_refund = min(available_refund, actual_refund_price)
                woocommerce_amount_subtotal_to_refund += min(available_refund, actual_refund_price)
                line.to_refund_total = available_refund

            if order_id and self.woocommerce_shipping_amount > 0.0:
                shipping_line = order_id.order_line.filtered(lambda x: x.is_delivery)
                shipping_taxes = shipping_line.tax_ids.compute_all(shipping_line.price_unit, order_id.currency_id, 1, product=shipping_line.product_id, partner=order_id.partner_shipping_id)
                shipping_taxes_total = sum(t.get('amount', 0.0) for t in shipping_taxes.get('taxes', []))
                woocommerce_tax_amount += (shipping_taxes_total - record.woocommerce_shipping_tax_refunded_amount)

            woocommerce_amount_subtotal = round(woocommerce_amount_subtotal_to_refund, 2)
            woocommerce_tax_amount = round(woocommerce_tax_amount, 2)
            manual_refund_amount = min(woocommerce_amount_subtotal_to_refund + woocommerce_tax_amount + record.woocommerce_shipping_amount, record.woocommerce_amount_total)

            record.update({
                'woocommerce_tax_amount': round(woocommerce_tax_amount, 2),
                'woocommerce_amount_subtotal': woocommerce_amount_subtotal,
                'woocommerce_manual_refund_amount': round(manual_refund_amount, 2),
                'woocommerce_shipping_tax_refunded_amount': round(record.woocommerce_shipping_tax_refunded_amount, 2)
            })

    woocommerce_is_api_refund = fields.Boolean("Refund via Payment Gateway?", default=True, help="When true, the payment gateway API is used to generate the refund.")
    woocommerce_refund_payment_lines = fields.One2many("woocommerce.refund.payment.line", "wizard_id", string="Refund payments")
    woocommerce_amount_total = fields.Monetary(string='Total Available to refund')
    woocommerce_amount_subtotal = fields.Monetary(string='Subtotal', store=True, readonly=True, compute='_woocommerce_amount_all')
    woocommerce_remaining_shipping_amount = fields.Monetary(string='Remaining shipping amount to Refund')
    woocommerce_shipping_amount = fields.Monetary(string='Shipping')
    woocommerce_tax_amount = fields.Monetary(string="Tax", store=True, compute='_woocommerce_amount_all')
    woocommerce_manual_refund_amount = fields.Monetary(string='Total Refund Amount')
    woocommerce_shipping_tax_refunded_amount = fields.Monetary(string='Shipping Tax Refunded')
    woocommerce_restock = fields.Boolean(string="Restock In WooCommerce", default=True, help="When true, the selected line items are restocked in WooCommerce")

    @api.onchange('woocommerce_manual_refund_amount')
    def onchange_woocommerce_manual_refund_amount(self):
        if self.woocommerce_manual_refund_amount > self.woocommerce_amount_total:
            self.woocommerce_manual_refund_amount = self.woocommerce_amount_total
        return {}

    @api.onchange('woocommerce_shipping_amount')
    def onchange_woocommerce_shipping_amount(self):
        if not self.woocommerce_remaining_shipping_amount and self.woocommerce_shipping_amount:
            self.woocommerce_shipping_amount = 0.0
        if self.woocommerce_shipping_amount > self.woocommerce_remaining_shipping_amount:
            self.woocommerce_shipping_amount = self.woocommerce_remaining_shipping_amount
        if self.woocommerce_remaining_shipping_amount < 0.0:
            self.woocommerce_shipping_amount = 0.0
        return {}

    def get_refunded_unit_price(self, refunded_vals):
        return refunded_vals.get('refunded', 0.0) and refunded_vals.get('total_refunded', 0.0) / refunded_vals.get('refunded', 0.0) or 0.0

    def woocommerce_refund_wizard_default_get(self, order_id):
        woocommerce_refund_payment_line_ids = self.env['woocommerce.refund.payment.line']
        refund_response, status_code = order_id.mk_instance_id.fetch_data_from_woocommerce('get', "orders/{id}/refunds".format(id=order_id.mk_id))
        refund_dict_qty_wise = {}
        total_tax_refunded, total_amount_refunded, total_shipping_refunded, total_shipping_tax_refunded = 0.0, 0.0, 0.0, 0.0

        for refund_dict in refund_response:
            if not refund_dict.get('line_items') and not refund_dict.get('shipping_lines') and not refund_dict.get('tax_lines'):
                return {}

            for refund_line in refund_dict.get('line_items'):
                tax_detail_dict = {}
                for tax_dict in refund_line.get('taxes', []):
                    tax_detail_dict.update({'id': tax_dict.get('id'), 'total': tax_dict.get('total')})

                order_line_mk_id = refund_line.get('meta_data', []) and int(refund_line.get('meta_data')[0].get('value')) or 0

                if order_line_mk_id in refund_dict_qty_wise:
                    updated_qty = refund_dict_qty_wise.get(order_line_mk_id).get('refunded') + abs(refund_line.get('quantity'))
                    updated_tax_refund = abs(float(tax_detail_dict.get('total', 0.0))) + abs(float(refund_dict_qty_wise.get(order_line_mk_id, False).get('tax_refunded')))
                    total_tax_refunded += abs(float(tax_detail_dict.get('total', 0.0)))
                    total_refunded = abs(float(refund_line.get('total', 0.0))) + abs(float(refund_dict_qty_wise.get(order_line_mk_id, False).get('total_refunded')))

                    if not refund_dict_qty_wise.get(order_line_mk_id, False).get('tax_ids') and tax_detail_dict.get('id'):
                        refund_dict_qty_wise[order_line_mk_id] = {'refunded': updated_qty,
                                                                  'tax_ids': tax_detail_dict.get('id'),
                                                                  'tax_refunded': updated_tax_refund,
                                                                  'total_refunded': total_refunded}
                    else:
                        refund_dict_qty_wise[order_line_mk_id].update({'refunded': updated_qty, 'tax_refunded': updated_tax_refund, 'total_refunded': total_refunded})
                else:
                    updated_tax_refund = abs(float(tax_detail_dict.get('total', 0.0)))
                    total_tax_refunded += updated_tax_refund
                    refund_dict_qty_wise.update({order_line_mk_id: {'refunded': abs(refund_line.get('quantity')),
                                                                    'tax_ids': tax_detail_dict.get('id'),
                                                                    'tax_refunded': updated_tax_refund,
                                                                    'total_refunded': abs(float(refund_line.get('total', 0.0)))}})

            total_shipping_refunded += sum([float(shipping_line.get('total', 0.0)) for shipping_line in refund_dict.get('shipping_lines')])
            total_shipping_tax_refunded += sum([float(shipping_line.get('total_tax', 0.0)) for shipping_line in refund_dict.get('shipping_lines')])

            for transaction in refund_dict.get('line_items'):
                total_amount_refunded += abs(float(transaction.get('total')))

        total_amount_refunded += abs(total_shipping_refunded)
        remaining_shipping_to_refund = 0.0

        for line in order_id.order_line:
            if line.price_subtotal <= 0:
                continue
            if line.is_delivery:
                remaining_shipping_to_refund = line.price_subtotal + total_shipping_refunded
                continue
            listing_item_mk_id = refund_dict_qty_wise.get(int(line.mk_id), {})
            if listing_item_mk_id:
                available_qty = line.product_uom_qty - listing_item_mk_id.get('refunded')
            else:
                available_qty = line.product_uom_qty
            if line.price_subtotal == listing_item_mk_id.get('total_refunded', 0.0) and line.price_tax == listing_item_mk_id.get('tax_refunded', 0.0):
                continue
            if not available_qty:
                continue

            line_subtotal = line.price_subtotal - abs(line.related_disc_sale_line_id.price_subtotal)
            woo_unit_price = round(line_subtotal, 2) / line.product_uom_qty
            refunded_unit_price = self.get_refunded_unit_price(listing_item_mk_id)
            to_refund_total = bool(listing_item_mk_id) and (line.price_subtotal - abs(line.related_disc_sale_line_id.price_subtotal)) or 0.0
            if refunded_unit_price and woo_unit_price != refunded_unit_price:
                continue
            to_refund_tax = bool(listing_item_mk_id) and ((line.price_tax - abs(line.related_disc_sale_line_id.price_tax)) - listing_item_mk_id.get('tax_refunded', 0.0)) or 0.0
            if to_refund_tax and to_refund_tax != line.price_tax:
                return {}

            woocommerce_refund_payment_line_ids |= self.env['woocommerce.refund.payment.line'].create({
                'product_id': line.product_id.id,
                'available_qty': available_qty,
                'to_refund_qty': available_qty,
                'price_unit': line.price_unit - (line.woocommerce_discount_amount / line.product_uom_qty),
                'refund_price_unit': (line.price_subtotal - abs(line.related_disc_sale_line_id.price_subtotal)) / line.product_uom_qty,
                'order_line_id': line.id,
                'tax_refunded': bool(listing_item_mk_id) and listing_item_mk_id.get('tax_refunded', 0.0) or 0.0,
                'to_refund_total': to_refund_total,
                'total_refunded': bool(listing_item_mk_id) and listing_item_mk_id.get('total_refunded', 0.0) or 0.0,
            })

        return {'woocommerce_refund_payment_lines': [(6, 0, woocommerce_refund_payment_line_ids.ids)],
                'woocommerce_remaining_shipping_amount': remaining_shipping_to_refund,
                'woocommerce_shipping_amount': max(remaining_shipping_to_refund, 0.0),
                'woocommerce_amount_total': order_id.amount_total - total_amount_refunded - total_tax_refunded - abs(total_shipping_tax_refunded),
                'woocommerce_shipping_tax_refunded_amount': abs(total_shipping_tax_refunded),
                'currency_id': order_id.currency_id.id,
                'create_refund_option_visible': True if order_id.invoice_ids.filtered(lambda x: x.move_type == 'out_invoice' and x.payment_state in ['paid']) else False}

    def woocommerce_mark_order_refunded(self):
        order_id = self.env['sale.order'].browse(self.env.context.get('active_id'))
        order_id.write({'force_refunded': True,
                        'woo_status_ts': 'refunded'})
        msg = _("Manually marked this order as refunded for WooCommerce.")
        order_id.message_post(body=msg)
        return True

    def do_cancel_in_woocommerce(self):
        active_id = self.env.context.get('active_id')
        order_id = self.env['sale.order'].browse(active_id)
        if not order_id:
            raise UserError(_("Can't find order to cancel. Please go back to order list, open order and try again!"))
        try:
            order_response, status_code = order_id.mk_instance_id.fetch_data_from_woocommerce('post', 'orders/{}'.format(order_id.mk_id),
                                                                                              data={'status': 'cancelled'})
            if status_code in [200, 201]:
                order_id.write({'canceled_in_marketplace': True, 'woo_status_ts': 'cancelled'})
            else:
                raise UserError("{}".format(order_response))
        except Exception as e:
            raise UserError("Something went wrong while canceling order in WooCommerce for order {} : {}".format(order_id.name, e))
        order_id.message_post(body='This order is cancelled in WooCommerce')
        return True

    def do_refund_in_woocommerce(self):
        active_id = self.env.context.get('active_id')
        order_id = self.env['sale.order'].browse(active_id)
        if not order_id:
            raise UserError(_("Can't find order to cancel. Please go back to order list, open order and try again!"))
        if self.woocommerce_refund_payment_lines and float_compare(round(self.woocommerce_amount_subtotal + self.woocommerce_shipping_amount + self.woocommerce_tax_amount, 2),
                                                                   self.woocommerce_manual_refund_amount, 2) != 0:
            raise UserError(_("Subtotal + Shipping + Tax isn't matching with the Refund Amount!"))
        refund_item_list = []
        for line in self.woocommerce_refund_payment_lines.filtered(lambda x: x.to_refund_qty):
            refund_total = line.to_refund_total
            tax_1 = (line.order_line_id.price_tax - abs(line.order_line_id.related_disc_sale_line_id.price_tax)) - line.tax_refunded
            tax_2 = round((line.order_line_id.price_tax - abs(line.order_line_id.related_disc_sale_line_id.price_tax)), 2) / line.order_line_id.product_uom_qty * line.to_refund_qty
            refund_tax_total = round(min(tax_1, tax_2), 2)
            if refund_total or refund_tax_total:
                refund_item_vals = {'id': line.order_line_id.mk_id,
                                    'quantity': line.to_refund_qty if refund_total else 0,
                                    'refund_total': refund_total}
                if refund_tax_total:
                    refund_item_vals.update({'refund_tax': [{'id': '1', 'refund_total': refund_tax_total}]})
                refund_item_list.append(refund_item_vals)
        if not refund_item_list and not self.woocommerce_amount_total and not self.woocommerce_manual_refund_amount:
            return False
        if self.woocommerce_shipping_amount:
            delivery_line = order_id.order_line.filtered(lambda x: x.is_delivery)
            refund_shipping_vals = {'id': delivery_line.mk_id,
                                    'quantity': delivery_line.product_uom_qty,
                                    'refund_total': round(self.woocommerce_shipping_amount, 2)}
            remaining_shipping_tax_refund = delivery_line.price_tax - self.woocommerce_shipping_tax_refunded_amount
            if remaining_shipping_tax_refund:
                refund_shipping_vals.update({'refund_tax': [{'id': '1', 'refund_total': remaining_shipping_tax_refund}]})
            refund_item_list.append(refund_shipping_vals)
        if not refund_item_list:
            raise UserError("Nothing to refund!!!")
        try:
            refund_response, status_code = order_id.mk_instance_id.fetch_data_from_woocommerce('post', "orders/{}/refunds".format(order_id.mk_id),
                                                                                               data={"reason": self.refund_description or '',
                                                                                                     "api_restock": self.woocommerce_restock,
                                                                                                     "api_refund": self.woocommerce_is_api_refund,
                                                                                                     "line_items": refund_item_list})
            if status_code in [200, 201]:
                order_dict, status_code = order_id.mk_instance_id.fetch_data_from_woocommerce('get', 'orders/{}'.format(order_id.mk_id))
                order_status = order_dict and order_dict[0].get('status', '')
            else:
                raise UserError("{}".format(refund_response))
        except Exception as e:
            raise UserError("Something went wrong while creating a refund in WooCommerce for order {} : {}".format(order_id.name, e))

        order_status and order_id.write({'woo_status_ts': order_status})
        body = _('This order is refunded {}in WooCommerce with amount <b>{}</b>'.format(' via payment gateway API ' if self.woocommerce_is_api_refund else '',
                                                                                        self.woocommerce_manual_refund_amount))
        order_id.message_post(body=body)
        if self.is_create_refund:
            self.create_woocommerce_refund_in_odoo(order_id)
        return True

    def create_woocommerce_refund_in_odoo(self, order_id):
        invoice_id = order_id.invoice_ids.filtered(lambda x: x.move_type == 'out_invoice' and x.payment_state in ['paid'])
        if invoice_id:
            move_reversal = self.env['account.move.reversal'].with_context(active_model="account.move", active_ids=invoice_id.ids).create({
                'reason': self.refund_description or "WooCommerce Refund",
                'date': self.date_invoice or datetime.now(),
                'journal_id': invoice_id.journal_id.id
            })
            reversal = move_reversal.reverse_moves()
            refund_invoice = self.env['account.move'].browse(reversal['res_id'])
            to_remove_lines = refund_invoice.invoice_line_ids
            new_move = move_reversal.new_move_ids
            for line in self.woocommerce_refund_payment_lines.filtered(lambda x: x.to_refund_qty):
                for inv_line in refund_invoice.invoice_line_ids.filtered(lambda x: x.product_id == line.order_line_id.product_id):
                    inv_line.with_context(check_move_validity=False).write({'quantity': line.to_refund_qty, 'price_unit': line.refund_price_unit})
                    to_remove_lines -= inv_line
            if self.woocommerce_shipping_amount:
                shipping_product_id = order_id.order_line.filtered(lambda x: x.is_delivery).mapped('product_id')
                for inv_line in refund_invoice.invoice_line_ids.filtered(lambda x: x.product_id == shipping_product_id):
                    inv_line.with_context(check_move_validity=False).write({'quantity': 1, 'price_unit': self.woocommerce_shipping_amount})
                    to_remove_lines -= inv_line
            if to_remove_lines:
                to_remove_lines.with_context(check_move_validity=False).unlink()
            new_move.with_context(**{'check_move_validity': False})._sync_dynamic_lines({'records': new_move})
            refund_invoice.action_post()
            self.env['account.payment.register'].with_context(active_model='account.move', active_ids=refund_invoice.ids).create(
                {'journal_id': self.payment_journal_id.id})._create_payments()
            return reversal
        return True


class WoocommerceRefundPaymentLines(models.TransientModel):
    _name = "woocommerce.refund.payment.line"
    _description = "WooCommerce Refund Line"

    @api.depends('to_refund_qty')
    def _compute_amount(self):
        for line in self:
            price_subtotal = line.price_unit * line.to_refund_qty
            line.update({'price_subtotal': price_subtotal})

    product_id = fields.Many2one('product.product', string='Product', ondelete='cascade', required=True)
    order_line_id = fields.Many2one('sale.order.line', string="Order Line", ondelete='cascade')
    available_qty = fields.Float(string='Available Quantity', digits='Product Unit', required=True, default=1.0)
    to_refund_qty = fields.Float(string='To Refund Quantity', digits='Product Unit', required=True, default=1.0)
    price_unit = fields.Float('Original Unit Price', required=True, digits='Product Price', default=0.0)
    refund_price_unit = fields.Float('Refund Unit Price', required=True, digits='Product Price', default=0.0)
    price_subtotal = fields.Monetary(compute='_compute_amount', string='Subtotal', store=True)
    wizard_id = fields.Many2one('mk.cancel.order', 'Woocommerce Refund Wizard')
    currency_id = fields.Many2one('res.currency', 'Currency', readonly=True, related='wizard_id.currency_id', store=True)
    tax_refunded = fields.Float('Tax Refunded', digits='Product Price', default=0.0)
    total_refunded = fields.Float(string='Total Refunded', digits='Product Unit', default=0.0)
    to_refund_total = fields.Float('To Refund Total', digits='Product Price', default=0.0)

    @api.onchange('to_refund_qty')
    def onchange_refund_qty(self):
        res = {}
        if not self.available_qty and self.to_refund_qty:
            self.to_refund_qty = False
        if self.to_refund_qty > self.available_qty:
            self.to_refund_qty = self.available_qty
        return res
