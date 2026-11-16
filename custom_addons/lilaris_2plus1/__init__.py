from collections import defaultdict

from odoo import models


class SaleOrder(models.Model):
    _inherit = 'sale.order'

    def _discountable_cheapest(self, reward):
        self.ensure_one()
        program_id = self.env['ir.config_parameter'].sudo().get_param(
            'lilaris_2plus1.program_id', default='0'
        )
        if (not program_id.isdecimal() or reward.program_id.id != int(program_id)
                or not self.website_id or reward.program_id.website_id != self.website_id):
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


    def _lilaris_recompute_coupons(self):
        self.ensure_one()
        program_id = self.env['ir.config_parameter'].sudo().get_param(
            'lilaris_2plus1.program_id', default='0'
        )
        if not program_id.isdecimal() or not self.website_id:
            return
        promo = self.order_line.filtered(
            lambda line: line.reward_id.program_id.id == int(program_id)
            and line.reward_id.program_id.website_id == self.website_id
        )
        if not promo:
            return
        coupon_lines = self.order_line.filtered(
            lambda line: line.reward_id.program_id.website_id == self.website_id
            and line.reward_id.program_id.trigger == 'with_code'
            and line.reward_id.reward_type == 'discount'
            and line.reward_id.discount_applicability == 'order'
        )
        for reward in coupon_lines.mapped('reward_id'):
            for coupon in coupon_lines.filtered(lambda line: line.reward_id == reward).mapped('coupon_id'):
                lines = coupon_lines.filtered(lambda line: line.reward_id == reward and line.coupon_id == coupon)
                lines._reset_loyalty()
                values = self._get_reward_line_values(reward, coupon)
                self._write_vals_from_reward_vals(values, lines)

    def _update_programs_and_rewards(self):
        result = super()._update_programs_and_rewards()
        self._lilaris_recompute_coupons()
        return result

    def _apply_program_reward(self, reward, coupon, **kwargs):
        result = super()._apply_program_reward(reward, coupon, **kwargs)
        self._lilaris_recompute_coupons()
        return result
