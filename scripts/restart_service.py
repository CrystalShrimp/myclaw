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
        # 1. 查找所有命令行或可执行路径中包含 myclaw 目录、app.main 或 tray.pyw 的 Python 进程
        ps_cmd = (
            'powershell -NoProfile -Command "'
            'Get-WmiObject Win32_Process | Where-Object { `$_.Name -like \'*python*\' } | '
            'Select-Object ProcessId, ExecutablePath, CommandLine | ConvertTo-Json"'
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
                            print(f"Killed MyClaw worker process tree {pid}")
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

    print("Verifying service startup, tray process & health...")
    success = False
    new_pid = None
    for i in range(15):
        time.sleep(1)
        # 验证 1: 检查 HTTP 端口
        try:
            req = urllib.request.urlopen("http://127.0.0.1:8080/health", timeout=2)
            if req.status == 200:
                data = json.loads(req.read().decode('utf-8'))
                # 验证 2: 确认新托盘进程在运行
                ps_chk = subprocess.run(
                    'powershell -NoProfile -Command "Get-WmiObject Win32_Process | Where-Object { $_.CommandLine -like \'*tray.pyw*\' } | Select-Object -ExpandProperty ProcessId"',
                    shell=True, capture_output=True, text=True
                )
                tray_pids = ps_chk.stdout.strip().split()
                if tray_pids:
                    new_pid = tray_pids[0]
                    print(f"Service health check OK: {data} (Tray PID: {new_pid})")
                    success = True
                    break
        except Exception:
            print(f"Waiting for service startup... ({i+1}/15)")

    if success:
        print(f"SUCCESS: MyClaw desktop service and tray icon (PID {new_pid}) are verified up and running!")
    else:
        print("WARNING: Health check timed out.")

if __name__ == "__main__":
    main()
