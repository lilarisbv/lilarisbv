import logging
from odoo import models, fields, api, _
from odoo.exceptions import ValidationError, AccessError

_logger = logging.getLogger("Teqstars:WooCommerce")

# WEBHOOK_EVENTS = [('customer.updated', 'Update Customer'),
#                   ('customer.deleted', 'Delete Customer'),
#                   ('order.created', 'Create Orders'),
#                   ('order.updated', 'Update Orders'),
#                   ('order.deleted', 'Delete Orders'),
#                   ('product.created', 'Create Product'),
#                   ('product.updated', 'Update Product'),
#                   ('product.deleted', 'Delete Product'),
#                   ("product.restored", "Restore Product")]

WEBHOOK_EVENTS = [('order.created', 'Create Orders'),
                  ('order.deleted', 'Delete Orders'),
                  ('product.created', 'Create Product'),
                  ('product.updated', 'Update Product'),
                  ('product.deleted', 'Delete Product')]

VALID_WEBHOOK_EVENT = ['order.created', 'order.deleted', 'product.created', 'product.updated', 'product.deleted']


class wooCommerceWebhook(models.Model):
    _name = "woocommerce.webhook.ts"
    _description = "woCommerce Webhook"

    name = fields.Char("Name", required=True)
    webhook_event = fields.Selection(WEBHOOK_EVENTS, "Webhook Event Type")
    webhook_id = fields.Char("Webhook ID", copy=False)
    active_webhook = fields.Boolean("Active", default=False)
    mk_instance_id = fields.Many2one('mk.instance', "Instance", ondelete='cascade')

    _woo_event_account_unique = models.Constraint("unique(webhook_event,mk_instance_id)", "You cannot create duplicate Webhook Events..")

    def fetch_all_webhook_from_woo(self, mk_instance_id):
        res, code = mk_instance_id and mk_instance_id.fetch_data_from_woocommerce('get', 'webhooks') or (False, 0)
        if code in [200, 201] and isinstance(res, list):
            for webhook in res:
                if webhook.get('delivery_url') != mk_instance_id.woo_webhook_url:
                    continue
                webhook_id = webhook.get('id')
                webhook_event = webhook.get('topic')
                if webhook_event in VALID_WEBHOOK_EVENT:
                    existing_webhook_id = self.search([('mk_instance_id', '=', mk_instance_id.id), ('webhook_id', '=', webhook_id)])
                    if not existing_webhook_id:
                        create_vals = {'name': dict(self._fields['webhook_event'].selection).get(webhook_event),
                                       'webhook_event': webhook_event,
                                       'webhook_id': webhook_id,
                                       'active_webhook': True,
                                       'mk_instance_id': mk_instance_id.id}
                        self.with_context(skip_create=True).create(create_vals)
        return True

    def create_webhook_in_woo(self, vals, mk_instance_id):
        if not mk_instance_id.woo_webhook_url.startswith('https://'):
            raise ValidationError("You can only create Webhook with secure URL (https).")
        data_vals = {"topic": self.webhook_event, "delivery_url": mk_instance_id.woo_webhook_url, "name": self.name, 'status': (self.active_webhook and 'active' or 'disabled')}
        res, code = mk_instance_id.fetch_data_from_woocommerce('post', 'webhooks', data=data_vals)
        if code in [200, 201]:
            self.webhook_id = res[0].get('id')
        else:
            message = f"{res[0].get('message')} - - {res[0].get('data')}"
            raise ValidationError(message)
        return vals

    @api.model_create_multi
    def create(self, vals):
        res = super(wooCommerceWebhook, self).create(vals)
        if not self.env.context.get('skip_create', False):
            for record in vals:
                mk_instance_id = self.env['mk.instance'].browse(record.get('mk_instance_id'))
                res.create_webhook_in_woo(record, mk_instance_id)
        return res

    def write(self, vals):
        res = super(wooCommerceWebhook, self).write(vals)
        for rec in self:
            if 'active_webhook' in vals or vals.get('name') or vals.get('webhook_event'):
                try:
                    data_vals = {"topic": rec.webhook_event, "delivery_url": rec.mk_instance_id.woo_webhook_url, "name": rec.name,
                                 'status': (rec.active_webhook and 'active' or 'disabled')}
                    result, code = rec.mk_instance_id.fetch_data_from_woocommerce('put', 'webhooks/{}'.format(rec.webhook_id), data=data_vals)
                except Exception as err:
                    _logger.error("wooCommerce WEBHOOK WRITE: Cannot found webhook in wooCommerce. ERROR: {}".format(err))
        return res

    def unlink(self):
        for record in self:
            if record.active_webhook:
                try:
                    result, code = record.mk_instance_id.fetch_data_from_woocommerce('delete', 'webhooks/{}'.format(record.webhook_id), params={'force': 'true'})
                except Exception as err:
                    _logger.error("wooCommerce WEBHOOK: Cannot found webhook in wooCommerce. ERROR: {}".format(err))
        res = super(wooCommerceWebhook, self).unlink()
        return res

    def woocommerce_delete_webhook(self):
        self.active_webhook = False
        return True
