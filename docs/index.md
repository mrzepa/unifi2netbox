---
title: unifi2netbox
---

# unifi2netbox

Production-focused synchronization from UniFi controllers to NetBox.

## Documentation

- [Configuration](./configuration.html)
- [Architecture](./architecture.html)
- [Troubleshooting](./troubleshooting.html)
- [FAQ](./faq.html)
- [Cleanup](./cleanup.html)
- [Device Specs](./device-specs.html)
- [QA Checklist](./qa-checklist.html)
- [Bug Report](./bug-report.html)

## Notes

- Primary sync direction is UniFi -> NetBox.
- DHCP-to-static conversion can update device IP settings in UniFi for affected devices.
- Local UniFi Integration API keys are supported; `unifi.ui.com` cloud API keys are not drop-in compatible.
