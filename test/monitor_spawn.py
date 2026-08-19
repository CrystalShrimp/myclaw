import subprocess
import time

def check():
    out = subprocess.check_output('wmic process get processid,parentprocessid,name,commandline /format:csv', shell=True).decode('gbk', errors='ignore')
    rows = [r.strip().split(',') for r in out.splitlines() if r.strip()]
    proc_map = {}
    for r in rows:
        if len(r) >= 4 and r[-2].isdigit():
            proc_map[r[-2]] = r

    for r in rows:
        if len(r) >= 4:
            cmd = r[1]
            if ('tray.pyw' in cmd.lower() or 'app.main' in cmd.lower()) and 'find_parent' not in cmd and 'monitor_spawn' not in cmd:
                pid = r[-2]
                ppid = r[-4]
                name = r[-3]
                parent = proc_map.get(ppid)
                parent_name = parent[-3] if parent else "未知/已退出"
                parent_cmd = parent[1] if parent else "未知/已退出"
                print(f"🚨 抓到了！【子进程】 PID: {pid} | Name: {name}")
                print(f"   Command: {cmd}")
                print(f"🔥 【孵化源头 (父进程)】 PPID: {ppid} | Name: {parent_name}")
                print(f"   Parent Command: {parent_cmd}")
                print("=" * 70)
                return True
    return False

def main():
    print("正在实时侦听并侦测孵化源头 (持续 15 秒)...")
    start = time.time()
    while time.time() - start < 15:
        if check():
            break
        time.sleep(0.5)

if __name__ == "__main__":
    main()
