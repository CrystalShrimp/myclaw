"""Fill a provider profile from the example template, set it active, and
verify the API key with a real request.

Called by MyClaw-Setup.bat via environment variables (key never touches the
process command line):
    MYCLAW_PROVIDER=glm|deepseek|kimi  MYCLAW_API_KEY=xxx  python setup_provider.py
"""
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = Path(os.environ.get("MYCLAW_CONFIG_DIR") or (ROOT / "config"))
EXAMPLES_DIR = ROOT / "examples"

LABELS = {"glm": "智谱 GLM", "deepseek": "DeepSeek", "kimi": "Kimi (月之暗面)"}


def verify_key(base_url: str, key: str, model: str) -> None:
    try:
        import httpx
    except ImportError:
        print("WARN: venv 缺少 httpx，跳过 Key 联网验证。")
        return
    payload = {"model": model, "max_tokens": 8, "messages": [{"role": "user", "content": "Say OK"}]}
    headers = {
        "x-api-key": key,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }
    # Anthropic 系端点有的是 {base}/v1/messages，有的是 {base}/messages，
    # 404 url.not_found 视为路径不对，换下一个再试。
    for suffix in ("/v1/messages", "/messages"):
        try:
            resp = httpx.post(f"{base_url.rstrip('/')}{suffix}", json=payload, headers=headers, timeout=30)
        except Exception as exc:
            print(f"WARN: 无法联网验证 Key ({exc})")
            print("     配置已保存，可稍后在飞书中用 /provider 切换并测试。")
            return
        if resp.status_code == 200:
            print("OK: API Key 联网验证通过 (HTTP 200)")
            return
        if resp.status_code == 404 and "not_found" in resp.text:
            continue
        print(f"WARN: 供应商返回 HTTP {resp.status_code}: {resp.text[:120]}")
        print("     配置已保存，请核对 Key 是否正确。")
        return
    print("WARN: 两个常见路径都返回 404，无法确认 Key 有效性（配置已保存）。")


def read_api_key(provider: str) -> str | None:
    """从环境变量或交互输入读取 Key。

    bat 的 set /p 对粘贴（前导换行/空白）不可靠，改为 python 自己 input()，
    strip 后空值最多重试 3 次。返回 None 表示非交互环境下的主动跳过。
    """
    key = os.environ.get("MYCLAW_API_KEY", "").strip()
    if key:
        return key
    for _ in range(3):
        try:
            raw = input(f"请粘贴 {LABELS.get(provider, provider)} 的 API Key 并回车: ")
        except EOFError:
            print("[-] 非交互环境且未提供 Key，跳过供应商配置。")
            return None
        key = raw.strip()
        if key:
            return key
        print("Key 为空，请重新粘贴（直接 Ctrl+C 可退出）。")
    return ""


def main() -> int:
    provider = os.environ.get("MYCLAW_PROVIDER", "").strip().lower()
    if provider not in LABELS:
        print("ERROR: 未知供应商:", provider or "(空)", "可选:", ", ".join(LABELS))
        return 1
    key = read_api_key(provider)
    if key is None:
        return 0
    if not key:
        print("ERROR: 多次输入均为空，未写入配置。")
        return 1

    example = EXAMPLES_DIR / f"settings_{provider}.example.json"
    if not example.exists():
        print(f"ERROR: 模板不存在: {example}")
        return 1

    data = json.loads(example.read_text("utf-8"))
    env = data.setdefault("env", {})
    env["ANTHROPIC_AUTH_TOKEN"] = key

    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    target = CONFIG_DIR / f"settings_{provider}.json"
    target.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", "utf-8")
    (CONFIG_DIR / "active_profile").write_text(provider, "utf-8")
    print(f"OK: 已写入 {target}")
    print(f"OK: 当前供应商已设为 {LABELS[provider]} ({provider})")
    print("提示: 飞书中可用 /provider 随时切换供应商。")

    base_url = env.get("ANTHROPIC_BASE_URL", "")
    model = env.get("ANTHROPIC_DEFAULT_HAIKU_MODEL") or env.get("ANTHROPIC_DEFAULT_OPUS_MODEL", "")
    if base_url and model:
        print("正在联网验证 API Key ...")
        verify_key(base_url, key, model)
    return 0


if __name__ == "__main__":
    sys.exit(main())
