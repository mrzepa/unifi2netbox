# NetBox Cleanup Guide

> **Warning**: The cleanup feature **permanently deletes data** from NetBox. There is no undo. Always test in a staging environment first.

---

## Overview

The cleanup phase runs **after** each sync cycle and removes stale/orphaned data from NetBox. It is disabled by default.

## Enabling Cleanup

```bash
NETBOX_CLEANUP=true
CLEANUP_STALE_DAYS=30
```

## What Gets Deleted

### 1. Stale Devices

Devices present in NetBox but **not found** in any UniFi controller are considered stale.

- The grace period (`CLEANUP_STALE_DAYS`) prevents deletion of temporarily offline devices
- Devices must have been last updated more than N days ago
- When a device is deleted, its interfaces, cables, and IP addresses are also removed
- Default grace period: **30 days**

### 2. Garbage Interfaces

Interfaces with `?` in the name are deleted. These are typically created by API parsing errors or malformed device data.

### 3. Orphan IP Addresses

IP addresses assigned to the tenant but with no `assigned_object` (the interface they were assigned to no longer exists).

### 4. Orphan Cables

Cables where one or both terminations are missing (the connected interface/device was deleted).

### 5. Unused Device Types

Ubiquiti device types with `device_count == 0` are deleted. Before deletion, all device type specs are refreshed from the community + hardcoded database.

---

## Safety Guidelines

### Before Enabling

1. **Test in staging first** — clone your NetBox database and test cleanup there
2. **Start with a high grace period** — set `CLEANUP_STALE_DAYS=9999` to see what would be affected without actually deleting recent devices
3. **Review logs** — every deletion is logged at INFO level with the device name and serial
4. **Back up NetBox** — ensure you have a recent database backup

### Recommended Settings

| Scenario | `CLEANUP_STALE_DAYS` | Notes |
|---|---|---|
| Conservative | `90` | 3 months grace period |
| Standard | `30` | 1 month (default) |
| Aggressive | `7` | 1 week |
| Immediate | `0` | No grace period — use with caution |

### What is Never Deleted

The cleanup phase will **never** touch:

- Sites
- Tenants
- Manufacturers
- Device roles
- VRFs
- Prefixes
- VLANs/WLANs
- Custom fields or tags

---

## Monitoring

Watch for these log entries:

```
Cleanup: deleted stale device SW-OLD (AABBCCDDEEFF) from site HQ
Cleanup: deleted 3 garbage interface(s) from site HQ
Cleanup: deleted 2 orphan IP(s)
Cleanup: deleted 1 orphan cable(s) from site HQ
Cleanup: refreshed 42 device type(s), deleted 5 unused device type(s)
```

A second run should report 0 deletions if cleanup was complete.

---

## Disabling Cleanup

Set `NETBOX_CLEANUP=false` or remove the variable entirely (default is disabled).
