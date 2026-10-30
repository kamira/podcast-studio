# -*- coding: utf-8 -*-
"""local-models 的 MCP 註冊管理(CHG-20261022-02 T1,FR-22):Claude Code / Codex 的 user scope。

只操作名稱 `local-models`、只用 user scope;寫設定一律只透過官方 CLI 的 `mcp add` / `mcp remove`
(`~/.local/bin/claude`、`~/.local/bin/codex`),**不直接寫** `.claude.json`、`config.toml`、
`~/.claude/settings.json`。

介面:
  NAME = "local-models"、CLIENTS = ("claude", "codex")、ACTIONS = ("register", "remove")
  paths() -> {"claude_cli", "codex_cli", "uv", "claude_json", "claude_settings", "repo_mcp_json", "codex_home"}
                                 每次呼叫依當下環境決定(D10):`CLAUDE_CONFIG_DIR` 有設時 claude_json 為
                                 `$CLAUDE_CONFIG_DIR/.claude.json`、claude_settings 為
                                 `$CLAUDE_CONFIG_DIR/settings.json`,否則 `~/.claude.json`、`~/.claude/settings.json`;
                                 codex_home 為 `$CODEX_HOME` 或 `~/.codex`
  server_argv() -> list[str]     [<uv>, "run", "--script", <repo>/local_models_mcp.py]
  status(check) -> {"server", "claude", "codex"}
                                 ClientStatus = {"installed", "state": "registered"|"other"|"absent"|"unknown",
                                 "command", "args", "env_keys", "timeout", "health", "detail", "shadowed"}。
                                 Claude 的狀態唯讀解析 claude_json 頂層 `mcpServers["local-models"]`(D8 (b));
                                 另只判斷 `projects[<repo>].mcpServers` 與 `<repo>/.mcp.json` 有無同名項(D17 遮蔽偵測),
                                 其餘內容不保留、不回傳。Codex 的狀態取自 `codex mcp get local-models --json`。
                                 check=False 不呼叫 `claude mcp get`;check=True 只在 Claude 為 registered 且
                                 shadowed 為 False 時才呼叫一次(cwd = repo 根目錄;會啟動 MCP 伺服器做健康檢查)。
                                 Claude 的 timeout = {"key": "MCP_TOOL_TIMEOUT", "value", "suggested"}(只讀 claude_settings);
                                 Codex 的 timeout = {"key": "tool_timeout_sec", "value", "snippet"}(snippet 為手動補的 TOML 片段)
  plan(client, action) -> {"argv", "files", "current", "warnings"}
                                 只組指令、不執行寫入;狀態不允許時丟 ValueError(繁中訊息)
  apply(client, action) -> {"ok", "exit_code", "output", "status"}
                                 執行 plan 的指令後一律重新查狀態(check=False);ok 以新狀態判斷——register 後為
                                 registered、remove 後為 absent,且結束碼為 0 才是 True(`codex mcp remove` 對不存在項也回 0)
  redact_env(text, known) -> str 去除 env 值,只留鍵名(D16):整段為 JSON 時所有名為 `env` 的欄位(物件換值、
                                 陣列的 `K=V` 留鍵名)、已知 env 值、`鍵=值` 行、`Environment:` 段。CLI 文字一律
                                 stdout、stderr 分別遮蔽後再相接,說明前綴在遮蔽之後才加;CLI 輸出能解析成 JSON 時,
                                 不論結束碼都先遞迴蒐集其中所有 `env` 欄位的值列入已知值(一次修正 M-1)
  scrub(text) -> str             路徑遮蔽(規則同 studio_server.scrub():repo → `<repo>`、家目錄 → `~`;
                                 另把不在家目錄 / repo 之下的 `CLAUDE_CONFIG_DIR`、`CODEX_HOME` 換成變數名)
  Busy                           另一個操作執行中(status / plan / apply 共用一把行程內鎖,D13;不進 heavy_lock)

安全規則(CHG-20261022-02 Global Constraints):
  - 子程序一律 argv list、shell=False、stdin=DEVNULL、逾時 30 秒、cwd = repo 根目錄;環境沿用本行程
    (含 CLAUDE_CONFIG_DIR、CODEX_HOME),並一律覆寫 EXTRA_ENV 三個變數(只加在子程序,不寫入註冊內容)。
  - 回傳的所有字串依「redact_env() → scrub() → 截斷 64 KB」的順序處理;env 只回鍵名。
  - 解析失敗、格式不認得、CLI 非 0 結束一律回 unknown 或 CLI 錯誤原文,不推論成 absent。
"""
from __future__ import annotations

