import base64
from odoo import http, SUPERUSER_ID, api, _
from odoo.http import request
from odoo.modules.registry import Registry


class MarketplaceProductImage(http.Controller):

    @http.route(['/marketplace/product/image/<string:db_name>/<string:encodedres>',
                 '/marketplace/product/image/<string:db_name>/<string:encodedres>/<string:filename>'], type='http', auth='public')
    def retrive_marketplace_image_from_url(self, db_name, encodedres='', filename='', **kwargs):
        try:
            if len(encodedres) and db_name:
                db_registry = Registry(db_name)
                if db_name and not request.session.db:
                    request.session.db = db_name
                with db_registry.cursor() as cr:
                    env = api.Environment(cr, SUPERUSER_ID, {})
                    decode_data = base64.urlsafe_b64decode(encodedres)
                    res_id = str(decode_data, "utf-8")
                    record = env['mk.listing.image'].sudo().browse(int(res_id))
                    stream = request.env['ir.binary']._get_image_stream_from(record,field_name='image',filename=filename).get_response()
                    return stream
        except Exception:
            return request.not_found()
        return request.not_found()
