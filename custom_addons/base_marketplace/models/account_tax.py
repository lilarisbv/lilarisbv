from odoo import models, api
from odoo.tools.float_utils import float_round


class AccountTax(models.Model):
    _inherit = 'account.tax'

    @api.model
    def _get_tax_totals_summary(self, base_lines, currency, company, cash_rounding=None):
        """
        Set Marketplace Discount Line for Invoice total.
        """
        for base_line in base_lines:
            record = base_line.get('record', False)
            if record and getattr(record, '_name', None) == 'account.move.line':
                marketplace_discount_amount = len(record.sale_line_ids) == 1 and record.sale_line_ids.marketplace_discount_amount or False
                qty = record.sale_line_ids.product_uom_qty
                marketplace_discount_amount and qty and base_line.update({'marketplace_discount_amount': marketplace_discount_amount / qty})
        res = super()._get_tax_totals_summary(base_lines, currency, company, cash_rounding)
        return res

    def _prepare_base_line_for_taxes_computation(self, record, **kwargs):
        """
        Set Marketplace Discount Line for Invoice total.
        """
        res = super()._prepare_base_line_for_taxes_computation(record, **kwargs)
        record = res.get('record', False)
        if isinstance(record, models.Model) and record._name == 'account.move.line':
            if len(record.sale_line_ids) == 1 and record.sale_line_ids.marketplace_discount_amount:
                marketplace_discount_amount = record.sale_line_ids.marketplace_discount_amount
                product_uom_qty = record.sale_line_ids.product_uom_qty
                if product_uom_qty:
                    res['marketplace_discount_amount'] = marketplace_discount_amount / product_uom_qty
                else:
                    res['marketplace_discount_amount'] = 0.0
        return res

    @api.model
    def _add_tax_details_in_base_line(self, base_line, company, rounding_method=None):
        price_unit_after_discount = 0
        marketplace_discount = 0
        marketplace_discount_amount = base_line.get('marketplace_discount_amount', 0.0)

        if marketplace_discount_amount:
            price_unit = base_line.get('price_unit', 0.0)
            quantity = base_line.get('quantity', 0.0)
            discount = base_line.get('discount', 0.0)

            price = price_unit * quantity
            final_marketplace_discount_amount = marketplace_discount_amount * quantity
            if price != 0:
                marketplace_discount = ((final_marketplace_discount_amount / price) * 100)
                precision = self.env['decimal.precision'].precision_get('Discount')
                marketplace_discount = float_round(marketplace_discount, precision_digits=precision)

            if discount != marketplace_discount:
                price_unit_after_discount = base_line['price_unit'] * (1 - (base_line['discount'] / 100.0))

            elif marketplace_discount_amount:
                price_unit_after_discount = base_line['price_unit'] - marketplace_discount_amount

            base_line.update({'tax_ids': base_line.get('tax_ids').with_context(price_unit_after_marketplace_discount=price_unit_after_discount)})
            return super()._add_tax_details_in_base_line(base_line, company, rounding_method)
        else:
            return super()._add_tax_details_in_base_line(base_line, company, rounding_method)

    def _get_tax_details(
            self,
            price_unit,
            quantity,
            precision_rounding=0.01,
            rounding_method='round_per_line',
            product=None,
            product_uom=None,
            special_mode=False,
            manual_tax_amounts=None,
            filter_tax_function=None,
    ):
        if 'price_unit_after_marketplace_discount' in self.env.context:
            price_unit = self.env.context.get('price_unit_after_marketplace_discount')
        return super()._get_tax_details(price_unit, quantity, precision_rounding, rounding_method, product, product_uom, special_mode, manual_tax_amounts, filter_tax_function)