import contextlib
import json
import os
import pwd
import re
import subprocess
import threading
from pathlib import Path
from typing import Any, NamedTuple

ROOT = Path(__file__).resolve().parent
NAME = "local-models"
CLIENTS = ("claude", "codex")
ACTIONS = ("register", "remove")
LABELS = {"claude": "Claude Code", "codex": "Codex"}
TIMEOUT = 30                          # 秒;每個 CLI 子程序
MAX_OUTPUT = 64 * 1024                # 送出的單一字串上限(UTF-8 位元組)
MAX_FILE = 16 * 1024 * 1024           # .claude.json / settings.json / .mcp.json 讀取上限
EXTRA_ENV = {
    "UV_OFFLINE": "1",                                # 確認閘第 3 題:擋 uv 下載依賴
    "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",  # 使用者 2026-10-30 選項:關閉 CLI 非必要流量
    "DISABLE_TELEMETRY": "1",
}
MASK = "(已設定)"
TRUNC_MARK = "\n…(輸出過長,已截斷)"
SUGGESTED_CLAUDE_TIMEOUT = "1800000"
SUGGESTED_CODEX_TIMEOUT = 1800
NOT_FOUND = "No MCP server named"     # 修改步驟 1 補查:Codex 找不到時結束碼 1 + stderr 含此字樣

_LOCK = threading.Lock()


class Busy(RuntimeError):
    """另一個 MCP 註冊操作執行中。"""


@contextlib.contextmanager
def _busy():
    if not _LOCK.acquire(blocking=False):
        raise Busy("另一個 MCP 註冊操作執行中,請稍後再試")
    try:
        yield
    finally:
        _LOCK.release()


# ---------- 路徑 ----------

def paths() -> dict[str, Path]:
    home = Path.home()
    bin_dir = home / ".local" / "bin"
    cfg_dir = os.environ.get("CLAUDE_CONFIG_DIR")
    codex_home = os.environ.get("CODEX_HOME")
    return {
        "claude_cli": bin_dir / "claude",
        "codex_cli": bin_dir / "codex",
        "uv": bin_dir / "uv",
        "claude_json": Path(cfg_dir) / ".claude.json" if cfg_dir else home / ".claude.json",
        "claude_settings": Path(cfg_dir) / "settings.json" if cfg_dir else home / ".claude" / "settings.json",
        "repo_mcp_json": Path(ROOT) / ".mcp.json",
        "codex_home": Path(codex_home) if codex_home else home / ".codex",
    }


def server_argv() -> list[str]:
    return [str(paths()["uv"]), "run", "--script", str(Path(ROOT) / "local_models_mcp.py")]


def _server_args() -> list[str]:
    return server_argv()[1:]


# ---------- 遮蔽:env 值 → 路徑 → 截斷 ----------

def _variants(p) -> set[str]:
    out = set()
    if not p:
        return out
    s = str(p)
    for v in (s, os.path.realpath(s)):
        v = v.rstrip(os.sep)
        if v and v != os.sep:
            out.add(v)
    return out


def _under(child: str, parents: set[str]) -> bool:
    return any(child == p or child.startswith(p + os.sep) for p in parents)


