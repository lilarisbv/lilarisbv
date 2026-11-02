19.0.1.1.4 (02-November-2026):

- Improved discount synchronization to correctly apply Bol.com promotional discounts (such as Buy 2, Get 1 Free) to order lines and invoices.
- Enhanced automatic retry handling to recover from temporary API errors more efficiently.
- Added bundle pricing validation to help prevent invalid pricing configurations from being submitted to Bol.com.
- Improved order status updates, cancellation, and return processing for better reliability and smoother operation.
- Added separate Sync from Bol.com and Sync to Bol.com buttons, making product synchronization easier and more convenient.

19.0.1.1.3 (21-October-2026):

- Support multi-database webhook URLs without a database filter.

19.0.1.1.2 (15-October-2026):

- Resolved an issue where deliveries with multiple tracking numbers sent incorrect shipment details to Bol.com.

19.0.1.1.1 (13-October-2026):

- Resolved an issue that caused some Bol.com orders to fail during import due to a savepoint error.

19.0.1.1.0 (08-October-2026):

- Improved Bol.com shipment handling with proper grouping of multiple order items shipped together under the same fulfilment tracking number, ensuring all related items are included in a single shipment.

19.0.1.0.9 (16-Sep-2026):

- Product export now checks the Bol.com catalog first. When Bol.com already knows the barcode the offer is exported as before; when it does not, the product is created in the Bol.com catalog and the offer is published automatically once Bol.com has processed it.

19.0.1.0.8 (11-Sep-2026):

- Improved Bol.com Marketplace User access rights with proper role-based permissions, allowing only the required Bol.com operations and configurations.
- Export Prices now also updates the price when a pricelist rule starts or ends on its date, not only when the rule is edited.
- Order status/tracking export is retried up to 3 times by the scheduled action; after that the delivery is marked as a marketplace exception for manual processing. The manual update button always works. Clearer messages when the Bol transporter or shipping method is missing.

19.0.1.0.7 (25-Jun-2026):

- Enforce Go Live safeguard for API requests on neutralized databases.

19.0.1.0.6 (08-May-2026):

-  Add real-time Webhook support for Order, Shipment, and Process Status events.

19.0.1.0.5 (08-May-2026):

-  Fix the error while exporting the product and add support for 14-digit barcodes.

19.0.1.0.4 (06-May-2026):

-  Updated the Bol.com Offers integration to use the latest supported API, improving reliability and compatibility with Bol.com. Improved offer configuration with clearer options for product condition, fulfilment, delivery promises, and order times.
-  Improved publishability information by showing the reasons why an offer cannot be published.
-  Improved product export by processing exports through background queue jobs, making large product exports more reliable and reducing the impact on the user interface.
-  Improved pricing updates so applicable pricelist changes are also reflected when a pricing rule becomes active or expires.
-  Added support and validation for 13-digit EAN numbers. Added a “Publish in Store” option to the product publishing wizard for better control over which products are published.
-  Added functionality to apply a pricelist across all products.Added a return action for improved return handling. Improved the “Name” field handling so it is no longer unnecessarily required.
-  Added dashboard functionality to provide a clearer overview of the Bol.com integration.
-  Refactored and improved the code for better stability, maintainability, and performance.

19.0.1.0.3 (25-February-2026):

-  Implement integration with Bol.com to fetch invoice requests and automate the upload of generated invoices directly to the Bol.com.

19.0.1.0.2 (5-Feb-2025):

- Fix export price method name.

19.0.1.0.1 (13-Jan-2025):

- Fixed issue for import shipment for open order.

19.0.1.0.0 (10-Nov-2025):

- Initial Release for Odoo v19.
