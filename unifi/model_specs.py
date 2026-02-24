"""Static UniFi device-type specifications used for NetBox enrichment."""

# UniFi device-type specs database.
# Keys: model strings as returned by UniFi APIs.
UNIFI_MODEL_SPECS = {
    # Switches
    "US8P60": {"part_number": "US-8-60W", "u_height": 0, "ports": [("Port {n}", "1000base-t", 8)], "poe_budget": 60},
    "US 8 60W": {"part_number": "US-8-60W", "u_height": 0, "ports": [("Port {n}", "1000base-t", 8)], "poe_budget": 60},
    "US8P150": {"part_number": "US-8-150W", "u_height": 0, "ports": [("Port {n}", "1000base-t", 8), ("SFP {n}", "1000base-x-sfp", 2)], "poe_budget": 150},
    "US 8 PoE 150W": {"part_number": "US-8-150W", "u_height": 0, "ports": [("Port {n}", "1000base-t", 8), ("SFP {n}", "1000base-x-sfp", 2)], "poe_budget": 150},
    "US68P": {"part_number": "USW-Lite-8-PoE", "u_height": 0, "ports": [("Port {n}", "1000base-t", 8)], "poe_budget": 52},
    "USLP8P": {"part_number": "USW-Lite-8-PoE", "u_height": 0, "ports": [("Port {n}", "1000base-t", 8)], "poe_budget": 52},
    "US16P150": {"part_number": "US-16-150W", "u_height": 1, "ports": [("Port {n}", "1000base-t", 16), ("SFP {n}", "1000base-x-sfp", 2)], "poe_budget": 150},
    "US 16 PoE 150W": {"part_number": "US-16-150W", "u_height": 1, "ports": [("Port {n}", "1000base-t", 16), ("SFP {n}", "1000base-x-sfp", 2)], "poe_budget": 150},
    "US24P250": {"part_number": "US-24-250W", "u_height": 1, "ports": [("Port {n}", "1000base-t", 24), ("SFP {n}", "1000base-x-sfp", 2)], "poe_budget": 250},
    "US 24 PoE 250W": {"part_number": "US-24-250W", "u_height": 1, "ports": [("Port {n}", "1000base-t", 24), ("SFP {n}", "1000base-x-sfp", 2)], "poe_budget": 250},
    "US48PRO": {"part_number": "USW-Pro-48-PoE", "u_height": 1, "ports": [("Port {n}", "1000base-t", 48), ("SFP+ {n}", "10gbase-x-sfpp", 4)], "poe_budget": 600},
    "USW Pro 48 PoE": {"part_number": "USW-Pro-48-PoE", "u_height": 1, "ports": [("Port {n}", "1000base-t", 48), ("SFP+ {n}", "10gbase-x-sfpp", 4)], "poe_budget": 600},
    "US6XG150": {"part_number": "US-XG-6POE", "u_height": 1, "ports": [("Port {n}", "10gbase-t", 4), ("SFP+ {n}", "10gbase-x-sfpp", 2)], "poe_budget": 150},
    "US XG 6 PoE": {"part_number": "US-XG-6POE", "u_height": 1, "ports": [("Port {n}", "10gbase-t", 4), ("SFP+ {n}", "10gbase-x-sfpp", 2)], "poe_budget": 150},
    "USMINI": {"part_number": "USW-Flex-Mini", "u_height": 0, "ports": [("Port {n}", "1000base-t", 5)], "poe_budget": 0},
    "USW Flex Mini": {"part_number": "USW-Flex-Mini", "u_height": 0, "ports": [("Port {n}", "1000base-t", 5)], "poe_budget": 0},
    "USXG": {"part_number": "USW-Aggregation", "u_height": 1, "ports": [("SFP+ {n}", "10gbase-x-sfpp", 8)], "poe_budget": 0},
    "USW Aggregation": {"part_number": "USW-Aggregation", "u_height": 1, "ports": [("SFP+ {n}", "10gbase-x-sfpp", 8)], "poe_budget": 0},
    "USAGGPRO": {"part_number": "USW-Pro-Aggregation", "u_height": 1, "ports": [("SFP+ {n}", "10gbase-x-sfpp", 28), ("SFP28 {n}", "25gbase-x-sfp28", 4)], "poe_budget": 0},
    "USW Pro Aggregation": {"part_number": "USW-Pro-Aggregation", "u_height": 1, "ports": [("SFP+ {n}", "10gbase-x-sfpp", 28), ("SFP28 {n}", "25gbase-x-sfp28", 4)], "poe_budget": 0},
    "USL8A": {"part_number": "USW-Lite-8", "u_height": 0, "ports": [("Port {n}", "1000base-t", 8)], "poe_budget": 0},
    "USPM16P": {"part_number": "USW-Pro-Max-16-PoE", "u_height": 0, "ports": [("Port {n}", "2.5gbase-t", 16), ("SFP+ {n}", "10gbase-x-sfpp", 2)], "poe_budget": 180},
    "USW Pro Max 16 PoE": {"part_number": "USW-Pro-Max-16-PoE", "u_height": 0, "ports": [("Port {n}", "2.5gbase-t", 16), ("SFP+ {n}", "10gbase-x-sfpp", 2)], "poe_budget": 180},
    "USPM24P": {"part_number": "USW-Pro-Max-24-PoE", "u_height": 1, "ports": [("Port {n}", "1000base-t", 16), ("Port {n+16}", "2.5gbase-t", 8), ("SFP+ {n}", "10gbase-x-sfpp", 2)], "poe_budget": 400},
    "USW Pro Max 24 PoE": {"part_number": "USW-Pro-Max-24-PoE", "u_height": 1, "ports": [("Port {n}", "1000base-t", 16), ("Port {n+16}", "2.5gbase-t", 8), ("SFP+ {n}", "10gbase-x-sfpp", 2)], "poe_budget": 400},
    "USW Pro 8 PoE": {"part_number": "USW-Pro-8-PoE", "u_height": 0, "ports": [("Port {n}", "1000base-t", 8), ("SFP+ {n}", "10gbase-x-sfpp", 2)], "poe_budget": 120},
    "USW Enterprise 8 PoE": {"part_number": "USW-Enterprise-8-PoE", "u_height": 0, "ports": [("Port {n}", "2.5gbase-t", 8), ("SFP+ {n}", "10gbase-x-sfpp", 2)], "poe_budget": 120},
    "US XG 16": {"part_number": "US-XG-16", "u_height": 1, "ports": [("Port {n}", "10gbase-t", 4), ("SFP+ {n}", "10gbase-x-sfpp", 12)], "poe_budget": 0},
    "USW Enterprise 24 PoE": {"part_number": "USW-Enterprise-24-PoE", "u_height": 1, "ports": [("Port {n}", "1000base-t", 12), ("Port {n+12}", "2.5gbase-t", 12), ("SFP+ {n}", "10gbase-x-sfpp", 2)], "poe_budget": 400},
    "US 24": {"part_number": "US-24", "u_height": 1, "ports": [("Port {n}", "1000base-t", 24), ("SFP {n}", "1000base-x-sfp", 2)], "poe_budget": 0},
    # Gateways
    "UXGPRO": {"part_number": "UXG-Pro", "u_height": 1, "ports": [("WAN 1", "1000base-t", 1), ("WAN 2", "1000base-t", 1), ("LAN 1", "10gbase-x-sfpp", 1), ("LAN 2", "10gbase-x-sfpp", 1)], "poe_budget": 0},
    "Gateway Pro": {"part_number": "UXG-Pro", "u_height": 1, "ports": [("WAN 1", "1000base-t", 1), ("WAN 2", "1000base-t", 1), ("LAN 1", "10gbase-x-sfpp", 1), ("LAN 2", "10gbase-x-sfpp", 1)], "poe_budget": 0},
    "UXG Fiber": {"part_number": "UXG-Fiber", "u_height": 1, "ports": [("Port 1", "2.5gbase-t", 1), ("Port 2", "2.5gbase-t", 1), ("Port 3", "2.5gbase-t", 1), ("Port 4 (PoE)", "2.5gbase-t", 1), ("Port 5 (WAN)", "10gbase-t", 1), ("SFP+ 6 (WAN)", "10gbase-x-sfpp", 1), ("SFP+ 7", "10gbase-x-sfpp", 1)], "poe_budget": 30},
    # Access points
    "U7LT": {"part_number": "UAP-AC-Lite", "u_height": 0, "ports": [("eth0", "1000base-t", 1)], "poe_budget": 0},
    "UAP-AC-Lite": {"part_number": "UAP-AC-Lite", "u_height": 0, "ports": [("eth0", "1000base-t", 1)], "poe_budget": 0},
    "U7MSH": {"part_number": "UAP-AC-M", "u_height": 0, "ports": [("eth0", "1000base-t", 1)], "poe_budget": 0},
    "AC Mesh": {"part_number": "UAP-AC-M", "u_height": 0, "ports": [("eth0", "1000base-t", 1)], "poe_budget": 0},
    "UAL6": {"part_number": "U6-LR", "u_height": 0, "ports": [("eth0", "1000base-t", 1)], "poe_budget": 0},
    "U6 Lite": {"part_number": "U6-Lite", "u_height": 0, "ports": [("eth0", "1000base-t", 1)], "poe_budget": 0},
    "UFLHD": {"part_number": "UAP-FlexHD", "u_height": 0, "ports": [("eth0", "1000base-t", 1)], "poe_budget": 0},
    "FlexHD": {"part_number": "UAP-FlexHD", "u_height": 0, "ports": [("eth0", "1000base-t", 1)], "poe_budget": 0},
    "U7 Pro": {"part_number": "U7-Pro", "u_height": 0, "ports": [("Port 1", "2.5gbase-t", 1)], "poe_budget": 0},
    # UISP / airMAX
    "Rocket Prism 5AC Gen2": {"part_number": "RP-5AC-Gen2", "u_height": 0, "ports": [("eth0", "1000base-t", 1)], "poe_budget": 0},
    "LiteAP AC": {"part_number": "LAP-120", "u_height": 0, "ports": [("eth0", "1000base-t", 1)], "poe_budget": 0},
    "LiteBeam 5AC Gen2": {"part_number": "LBE-5AC-Gen2", "u_height": 0, "ports": [("eth0", "1000base-t", 1)], "poe_budget": 0},
    "Nanostation 5AC": {"part_number": "NS-5AC", "u_height": 0, "ports": [("eth0", "1000base-t", 1)], "poe_budget": 0},
}