def _prefixes() -> list[tuple[str, str]]:
    homes = _variants(Path.home())
    try:
        homes |= _variants(pwd.getpwuid(os.getuid()).pw_dir)
    except KeyError:
        pass
    repo = _variants(ROOT)
    pairs = [(p, "<repo>") for p in repo] + [(p, "~") for p in homes]
    for var in ("CLAUDE_CONFIG_DIR", "CODEX_HOME"):
        for p in _variants(os.environ.get(var)):
            if not _under(p, homes | repo):
                pairs.append((p, "$" + var))
    # 長的先換:repo 通常在家目錄之下,要先換成 <repo>
    return sorted(pairs, key=lambda x: len(x[0]), reverse=True)


def scrub(text: str) -> str:
    for prefix, repl in _prefixes():
        text = text.replace(prefix, repl)
    return text


_KV_KEY = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_KV_LINE = re.compile(r"^(\s*(?:export\s+)?)([A-Za-z_][A-Za-z0-9_]*)=(.*)$")
_KV_TOKEN = re.compile(r"([A-Za-z_][A-Za-z0-9_]*)=\S*")
_ENV_HEAD = re.compile(r"^(\s*)Environment:(.*)$")


def _mask_env_item(item: Any) -> Any:
    """env 為陣列時的單一元素:`K=V` 字串留鍵名,其他一律換成遮蔽字樣。"""
    if isinstance(item, str) and "=" in item:
        key = item.split("=", 1)[0]
        if _KV_KEY.fullmatch(key):
            return f"{key}={MASK}"
    return MASK


def _collect_env_values(obj: Any, known: set) -> None:
    """遞迴蒐集所有名為 `env` 的欄位中的字串值(dict 取各值;list 取 `K=V` 的 `V`)放進 known。"""
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k == "env" and isinstance(v, dict):
                known.update(x for x in v.values() if isinstance(x, str) and x)
            elif k == "env" and isinstance(v, list):
                for x in v:
                    if isinstance(x, str) and "=" in x and x.split("=", 1)[1]:
                        known.add(x.split("=", 1)[1])
            else:
                _collect_env_values(v, known)
    elif isinstance(obj, list):
        for v in obj:
            _collect_env_values(v, known)


def _collect_from_text(text: str, known: set) -> None:
    """CLI 文字若整段是 JSON 物件 / 陣列,蒐集其中的 env 值(不論結束碼)。"""
    stripped = (text or "").strip()
    if stripped[:1] not in ("{", "["):
        return
    try:
        obj = json.loads(stripped)
    except ValueError:
        return
    _collect_env_values(obj, known)


def _mask_json_env(obj: Any) -> Any:
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            if k == "env" and isinstance(v, dict):
                out[k] = {ek: MASK for ek in v}
            elif k == "env" and isinstance(v, list):
                out[k] = [_mask_env_item(x) for x in v]
            else:
                out[k] = _mask_json_env(v)
        return out
    if isinstance(obj, list):
        return [_mask_json_env(v) for v in obj]
    return obj


def redact_env(text: str, known=()) -> str:
    """去除 env 值、只留鍵名(D16):JSON 的 env 物件、已知 env 值、`鍵=值` 行、`Environment:` 段。"""
    stripped = text.strip()
    if stripped[:1] in ("{", "["):
        try:
            obj = json.loads(stripped)
        except ValueError:
            pass
        else:
            if isinstance(obj, (dict, list)):
                text = json.dumps(_mask_json_env(obj), ensure_ascii=False)
    for value in sorted({v for v in known if isinstance(v, str) and v}, key=len, reverse=True):
        text = text.replace(value, MASK)
    lines = text.split("\n")
    env_indent = None
    for i, line in enumerate(lines):
        if env_indent is not None:
            indent = len(line) - len(line.lstrip())
            if not line.strip() or indent <= env_indent:
                env_indent = None
            else:
                body = line.strip()
                cut = min((body.find(c) for c in "=:" if c in body), default=-1)
                sep = body[cut] if cut >= 0 else ""
                key = body[:cut].strip() if cut >= 0 else ""
                lines[i] = line[:indent] + (f"{key}{'=' if sep == '=' else ': '}{MASK}" if key else MASK)
                continue
        head = _ENV_HEAD.match(line)
        if head:
            env_indent = len(head.group(1))
            rest = head.group(2).strip()
            if rest:
                masked = _KV_TOKEN.sub(lambda m: f"{m.group(1)}={MASK}", rest)
                lines[i] = f"{head.group(1)}Environment: {masked if masked != rest else MASK}"
            continue
        kv = _KV_LINE.match(line)
        if kv:
            lines[i] = f"{kv.group(1)}{kv.group(2)}={MASK}"
    return "\n".join(lines)


