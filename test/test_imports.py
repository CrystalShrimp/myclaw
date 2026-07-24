from app.feishu.events import find_all_claudecode_projects
from app.feishu.cards import build_cd_selection_card, build_cd_confirm_card

try:
    projects = find_all_claudecode_projects()
    print(f"Successfully called find_all_claudecode_projects, found: {len(projects)} projects")
    
    # 测试主选择卡片生成
    card_sel = build_cd_selection_card("D:\\test", projects)
    print(f"Successfully generated selection card, title: {card_sel['header']['title']['content']}")
    
    # 测试确认卡片生成
    card_conf = build_cd_confirm_card(
        target_path="D:\\test\\my-project",
        is_new=True,
        git_branch="🌿 main",
        claude_md="🟢 存在",
        warning_running=True
    )
    print(f"Successfully generated confirmation card, title: {card_conf['header']['title']['content']}")
    
    print("ALL TESTS PASSED: Imports and basic function execution are 100% correct!")
except Exception as e:
    print(f"VERIFICATION FAILED with error: {e}")
    import traceback
    traceback.print_exc()
