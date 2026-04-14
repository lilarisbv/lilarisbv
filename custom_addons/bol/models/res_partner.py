from odoo import models, _


class Partner(models.Model):
    _inherit = "res.partner"

    def bol_get_find_partner_where_clause(self, type):
        if type in ['invoice', 'delivery']:
            where_clause = ['name', 'street_number', 'street_name', 'zip', 'country_id', 'email', 'parent_id']
        elif type == 'company':
            where_clause = ['name', 'vat']
        else:
            where_clause = ['name', 'street_number', 'street_name', 'zip', 'country_id', 'email']
        return where_clause

    def _extract_customer_data_from_bol_dict(self, customer_dict, type='contact', parent_id=False):
        mk_log_id = self.env.context.get('mk_log_id', False)
        queue_line_id = self.env.context.get('queue_line_id', False)
        name = "{} {}".format(customer_dict.get('firstName', ''), customer_dict.get('surname', ''))
        company_type = 'person'
        if type == 'company' and customer_dict.get('company', False):
            name = customer_dict.get('company')
            partner_vals = {
                'name': name.strip(),
                'vat': customer_dict.get('vatNumber'),
                'company_type': 'company',
                'type': 'contact'
            }
            return partner_vals
        if not name or not name.strip():
            log_message = _("IMPORT CUSTOMER FAILED : Name not found!")
            self.env['mk.log'].create_update_log(mk_log_id=mk_log_id, mk_log_line_dict={'error': [{'log_message': log_message, 'queue_job_line_id': queue_line_id and queue_line_id.id or False}]})
            queue_line_id and queue_line_id.write({'state': 'failed'})
            return False
        country = self.env['res.country'].search([('code', '=', customer_dict.get('countryCode'))], limit=1)
        partner_vals = {
            'name': name.strip(),
            'email': customer_dict.get('email') if customer_dict.get('email') is not None else '',
            'street_number': customer_dict.get('houseNumber') if not customer_dict.get('houseNumberExtension') else '{}{}'.format(customer_dict.get('houseNumber'), customer_dict.get('houseNumberExtension')),
            'street_name': customer_dict.get('streetName'), # not street because odoo will auto generate street field and if we provide street field then it will be override.
            'street2': '',
            'city': customer_dict.get('city'),
            'state_id': False,
            'country_id': country.id,
            'zip': customer_dict.get('zipCode'),
            'phone': customer_dict.get('deliveryPhoneNumber', ''),
            'vat': customer_dict.get('vatNumber'),
            'type': type,
            'comment': '',
            'company_type': company_type,
        }
        if parent_id and parent_id.is_company:
            partner_vals['parent_id'] = parent_id.id
        return partner_vals

    def create_update_bol_customers(self, customer_dict, mk_instance_id, type='contact', parent_id=False):
        mk_log_id = self.env.context.get('mk_log_id', False)
        queue_line_id = self.env.context.get('queue_line_id', False)
        partner = self.env['res.partner']
        try:
            partner_vals = self._extract_customer_data_from_bol_dict(customer_dict, type=type, parent_id=parent_id)
            if partner_vals:
                partner = self.get_marketplace_partners(partner_vals, mk_instance_id, type=type, parent_id=parent_id)
                if not partner.phone and customer_dict.get('deliveryPhoneNumber', False):
                    partner.write({'phone': customer_dict.get('deliveryPhoneNumber', '')})
                bol_category_id = self.env.ref('bol.res_partner_category_bol', raise_if_not_found=False)
                if bol_category_id:
                    partner.category_id = [(4, bol_category_id.id)]
        except Exception as err:
            log_message = _('IMPORT CUSTOMER: TECHNICAL EXCEPTION : {}'.format(err))
            self.env['mk.log'].create_update_log(mk_log_id=mk_log_id, mk_log_line_dict={'error': [{'log_message': log_message, 'queue_job_line_id': queue_line_id and queue_line_id.id or False}]})
            queue_line_id and queue_line_id.write({'state': 'failed'})
        return partner
