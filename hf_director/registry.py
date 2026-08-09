from __future__ import annotations

SOURCES = {
    "automatic": {
        "name": "Automatic",
        "description": "Choose the first fresh available provider in the product priority list.",
        "licence": "Varies by selected provider",
        "homepage": "",
        "implemented": True,
    },
    "metservice": {
        "name": "MetService",
        "description": "Current New Zealand radar, forecast, satellite and marine products.",
        "licence": "Provider terms; redistribution permission to be confirmed",
        "homepage": "https://www.metservice.com/",
        "implemented": True,
    },
    "ecmwf": {
        "name": "ECMWF OpenCharts",
        "description": "Ready-rendered global forecast charts downloaded as PNG images.",
        "licence": "CC BY 4.0",
        "homepage": "https://charts.ecmwf.int/",
        "implemented": True,
    },
    "noaa": {
        "name": "NOAA/NWS",
        "description": "Public-domain marine text and Pacific radiofax products.",
        "licence": "US public domain unless a product says otherwise",
        "homepage": "https://ocean.weather.gov/",
        "implemented": False,
    },
}

# Logical products are stable router concepts. Provider product IDs remain
# metadata, so schedules do not depend on a vendor's API naming.
PRODUCTS = {
    "pressure-analysis": {
        "name": "Pressure analysis",
        "providers": ["ecmwf", "metservice", "noaa"],
        "description": "Mean sea-level pressure and synoptic wind context.",
    },
    "rain-forecast": {
        "name": "Rain forecast",
        "providers": ["ecmwf", "metservice"],
        "description": "Forecast precipitation; never labelled as observed radar.",
    },
    "rain-accumulation": {
        "name": "Accumulated rain",
        "providers": ["ecmwf"],
        "description": "Model accumulated precipitation.",
    },
    "rain-radar": {
        "name": "NZ rain radar",
        "providers": ["metservice"],
        "description": "Observed national radar composite.",
    },
    "surface-wind": {
        "name": "Surface wind",
        "providers": ["ecmwf", "noaa"],
        "description": "10 metre wind and mean sea-level pressure.",
    },
    "waves": {
        "name": "Significant waves",
        "providers": ["ecmwf", "noaa"],
        "description": "Significant wave height and mean direction.",
    },
    "swell": {
        "name": "Total swell",
        "providers": ["ecmwf"],
        "description": "Significant total swell height and mean direction.",
    },
    "satellite-infrared": {
        "name": "Tasman infrared satellite",
        "providers": ["metservice", "noaa"],
        "description": "Infrared satellite imagery for the Tasman region.",
    },
    "marine-high-seas-chart": {
        "name": "METAREA XIV chart",
        "providers": ["metservice"],
        "description": "High-seas responsibility chart.",
    },
    "marine-pacific": {
        "name": "Pacific high-seas bulletin",
        "providers": ["metservice", "noaa"],
        "description": "Pacific high-seas forecast text.",
    },
}


def provider_order(product: str, requested: str) -> list[str]:
    providers = PRODUCTS.get(product, {}).get("providers", [])
    if requested == "automatic":
        return list(providers)
    return [requested] if requested in providers else []

