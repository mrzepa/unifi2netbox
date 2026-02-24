# Troubleshooting

## Common Issues

### Connection Errors

**Problem**: `ConnectionError` or `Timeout` when connecting to UniFi controller.

**Solutions**:
- Verify the URL is correct and reachable from the Docker container/host
- For Integration API: use `/proxy/network/integration/v1` (or `/integration/v1`)
- For Legacy API: ensure port 8443 is accessible
- Increase `UNIFI_REQUEST_TIMEOUT` (default: 15 seconds)
- Check firewall rules

---

### Authentication Failures

**Problem**: `401 Unauthorized` or `403 Forbidden`.

**Solutions**:
- **Integration API**: verify `UNIFI_API_KEY` is valid and has read access
- `unifi.ui.com` cloud API keys are not interchangeable with local Integration API keys
- **Legacy API**: verify username/password, check if MFA is required (`UNIFI_MFA_SECRET`)
- Session cookies may expire — the tool handles re-authentication automatically
- Check if the API key has been revoked or rotated
- If local Integration API is unavailable, switch to base controller URL + `UNIFI_USERNAME`/`UNIFI_PASSWORD`

---

### NetBox API Errors

**Problem**: `RequestError` from pynetbox.

**Solutions**:
- Verify `NETBOX_TOKEN` has write access to DCIM, IPAM, and Tenancy
- Check that the tenant (`NETBOX_IMPORT_TENANT` or `NETBOX_TENANT`) exists in NetBox
- Ensure NetBox is running and accessible from the container
- Check NetBox logs for more detailed error messages

---

### Duplicate VRF/Role Creation

**Problem**: Multiple VRFs or roles with the same name.

**Solutions**:
- This is handled automatically with thread-safe locking
- If duplicates already exist, the tool picks the oldest (lowest ID)
- Clean up duplicates manually in NetBox if needed

---

### Missing Interface Templates

**Problem**: Device type has no interface templates after sync.

**Solutions**:
- Check if the device model is in `unifi/model_specs.py` (`UNIFI_MODEL_SPECS`) or the community database
- Run with `-v` flag to see debug output for template sync
- Templates are only synced once per device type per run

---

### Docker Container Crash Loop

**Problem**: Container keeps restarting.

**Solutions**:
- Check logs: `docker compose logs -f`
- Common cause: syntax errors in `.env` file
- Ensure all required variables are set (`UNIFI_URLS`, `NETBOX_URL`, `NETBOX_TOKEN`, `NETBOX_IMPORT_TENANT`/`NETBOX_TENANT`)
- Verify Python syntax: `python -m py_compile main.py`

---

### SSL Certificate Warnings

**Problem**: `InsecureRequestWarning` in logs.

**Solutions**:
- Keep `UNIFI_VERIFY_SSL=true` and `NETBOX_VERIFY_SSL=true` in production
- For self-signed lab setups, set one or both to `false` only if you accept the risk
- Install a trusted CA on the host/container when possible instead of disabling verification

---

### High Memory Usage

**Problem**: Container uses excessive memory.

**Solutions**:
- Reduce thread counts (`MAX_CONTROLLER_THREADS`, `MAX_SITE_THREADS`, `MAX_DEVICE_THREADS`)
- Lower `SYNC_INTERVAL` to avoid overlapping sync runs
- Check if the environment has an unusually large number of devices

---

### DHCP Static IP Assignment Issues

**Problem**: Devices keep getting new static IPs or IPs conflict.

**Solutions**:
- Verify `DHCP_RANGES` or auto-discovered ranges are correct
- Check that candidate IPs are not already in use (ping verification)
- Routers/gateways are exempt from DHCP-to-static conversion
- Review NetBox prefix configuration

---

## Debug Logging

Enable verbose logging for troubleshooting:

```bash
# Docker
docker compose up  # -v flag is default in docker-compose.yml

# Bare-metal
python main.py -v
```

Debug output includes:
- API request/response details
- Device processing steps
- Template comparison results
- Cache hit/miss information
- Thread pool activity

---

## Getting Help

1. Check the logs with `-v` flag
2. Review the [FAQ](faq.md)
3. Open an issue with:
   - Error message (full traceback)
   - Environment details (Docker/LXC/bare-metal)
   - NetBox and UniFi versions
   - Relevant `.env` settings (redact credentials)
