import base64
import logging
import re

from odoo import http, SUPERUSER_ID, api, _
from odoo.http import request, Stream, STATIC_CACHE
from odoo.modules.registry import Registry
from odoo.tools.misc import file_path
from odoo.addons.base_marketplace.models.misc import resolve_marketplace_db_name

_logger = logging.getLogger("Teqstars:Base Marketplace")


def _safe_open_db_registry(db_segment, host=None):
    """
     Task: T9113 - Safely open the Odoo registry for a requested database.
         The URL segment is either the database name or its token. It is resolved to a database that exists in the ``db_list`` for the
         given host, which already applies the operator's configured ``db_filter``, before its registry is opened. If the segment is
         missing, unknown, not permitted, or an error occurs during validation, no registry is opened.
     Args:
         db_segment (str): The database name or database token taken from the URL.
         host (str, optional): The HTTP host used when applying the database filter and retrieving the database list.
     Returns:
         Registry or None: The registry of the resolved database when it is valid. Its ``db_name`` is the real database name.
    """
    if not db_segment:
        _logger.info("Database registry request rejected: no database segment provided (host=%s)", host)
        return None
    try:
        resolved_db_name = resolve_marketplace_db_name(db_segment, host=host)
    except Exception:
        _logger.info("Database registry request rejected: error validating database segment=%s for host=%s", db_segment, host)
        return None
    if not resolved_db_name:
        _logger.info("Database registry request rejected: segment=%s is not a database or database token permitted by db_filter for host=%s", db_segment, host)
        return None
    return Registry(resolved_db_name)

class MarketplaceProductImage(http.Controller):

    @http.route(['/marketplace/product/image/<string:db_name>/<string:encodedres>',
                 '/marketplace/product/image/<string:db_name>/<string:encodedres>/<string:filename>'], type='http', auth='public')
    def retrive_marketplace_image_from_url(self, db_name, encodedres='', filename='', **kwargs):
        try:
            if len(encodedres) and db_name:
                # Task: T9113 - Validate the requested database before accessing its registry.
                host = request.httprequest.environ.get('HTTP_HOST')
                db_registry = _safe_open_db_registry(db_name, host=host)
                if not db_registry:
                    _logger.warning("Marketplace image request rejected: db_name=%s not permitted for host=%s", db_name, host)
                    return request.not_found()
                # Task: T9113 - Store the real database name, never the token from the URL.
                if not request.session.db:
                    request.session.db = db_registry.db_name
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
