from collections import defaultdict

from odoo import models


class SaleOrder(models.Model):
    _inherit = 'sale.order'

    def _discountable_cheapest(self, reward):
        self.ensure_one()
        program_id = self.env['ir.config_parameter'].sudo().get_param(
            'siveno_2plus1.program_id', default='0'
        )
        if not program_id.isdecimal() or reward.program_id.id != int(program_id):
            return super()._discountable_cheapest(reward)
        if not self.website_id or reward.program_id.website_id != self.website_id:
            return super()._discountable_cheapest(reward)

        domain = reward._get_discount_product_domain()
        candidates = []
        for line in self.order_line - self._get_no_effect_on_threshold_lines():
            qty = line.product_uom_qty
            if (line.reward_id or line.combo_item_id or qty <= 0
                    or qty != int(qty) or line.price_total <= 0
                    or not line.product_id.filtered_domain(domain)):
                continue
            candidates.append((line, int(qty), line.price_total / qty))
        remaining = sum(qty for _, qty, _ in candidates) // 3
        discountable = 0.0
        per_tax = defaultdict(float)
        for line, qty, _ in sorted(candidates, key=lambda item: item[2]):
            free_qty = min(qty, remaining)
            if not free_qty:
                continue
            discountable += line.price_total / line.product_uom_qty * free_qty
            taxes = line.tax_ids.filtered(lambda tax: tax.amount_type != 'fixed')
            per_tax[taxes] += line.price_unit * (1 - (line.discount or 0) / 100) * free_qty
            remaining -= free_qty
            if not remaining:
                break
        return (discountable, per_tax) if discountable else (False, False)

    def _update_programs_and_rewards(self):
        result = super()._update_programs_and_rewards()
        if self.env.context.get('siveno_promo_second_pass') or not self.website_id:
            return result
        params = self.env['ir.config_parameter'].sudo()
        promo_id = params.get_param('siveno_2plus1.program_id', default='0')
        coupon_id = params.get_param('siveno_2plus1.coupon_program_id', default='0')
        if not promo_id.isdecimal() or not coupon_id.isdecimal():
            return result
        promo = self.order_line.filtered(lambda line: line.reward_id.program_id.id == int(promo_id))
        coupon = self.order_line.filtered(lambda line: line.reward_id.program_id.id == int(coupon_id))
        if not promo or not coupon or promo[0].reward_id.program_id.website_id != self.website_id:
            return result
        base = max(self.order_line.filtered(lambda line: not line.reward_id).mapped('sequence'), default=10) + 1
        promo.filtered(lambda line: line.sequence != base).with_context(siveno_promo_second_pass=True).write({'sequence': base})
        coupon.filtered(lambda line: line.sequence != base + 1).with_context(siveno_promo_second_pass=True).write({'sequence': base + 1})
        self.with_context(siveno_promo_second_pass=True)._update_programs_and_rewards()
        return result
