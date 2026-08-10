import html
import base64
import logging
import requests
from odoo import models, fields, api, _
from odoo.exceptions import ValidationError

_logger = logging.getLogger("Teqstars:WooCommerce")


class WooCommerceProductCategory(models.Model):
    _name = 'woo.product.category.ts'
    _inherit = ['image.mixin']
    _parent_name = "parent_id"
    _parent_store = True
    _rec_name = 'complete_name'
    _order = 'complete_name'
    _description = "Product Category"

    name = fields.Char("Name", required=True, copy=False)
    mk_id = fields.Char("Marketplace Identification", copy=False)
    mk_instance_id = fields.Many2one('mk.instance', "Instance", copy=False, ondelete='cascade', required=True)
    slug = fields.Char("Code", copy=False)
    complete_name = fields.Char('Complete Name', compute='_compute_complete_name', store=True, recursive=True)
    parent_id = fields.Many2one('woo.product.category.ts', 'Parent Category', index=True, ondelete='cascade')
    parent_path = fields.Char(index=True)
    exported_in_woocommerce = fields.Boolean("Exported in WooCommerce", default=False, copy=False)
    child_id = fields.One2many('woo.product.category.ts', 'parent_id', 'Child Categories')
    description = fields.Text('Short Description', translate=True)
    url = fields.Char('Image URL')
    display_type = fields.Selection([('default', 'Default'),
                                     ('products', 'Products'),
                                     ('subcategories', 'Subcategories'),
                                     ('both', 'Both')], default='default')

    @api.depends('name', 'parent_id.complete_name')
    def _compute_complete_name(self):
        for category in self:
            if category.parent_id:
                category.complete_name = '%s / %s' % (category.parent_id.complete_name, category.name)
            else:
                category.complete_name = category.name

    @api.constrains('parent_id')
    def _check_category_recursion(self):
        if self._has_cycle():
            raise ValidationError(_('You cannot create recursive categories.'))
        return True

    @api.model
    def name_create(self, name):
        record = self.create({'name': name})
        return record.id, record.display_name

    def html_to_plain_text(self, name):
        return html.unescape(name)

    def woocommerce_get_product_category(self, category_list, mk_instance_id):
        category_ids = self
        for category_dict in category_list:
            category_id = self.search([('mk_id', '=', category_dict.get('id')), ('mk_instance_id', '=', mk_instance_id.id)], limit=1)
            if category_id:
                if category_id not in category_ids:
                    category_ids += category_id
                continue
            self.import_woocommerce_categories(mk_instance_id)
            category_ids += self.woocommerce_get_product_category(category_list, mk_instance_id)
        return category_ids

    def get_woocommerce_category_image(self, url):
        image = False
        try:
            image_types = ["image/jpeg", "image/png", "image/tiff", "image/vnd.microsoft.icon", "image/x-icon", "image/vnd.djvu", "image/svg+xml", "image/gif"]
            headers = requests.utils.default_headers()
            headers.update({'User-Agent': 'Odoo Connection'})
            response = requests.get(url, stream=True, verify=False, timeout=10, headers=headers)
            if response.status_code == 200:
                if response.headers["Content-Type"] in image_types:
                    image = base64.b64encode(response.content)
        except Exception as e:
            _logger.error("Error While getting category Image. ERROR: {}".format(e))
        return image

    def prepare_vals_for_update_odoo_woocommerce_category(self, category_dict, mk_instance_id, parent_category_id=False):
        url = category_dict.get('image', {}) and category_dict.get('image', {}).get('src', '') or ''
        categ_vals = {'name': self.html_to_plain_text(category_dict.get('name')),
                      'mk_instance_id': mk_instance_id.id,
                      'mk_id': category_dict.get('id'),
                      'slug': category_dict.get('slug'),
                      'exported_in_woocommerce': True,
                      'description': category_dict.get('description'),
                      'display_type': category_dict.get('display'),
                      'parent_id': parent_category_id,
                      'url': url}
        if url:
            image = self.get_woocommerce_category_image(categ_vals.get('url'))
            image and categ_vals.update({'image_1920': image})
        return categ_vals

    def woocommerce_create_parent_category(self, mk_instance_id, category_dict, category_list, parent_category_id=False, odoo_parent_categ_id=False):
        parent_category_id = parent_category_id and parent_category_id.id or False
        odoo_parent_categ_id = odoo_parent_categ_id and odoo_parent_categ_id.id or False
        parent = category_dict.get('parent', 0)
        category_obj = self.env['product.category']
        category_name = self.html_to_plain_text(category_dict.get('name'))
        if parent:
            parent_dict = next((item for item in category_list if item['id'] == parent), None)
            parent_category_id, odoo_parent_categ_id = self.woocommerce_create_parent_category(mk_instance_id, parent_dict, category_list, parent_category_id=parent_category_id,
                                                                                               odoo_parent_categ_id=odoo_parent_categ_id)
            parent_category_id = parent_category_id and parent_category_id.id or False
        category_id = self.search([('mk_id', '=', category_dict.get('id', 0)), ('parent_id', '=', parent_category_id), ('mk_instance_id', '=', mk_instance_id.id)], limit=1)
        categ_vals = self.prepare_vals_for_update_odoo_woocommerce_category(category_dict, mk_instance_id, parent_category_id)
        if not category_id:
            if self.env.context.get('create_odoo_categories', False):
                odoo_category_id = category_obj.search([('name', '=', category_name), ('parent_id', '=', odoo_parent_categ_id)], limit=1)
                if not odoo_category_id:
                    category_obj.create({'name': category_name, 'parent_id': odoo_parent_categ_id})
            category_id = self.create(categ_vals)
        else:
            category_id.write(categ_vals)
        return category_id, odoo_parent_categ_id

    def woocommerce_check_category(self, category_dict, category_list, mk_instance_id):
        category_id, odoo_categ_id = self.woocommerce_create_parent_category(mk_instance_id, category_dict, category_list, parent_category_id=False)
        return category_id

    def import_woocommerce_categories(self, mk_instance_id):
        category_list, status_code = mk_instance_id.fetch_data_from_woocommerce('get', 'products/categories')
        if status_code not in [200, 201]:
            return False
        for category_dict in category_list:
            mk_id = category_dict.get('id')
            category_id = self.search([('mk_id', '=', mk_id), ('mk_instance_id', '=', mk_instance_id.id)])
            if category_id:
                continue
            self.woocommerce_check_category(category_dict, category_list, mk_instance_id)
        return True

    def search_woocommerce_category(self, odoo_categ_id, parent_categ_id, mk_instance_id):
        domain = [('name', '=', odoo_categ_id.name), ('mk_instance_id', '=', mk_instance_id.id)]
        parent_categ_id and domain.append(('parent_id', '=', parent_categ_id.id))
        return self.search(domain)

    def create_woocommerce_category(self, odoo_categ_id, parent_categ_id, mk_instance_id):
        categ_vals = {'name': odoo_categ_id.name, 'mk_instance_id': mk_instance_id.id}
        parent_categ_id and categ_vals.update({'parent_id': parent_categ_id.id})
        return self.create(categ_vals)

    def get_woocommerce_category(self, odoo_categ_id, mk_instance_id):
        parent_categ_id = False
        if odoo_categ_id.parent_id:
            parent_categ_id = self.get_woocommerce_category(odoo_categ_id.parent_id, mk_instance_id)
        woo_categ_id = self.search_woocommerce_category(odoo_categ_id, parent_categ_id, mk_instance_id)
        if not woo_categ_id:
            woo_categ_id = self.create_woocommerce_category(odoo_categ_id, parent_categ_id, mk_instance_id)
        return woo_categ_id

    def prepare_woocommerce_categories(self, woo_categ_id, categ_list=[]):
        if woo_categ_id.parent_id:
            self.prepare_woocommerce_categories(woo_categ_id.parent_id, categ_list)
            if woo_categ_id not in categ_list and not woo_categ_id.exported_in_woocommerce:
                categ_list.append(woo_categ_id)
        elif not woo_categ_id.exported_in_woocommerce:
            categ_list.append(woo_categ_id)
        return categ_list

    def prepare_woocommerce_category_vals_for_export(self):
        category_vals = {'name': self.name, 'display': self.display_type}
        if self.description:
            category_vals.update({'description': self.description})
        if self.slug:
            category_vals.update({'slug': self.slug})
        if self.parent_id.mk_id:
            category_vals.update({'parent': self.parent_id.mk_id})
        if self.image_1920:
            base_url = self.get_base_url()
            img_url = base_url + '/web/image/{model}/{res_id}/image_1920'.format(model=self._name, res_id=self.id)
            category_vals.update({'image': {'src': img_url}})
        return category_vals

    def export_woocommerce_category(self, category_vals):
        category_response, status_code = self.mk_instance_id.fetch_data_from_woocommerce('post', 'products/categories', data=category_vals)
        if status_code not in [200, 201]:
            queue_line_id = self.env.context.get('queue_line_id', False)
            mk_log_id = self.env.context.get('mk_log_id', False)
            log_message = "Error while export Product Category {}. ERROR: {}".format(self.name, category_response)
            self.env['mk.log'].create_update_log(mk_instance_id=self.mk_instance_id, mk_log_id=mk_log_id,
                                                 mk_log_line_dict={'error': [{'log_message': log_message, 'queue_job_line_id': queue_line_id and queue_line_id.id or False}]})
        else:
            category_vals = self.prepare_vals_for_update_odoo_woocommerce_category(category_response[0], self.mk_instance_id, parent_category_id=False)
            self.write(category_vals)
        return category_response

    def export_categories_to_woocommerce(self, woo_categ_ids):
        for woo_categ_id in woo_categ_ids:
            sorted_woo_categ_ids = self.prepare_woocommerce_categories(woo_categ_id, categ_list=[])
            for sorted_woo_categ_id in sorted_woo_categ_ids:
                category_vals = sorted_woo_categ_id.prepare_woocommerce_category_vals_for_export()
                sorted_woo_categ_id.export_woocommerce_category(category_vals)
        return woo_categ_ids
