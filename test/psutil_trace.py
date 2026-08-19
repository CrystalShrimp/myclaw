import psutil

def main():
    print("=================== PSUTIL 进程树全扫描 ===================")
    for p in psutil.process_iter(['pid', 'ppid', 'name', 'cmdline']):
        try:
            cmd = " ".join(p.info['cmdline'] or [])
            name = p.info['name'] or ""
            if any(k in cmd.lower() or k in name.lower() for k in ['myclaw', 'tray.pyw', 'app.main']):
                pid = p.info['pid']
                ppid = p.info['ppid']
                try:
                    parent = psutil.Process(ppid)
                    parent_name = parent.name()
                    parent_cmd = " ".join(parent.cmdline() or [])
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    parent_name = "已退出/无权限"
                    parent_cmd = "已退出/无权限"

                print(f"[FOUND PROC] PID: {pid} | Name: {name}")
                print(f"   Cmd: {cmd}")
                print(f"   [PARENT PROC] PPID: {ppid} | Parent Name: {parent_name}")
                print(f"   Parent Cmd: {parent_cmd}")
                print("=" * 75)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass

if __name__ == "__main__":
    main()
