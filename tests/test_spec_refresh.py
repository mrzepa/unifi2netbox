from __future__ import annotations

from unifi import spec_refresh


def _sample_store_product() -> dict:
    return {
        "id": "p1",
        "name": "USW-Pro-Max-16-PoE",
        "title": "Switch Pro Max 16 PoE",
        "shortTitle": "Pro Max 16 PoE",
        "slug": "usw-pro-max-16-poe",
        "technicalSpecification": {
            "sections": [
                {
                    "section": {"label": "Overview"},
                    "features": [
                        {"feature": {"label": "1 GbE RJ45"}, "value": "12"},
                        {"feature": {"label": "2.5 GbE RJ45"}, "value": "4"},
                        {"feature": {"label": "10G SFP+"}, "value": "2"},
                        {"feature": {"label": "Total PoE Availability"}, "value": "180W"},
                        {"feature": {"label": "Form Factor"}, "value": "Rack mount (1U), desktop"},
                    ],
                },
                {
                    "section": {"label": "Hardware"},
                    "features": [
                        {"feature": {"label": "Weight"}, "value": "2.1 kg (4.6 lb)"},
                    ],
                },
            ]
        },
    }


def test_build_bundle_from_devicetype_docs_normalizes_templates():
    docs = [
        {
            "manufacturer": "Ubiquiti",
            "model": "USW Pro Max 16 PoE",
            "part_number": "USW-Pro-Max-16-PoE",
            "slug": "ubiquiti-usw-pro-max-16-poe",
            "u_height": 1,
            "interfaces": [
                {"name": "Port 1", "type": "1000base-t", "poe_mode": "pse"},
                {"name": "SFP+ 1", "type": "10gbase-x-sfpp"},
            ],
            "console-ports": [{"name": "Console", "type": "rj-45"}],
            "power-ports": [{"name": "PS1", "type": "iec-60320-c14", "maximum_draw": 210}],
        }
    ]

    bundle = spec_refresh.build_bundle_from_devicetype_docs(docs)
    assert "USW-Pro-Max-16-PoE" in bundle["by_part"]
    assert "USW Pro Max 16 PoE" in bundle["by_model"]

    part = bundle["by_part"]["USW-Pro-Max-16-PoE"]
    assert part["interfaces"][0]["poe_mode"] == "pse"
    assert part["console_ports"][0]["name"] == "Console"
    assert part["power_ports"][0]["maximum_draw"] == 210


def test_extract_store_spec_maps_interfaces_and_metadata():
    spec = spec_refresh.extract_store_spec(_sample_store_product())

    assert spec is not None
    assert spec["part_number"] == "USW-Pro-Max-16-PoE"
    assert spec["model"] == "Switch Pro Max 16 PoE"
    assert spec["u_height"] == 1
    assert spec["poe_budget"] == 180
    assert spec["weight"] == 2.1
    assert spec["weight_unit"] == "kg"

    # 12 copper + 4 copper + 2 SFP+
    assert len(spec["interfaces"]) == 18
    assert spec["interfaces"][0] == {"name": "Port 1", "type": "1000base-t"}
    assert spec["interfaces"][11] == {"name": "Port 12", "type": "1000base-t"}
    assert spec["interfaces"][12] == {"name": "Port 13", "type": "2.5gbase-t"}
    assert spec["interfaces"][16] == {"name": "SFP+ 1", "type": "10gbase-x-sfpp"}


def test_augment_bundle_with_store_specs_adds_aliases(monkeypatch):
    bundle = {
        "by_part": {
            "USW-Pro-Max-16-PoE": {
                "manufacturer": "Ubiquiti",
                "model": "USPM16P",
                "part_number": "USW-Pro-Max-16-PoE",
                "interfaces": [{"name": "Port 1", "type": "1000base-t"}],
            }
        },
        "by_model": {
            "USPM16P": {
                "manufacturer": "Ubiquiti",
                "model": "USPM16P",
                "part_number": "USW-Pro-Max-16-PoE",
            }
        },
    }

    monkeypatch.setattr(spec_refresh, "_fetch_store_product", lambda slug, timeout=15: _sample_store_product())

    enriched = spec_refresh.augment_bundle_with_store_specs(bundle, timeout=1, max_workers=1)

    assert "Switch Pro Max 16 PoE" in enriched["by_model"]
    merged = enriched["by_part"]["USW-Pro-Max-16-PoE"]
    # Existing interface template source remains primary; store fills missing metadata.
    assert merged["interfaces"] == [{"name": "Port 1", "type": "1000base-t"}]
    assert merged["poe_budget"] == 180
    assert merged["u_height"] == 1
