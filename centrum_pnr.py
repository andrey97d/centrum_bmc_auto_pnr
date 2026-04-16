#!/usr/bin/env python3
"""
centrum_pnr.py — ПНР проверка серверов Centrum
Заполните ip.txt (один IP на строку) и запустите: python centrum_pnr.py
"""

import csv, socket, sys, time
from datetime import datetime
from getpass import getpass
from pathlib import Path

import requests, urllib3
urllib3.disable_warnings()

# ── Эталонные значения — менять здесь ──────────────────────────

TARGET_BMC_VERSION  = "2.08.81"
TARGET_BIOS_VERSION = "EGTSA.01A.26"
TARGET_CPLD_VERSION = "0x14"

TARGET_PCIE_FIRMWARE = {
    "00_2A_00_00": "14.0.505.31",   # Emulex LPe32002-AP FC HBA
    "00_2A_00_01": "14.0.505.31",
    "00_3D_00_00": "226.1.107.1",   # Broadcom BCM957504-N425G NIC
    "00_3D_00_01": "226.1.107.1",
    "00_3D_00_02": "226.1.107.1",
    "00_3D_00_03": "226.1.107.1",
}

TARGET_STORAGE_FIRMWARE = {
    "VMD_Device0": "YCV10100",
    "VMD_Device1": "YCV10100",
    "VMD_Device2": "YCV10100",
}

TARGET_MEMORY_SLOTS = [
    "DevType2_DIMM0",  "DevType2_DIMM1",  "DevType2_DIMM2",  "DevType2_DIMM3",
    "DevType2_DIMM4",  "DevType2_DIMM5",  "DevType2_DIMM6",  "DevType2_DIMM7",
    "DevType2_DIMM8",  "DevType2_DIMM9",  "DevType2_DIMM10", "DevType2_DIMM11",
    "DevType2_DIMM12", "DevType2_DIMM13", "DevType2_DIMM14", "DevType2_DIMM15",
]

# ── HTTP ────────────────────────────────────────────────────────

def get(ip, user, pwd, path):
    r = requests.get(f"https://{ip}{path}", auth=(user, pwd), verify=False, timeout=15)
    r.raise_for_status()
    return r.json()

def post(ip, user, pwd, path, body):
    requests.post(f"https://{ip}{path}", auth=(user, pwd),
                  json=body, verify=False, timeout=15).raise_for_status()

def patch(ip, user, pwd, path, body):
    requests.patch(f"https://{ip}{path}", auth=(user, pwd),
                   json=body, verify=False, timeout=15).raise_for_status()

# ── Главная функция — проверка одного сервера ───────────────────

