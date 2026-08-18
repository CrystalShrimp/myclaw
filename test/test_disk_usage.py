import os
from pathlib import Path

root = Path(r"d:\ForRunning\ForDev\myclaw")

def get_dir_size(path):
    total = 0
    if not path.exists():
        return 0
    for p in path.rglob("*"):
        try:
            if p.is_file() and not p.is_symlink():
                total += p.stat().st_size
        except Exception:
            pass
    return total

def main():
    print("=== MYCLAW DISK STORAGE FOOTPRINT ===")
    venv_bytes = get_dir_size(root / ".venv")
    auto_feishu_bytes = get_dir_size(root / "auto_feishu")
    logs_bytes = get_dir_size(root / "logs")
    src_bytes = get_dir_size(root / "app")
    total_bytes = get_dir_size(root)

    print(f"1. Python 虚拟环境 (.venv): {venv_bytes / (1024*1024):.2f} MB")
    print(f"2. 飞书自动化工具链 (auto_feishu): {auto_feishu_bytes / (1024*1024):.2f} MB")
    print(f"3. 运行时日志 (logs/): {logs_bytes / (1024*1024):.2f} MB")
    print(f"4. 核心源代码 (app/): {src_bytes / (1024*1024):.2f} MB")
    print(f"5. 项目静态总磁盘占用: {total_bytes / (1024*1024):.2f} MB")

if __name__ == "__main__":
    main()
