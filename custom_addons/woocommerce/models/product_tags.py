from odoo import models, fields, _


class WooCommerceProductTags(models.Model):
    _name = 'woo.product.tags.ts'
    _description = "WooCommerce Product Tags"

    name = fields.Char("Name", required=True)
    slug = fields.Char("Code", copy=False)
    mk_id = fields.Char("Marketplace Identification", copy=False)
    description = fields.Text('Short Description', translate=True)
    exported_in_woocommerce = fields.Boolean("Exported in WooCommerce", default=False, copy=False)
    mk_instance_id = fields.Many2one('mk.instance', "Instance", copy=False, ondelete='cascade', required=True)

    def prepare_vals_for_update_odoo_woocommerce_tags(self, tag_dict, mk_instance_id):
        tags_vals = {'name': tag_dict.get('name'),
                     'slug': tag_dict.get('slug'),
                     'mk_id': tag_dict.get('id'),
                     'description': tag_dict.get('description'),
                     'exported_in_woocommerce': True,
                     'mk_instance_id': mk_instance_id.id}
        return tags_vals

    def import_woocommerce_tags(self, mk_instance_id):
        tags_list, status_code = mk_instance_id.fetch_data_from_woocommerce('get', 'products/tags')
        if status_code not in [200, 201]:
            return False
        for tag_dict in tags_list:
            name = tag_dict.get('name')
            tag_id = self.search([('name', '=', name), ('mk_instance_id', '=', mk_instance_id.id)])
            tags_vals = self.prepare_vals_for_update_odoo_woocommerce_tags(tag_dict, mk_instance_id)
            if not tag_id:
                self.create(tags_vals)
            else:
                tag_id.write(tags_vals)
        return True

    def woocommerce_get_product_tags(self, tag_list, mk_instance_id):
        tag_ids = []
        for tag_dict in tag_list:
            tag_id = self.search([('mk_id', '=', tag_dict.get('id')), ('mk_instance_id', '=', mk_instance_id.id)], limit=1)
            if tag_id:
                tag_ids.append(tag_id.id)
                continue
            self.import_woocommerce_tags(mk_instance_id)
            tag_ids = self.woocommerce_get_product_tags(tag_list, mk_instance_id)
        return list(set(tag_ids))

    def prepare_woocommerce_tag_vals_for_export(self):
        tag_vals = {'name': self.name}
        if self.description:
            tag_vals.update({'description': self.description})
        if self.slug:
            tag_vals.update({'slug': self.slug})
        return tag_vals

    def update_odoo_woocommerce_tags(self, tag_response, mk_instance_id):
        tag_batch_list = tag_response and tag_response[0].get('create', False)
        for tag_dict in tag_batch_list:
            tag_vals = self.prepare_vals_for_update_odoo_woocommerce_tags(tag_dict, mk_instance_id)
            tag_id = self.search([('name', '=', tag_vals.get('name')), ('mk_instance_id', '=', mk_instance_id.id)])
            tag_id and tag_id.write(tag_vals)
        return tag_batch_list

    def export_woocommerce_tags(self, tag_vals, mk_instance_id):
        tag_response, status_code = mk_instance_id.fetch_data_from_woocommerce('post', "products/tags/batch", data={"create": tag_vals})
        if status_code not in [200, 201]:
            queue_line_id = self.env.context.get('queue_line_id', False)
            mk_log_id = self.env.context.get('mk_log_id', False)
            log_message = "Error while export Product Tags {}. ERROR: {}".format(self.name, tag_response)
            self.env['mk.log'].create_update_log(mk_instance_id=mk_instance_id, mk_log_id=mk_log_id, operation_type='export',
                                                 mk_log_line_dict={'error': [{'log_message': log_message, 'queue_job_line_id': queue_line_id and queue_line_id.id or False}]})
        else:
            self.update_odoo_woocommerce_tags(tag_response, mk_instance_id)
        return tag_response

    def export_tags_to_woocommerce(self, woo_tags_ids, mk_instance_id):
        tag_batch_list = []
        for woo_tag_id in woo_tags_ids:
            not woo_tag_id.exported_in_woocommerce and tag_batch_list.append(woo_tag_id.prepare_woocommerce_tag_vals_for_export())
        tag_batch_list and self.export_woocommerce_tags(tag_batch_list, mk_instance_id)
        return woo_tags_ids
