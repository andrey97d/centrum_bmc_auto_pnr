import requests
import urllib3
import json
from typing import Optional

urllib3.disable_warnings()

# ResetType values for ComputerSystem.Reset
POWER_RESET_TYPES = [
    "On",
    "ForceOff",
    "GracefulShutdown",
    "GracefulRestart",
    "ForceRestart",
    "Nmi",
    "PushPowerButton",
]

# ResetType values for Manager.Reset (BMC)
BMC_RESET_TYPES = [
    "GracefulRestart",
    "ForceRestart",
]


class RedfishClient:
    def __init__(self, ip: str, username: str, password: str):
        self.host = f"https://{ip}"
        self.auth = (username, password)
        self.session = requests.Session()
        self.session.verify = False

    def _get(self, path: str) -> dict:
        url = f"{self.host}{path}"
        resp = self.session.get(url, auth=self.auth)
        resp.raise_for_status()
        return resp.json()

    def _post(self, path: str, payload: dict) -> requests.Response:
        url = f"{self.host}{path}"
        resp = self.session.post(url, auth=self.auth, json=payload)
        resp.raise_for_status()
        return resp

    # -------------------------------------------------------------------------
    # Power management
    # -------------------------------------------------------------------------

    def power_action(self, reset_type: str) -> dict:
        """Send a power action to the server.

        reset_type: On | ForceOff | GracefulShutdown | GracefulRestart |
                    ForceRestart | Nmi | PushPowerButton
        """
        if reset_type not in POWER_RESET_TYPES:
            raise ValueError(f"Invalid reset_type. Use one of: {POWER_RESET_TYPES}")
        resp = self._post(
            "/redfish/v1/Systems/Self/Actions/ComputerSystem.Reset",
            {"ResetType": reset_type},
        )
        return {"status_code": resp.status_code, "reset_type": reset_type}

    def power_on(self) -> dict:
        return self.power_action("On")

    def power_off(self) -> dict:
        """Graceful shutdown."""
        return self.power_action("GracefulShutdown")

    def power_force_off(self) -> dict:
        return self.power_action("ForceOff")

    def reboot(self) -> dict:
        """Graceful OS reboot."""
        return self.power_action("GracefulRestart")

    def force_reboot(self) -> dict:
        return self.power_action("ForceRestart")

    def get_power_state(self) -> dict:
        """Return current power state and last reset time."""
        data = self._get("/redfish/v1/Systems/Self")
        return {
            "PowerState": data.get("PowerState"),
            "LastResetTime": data.get("LastResetTime"),
            "Status": data.get("Status"),
            "BiosVersion": data.get("BiosVersion"),
        }

    # -------------------------------------------------------------------------
    # BMC reboot
    # -------------------------------------------------------------------------

    def reboot_bmc(self, force: bool = False) -> dict:
        """Reboot the BMC.

        force=True uses ForceRestart, otherwise GracefulRestart.
        """
        reset_type = "ForceRestart" if force else "GracefulRestart"
        resp = self._post(
            "/redfish/v1/Managers/Self/Actions/Manager.Reset",
            {"ResetType": reset_type},
        )
        return {"status_code": resp.status_code, "reset_type": reset_type}

    def get_bmc_info(self) -> dict:
        """Return BMC firmware version and status."""
        data = self._get("/redfish/v1/Managers/Self")
        return {
            "FirmwareVersion": data.get("FirmwareVersion"),
            "Status": data.get("Status"),
            "ManagerType": data.get("ManagerType"),
            "Model": data.get("Model"),
            "DateTime": data.get("DateTime"),
        }

    # -------------------------------------------------------------------------
    # DNS settings
    # -------------------------------------------------------------------------

    def get_dns(self, interface: str = "eth0") -> dict:
        """Return DNS nameservers configured on the given BMC interface.

        Reads NameServers and StaticNameServers from
        /redfish/v1/Managers/Self/EthernetInterfaces/{interface}
        """
        data = self._get(f"/redfish/v1/Managers/Self/EthernetInterfaces/{interface}")
        return {
            "Interface": interface,
            "NameServers": data.get("NameServers", []),
            "StaticNameServers": [s for s in data.get("StaticNameServers", []) if s],
            "HostName": data.get("HostName"),
            "FQDN": data.get("FQDN"),
            "DHCPv4Enabled": data.get("DHCPv4", {}).get("DHCPEnabled"),
        }

    def get_all_dns(self) -> list[dict]:
        """Return DNS info for all BMC Ethernet interfaces."""
        ifaces = self._get("/redfish/v1/Managers/Self/EthernetInterfaces")
        results = []
        for member in ifaces.get("Members", []):
            iface_id = member["@odata.id"].split("/")[-1]
            try:
                results.append(self.get_dns(iface_id))
            except requests.HTTPError:
                pass
        return results

    # -------------------------------------------------------------------------
    # NTP settings
    # -------------------------------------------------------------------------

    def get_ntp(self) -> dict:
        """Return NTP configuration from NetworkProtocol."""
        data = self._get("/redfish/v1/Managers/Self/NetworkProtocol")
        ntp = data.get("NTP", {})
        return {
            "NTPServers": ntp.get("NTPServers", []),
            "Port": ntp.get("Port"),
            "ProtocolEnabled": ntp.get("ProtocolEnabled"),
            "HostName": data.get("HostName"),
            "FQDN": data.get("FQDN"),
        }

    # -------------------------------------------------------------------------
    # BIOS settings
    # -------------------------------------------------------------------------

    def get_bios(self) -> dict:
        """Return current BIOS attributes."""
        return self._get("/redfish/v1/Systems/Self/Bios")

    def get_bios_attributes(self) -> dict:
        """Return only the Attributes section of BIOS."""
        data = self.get_bios()
        return {
            "BiosVersion": self.get_power_state().get("BiosVersion"),
            "Attributes": data.get("Attributes", {}),
        }

    # -------------------------------------------------------------------------
    # Firmware inventory
    # -------------------------------------------------------------------------

    def get_firmware_inventory(self) -> list[dict]:
        """Return all firmware components with version and state."""
        collection = self._get("/redfish/v1/UpdateService/FirmwareInventory")
        result = []
        for member in collection.get("Members", []):
            path = member["@odata.id"]
            try:
                item = self._get(path)
                oem_versions = (
                    item.get("AdditionalVersions", {}).get("Oem", {})
                )
                result.append({
                    "Id": item.get("Id"),
                    "Name": item.get("Name"),
                    "Version": item.get("Version"),
                    "State": item.get("State"),
                    "Updateable": item.get("Updateable"),
                    "OemInfo": oem_versions if oem_versions else None,
                })
            except requests.HTTPError:
                pass
        return result

    def get_firmware_summary(self) -> dict:
        """Return a concise summary: BMC active image, BIOS version, CPLD."""
        update_svc = self._get("/redfish/v1/UpdateService")
        bmc_cfg = update_svc.get("Oem", {}).get("BMC", {}).get(
            "DualImageConfigurations", {}
        )
        bios_active = update_svc.get("Oem", {}).get("BIOS", {}).get("ActiveImage")
        return {
            "BIOS_ActiveImage": bios_active,
            "BMC_ActiveImage": bmc_cfg.get("ActiveImage"),
            "BMC_BootImage": bmc_cfg.get("BootImage"),
            "BMC_Image1": {
                "Name": bmc_cfg.get("FirmwareImage1Name"),
                "Version": bmc_cfg.get("FirmwareImage1Version"),
            },
            "BMC_Image2": {
                "Name": bmc_cfg.get("FirmwareImage2Name"),
                "Version": bmc_cfg.get("FirmwareImage2Version"),
            },
        }


