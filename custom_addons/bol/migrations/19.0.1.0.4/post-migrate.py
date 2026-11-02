# -*- coding: utf-8 -*-
import logging

from odoo import api, SUPERUSER_ID

_logger = logging.getLogger("TeqStars:bol-post-migration-v1.1.0")

# v10 condition -> v11 (category, state)
CONDITION_MAP = {
    'NEW': ('NEW', None),
    'AS_NEW': ('SECONDHAND', 'AS_NEW'),
    'GOOD': ('SECONDHAND', 'GOOD'),
    'MODERATE': ('SECONDHAND', 'MODERATE'),
    'REASONABLE': ('SECONDHAND', 'REASONABLE'),
}

# v10 delivery code -> v11 (schedule, min, max, ultimateOrderTime)
DELIVERY_MAP = {
    '24uurs-12': ('BOL_DELIVERY_PROMISE', 0, 1, '12:00'),
    '24uurs-13': ('BOL_DELIVERY_PROMISE', 0, 1, '13:00'),
    '24uurs-14': ('BOL_DELIVERY_PROMISE', 0, 1, '14:00'),
    '24uurs-15': ('BOL_DELIVERY_PROMISE', 0, 1, '15:00'),
    '24uurs-16': ('BOL_DELIVERY_PROMISE', 0, 1, '16:00'),
    '24uurs-17': ('BOL_DELIVERY_PROMISE', 0, 1, '17:00'),
    '24uurs-18': ('BOL_DELIVERY_PROMISE', 0, 1, '18:00'),
    '24uurs-19': ('BOL_DELIVERY_PROMISE', 0, 1, '19:00'),
    '24uurs-20': ('BOL_DELIVERY_PROMISE', 0, 1, '20:00'),
    '24uurs-21': ('BOL_DELIVERY_PROMISE', 0, 1, '21:00'),
    '24uurs-22': ('BOL_DELIVERY_PROMISE', 0, 1, '22:00'),
    '24uurs-23': ('BOL_DELIVERY_PROMISE', 0, 1, '23:00'),
    '1-2d': ('BOL_DELIVERY_PROMISE', 1, 2, None),
    '2-3d': ('BOL_DELIVERY_PROMISE', 2, 3, None),
    '3-5d': ('BOL_DELIVERY_PROMISE', 3, 5, None),
    '4-8d': ('BOL_DELIVERY_PROMISE', 4, 8, None),
    '1-8d': ('BOL_DELIVERY_PROMISE', 1, 8, None),
    'VVB': ('SHIPPING_VIA_BOL', None, None, None),
    'MijnLeverbelofte': ('MY_DELIVERY_PROMISE', None, None, None),
}


def _refresh_crons(env):
    for instance in env['mk.instance'].search([('marketplace', '=', 'bol')]):
        try:
            instance.create_update_schedule_actions()
        except Exception as e:
            _logger.exception("Failed to update Bol.com cron jobs for instance %s: %s", instance.name, e)


def _backfill_conditions(cr):
    for old_value, (category, state) in CONDITION_MAP.items():
        cr.execute(
            """
            UPDATE mk_listing
               SET bol_condition_category = %s,
                   bol_condition_state = %s
             WHERE bol_condition_category IS NULL
               AND bol_condition = %s
            """,
            (category, state, old_value),
        )
    # Catch listings with null legacy condition too -> default NEW
    cr.execute(
        """
        UPDATE mk_listing
           SET bol_condition_category = 'NEW'
         WHERE bol_condition_category IS NULL
           AND mk_instance_id IN (SELECT id FROM mk_instance WHERE marketplace = 'bol')
        """
    )


def _backfill_fulfilment(cr):
    for code, (schedule, min_d, max_d, ult) in DELIVERY_MAP.items():
        cr.execute(
            """
            UPDATE mk_listing
               SET bol_fulfilment_schedule = %s,
                   bol_delivery_min_days = COALESCE(%s, 0),
                   bol_delivery_max_days = COALESCE(%s, 0),
                   bol_ultimate_order_time = %s
             WHERE bol_fulfilment_schedule IS NULL
               AND bol_delivery_code = %s
            """,
            (schedule, min_d, max_d, ult, code),
        )


def migrate(cr, version):
    """Post-migration for Bol Offers API v10 -> v11.

    Backfills run first (pure SQL, safe). Cron refresh runs last (ORM, may fail).
    All backfills are idempotent: only rows where the new column is still NULL
    get written.
    """
    _backfill_conditions(cr)
    _backfill_fulfilment(cr)
    env = api.Environment(cr, SUPERUSER_ID, {})
    _refresh_crons(env)