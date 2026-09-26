"""DeepSeek 调用封装（P0-02 主体 A3）。

钉死的接口（[你敲] 的 agents/intent.py、harness/agent.py 只依赖这一个函数）：

    complete_json(system, user, schema, *, model="flash") -> dict

三层护栏（面试可讲的部分就在注释里）：
  1. **JSON mode**：让模型在解码层面只能产出 JSON 对象，从源头砍掉"先聊天再夹一段 JSON"这类格式错误；
  2. **schema 提示**：把 JSON Schema 原样贴进 system —— DeepSeek 的 json_object 模式
     *不校验* schema，所以它只是"很强的提示"。真正的校验在调用方（Pydantic），
     这里绝不假装自己校验过了；
  3. **重试一次**：超时 / 5xx / 连不上 / 解析不出 dict → 退避 0.5s 重来一次；再失败就抛
     DeepSeekError。<b>不静默降级成 {} </b> —— 空字典会被上层当成"模型说没有参数"，
     那是把网络故障伪装成业务结论。

key 从 python-service/.env 读（该文件已被 .gitignore 覆盖，永不入库）；也支持同名环境变量（env 优先）。
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

import httpx

ENV_FILE = Path(__file__).resolve().parent.parent / ".env"
DEFAULT_BASE_URL = "https://api.deepseek.com"

#: 模型别名 → 真实模型 id。别名让上层代码读起来是"要 flash 还是 pro"（路由决策），
#: 而不是把厂商的型号名硬编码到业务里；换型号只改这里（或用环境变量覆盖）。
MODEL_ALIASES = {
    "flash": "DEEPSEEK_MODEL_FLASH",
    "pro": "DEEPSEEK_MODEL_PRO",
}
MODEL_DEFAULTS = {
    "flash": "deepseek-flash",
    "pro": "deepseek-v4-pro",
}

RETRY_BACKOFF_SECONDS = 0.5
REQUEST_TIMEOUT = httpx.Timeout(60.0, connect=10.0)

_env_cache: dict[str, str] | None = None


class DeepSeekError(RuntimeError):
    """调用 DeepSeek 失败（网络 / 非 200 / 响应不是 JSON 对象）。"""


def _dotenv() -> dict[str, str]:
    """读 python-service/.env（手写 8 行，不引 python-dotenv）。

    为什么不引依赖：我们只要"KEY=VALUE、# 注释、忽略空行"这一种子集，
    而 python-dotenv 还带变量插值、多文件合并等行为 —— 多出来的行为意味着
    多一种"为什么这里读到的是那个值"的解释成本。窄接口 + 显式实现更适合教学项目。
    """
    global _env_cache
    if _env_cache is not None:
        return _env_cache
    values: dict[str, str] = {}
    if ENV_FILE.is_file():
        for raw in ENV_FILE.read_text(encoding="utf-8-sig").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip().strip('"').strip("'")
    _env_cache = values
    return values


def _setting(name: str, default: str = "") -> str:
    """取配置：环境变量优先于 .env（便于 CI / 临时覆盖，不用改文件）。"""
    return os.environ.get(name) or _dotenv().get(name) or default


def resolve_model(model: str) -> str:
    """把别名（flash / pro）换成真实模型 id；不认识的字符串原样透传（允许直接写型号名）。"""
    key = MODEL_ALIASES.get(model)
    if key is None:
        return model
    return _setting(key, MODEL_DEFAULTS[model])


def complete_json(
    system: str,
    user: str,
    schema: dict[str, Any],
    *,
    model: str = "flash",
    temperature: float = 0.0,
) -> dict[str, Any]:
    """让模型按 schema 产出 JSON 对象。

    :param system: 角色与规则（稳定前缀，利于命中服务端 prompt 缓存）
    :param user: 本轮用户输入 / 观察结果
    :param schema: JSON Schema（通常来自 Pydantic 的 model_json_schema()，别手写副本——会漂）
    :param model: "flash" / "pro" / 直接写型号名
    :raises DeepSeekError: 两次尝试都失败
    """
    api_key = _setting("DEEPSEEK_API_KEY")
    if not api_key:
        raise DeepSeekError("DEEPSEEK_API_KEY 未配置：请写入 python-service/.env 或设为环境变量")

    base_url = _setting("DEEPSEEK_BASE_URL", DEFAULT_BASE_URL).rstrip("/")
    payload = {
        "model": resolve_model(model),
        "messages": [
            {
                "role": "system",
                # 必须出现 "json" 字样：DeepSeek 的 JSON mode 以此作为约束生效的显式信号
                "content": system + "\n\n你必须只输出一个 JSON 对象，不要输出解释文字。JSON Schema:\n"
                + json.dumps(schema, ensure_ascii=False),
            },
            {"role": "user", "content": user},
        ],
        "response_format": {"type": "json_object"},
        "temperature": temperature,
        "stream": False,
    }
    headers = {"Authorization": "Bearer " + api_key, "Content-Type": "application/json"}

    last_error: Exception | None = None
    for attempt in (1, 2):
        try:
            response = httpx.post(base_url + "/chat/completions", json=payload, headers=headers,
                                  timeout=REQUEST_TIMEOUT)
            if response.status_code != 200:
                raise DeepSeekError(f"HTTP {response.status_code}: {response.text[:300]}")
            body = response.json()
            content = body["choices"][0]["message"]["content"]
            parsed = json.loads(content)
            if not isinstance(parsed, dict):
                raise DeepSeekError(f"模型返回的不是 JSON 对象：{type(parsed).__name__}")
            usage = body.get("usage") or {}
            print(f"[deepseek] model={payload['model']} attempt={attempt} "
                  f"prompt_tokens={usage.get('prompt_tokens')} completion_tokens={usage.get('completion_tokens')}")
            return parsed
        except Exception as exc:  # 网络、非 200、解析失败都走同一条重试路径
            last_error = exc
            if attempt == 1:
                time.sleep(RETRY_BACKOFF_SECONDS)

    raise DeepSeekError(f"两次尝试都失败：{type(last_error).__name__}: {last_error}")
