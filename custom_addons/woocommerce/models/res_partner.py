import pprint
from odoo import models,fields, tools


class Partner(models.Model):
    _inherit = "res.partner"

    def woocommerce_get_find_partner_where_clause(self, type):
        if type in ['invoice', 'delivery']:
            where_caluse = ['name', 'state_id', 'city', 'zip', 'street', 'street2', 'country_id', 'email', 'parent_id']
        else:
            where_caluse = ['name', 'state_id', 'city', 'zip', 'street', 'street2', 'country_id', 'email']
        return where_caluse

    def _extract_customer_data_from_woocommerce_dict(self, customer_dict, mk_instance_id, type='contact'):
        # default_address_dict = customer_dict.get('default_address') if customer_dict.get('default_address', False) else customer_dict
        if customer_dict.get('first_name', '') or customer_dict.get('last_name', ''):
            name = "{} {}".format(customer_dict.get('first_name', ''), customer_dict.get('last_name', ''))
        else:
            name = customer_dict.get('email') or customer_dict.get('email') or "Untitled"
        company_type = 'person'
        if mk_instance_id.is_create_company_contact_woo and type == 'contact':
            name = customer_dict.get('company', False) if customer_dict.get('company', False) else name
            company_type = 'company' if customer_dict.get('company', False) else 'person'
        country = self.env['res.country'].search([('code', '=', customer_dict.get('country'))], limit=1)
        state = self.env['res.country.state'].search([('country_id', '=', country.id), ('code', '=', customer_dict.get('state'))], limit=1)
        # if not state and customer_dict.get('postcode'):
        #     state = self.env['res.country.state'].with_context(tracking_disable=True).create(
        #         {'country_id': country.id, 'name': default_address_dict.get('province'), 'code': default_address_dict.get('province_code')})
        partner_vals = {
            'name': name,
            'email': customer_dict.get('email') or customer_dict.get('email'),
            'street': customer_dict.get('address_1'),
            'street2': customer_dict.get('address_2'),
            'city': customer_dict.get('city'),
            'state_id': state.id,
            'country_id': country.id,
            'zip': customer_dict.get('postcode'),
            'phone': customer_dict.get('phone') or customer_dict.get('phone'),
            'type': type,
            'comment': customer_dict.get('note', ''),
            'company_type': company_type
        }
        return partner_vals

    def create_update_woocommerce_customers(self, customer_dict, mk_instance_id, type='contact', parent_id=False):
        partner = self.env['res.partner']
        mk_log_id = self.env.context.get('mk_log_id', False)
        queue_line_id = self.env.context.get('queue_line_id', False)
        try:
            partner_vals = self._extract_customer_data_from_woocommerce_dict(customer_dict, mk_instance_id, type=type)
            if partner_vals:
                partner = self.get_marketplace_partners(partner_vals, mk_instance_id, type=type, parent_id=parent_id)
                woocommerce_category_id = self.env.ref('woocommerce.res_partner_category_woocommerce', raise_if_not_found=False)
                if woocommerce_category_id:
                    partner.category_id = [(4, woocommerce_category_id.id)]
                # meta field import with customers
                if 'meta_data' in customer_dict:
                    custom_field_mapping_ids = mk_instance_id.custom_field_mapping_ids.filtered(
                        lambda meta: meta.woo_operation == 'import_customer')

                    # order metafield
                    for custom_field_mapping in custom_field_mapping_ids.filtered(
                            lambda meta: meta.model_id.model == partner._name):
                        mk_instance_id.woocommerce_custom_field_mapping(custom_field_mapping,
                                                                        customer_dict.get('meta_data'), "import", partner)
        except Exception as err:
            log_message = 'IMPORT CUSTOMER: IMPORT CUSTOMER: TECHNICAL EXCEPTION : {}'.format(err)
            self.env['mk.log'].create_update_log(mk_log_id=mk_log_id, operation_type='import',
                                                 mk_log_line_dict={'error': [{'log_message': log_message, 'queue_job_line_id': queue_line_id and queue_line_id.id or False}]})
            queue_line_id and queue_line_id.write({'state': 'failed'})
        return partner

    def woocommerce_import_customers(self, instance_id):
        customer_list, status_code = instance_id.fetch_data_from_woocommerce('get', 'customers')
        if not customer_list or status_code not in [200, 201]:
            return False
        batch_size = instance_id.queue_batch_limit or 100
        for woo_customers in tools.split_every(batch_size, customer_list):
            queue_id = instance_id.action_create_queue(type='customer')
            for customer_dict in woo_customers:
                name = "%s %s" % (customer_dict.get('first_name') or '', customer_dict.get('last_name') or '')
                line_vals = {
                    'mk_id': customer_dict.get('id') or '',
                    'state': 'draft',
                    'name': name.strip(),
                    'data_to_process': pprint.pformat(customer_dict),
                    'mk_instance_id': instance_id.id,
                }
                queue_id.action_create_queue_lines(line_vals)
        instance_id.last_customer_import_date = fields.Datetime.now()
        return True