def _truncate(text: str) -> str:
    data = text.encode("utf-8")
    if len(data) <= MAX_OUTPUT:
        return text
    keep = MAX_OUTPUT - len(TRUNC_MARK.encode("utf-8"))
    return data[:keep].decode("utf-8", "ignore") + TRUNC_MARK


def _clean(text: str, known) -> str:
    return _truncate(scrub(redact_env(text, known)))


def _clean_obj(obj: Any, known) -> Any:
    if isinstance(obj, str):
        return _clean(obj, known)
    if isinstance(obj, dict):
        return {k: _clean_obj(v, known) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_clean_obj(v, known) for v in obj]
    return obj


# ---------- 子程序 ----------

class _Result(NamedTuple):
    code: int | None
    stdout: str
    stderr: str
    error: str | None      # 逾時或無法執行時的繁中說明

    def redacted(self, known) -> str:
        """stdout、stderr 先分別經 redact_env() 再相接(JSON 輸出不會因與 stderr 相接而跳過 JSON 遮蔽)。"""
        if self.error:
            return self.error
        _collect_from_text(self.stdout, known)
        _collect_from_text(self.stderr, known)
        return "\n".join(redact_env(s, known).rstrip("\n") for s in (self.stdout, self.stderr) if s.strip())


def _env() -> dict[str, str]:
    env = dict(os.environ)
    env.update(EXTRA_ENV)
    return env


def _run(argv: list[str]) -> _Result:
    try:
        proc = subprocess.run(list(argv), cwd=str(ROOT), env=_env(), stdin=subprocess.DEVNULL,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=TIMEOUT,
                              shell=False, check=False)
    except subprocess.TimeoutExpired:
        return _Result(None, "", "", f"CLI 執行逾時(超過 {TIMEOUT} 秒)")
    except OSError as e:
        return _Result(None, "", "", f"無法執行 CLI:{e.strerror or type(e).__name__}")
    dec = lambda b: (b or b"").decode("utf-8", "replace") if isinstance(b, (bytes, bytearray)) else (b or "")
    return _Result(proc.returncode, dec(proc.stdout), dec(proc.stderr), None)


def _installed(p: Path) -> bool:
    return p.is_file() and os.access(p, os.X_OK)


# ---------- 讀檔(唯讀) ----------

def _read_json(path: Path) -> tuple[str, Any]:
    """回 ("missing" | "too_big" | "error" | "ok", 內容)。"""
    try:
        with open(path, "rb") as f:
            if os.fstat(f.fileno()).st_size > MAX_FILE:
                return "too_big", None
            raw = f.read(MAX_FILE + 1)
    except FileNotFoundError:
        return "missing", None
    except OSError:
        return "error", None
    if len(raw) > MAX_FILE:
        return "too_big", None
    try:
        return "ok", json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return "error", None


def _entry_state(entry: Any, cmd_key: str = "command") -> tuple[str, str | None, list | None, dict | None]:
    """比對單一註冊項:回 (state, command, args, env);entry 必須是 dict。"""
    ttype = entry.get("type")
    command, args, env = entry.get(cmd_key), entry.get("args"), entry.get("env")
    if args is None:
        args = []
    env_ok = env is None or isinstance(env, dict)
    if ttype not in (None, "stdio"):
        return "other", command if isinstance(command, str) else None, None, env if isinstance(env, dict) else None
    if not isinstance(command, str) or not isinstance(args, list) or not all(isinstance(a, str) for a in args) \
            or not env_ok:
        return "unknown", None, None, None
    state = "registered" if command == str(paths()["uv"]) and args == _server_args() else "other"
    return state, command, args, env


