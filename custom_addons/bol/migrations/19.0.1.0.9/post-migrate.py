# -*- coding: utf-8 -*-
"""19.0.1.0.9 — register the push subscriptions, and backfill placeholder listing names.

Two jobs, both one-off, each instance/listing handled on its own so one unreachable shop or listing can never
stop the module from upgrading:

* Existing instances are subscribed to the bol.com push notifications the connector needs, so the catalog result
  (published or declined) reaches them like it does for a newly confirmed instance.
* Listings imported before this release are named after their SKU or barcode, because up to 1.0.8 the importer used
  `reference or ean`. Each one still carrying that placeholder name gets the real bol.com catalog title now.
"""
import logging

from odoo import api, SUPERUSER_ID

_logger = logging.getLogger("TeqStars:bol")


def migrate(cr, version):
    if not version:
        return
    env = api.Environment(cr, SUPERUSER_ID, {})
    instance_ids = env['mk.instance'].search([('marketplace', '=', 'bol'), ('state', '=', 'confirmed')])
    for instance in instance_ids:
        try:
            instance._bol_register_default_webhooks()
            cr.commit()
        except Exception as error:
            cr.rollback()
            _logger.warning("BOL WEBHOOK: Could not register the subscriptions for %s | Error: %s", instance.name, error)

    listing_obj = env['mk.listing']
    for instance in instance_ids:
        listings = listing_obj.search([('mk_instance_id', '=', instance.id), ('mk_id', '!=', False)])
        for listing in listings.filtered(lambda listing: listing._bol_name_is_placeholder()):
            try:
                listing._bol_sync_name_from_catalog()
                cr.commit()
            except Exception as error:
                cr.rollback()
                _logger.warning("BOL NAME BACKFILL: Could not refresh name for %s | Error: %s", listing.name, error)