# ---------------------------------------------------------------------------
# CLI helper
# ---------------------------------------------------------------------------

def _build_client() -> RedfishClient:
    ip = input("IP address: ")
    user = input("Username: ")
    password = input("Password: ")
    return RedfishClient(ip, user, password)


def _print_json(data) -> None:
    print(json.dumps(data, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    import sys

    actions = {
        "power-state":       lambda c: c.get_power_state(),
        "power-on":          lambda c: c.power_on(),
        "power-off":         lambda c: c.power_off(),
        "force-off":         lambda c: c.power_force_off(),
        "reboot":            lambda c: c.reboot(),
        "force-reboot":      lambda c: c.force_reboot(),
        "reboot-bmc":        lambda c: c.reboot_bmc(),
        "force-reboot-bmc":  lambda c: c.reboot_bmc(force=True),
        "bmc-info":          lambda c: c.get_bmc_info(),
        "dns":               lambda c: c.get_dns(),
        "all-dns":           lambda c: c.get_all_dns(),
        "ntp":               lambda c: c.get_ntp(),
        "bios":              lambda c: c.get_bios_attributes(),
        "firmware":          lambda c: c.get_firmware_inventory(),
        "firmware-summary":  lambda c: c.get_firmware_summary(),
    }

    if len(sys.argv) < 2 or sys.argv[1] not in actions:
        print(f"Usage: python redfish_manage.py <action>")
        print(f"Actions: {', '.join(actions)}")
        sys.exit(1)

    client = _build_client()
    _print_json(actions[sys.argv[1]](client))