def _claude_shadow(data: Any) -> bool | None:
    """本 repo 的 local / project scope 是否有同名項;無法判斷回 None。只判斷有無,不取內容。

    鍵存在但型別不是預期型別(`projects`、`projects[<repo>]`、其 `mcpServers`、`.mcp.json` 本身或其
    `mcpServers` 不是物件),或 `.mcp.json` 無法讀取 / 解析時,視為無法判斷;已確定有同名項時回 True。
    """
    if not isinstance(data, dict):
        return None
    shadow = False
    unsure = False

    def look(servers: Any) -> None:
        nonlocal shadow, unsure
        if not isinstance(servers, dict):
            unsure = True
        elif NAME in servers:
            shadow = True

    if "projects" in data:
        projects = data["projects"]
        if not isinstance(projects, dict):
            unsure = True
        else:
            for key in _variants(ROOT):
                if key not in projects:
                    continue
                proj = projects[key]
                if not isinstance(proj, dict):
                    unsure = True
                elif "mcpServers" in proj:
                    look(proj["mcpServers"])
    kind, mcp = _read_json(paths()["repo_mcp_json"])
    if kind == "ok":
        if not isinstance(mcp, dict):
            unsure = True
        elif "mcpServers" in mcp:
            look(mcp["mcpServers"])
    elif kind != "missing":
        unsure = True
    if shadow:
        return True
    return None if unsure else False


def _fmt_num(v):
    return int(v) if isinstance(v, float) and v.is_integer() else v


def _codex_snippet(value) -> str:
    return f"[mcp_servers.{NAME}]\ntool_timeout_sec = {_fmt_num(value if value is not None else SUGGESTED_CODEX_TIMEOUT)}"


def _blank(installed: bool) -> dict:
    return {"installed": installed, "state": "unknown", "command": None, "args": None, "env_keys": [],
            "timeout": None, "health": None, "detail": None, "shadowed": False}


SHADOW_MSG = "本 repo 有同名 local / project scope 註冊,會優先於 user scope"


def _claude_status(check: bool, known: set) -> dict:
    p = paths()
    st = _blank(_installed(p["claude_cli"]))
    # MCP_TOOL_TIMEOUT(只讀)
    kind, settings = _read_json(p["claude_settings"])
    value = None
    if kind == "ok" and isinstance(settings, dict) and isinstance(settings.get("env"), dict):
        v = settings["env"].get("MCP_TOOL_TIMEOUT")
        if isinstance(v, (str, int)) and not isinstance(v, bool):
            value = str(v)
    st["timeout"] = {"key": "MCP_TOOL_TIMEOUT", "value": value, "suggested": SUGGESTED_CLAUDE_TIMEOUT}

    kind, data = _read_json(p["claude_json"])
    if kind == "missing":
        st["state"] = "absent"
        st["shadowed"] = _claude_shadow({})
    elif kind == "too_big":
        st["detail"], st["shadowed"] = "狀態不明(設定檔超過 16 MB)", None
    elif kind == "error" or not isinstance(data, dict):
        st["detail"], st["shadowed"] = "狀態不明(設定檔格式無法辨識)", None
    else:
        servers = data.get("mcpServers")
        st["shadowed"] = _claude_shadow(data)
        if servers is None:
            st["state"] = "absent"
        elif not isinstance(servers, dict):
            st["detail"] = "狀態不明(設定檔格式無法辨識)"
        elif NAME not in servers:
            st["state"] = "absent"
        elif not isinstance(servers[NAME], dict):
            st["detail"] = "狀態不明(設定檔格式無法辨識)"
        else:
            _collect_env_values({"entry": servers[NAME]}, known)
            state, command, args, env = _entry_state(servers[NAME])
            st.update(state=state, command=command, args=args)
            if env:
                st["env_keys"] = sorted(str(k) for k in env)
            if state == "unknown":
                st["detail"] = "狀態不明(設定檔格式無法辨識)"
    if st["shadowed"] is True and not st["detail"]:
        st["detail"] = SHADOW_MSG

    if check:
        if not st["installed"]:
            reason = "不做健康檢查:Claude Code 未安裝"
        elif st["state"] != "registered":
            reason = "不做健康檢查:註冊內容不是本 repo 的伺服器"
        elif st["shadowed"] is True:
            reason = "不做健康檢查:" + SHADOW_MSG
        elif st["shadowed"] is None:
            reason = "不做健康檢查:無法判斷本 repo 是否有同名 local / project scope 註冊"
        else:
            reason = None
        if reason:
            st["detail"] = f"{st['detail']};{reason}" if st["detail"] and st["detail"] != SHADOW_MSG else reason
        else:
            res = _run([str(p["claude_cli"]), "mcp", "get", NAME])
            if res.error:
                st["health"], st["detail"] = "unknown", res.error
            elif res.code != 0:
                st["health"], st["detail"] = "failed", res.redacted(known)
            else:
                m = re.search(r"^\s*Status:\s*(.+)$", res.stdout, re.MULTILINE)
                line = m.group(1) if m else ""
                if "Connected" in line or "✔" in line:
                    st["health"] = "ok"
                elif any(w in line.lower() for w in ("fail", "error")) or "✘" in line or "✗" in line:
                    st["health"], st["detail"] = "failed", res.redacted(known)
                else:
                    st["health"], st["detail"] = "unknown", "連線狀態不明\n" + res.redacted(known)
    return st


