19.0.1.1.0 (04-Aug-2026):

- Fix marketplace errors and warning messages during Odoo unit tests.

19.0.1.0.10 (25-June-2026)

- Updated the summary in manifest (10-July-2026).
- Block marketplace API operations on neutralized databases until Go Live is enabled (25-June-2026).

19.0.1.0.9 (08-June-2026)

- Fix issue where multiple onboarding panels were displayed and dark mode visibility issues occurred due to the OS theme instead of the Odoo theme.
- Use Marketplace Exception instead of Odoo Exception. 
- Migrate deprecated `read_group` calls with `_read_group` to align with the latest framework standards.

19.0.1.0.8 (01-June-2026)

- Improved dark mode support for the Marketplace Dashboard Overview.
- Added support for multiple languages in Marketplace.

19.0.1.0.7 (04-May-2026)

- Fix dashboard to filter records based on the user’s active company.
- Fix the access right issue from product template while not have marketplace access. 

19.0.1.0.6 (28-April-2026)

- Added quick onboarding panel for faster marketplace instance setup.
- Improved Kanban box UI for marketplace instances with refined layout and visual polish.

19.0.1.0.5(17-April-2026)

- Fix invoice not created for delivered products with single invoice enabled and add proper validation to invoice
  processing.

19.0.1.0.4(17-April-2026)

- Added a ``Marketplace Discount Amount`` field in the sales order line to handle discounts at a global/base marketplace
  level instead of defining them separately in each marketplace integration, improving code reusability and streamlining
  the process.

19.0.1.0.3(17-April-2026)

- Added an environment field to the instance, allowing selection between Production and Sandbox environments.

19.0.1.0.2(06-April-2026)

- Added functionality to update quantity and price from Shopify listing items.

19.0.1.0.1 (06-March-2026)

- Added marketplace-specific image handling when adding products to listings.
- Manages a Security Lead Time that automatically adjusts when an order is confirmed through the automation workflow.
- Fixed an issue where log lines remained even after the corresponding log was deleted.

19.0.1.0.0 (10-Nov-2025)

- Initial Release for Odoo v19. 

