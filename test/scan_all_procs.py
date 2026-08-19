import subprocess
import winreg

def scan_processes():
    print("=================== 1. 当前所有 Python / PowerShell / CMD / Auto-run 进程扫描 ===================")
    out = subprocess.check_output('wmic process get processid,parentprocessid,name,commandline /format:csv', shell=True).decode('gbk', errors='ignore')
    rows = [r.strip().split(',') for r in out.splitlines() if r.strip()]
    proc_map = {r[-2]: r for r in rows if len(r) >= 4 and r[-2].isdigit()}

    targets = []
    for r in rows:
        if len(r) >= 4:
            cmd = r[1].lower()
            name = r[-3].lower()
            if any(k in cmd or k in name for k in ['python', 'powershell', 'cmd.exe', 'myclaw', 'tray', 'pytorch']):
                pid = r[-2]
                ppid = r[-4]
                parent = proc_map.get(ppid)
                parent_cmd = parent[1] if parent else "N/A"
                targets.append((pid, r[-3], r[1], ppid, parent_cmd))

    for pid, name, cmd, ppid, parent_cmd in targets:
        print(f"PID: {pid:5s} | Name: {name:15s} | Cmd: {cmd}")
        if parent_cmd != "N/A":
            print(f"        └─ [Parent PPID: {ppid}] Cmd: {parent_cmd}")
        print("-" * 80)

def scan_registry():
    print("\n=================== 2. Windows 注册表 Run / RunOnce 启动项扫描 ===================")
    locations = [
        (winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run"),
        (winreg.HKEY_LOCAL_MACHINE, r"Software\Microsoft\Windows\CurrentVersion\Run"),
    ]
    for root, subkey in locations:
        try:
            key = winreg.OpenKey(root, subkey)
            i = 0
            while True:
                name, value, _ = winreg.EnumValue(key, i)
                if 'python' in value.lower() or 'myclaw' in value.lower() or 'tray' in value.lower() or 'anaconda' in value.lower():
                    print(f"🚨 [REGISTRY MATCH] {subkey} -> {name}: {value}")
                else:
                    print(f"   [Registry] {name}: {value}")
                i += 1
        except OSError:
            pass

if __name__ == "__main__":
    scan_processes()
    scan_registry()
