# -*- coding: utf-8 -*-

from odoo import api, SUPERUSER_ID
from odoo.tools.sql import column_exists, constraint_definition


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    if column_exists(env.cr, "mk_instance", "analytic_account_id"):
        cr.execute("""alter table mk_instance add column temp_analytic_id int4""")
        cr.execute("""update mk_instance set temp_analytic_id = analytic_account_id""")
    if column_exists(env.cr, "mk_instance", "account_receivable_id"):
        if constraint_definition(cr, "mk_instance", "mk_instance_account_receivable_id_fkey"):
            cr.execute("""ALTER TABLE mk_instance DROP CONSTRAINT mk_instance_account_receivable_id_fkey""")
        cr.execute("""
            ALTER TABLE mk_instance 
            ALTER COLUMN account_receivable_id TYPE jsonb 
            USING jsonb_build_object(company_id::text, account_receivable_id);
        """)
