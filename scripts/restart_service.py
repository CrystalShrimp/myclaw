import os
import subprocess
import time
import urllib.request
import json
from pathlib import Path

def stop_existing_processes(current_pid: int) -> None:
    """第一性原理全量清杀：彻底清理 myclaw 关联的所有 4 层 Python 进程与端口占用。"""
    print("Stopping existing MyClaw processes and child workers...")
    root_dir = str(Path(__file__).resolve().parent.parent).lower()

    try:
        # 1. 用 CIM（比 WMI 快得多）枚举所有 python/pythonw 进程，按命令行/可执行路径过滤
        ps_cmd = (
            'powershell -NoProfile -Command "'
            'Get-CimInstance Win32_Process | '
            'Where-Object { $_.Name -match \'python\' } | '
            'Select-Object ProcessId, ExecutablePath, CommandLine | ConvertTo-Json -Depth 3"'
        )
        res = subprocess.run(ps_cmd, shell=True, capture_output=True, text=True)
        if res.returncode == 0 and res.stdout.strip():
            try:
                items = json.loads(res.stdout)
                if isinstance(items, dict):
                    items = [items]
                for item in items:
                    pid = item.get("ProcessId")
                    cmdline = (item.get("CommandLine") or "").lower()
                    exepath = (item.get("ExecutablePath") or "").lower()

                    if pid and int(pid) != current_pid:
                        # 只要进程命令行或可执行路径与本项目有关，全部予以终止
                        if "app.main" in cmdline or "tray.pyw" in cmdline or root_dir in cmdline or root_dir in exepath:
                            subprocess.run(f"taskkill /F /T /PID {pid}", shell=True, capture_output=True)
                            exetag = " (anaconda)" if "anaconda" in exepath else ""
                            print(f"Killed MyClaw worker process tree {pid}{exetag}")
            except Exception as parse_err:
                print("JSON parse warning:", parse_err)
    except Exception as e:
        print("Error stopping processes:", e)

    # 2. 检查并强制释放 8080 端口占用者
    try:
        netstat_cmd = 'netstat -ano | findstr :8080'
        res = subprocess.run(netstat_cmd, shell=True, capture_output=True, text=True)
        if res.stdout.strip():
            lines = res.stdout.strip().splitlines()
            for line in lines:
                parts = line.split()
                if len(parts) >= 5 and "LISTENING" in line:
                    port_pid = parts[-1]
                    if port_pid.isdigit() and int(port_pid) != current_pid:
                        subprocess.run(f"taskkill /F /PID {port_pid}", shell=True, capture_output=True)
                        print(f"Killed process {port_pid} holding port 8080")
    except Exception as port_err:
        print("Error clearing port 8080:", port_err)

def verify_port_released() -> None:
    """确认 8080 端口已完全被 Windows 内核释放"""
    for _ in range(5):
        res = subprocess.run('netstat -ano | findstr :8080', shell=True, capture_output=True, text=True)
        if not res.stdout.strip():
            return
        time.sleep(0.5)

def main():
    current_pid = os.getpid()
    stop_existing_processes(current_pid)
    verify_port_released()

    time.sleep(1)

    root_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    bat_path = os.path.join(root_dir, "MyClaw.bat")

    print(f"Launching desktop application via MyClaw.bat: {bat_path}")

    # 使用 PowerShell 在当前用户 Active Session 下原生启动 MyClaw.bat
    bat_abs = str(Path(bat_path).resolve()).replace("'", "''")
    root_abs = str(Path(root_dir).resolve()).replace("'", "''")

    ps_cmd = (
        f'powershell -ExecutionPolicy Bypass -NoProfile -Command "'
        f'Start-Process -FilePath \'{bat_abs}\' -WorkingDirectory \'{root_abs}\'"'
    )
    subprocess.run(ps_cmd, shell=True)
    print("Successfully triggered desktop launcher.")

    print("Verifying service startup via /health...")
    success = False
    last_detail = ""
    for i in range(30):
        time.sleep(1)
        try:
            req = urllib.request.urlopen("http://127.0.0.1:8080/health", timeout=2)
            if req.status == 200:
                data = json.loads(req.read().decode('utf-8'))
                last_detail = f"ws_connected={data.get('ws_connected')}"
                print(f"Service health check OK: {last_detail}")
                success = True
                break
        except Exception as exc:
            print(f"Waiting for service startup... ({i+1}/30) {exc}")

    if success:
        print("SUCCESS: MyClaw backend is up. Tray icon may take a few more seconds; check system tray.")
    else:
        print("WARNING: /health did not return 200 within 30s. Check logs/myclaw.log and myclaw-tray-error.log.")

if __name__ == "__main__":
    main()
