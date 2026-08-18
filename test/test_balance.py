import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import asyncio
import json
from app.balance import get_profile_balance
from app.feishu.cards import build_balance_card

async def main():
    ds_res = await get_profile_balance("deepseek")
    ds_card = build_balance_card(ds_res)
    glm_res = await get_profile_balance("glm")
    glm_card = build_balance_card(glm_res)

    out = {
        "deepseek": ds_card,
        "glm": glm_card
    }
    Path("logs/test_balance_output.json").write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print("SUCCESS: written to logs/test_balance_output.json")

if __name__ == "__main__":
    asyncio.run(main())
