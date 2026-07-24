import os
import re
import json
from pathlib import Path

def restore_project_path(encoded_name: str) -> str | None:
    if len(encoded_name) < 4 or encoded_name[1:3] != "--":
        return None
    drive = encoded_name[0] + ":\\"
    remaining = encoded_name[3:]
    
    def clean(s: str) -> str:
        return re.sub(r'[^a-zA-Z0-9]', '', s).lower()
        
    matched = []
    
    def dfs(current_dir: Path, rem_str: str):
        cleaned_rem = clean(rem_str)
        if not cleaned_rem:
            matched.append(str(current_dir))
            return
            
        try:
            # 过滤掉一些绝对不需要遍历的系统敏感或巨大目录（例如 .venv, node_modules）以防万一
            subdirs = [
                x for x in current_dir.iterdir() 
                if x.is_dir() and x.name not in (".venv", "node_modules", ".git")
            ]
        except Exception:
            return
            
        for sub in subdirs:
            cleaned_sub = clean(sub.name)
            if not cleaned_sub:
                continue
            if cleaned_rem.startswith(cleaned_sub):
                rest_str = consume_prefix(rem_str, sub.name)
                if rest_str is not None:
                    dfs(sub, rest_str)
                    
    def consume_prefix(rem_str: str, sub_name: str) -> str | None:
        rem_idx = 0
        sub_idx = 0
        while sub_idx < len(sub_name) and rem_idx < len(rem_str):
            c_rem = rem_str[rem_idx].lower()
            c_sub = sub_name[sub_idx].lower()
            
            if c_rem.isalnum() and c_sub.isalnum():
                if c_rem == c_sub:
                    rem_idx += 1
                    sub_idx += 1
                else:
                    return None
            elif not c_rem.isalnum():
                rem_idx += 1
            elif not c_sub.isalnum():
                sub_idx += 1
                
        while sub_idx < len(sub_name):
            if sub_name[sub_idx].isalnum():
                return None
            sub_idx += 1
            
        while rem_idx < len(rem_str) and not rem_str[rem_idx].isalnum():
            rem_idx += 1
            
        return rem_str[rem_idx:]
        
    dfs(Path(drive), remaining)
    return matched[0] if matched else None

def find_all_claudecode_projects() -> list[str]:
    projects = set()
    
    # 1. 从 history.jsonl 中读取项目路径
    history_file = Path.home() / ".claude" / "history.jsonl"
    if history_file.exists():
        try:
            with open(history_file, "r", encoding="utf-8", errors="replace") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        data = json.loads(line)
                        p = data.get("project")
                        if p and os.path.isabs(p):
                            norm_p = str(Path(p).resolve())
                            projects.add(norm_p)
                    except Exception:
                        continue
        except Exception as e:
            print(f"Error reading history.jsonl: {e}")
            
    # 2. 从 ~/.claude/projects/ 还原绝对路径
    projects_dir = Path.home() / ".claude" / "projects"
    if projects_dir.exists():
        for item in projects_dir.iterdir():
            if item.is_dir():
                restored = restore_project_path(item.name)
                if restored:
                    projects.add(restored)
                else:
                    print(f"Failed to restore project path for: {item.name}")
                    
    # 3. 过滤出在磁盘上真实存在，且符合 claudecode 项目特征的目录
    valid_projects = []
    for p_str in projects:
        p = Path(p_str)
        if p.exists() and p.is_dir():
            if (p / ".claude").exists() or (p / ".claudecode").exists():
                valid_projects.append(p_str)
                
    return sorted(valid_projects)

if __name__ == "__main__":
    print("Testing restore_project_path algorithm...")
    # 测试一些已知的编码项目名
    test_cases = [
        "D--ForRunning-ForDev-openclaw",
        "D--ForRunning-ForDev-cg-com-avgorange-dating",
    ]
    for case in test_cases:
        res = restore_project_path(case)
        print(f"Encoded: {case} -> Restored: {res}")
        
    print("\nScanning all projects...")
    all_projs = find_all_claudecode_projects()
    print(f"Found {len(all_projs)} projects:")
    for proj in all_projs:
        print(f" - {proj}")
