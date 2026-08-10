import base64
import hashlib
import urllib.parse

import requests

from odoo import models, fields, api, _
from odoo.addons.base_marketplace.models.misc import guess_mimetype


class ListingImage(models.Model):
    _name = 'mk.listing.image'
    _description = 'Listing Image'
    _order = 'sequence, id'

    @api.depends('image')
    def get_image_hex(self):
        for record in self:
            record.image_hex = hashlib.md5(record.image).hexdigest() if record.image else False

    name = fields.Char('Name')
    sequence = fields.Integer(help='Sequence', index=True, default=10)
    image = fields.Binary('Image', attachment=True)
    url = fields.Char('Image URL')
    image_hex = fields.Char('Image Hex', compute='get_image_hex', store=True, help="Technical field to identify the duplicate image")
    mk_id = fields.Char("Marketplace Identification", copy=False)
    mk_listing_id = fields.Many2one('mk.listing', 'Related Listing', copy=False, ondelete='cascade')
    mk_instance_id = fields.Many2one('mk.instance', string='Marketplace', related='mk_listing_id.mk_instance_id', store=True)
    marketplace = fields.Selection(related="mk_instance_id.marketplace", string='Marketplace Name')
    mk_listing_item_ids = fields.Many2many('mk.listing.item', 'mk_listing_image_listing_rel', 'mk_listing_image_id', 'listing_item_id', string="Related Listing Item")

    @api.onchange('url')
    def _onchange_url(self):
        if not self.url:
            self.image = False
            return {}
        image_types = ["image/jpeg", "image/png", "image/tiff", "image/vnd.microsoft.icon", "image/x-icon", "image/vnd.djvu", "image/svg+xml", "image/gif"]
        message = "There seems to problem while fetching Image from URL"
        try:
            response = requests.get(self.url, stream=True, verify=False, timeout=10)
            if response.status_code == 200:
                if response.headers["Content-Type"] in image_types:
                    image = base64.b64encode(response.content)
                    self.image = image
                else:
                    message = "Invalid URL/Image type."
                    raise
        except:
            self.image = False
            warning = {}
            # Task: T7493 - Converted strings to f-strings.
            title = _(f"Warning for : {self.mk_listing_id.name}")
            warning['title'] = title
            warning['message'] = message
            return {'warning': warning}
        return {}

    @api.model_create_multi
    def create(self, vals):
        for i, image_dict in enumerate(vals):
            if 'image' in image_dict and not image_dict.get('image'):
                del vals[i]
        res = super(ListingImage, self).create(vals)
        base_url = self.env['ir.config_parameter'].sudo().get_param('web.base.url')
        for record in res:
            # Added below line to detect correct, extensions of the image.
            decode_binary = base64.b64decode(record.image, validate=True)
            mimetype = guess_mimetype(decode_binary, default='image/png')
            imgext = '.' + mimetype.split('/')[1]
            if imgext == '.svg+xml':
                imgext = '.svg'

            safe_name = urllib.parse.quote(record.name).replace('/', '-')
            # Task: T7493 - Converted strings to f-strings.
            url = f"{base_url}/marketplace/product/image/{self.env.cr.dbname}/{base64.urlsafe_b64encode(str(record.id).encode('utf-8')).decode('utf-8')}/{safe_name + imgext}"
            if record.mk_listing_item_ids and not record.mk_listing_id:
                record.write({'mk_listing_id': record.mk_listing_item_ids.mapped('mk_listing_id') and record.mk_listing_item_ids.mapped('mk_listing_id')[0].id or False})
            record.write({'url': url})
        return res
