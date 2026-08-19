import subprocess

def main():
    out = subprocess.check_output('wmic process get processid,parentprocessid,name,commandline /format:csv', shell=True).decode('gbk', errors='ignore')
    rows = [r.strip().split(',') for r in out.splitlines() if r.strip()]
    proc_map = {}
    for r in rows:
        if len(r) >= 4 and r[-2].isdigit():
            proc_map[r[-2]] = r

    print("=================== 寻找孵化源头父进程 ===================")
    found = False
    for r in rows:
        if len(r) >= 4:
            cmd = r[1]
            if 'tray.pyw' in cmd.lower() or 'app.main' in cmd.lower():
                found = True
                pid = r[-2]
                ppid = r[-4]
                name = r[-3]
                parent = proc_map.get(ppid)
                parent_name = parent[-3] if parent else "未知/已退出"
                parent_cmd = parent[1] if parent else "未知/已退出"
                print(f"【子进程】 PID: {pid} | Name: {name}")
                print(f"  Command: {cmd}")
                print(f"【父进程 (孵化源头)】 PPID: {ppid} | Parent Name: {parent_name}")
                print(f"  Parent Command: {parent_cmd}")
                print("-" * 60)
    
    if not found:
        print("当前未检测到正在运行的 tray.pyw 或 app.main 进程。")

if __name__ == "__main__":
    main()
