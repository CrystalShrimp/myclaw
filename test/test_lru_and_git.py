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

def get_project_meta(path_str: str) -> dict:
    """获取指定路径的项目关联信息 (Git 分支和 CLAUDE.md)"""
    p = Path(path_str)
    meta = {
        "git_branch": "未知 (非 Git 仓库)",
        "claude_md": "不存在",
        "is_exists": p.exists()
    }
    
    if not p.exists():
        return meta
        
    if (p / "CLAUDE.md").exists():
        meta["claude_md"] = "🟢 存在"
    else:
        meta["claude_md"] = "⚪ 不存在"
        
    git_dir = p / ".git"
    if git_dir.exists():
        try:
            head_file = git_dir / "HEAD"
            if head_file.exists():
                head_content = head_file.read_text("utf-8").strip()
                if head_content.startswith("ref:"):
                    meta["git_branch"] = f"🌿 {head_content.split('/')[-1]}"
                else:
                    meta["git_branch"] = f"🌿 {head_content[:8]}"
        except Exception:
            pass
            
    return meta

def find_all_claudecode_projects() -> list[str]:
    projects = set()
    project_last_active = {}
    
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
                        t = data.get("timestamp", 0)
                        if p and os.path.isabs(p):
                            norm_p = str(Path(p).resolve())
                            projects.add(norm_p)
                            project_last_active[norm_p] = max(project_last_active.get(norm_p, 0), t)
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
                    
    # 3. 过滤出在磁盘上真实存在，且符合 claudecode 项目特征的目录
    valid_projects = []
    for p_str in projects:
        p = Path(p_str)
        if p.exists() and p.is_dir():
            if (p / ".claude").exists() or (p / ".claudecode").exists():
                valid_projects.append(p_str)
                
    # 按照最近活跃时间（时间戳降序）进行排序。若时间戳相同，按路径名字字母序升序排序。
    sorted_projects = sorted(
        valid_projects,
        key=lambda x: (-project_last_active.get(x, 0), x)
    )
    
    # 输出部分项目的时间戳进行校验
    print("\nLRU Sorted active timestamps:")
    for proj in sorted_projects[:5]:
        print(f" - {proj}: {project_last_active.get(proj, 0)}")
        
    return sorted_projects

if __name__ == "__main__":
    print("Testing get_project_meta...")
    # 获取当前目录元信息
    current_dir = os.getcwd()
    meta = get_project_meta(current_dir)
    print(f"Directory: {current_dir}")
    print(f"Git branch: {meta['git_branch']}")
    print(f"CLAUDE.md: {meta['claude_md']}")
    
    print("\nScanning and sorting all projects with LRU...")
    all_projs = find_all_claudecode_projects()
    print(f"\nFound {len(all_projs)} projects. Top 5 most active:")
    for proj in all_projs[:5]:
        print(f" - {proj}")
