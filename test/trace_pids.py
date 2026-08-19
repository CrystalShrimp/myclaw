import subprocess

pids = ['4232', '32728', '32868', '33036']

def main():
    out = subprocess.check_output('wmic process get processid,parentprocessid,name,commandline /format:csv', shell=True).decode('gbk', errors='ignore')
    rows = [r.strip().split(',') for r in out.splitlines() if r.strip()]
    proc_map = {r[-2]: r for r in rows if len(r) >= 4 and r[-2].isdigit()}

    print("=================== 锁定 PID 深入追溯 ===================")
    for p in pids:
        if p in proc_map:
            r = proc_map[p]
            cmd = r[1]
            ppid = r[-4]
            name = r[-3]
            parent = proc_map.get(ppid)
            parent_name = parent[-3] if parent else "未知/已退出"
            parent_cmd = parent[1] if parent else "未知/已退出"
            print(f"[TARGET PROC] PID: {p} | Name: {name}")
            print(f"   Command: {cmd}")
            print(f"   [PARENT PROC] PPID: {ppid} | Parent Name: {parent_name}")
            print(f"   Parent Command: {parent_cmd}")
            print("=" * 70)

if __name__ == "__main__":
    main()