def check_server(ip, user, pwd):
    r = {"ip": ip, "ok": True, "errors": []}

    # Ping (TCP 443)
    try:
        socket.create_connection((ip, 443), timeout=5).close()
    except OSError:
        r["ok"] = False
        r["errors"].append("Недоступен (порт 443)")
        return r

    # Auth
    try:
        get(ip, user, pwd, "/redfish/v1/")
    except Exception as e:
        r["ok"] = False
        r["errors"].append(f"Ошибка авторизации: {e}")
        return r

    # Базовая информация
    try:
        sys_d = get(ip, user, pwd, "/redfish/v1/Systems/Self")
        upd   = get(ip, user, pwd, "/redfish/v1/UpdateService")
        cpld  = get(ip, user, pwd, "/redfish/v1/UpdateService/FirmwareInventory/CPLD")
        bmc   = upd.get("Oem", {}).get("BMC", {}).get("DualImageConfigurations", {})

        r["model"]  = (sys_d.get("Model") or "N/A").strip()
        r["serial"] = sys_d.get("SerialNumber", "")
        r["power"]  = sys_d.get("PowerState", "")
        r["bios"]   = sys_d.get("BiosVersion", "")
        r["bmc1"]   = bmc.get("FirmwareImage1Version", "")
        r["bmc2"]   = bmc.get("FirmwareImage2Version", "")
        r["cpld"]   = cpld.get("Version", "")

        diffs = []
        if r["bmc1"] != TARGET_BMC_VERSION:  diffs.append(f"BMC1={r['bmc1']!r}")
        if r["bmc2"] != TARGET_BMC_VERSION:  diffs.append(f"BMC2={r['bmc2']!r}")
        if r["bios"] != TARGET_BIOS_VERSION: diffs.append(f"BIOS={r['bios']!r}")
        if r["cpld"] != TARGET_CPLD_VERSION: diffs.append(f"CPLD={r['cpld']!r}")
        r["versions_ok"]   = not diffs
        r["versions_diff"] = diffs
    except Exception as e:
        r["errors"].append(f"versions: {e}")

    # Прошивки PCIe
    try:
        col   = get(ip, user, pwd, "/redfish/v1/Chassis/Self/PCIeDevices")
        diffs = []
        for m in col.get("Members", []):
            dev_id = m["@odata.id"].split("/")[-1]
            if dev_id not in TARGET_PCIE_FIRMWARE:
                continue
            try:
                got = get(ip, user, pwd, m["@odata.id"]).get("FirmwareVersion", "")
            except Exception:
                got = "ERR"
            exp = TARGET_PCIE_FIRMWARE[dev_id]
            if got != exp:
                diffs.append(f"{dev_id}: {got!r} (эталон {exp!r})")
        r["pcie_ok"]   = not diffs
        r["pcie_diff"] = diffs
    except Exception as e:
        r["errors"].append(f"pcie: {e}")

    # Прошивки Storage
    try:
        col   = get(ip, user, pwd, "/redfish/v1/Systems/Self/Storage")
        diffs = []
        for unit_ref in col.get("Members", []):
            unit = get(ip, user, pwd, unit_ref["@odata.id"])
            for drv_ref in unit.get("Drives", []):
                drv_id = drv_ref["@odata.id"].split("/")[-1]
                if drv_id not in TARGET_STORAGE_FIRMWARE:
                    continue
                try:
                    got = get(ip, user, pwd, drv_ref["@odata.id"]).get("Revision", "")
                except Exception:
                    got = "ERR"
                exp = TARGET_STORAGE_FIRMWARE[drv_id]
                if got != exp:
                    diffs.append(f"{drv_id}: {got!r} (эталон {exp!r})")
        r["storage_ok"]   = not diffs
        r["storage_diff"] = diffs
    except Exception as e:
        r["errors"].append(f"storage: {e}")

    # Health
    try:
        chassis = get(ip, user, pwd, "/redfish/v1/Chassis/Self")
        system  = get(ip, user, pwd, "/redfish/v1/Systems/Self")
        diffs   = []
        for k, v in {
            "chassis": chassis.get("Status", {}).get("Health", ""),
            "rollup":  chassis.get("Status", {}).get("HealthRollup", ""),
            "system":  system.get("Status", {}).get("Health", ""),
        }.items():
            if v and v != "OK":
                diffs.append(f"{k}={v}")
        r["health_ok"]   = not diffs
        r["health_diff"] = diffs
    except Exception as e:
        r["errors"].append(f"health: {e}")

    # Память
    try:
        col     = get(ip, user, pwd, "/redfish/v1/Systems/Self/Memory")
        enabled = set()
        for m in col.get("Members", []):
            try:
                d = get(ip, user, pwd, m["@odata.id"])
                if d.get("Status", {}).get("State") == "Enabled":
                    enabled.add(m["@odata.id"].split("/")[-1])
            except Exception:
                pass
        expected = set(TARGET_MEMORY_SLOTS)
        diffs    = []
        if expected - enabled: diffs.append(f"нет: {', '.join(sorted(expected - enabled))}")
        if enabled - expected: diffs.append(f"лишние: {', '.join(sorted(enabled - expected))}")
        r["memory_ok"]   = not diffs
        r["memory_diff"] = diffs
    except Exception as e:
        r["errors"].append(f"memory: {e}")

    return r

# ── Вывод ───────────────────────────────────────────────────────

G = lambda s: f"\033[92m{s}\033[0m"
R = lambda s: f"\033[91m{s}\033[0m"
Y = lambda s: f"\033[93m{s}\033[0m"
B = lambda s: f"\033[1m{s}\033[0m"

def show(r):
    print(f"\n  {B(r['ip'])}")
    if r["errors"] and not r.get("model"):
        for e in r["errors"]: print(f"  {R('!')} {e}")
        return
    print(f"  model={r.get('model','')}  serial={r.get('serial','')}  power={r.get('power','')}")
    print(f"  bmc1={r.get('bmc1','')}  bmc2={r.get('bmc2','')}  bios={r.get('bios','')}  cpld={r.get('cpld','')}")
    for label, ok_key, diff_key in [
        ("Версии",  "versions_ok", "versions_diff"),
        ("PCIe",    "pcie_ok",     "pcie_diff"),
        ("Storage", "storage_ok",  "storage_diff"),
        ("Health",  "health_ok",   "health_diff"),
        ("Память",  "memory_ok",   "memory_diff"),
    ]:
        ok    = r.get(ok_key, None)
        diffs = r.get(diff_key, [])
        sym   = G("[OK  ]") if ok else R("[DIFF]")
        line  = f"  {sym} {label}"
        if diffs: line += "  " + Y("; ".join(diffs))
        print(line)
    if r["errors"]:
        print(f"  {R('Ошибки:')} {'; '.join(r['errors'])}")

# ── Main ────────────────────────────────────────────────────────