def _codex_status(known: set) -> dict:
    p = paths()
    st = _blank(_installed(p["codex_cli"]))
    st["timeout"] = {"key": "tool_timeout_sec", "value": None, "snippet": _codex_snippet(None)}
    if not st["installed"]:
        st["detail"] = "未安裝(找不到 ~/.local/bin/codex)"
        return st
    res = _run([str(p["codex_cli"]), "mcp", "get", NAME, "--json"])
    if res.error:
        st["detail"] = res.error
        return st
    try:
        obj = json.loads(res.stdout)
    except ValueError:
        obj = None
    _collect_env_values(obj, known)      # 不論結束碼與格式,先蒐集 env 值(統整報告 RS-4)
    if res.code != 0:
        if res.code == 1 and NOT_FOUND in res.stderr:
            st["state"] = "absent"
        else:
            st["detail"] = res.redacted(known) or f"CLI 結束碼 {res.code}"
        return st
    transport = obj.get("transport") if isinstance(obj, dict) else None
    if not isinstance(transport, dict):
        st["detail"] = "狀態不明(CLI 輸出格式無法辨識)\n" + res.redacted(known)
        return st
    state, command, args, env = _entry_state(transport)
    st.update(state=state, command=command, args=args)
    if env:
        st["env_keys"] = sorted(str(k) for k in env)
    if state == "unknown":
        st["detail"] = "狀態不明(CLI 輸出格式無法辨識)\n" + res.redacted(known)
    t = obj.get("tool_timeout_sec")
    if isinstance(t, (int, float)) and not isinstance(t, bool):
        st["timeout"] = {"key": "tool_timeout_sec", "value": _fmt_num(t), "snippet": _codex_snippet(t)}
    return st


def _status(check: bool) -> tuple[dict, set]:
    known: set = set()
    out = {"server": server_argv(), "claude": _claude_status(check, known), "codex": _codex_status(known)}
    return out, known


def status(check: bool = False) -> dict:
    if not isinstance(check, bool):
        raise ValueError("check 必須是布林值")
    with _busy():
        st, known = _status(check)
    return _clean_obj(st, known)


# ---------- 指令組裝 ----------

