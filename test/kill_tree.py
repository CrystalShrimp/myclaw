import psutil

def main():
    print("=== KILLING ALL MYCLAW & TRAY PROCESSES ===")
    for p in psutil.process_iter(['pid', 'name', 'cmdline']):
        try:
            cmd = " ".join(p.info['cmdline'] or [])
            name = p.info['name'] or ""
            if any(k in cmd.lower() for k in ['myclaw', 'tray.pyw', 'app.main']):
                pid = p.info['pid']
                p.kill()
                print(f"Killed [PID {pid}] ({name}): {cmd}")
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass

if __name__ == "__main__":
    main()