def main():
    print(B("\n  Centrum ПНР\n"))

    ips = [l.strip() for l in Path("ip.txt").read_text().splitlines()
           if l.strip() and not l.startswith("#")]
    if not ips:
        sys.exit("  ip.txt пустой или не найден")
    print(f"  IP: {', '.join(ips)}\n")

    user = input("  Логин : ").strip()
    pwd  = getpass("  Пароль: ")
    print()

    # Фаза 1 — доступность
    print(B("  ФАЗА 1 — Доступность"))
    reachable = []
    for ip in ips:
        try:
            socket.create_connection((ip, 443), timeout=5).close()
            ping = True
        except OSError:
            ping = False
        try:
            get(ip, user, pwd, "/redfish/v1/") if ping else (_ for _ in ()).throw(Exception())
            auth = True
        except Exception:
            auth = False
        print(f"  {ip:<18}  ping:{G('OK') if ping else R('NO')}  auth:{G('OK') if auth else R('NO')}")
        if ping and auth:
            reachable.append(ip)
    print(f"\n  Доступно: {len(reachable)}/{len(ips)}\n")
    if not reachable:
        sys.exit(R("  Нет доступных серверов."))

    # Фаза 2 — перезагрузка и включение
    print(B("  ФАЗА 2 — Перезагрузка и включение"))
    for ip in reachable:
        try:
            post(ip, user, pwd, "/redfish/v1/Managers/Self/Actions/Manager.Reset",
                 {"ResetType": "ForceRestart"})
            print(f"  {ip}  BMC reboot: {G('OK')}")
        except Exception as e:
            print(f"  {ip}  BMC reboot: {R(str(e))}")

    print("  Ожидание BMC (30 сек)...")
    time.sleep(30)

    for ip in reachable:
        try:
            patch(ip, user, pwd, "/redfish/v1/Systems/Self",
                  {"Boot": {"BootSourceOverrideEnabled": "Once",
                            "BootSourceOverrideTarget": "UefiShell"}})
            print(f"  {ip}  OnceBootUEFI: {G('OK')}")
        except Exception as e:
            print(f"  {ip}  OnceBootUEFI: {R(str(e))}")

    for ip in reachable:
        try:
            post(ip, user, pwd, "/redfish/v1/Systems/Self/Actions/ComputerSystem.Reset",
                 {"ResetType": "On"})
            print(f"  {ip}  Power On: {G('OK')}")
        except Exception as e:
            print(f"  {ip}  Power On: {R(str(e))}")

    print("  Ожидание загрузки (5 мин)...")
    time.sleep(300)

    # Фаза 3 — аудит
    print(B("\n  ФАЗА 3 — Проверка данных"))
    results = []
    for ip in reachable:
        r = check_server(ip, user, pwd)
        show(r)
        results.append(r)

    # Итог
    print(B("\n  ИТОГ"))
    cols = ["versions_ok", "pcie_ok", "storage_ok", "health_ok", "memory_ok"]
    print(f"  {'IP':<18}  Версии   PCIe     Storage  Health   Память")
    print("  " + "─" * 65)
    for r in results:
        row = "  ".join(G(f"{'OK':<7}") if r.get(k) else R(f"{'DIFF':<7}") for k in cols)
        print(f"  {r['ip']:<18}  {row}")

    # CSV
    print()
    profile = ""
    if input("  Продолжить настройку BMC? [y/n]: ").strip().lower() == "y":
        choice  = input("  Профиль — 1:PES  2:Lnx  3:MAAS  4:Custom: ").strip()
        profile = {"1": "PES", "2": "Lnx", "3": "MAAS", "4": "Custom"}.get(choice, "Custom")

    fname = f"pnr_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
    with open(fname, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["ip", "model", "serial", "power", "bios", "bmc1", "bmc2", "cpld",
                    "versions", "pcie", "storage", "health", "memory", "profile", "errors"])
        for r in results:
            w.writerow([
                r.get("ip"), r.get("model",""), r.get("serial",""),
                r.get("power",""), r.get("bios",""), r.get("bmc1",""),
                r.get("bmc2",""), r.get("cpld",""),
                "OK" if r.get("versions_ok") else "; ".join(r.get("versions_diff",[])),
                "OK" if r.get("pcie_ok")     else "; ".join(r.get("pcie_diff",[])),
                "OK" if r.get("storage_ok")  else "; ".join(r.get("storage_diff",[])),
                "OK" if r.get("health_ok")   else "; ".join(r.get("health_diff",[])),
                "OK" if r.get("memory_ok")   else "; ".join(r.get("memory_diff",[])),
                profile, "; ".join(r.get("errors",[])),
            ])
    print(f"  {G('Отчёт:')} {fname}\n")


if __name__ == "__main__":
    main()