def _check_enum(client, action) -> None:
    if not isinstance(client, str) or client not in CLIENTS:
        raise ValueError("不支援的 client:只能是 claude 或 codex")
    if not isinstance(action, str) or action not in ACTIONS:
        raise ValueError("不支援的動作:只能是 register 或 remove")


def _config_file(client: str) -> Path:
    p = paths()
    return p["claude_json"] if client == "claude" else p["codex_home"] / "config.toml"


def _plan(client: str, action: str, st: dict) -> tuple[list[str], dict]:
    p = paths()
    cs = st[client]
    label = LABELS[client]
    cli = str(p[f"{client}_cli"])
    if not cs["installed"]:
        raise ValueError(f"{label} 未安裝(找不到 ~/.local/bin/{client}),無法執行")
    if cs["state"] == "unknown":
        raise ValueError(f"{label} 的註冊狀態不明,無法執行;請先排除設定檔或 CLI 的問題")
    if action == "register" and cs["state"] == "registered":
        raise ValueError(f"{label} 已註冊且指向本 repo,不需要再註冊")
    if action == "register" and cs["state"] == "other":
        raise ValueError(f"{label} 已註冊但指向其他位置;請先移除再註冊")
    if action == "remove" and cs["state"] == "absent":
        raise ValueError(f"{label} 尚未註冊,沒有可移除的項目")

    cfg = scrub(str(_config_file(client)))
    warnings: list[str] = []
    current = None
    if action == "register":
        if client == "claude":
            argv = [cli, "mcp", "add", "-s", "user", NAME, "--", *server_argv()]
            if cs["timeout"]["value"] is None:
                warnings.append(f"Claude Code 的 MCP_TOOL_TIMEOUT 尚未設定,長工具可能逾時。網頁不會修改 "
                                f"{scrub(str(p['claude_settings']))},請自行在其 env 加入 MCP_TOOL_TIMEOUT"
                                f"(建議值 {SUGGESTED_CLAUDE_TIMEOUT})。")
        else:
            argv = [cli, "mcp", "add", NAME, "--", *server_argv()]
            warnings.append(f"codex mcp add 無法設定 tool_timeout_sec,註冊後不會有這個設定,長工具可能逾時。"
                            f"請手動在 {cfg} 的 [mcp_servers.{NAME}] 加入:tool_timeout_sec = {SUGGESTED_CODEX_TIMEOUT}")
    else:
        argv = [cli, "mcp", "remove", NAME, "-s", "user"] if client == "claude" else [cli, "mcp", "remove", NAME]
        current = {"command": cs["command"], "args": cs["args"], "env_keys": cs["env_keys"], "timeout": cs["timeout"]}
        if cs["env_keys"]:
            warnings.append(f"env({'、'.join(cs['env_keys'])})會一起消失,重新註冊不會帶回。")
        if client == "codex" and cs["timeout"]["value"] is not None:
            v = cs["timeout"]["value"]
            warnings.append(f"目前 tool_timeout_sec = {v} 會一起消失,重新註冊也不會帶回;長工具可能逾時。"
                            f"要恢復請手動在 {cfg} 的 [mcp_servers.{NAME}] 加入:tool_timeout_sec = {v}")
        if cs["state"] == "other":
            warnings.append("目前的註冊不是指向本 repo;移除後要再按「註冊」才會指向本 repo。")
    pub = {"argv": argv, "files": [cfg], "current": current, "warnings": warnings}
    return argv, pub


def plan(client, action) -> dict:
    _check_enum(client, action)
    with _busy():
        st, known = _status(False)
        _, pub = _plan(client, action, st)
    return _clean_obj(pub, known)


def apply(client, action) -> dict:
    _check_enum(client, action)
    with _busy():
        st, known = _status(False)
        argv, _ = _plan(client, action, st)
        res = _run(argv)
        new, known2 = _status(False)
    known |= known2
    output = res.redacted(known)
    expected = "registered" if action == "register" else "absent"
    ok = res.code == 0 and new[client]["state"] == expected
    return _clean_obj({"ok": ok, "exit_code": res.code, "output": output, "status": new}, known)
