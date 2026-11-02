import logging
from uuid import uuid4

from odoo import models, fields, api, _
from odoo.addons.base_marketplace.models.exceptions import MarketplaceException

BOL_WEBHOOK_ACCEPT_V11 = 'application/vnd.subscriptions.v11+json'

_logger = logging.getLogger("Teqstars:bol")

WEBHOOK_EVENTS = [
    ('ORDER', 'Order'),
    ('SHIPMENT', 'Shipment'),
    ('PROCESS_STATUS', 'Process Status'),
    # OFFER_FOR_SALE / OFFER_NOT_FOR_SALE: Bol only delivers these via GCP Pub/Sub or AWS SQS,
    # never WEBHOOK. See subscriptions-api/supported-event-types.html (Offers subscriptions).
    # ('OFFER_FOR_SALE', 'Offer For Sale'),
    # ('OFFER_NOT_FOR_SALE', 'Offer Not For Sale'),
]


class BolWebhook(models.Model):
    _name = 'bol.webhook.ts'
    _description = 'Bol Webhook'
    _rec_name = 'webhook_event'

    webhook_event = fields.Selection(WEBHOOK_EVENTS, "Webhook Event Type")
    webhook_id = fields.Char("Subscription ID", copy=False, help="The subscription id returned by Bol.com.")
    active_webhook = fields.Boolean("Active", default=False)
    mk_instance_id = fields.Many2one('mk.instance', "Instance", ondelete='cascade')

    _event_account_unique = models.Constraint(
        'unique(webhook_event, mk_instance_id)',
        "You cannot create duplicate Webhook Events.",
    )

    def _bol_subscription_payload(self, mk_instance_id, enabled=None):
        vals = {'resources': [self.webhook_event], 'eventDestination': mk_instance_id.bol_webhook_url, 'subscriptionType': 'WEBHOOK'}
        if enabled is not None:
            vals['enabled'] = enabled
        return vals

    def _bol_get_live_subscriptions(self, mk_instance_id):
        response = mk_instance_id.with_context(skip_raise=True)._send_bol_request('retailer/subscriptions', {}, method='GET', accept=BOL_WEBHOOK_ACCEPT_V11)
        if not isinstance(response, dict):
            _logger.error("BOL WEBHOOK: Invalid response while fetching subscriptions for instance %s: %s", mk_instance_id.display_name, response)
            return False
        subscriptions = response.get('subscriptions')
        if not isinstance(subscriptions, list):
            _logger.error("BOL WEBHOOK: Invalid subscriptions response for instance %s: %s", mk_instance_id.display_name, response)
            return False
        return subscriptions

    def fetch_all_webhook_from_bol(self, mk_instance_id):
        """Reconcile local webhook records with the live subscriptions on Bol.com."""
        if not mk_instance_id.bol_webhook_url:
            mk_instance_id.bol_webhook_url = mk_instance_id._get_bol_webhook_url()
        webhook_url = mk_instance_id.bol_webhook_url
        subscriptions = self._bol_get_live_subscriptions(mk_instance_id)
        if subscriptions is False:
            _logger.warning("BOL WEBHOOK: Subscription reconciliation skipped for instance %s because subscriptions could not be fetched.",mk_instance_id.display_name)
            return False
        seen_events = set()
        for subscription in subscriptions:
            if subscription.get('subscriptionType') != 'WEBHOOK' or subscription.get('eventDestination') != webhook_url:
                continue
            for resource in (subscription.get('resources') or []):
                if resource not in dict(WEBHOOK_EVENTS):
                    continue
                seen_events.add(resource)
                existing_webhook_id = self.search([('mk_instance_id', '=', mk_instance_id.id), ('webhook_event', '=', resource)], limit=1)
                vals = {'webhook_id': subscription.get('id'), 'active_webhook': bool(subscription.get('enabled', True))}
                if existing_webhook_id:
                    existing_webhook_id.with_context(skip_create=True).write(vals)
                else:
                    self.with_context(skip_create=True).create({**vals, 'webhook_event': resource, 'mk_instance_id': mk_instance_id.id})
        # Bol no longer has a subscription for this resource at all (deleted, not just disabled) -- clear it locally.
        stale = self.search([('mk_instance_id', '=', mk_instance_id.id), ('active_webhook', '=', True), ('webhook_event', 'not in', list(seen_events))])
        if stale:
            stale.with_context(skip_create=True).write({'active_webhook': False})
        return True

    def create_webhook_in_bol(self, mk_instance_id):
        self.ensure_one()
        if not mk_instance_id:
            raise MarketplaceException(_("You have to create/save the Instance first to activate a webhook."))
        if not mk_instance_id.bol_webhook_uuid:
            mk_instance_id.bol_webhook_uuid = str(uuid4())
        if not mk_instance_id.bol_webhook_url:
            mk_instance_id.bol_webhook_url = mk_instance_id._get_bol_webhook_url()
        if not mk_instance_id.bol_webhook_url.startswith('https://'):
            raise MarketplaceException(_("You can only create a webhook with a secure URL (https)."))
        subscriptions = self._bol_get_live_subscriptions(mk_instance_id)
        if subscriptions is False:
            _logger.warning("BOL WEBHOOK: Cannot create/re-enable %s for instance %s because subscriptions could not be fetched.", self.webhook_event, mk_instance_id.display_name)
            return False
        existing = next((s for s in subscriptions if s.get('subscriptionType') == 'WEBHOOK' and self.webhook_event in (s.get('resources') or []) and s.get('eventDestination') == mk_instance_id.bol_webhook_url), False)
        if existing:
            self.webhook_id = existing.get('id')
            # Bol.com can hand back a subscription that's switched off; re-enable it to match the Odoo toggle.
            if not existing.get('enabled', True):
                mk_instance_id._send_bol_request('retailer/subscriptions/%s' % existing.get('id'), {'enabled': True}, method='PATCH', accept=BOL_WEBHOOK_ACCEPT_V11)
            return existing
        response = mk_instance_id._send_bol_request('retailer/subscriptions', self._bol_subscription_payload(mk_instance_id), method='POST', accept=BOL_WEBHOOK_ACCEPT_V11)
        # Bol.com answers a v11 subscription POST with 204 and no body, so the new subscription is read back from the list.

        subscriptions = self._bol_get_live_subscriptions(mk_instance_id)
        if subscriptions is False:
            _logger.warning("BOL WEBHOOK: Subscription creation succeeded but the new subscription could not be fetched for instance %s.", mk_instance_id.display_name)
            return response
        match = next((s for s in subscriptions if s.get('subscriptionType') == 'WEBHOOK' and self.webhook_event in (s.get('resources') or []) and s.get('eventDestination') == mk_instance_id.bol_webhook_url), False)

        if match:
            self.webhook_id = match.get('id')
            # Bol.com can hand back a subscription it had switched off, and a disabled subscription sends nothing.
            if not match.get('enabled', True):
                mk_instance_id._send_bol_request('retailer/subscriptions/%s' % match.get('id'), {'enabled': True}, method='PATCH', accept=BOL_WEBHOOK_ACCEPT_V11)
            return match
        return response

    def bol_delete_webhook(self):
        for record in self.filtered('webhook_id'):
            mk_instance_id = record.mk_instance_id
            subscriptions = record._bol_get_live_subscriptions(mk_instance_id)
            if subscriptions is False:
                _logger.warning("BOL WEBHOOK: Cannot delete webhook %s for instance %s because subscriptions could not be fetched.", record.webhook_event, mk_instance_id.display_name)
                return False
            for subscription in subscriptions:
                resources = subscription.get('resources') or []
                if subscription.get('subscriptionType') == 'WEBHOOK' and subscription.get('eventDestination') == mk_instance_id.bol_webhook_url and record.webhook_event in resources:
                    try:
                        remaining_resources = [resource for resource in resources if resource != record.webhook_event]
                        if remaining_resources:
                            mk_instance_id._send_bol_request('retailer/subscriptions/{}'.format(subscription.get('id')), {'resources': remaining_resources}, method='PATCH', accept=BOL_WEBHOOK_ACCEPT_V11)
                        else:
                            mk_instance_id._send_bol_request('retailer/subscriptions/{}'.format(subscription.get('id')), {}, method='DELETE', accept=BOL_WEBHOOK_ACCEPT_V11)
                    except Exception as err:
                        _logger.error("BOL WEBHOOK DELETE: Cannot delete/update webhook %s in Bol. ERROR: %s", record.webhook_event, err)
                        return False
                    break
            record.with_context(skip_create=True).write({'active_webhook': False, 'webhook_id': False})
        return True

    @api.model_create_multi
    def create(self, vals_list):
        records = super(BolWebhook, self).create(vals_list)
        for record in records:
            if record.active_webhook and not self.env.context.get('skip_create', False):
                record.create_webhook_in_bol(record.mk_instance_id)
        return records

    def write(self, vals):
        # Persist first so the record (and any incoming webhook_id in vals) is committed,
        # then register on Bol -- mirrors create() ordering and avoids the pre-write
        # attribute writes in create_webhook_in_bol being clobbered by super().write.
        res = super(BolWebhook, self).write(vals)
        if vals.get('active_webhook') and not self.env.context.get('skip_create', False):
            for record in self:
                record.create_webhook_in_bol(record.mk_instance_id)
        if vals.get('active_webhook') is False and not self.env.context.get('skip_create', False):
            for record in self.filtered('webhook_id'):
                record.mk_instance_id._send_bol_request('retailer/subscriptions/%s' % record.webhook_id, {'enabled': False}, method='PATCH', accept=BOL_WEBHOOK_ACCEPT_V11)
        return res

    def unlink(self):
        for record in self.filtered('webhook_id'):
            if record.bol_delete_webhook() is False:
                return False
        return super(BolWebhook, self).unlink()
