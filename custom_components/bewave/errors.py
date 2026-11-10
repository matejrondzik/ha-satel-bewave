"""Human-readable texts for hub / device trouble codes (attribute ``problems``)."""
from __future__ import annotations

TEXTS = {
    "en": {
        "tamper": "Tamper (cover open)", "radio_jammed": "Radio jamming", "battery_low": "Low battery",
        "battery_disconnected": "Battery disconnected", "battery_replace": "Battery needs replacement",
        "ac_power": "Mains power failure", "ethernet_disconnected": "Ethernet cable disconnected",
        "wifi": "Wi-Fi problem", "sim1_missing": "No SIM 1", "sim1_pin": "SIM 1 PIN required",
        "sim1_puk": "SIM 1 PUK required", "sim2_missing": "No SIM 2", "sim2_pin": "SIM 2 PIN required",
        "sim2_puk": "SIM 2 PUK required", "sd_missing": "No SD card", "sd_low_memory": "SD card almost full",
        "sd_format": "SD card needs formatting", "sd_error": "SD card error",
        "reporting_trouble": "Monitoring station reporting trouble", "satel_cloud": "SATEL cloud unreachable",
        "invalid_dc": "Invalid DC voltage", "charger_fault": "Battery charger fault", "hub_overheat": "Hub overheating",
        "event_buffer_full": "Event buffer full", "aps_communication": "Power supply communication trouble",
        "aps_overload": "Power supply overload", "aps_overheat": "Power supply overheating",
        "vcc_output_overload": "VCC output overload",
        "connection_failure": "No communication", "connection_disconnected": "Disconnected",
        "sensor_dirty": "Sensor dirty", "output_overload": "Output overload", "aps_trouble": "Power supply trouble",
        "masking": "Detector masked", "fault": "Fault", "overheat": "Overheating", "sensor_fault": "Sensor fault",
        "smoke_chamber_dirty": "Smoke chamber dirty", "internal_fault": "Internal fault",
        "ac_sync": "Mains sync problem", "overload": "Overload", "no_load": "No load",
        "hardware_fault": "Hardware fault", "calibration_required": "Calibration required",
        "calibration_problem": "Calibration problem", "device_inverted": "Device mounted upside down",
        "valve_dc_start": "Valve motor start problem", "valve_over_range": "Valve out of range",
        "valve_button": "Head button problem", "valve_motor": "Valve motor connection problem",
        "valve_internal": "Head internal error", "valve_calibration": "Valve calibration problem",
        "sensor_detection": "Sensor detection error",
    },
    "sk": {
        "tamper": "Sabotáž (otvorený kryt)", "radio_jammed": "Rušenie rádia", "battery_low": "Slabá batéria",
        "battery_disconnected": "Odpojená batéria", "battery_replace": "Treba vymeniť batériu",
        "ac_power": "Výpadok napájania 230 V", "ethernet_disconnected": "Odpojený ethernet kábel",
        "wifi": "Problém s Wi-Fi", "sim1_missing": "Chýba SIM 1", "sim1_pin": "SIM 1 žiada PIN",
        "sim1_puk": "SIM 1 žiada PUK", "sim2_missing": "Chýba SIM 2", "sim2_pin": "SIM 2 žiada PIN",
        "sim2_puk": "SIM 2 žiada PUK", "sd_missing": "Chýba SD karta", "sd_low_memory": "SD karta je takmer plná",
        "sd_format": "SD kartu treba naformátovať", "sd_error": "Chyba SD karty",
        "reporting_trouble": "Problém s hlásením na PCO", "satel_cloud": "SATEL cloud nedostupný",
        "invalid_dc": "Nesprávne DC napätie", "charger_fault": "Porucha nabíjačky batérie",
        "hub_overheat": "Prehriata ústredňa", "event_buffer_full": "Plná pamäť udalostí",
        "aps_communication": "Problém komunikácie so zdrojom", "aps_overload": "Preťažený zdroj",
        "aps_overheat": "Prehriaty zdroj", "vcc_output_overload": "Preťažený výstup VCC",
        "connection_failure": "Bez komunikácie", "connection_disconnected": "Odpojené",
        "sensor_dirty": "Znečistený senzor", "output_overload": "Preťažený výstup", "aps_trouble": "Porucha zdroja",
        "masking": "Zamaskovaný detektor", "fault": "Porucha", "overheat": "Prehriatie",
        "sensor_fault": "Porucha senzora", "smoke_chamber_dirty": "Znečistená dymová komora",
        "internal_fault": "Vnútorná porucha", "ac_sync": "Problém synchronizácie so sieťou",
        "overload": "Preťaženie", "no_load": "Bez záťaže", "hardware_fault": "Hardvérová porucha",
        "calibration_required": "Treba kalibráciu", "calibration_problem": "Problém s kalibráciou",
        "device_inverted": "Zariadenie je otočené", "valve_dc_start": "Problém so štartom motora ventilu",
        "valve_over_range": "Ventil mimo rozsahu", "valve_button": "Problém s tlačidlom hlavice",
        "valve_motor": "Problém s pripojením motora ventilu", "valve_internal": "Vnútorná chyba hlavice",
        "valve_calibration": "Problém s kalibráciou ventilu", "sensor_detection": "Chyba detekcie senzora",
    },
}


def describe(keys: list[str], language: str | None) -> list[str]:
    t = TEXTS.get((language or "en").split("-")[0], TEXTS["en"])
    return [t.get(k, TEXTS["en"].get(k, k)) for k in keys]
