import base64
import re

from odoo import http, SUPERUSER_ID, api, _
from odoo.http import request, Stream, STATIC_CACHE
from odoo.modules.registry import Registry
from odoo.tools.misc import file_path


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


class MarketplaceOnboardingPicker(http.Controller):
    """Serve connector ``static/description/icon.png`` via Odoo HTTP (same routing as the web client)."""

    @http.route("/base_marketplace/picker_icon/<string:module_name>", type="http", auth="user", readonly=True)
    def marketplace_picker_icon(self, module_name, **kwargs):
        if not re.match(r"^[a-z][a-z0-9_]*$", module_name or ""):
            return request.not_found()
        if not request.env["mk.instance"].browse().has_access("read"):
            return request.not_found()
        module_obj = request.env["ir.module.module"].sudo()
        if not module_obj.search([("name", "=", module_name), ("state", "=", "installed")], limit=1):
            return request.not_found()
        rel_icon = "%s/static/description/icon.png" % module_name
        try:
            fs_path = file_path(rel_icon, filter_ext=(".png",))
        except (FileNotFoundError, ValueError):
            try:
                fs_path = file_path("web/static/img/placeholder.png", filter_ext=(".png",))
            except (FileNotFoundError, ValueError):
                return request.not_found()
        return Stream.from_path(fs_path, public=True).get_response(
            max_age=STATIC_CACHE,
            content_security_policy=None,
        )
