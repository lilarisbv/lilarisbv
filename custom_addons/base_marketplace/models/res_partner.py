from odoo import fields, models, _
from odoo.fields import Domain

class ResPartnerMk(models.Model):
    _name = "res.partner.mk"
    _description = "Partner Marketplaces"

    partner_id = fields.Many2one("res.partner", string="Partner", required=True)
    mk_id = fields.Char("Marketplace Identification", copy=False, required=True)
    mk_instance_id = fields.Many2one('mk.instance', string="Instance", ondelete="cascade", copy=False, required=True)


class ResPartner(models.Model):
    _inherit = "res.partner"

    mk_instance_ids = fields.Many2many("mk.instance", "mk_instance_res_partner_rel", "marketplace_id", "partner_id", string="Marketplaces", copy=False)

    def _find_marketplace_partner(self, partner_vals, where_clause=None):
        if where_clause is None:
            where_clause = []
        if where_clause and partner_vals:
            domain = []
            for key in where_clause:
                value = partner_vals.get(key)
                if not value:
                    continue

                if (key in partner_vals):
                    if key in ['email', 'phone']:
                        email_phone_domain = Domain.OR([[(key, '=ilike', partner_vals.get(key))], [(key, '=', False)]])
                        domain = domain + email_phone_domain
                    elif isinstance(value, str):
                        domain = Domain.AND([domain, [(key, '=ilike', value)]])
                    else:
                        domain = Domain.AND([domain, [(key, '=', partner_vals.get(key))]])
            return domain and self.search(domain, limit=1) or False
        return False

    def get_marketplace_partners(self, partner_vals, mk_instance_id, type=False, parent_id=False):
        res_partner = False
        mk_log_id = self.env.context.get('mk_log_id', False)
        queue_line_id = self.env.context.get('queue_line_id', False)
        where_clause = ['email']
        if hasattr(self, '%s_get_find_partner_where_clause' % mk_instance_id.marketplace):
            where_clause = getattr(self, '%s_get_find_partner_where_clause' % mk_instance_id.marketplace)(type=type)
        if not res_partner:
            res_partner = self._find_marketplace_partner(partner_vals, where_clause)
            if res_partner:
                update_partner_vals = {}
                if not res_partner.email and partner_vals.get('email', False):
                    update_partner_vals.update({'email': partner_vals.get('email')})
                if not res_partner.phone and partner_vals.get('phone', False):
                    update_partner_vals.update({'phone': partner_vals.get('phone')})
                not parent_id and update_partner_vals.update({'mk_instance_ids': [(4, mk_instance_id.id)]})
                update_partner_vals and res_partner.write(update_partner_vals)
                log_message = _("EXISTING CUSTOMER FOUND: Found customer with same address, CUSTOMER NAME: %s (%s)") % (res_partner.name, res_partner.email)
                self.env['mk.log'].create_update_log(mk_log_id=mk_log_id,
                                                     mk_log_line_dict={'success': [{'log_message': log_message, 'queue_job_line_id': queue_line_id and queue_line_id.id or False}]})
                if not self.env.context.get('skip_queue_change_state', False):
                    queue_line_id and queue_line_id.write({'state': 'processed'})
        if not res_partner:
            not parent_id and partner_vals.update({'mk_instance_ids': [(4, mk_instance_id.id)]})
            if mk_instance_id.account_receivable_id:
                partner_vals.update({'property_account_receivable_id': mk_instance_id.account_receivable_id.id})
            if not partner_vals.get('email', False) and parent_id:
                partner_vals.update({'email': parent_id.email})
            res_partner = self.with_context(tracking_disable=True).create({'lang': mk_instance_id.lang or self.env.user.lang, 'parent_id': parent_id and parent_id.id or False,
                                                                           'property_product_pricelist': mk_instance_id.pricelist_id.id, **partner_vals})
            log_message = _("IMPORT CUSTOMER: Successfully created new customer with name: %s (%s)") % (res_partner.name, res_partner.email)
            self.env['mk.log'].create_update_log(mk_log_id=mk_log_id,
                                                 mk_log_line_dict={'success': [{'log_message': log_message, 'queue_job_line_id': queue_line_id and queue_line_id.id or False}]})
            if not self.env.context.get('skip_queue_change_state', False):
                queue_line_id and queue_line_id.write({'state': 'processed'})
        return res_partner
