#!/usr/bin/env python3
"""靜態與安全檢查(CHG-20260803-06)。stdlib-only、不執行被檢查的程式碼、三平台一致。

**為什麼內建而非委派**:其他驗證層(型別、覆蓋率、SAST、相依漏洞)需要外部工具,
所以採「宣告 + 產物」的委派模式。但這一層不行——**安全檢查不該有「這個專案沒裝工具
所以跳過」這種出口**。凡是能用 stdlib 的 AST 做到的,就內建、always-on、無法關閉。

**為什麼是 AST 而非 grep**:`# eval(x)` 是註解、`"eval("` 是字串,grep 都會誤報;
而 `getattr(builtins, "ev"+"al")` grep 抓不到。AST 看的是語法結構,不是文字。

**涵蓋範圍的誠實聲明**:本檢查抓的是**已知形態**的問題。它不是 SAST,不做資料流分析,
不知道某個變數是否來自使用者輸入。委派層的 SAST(見 autopilot 的 quality gate)才做那些。
把本檢查通過當成「沒有安全問題」是誤讀——它只代表「沒有這幾種已知形態」。

用法:
  python3 static_check.py --repo .                     # 全 repo
  python3 static_check.py --repo . --paths a.py b.py   # 指定檔案(供逐 task 用)
  python3 static_check.py --repo . --json
退出碼:0 = 無發現;1 = 有發現;2 = 參數錯誤。
"""
from __future__ import annotations
import argparse
import ast
import json
import re
import sys
from pathlib import Path

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

ALLOWLIST = Path(__file__).resolve().parent.parent / "assets" / "static_allowlist.json"
# `.venv` / `venv`(CHG-20260806-02):開發者的專案 venv 落在 repo 內,裡面是**第三方
# 套件的原始碼**——那不是被測程式,掃它只會得到 urllib3 的 unused-import 之類的噪音,
# 而噪音是讓人把整道閘關掉的第一因。同理排除 `artifacts/`(產物,不是原始碼)。
SKIP_PARTS = (".git", "node_modules", "_site", "__pycache__", ".claude",
              ".venv", "venv", "site-packages", "artifacts")
# plugins/*/skills/ 是 build_suite 生成的複本;檢查來源即可,否則每個發現都重複兩次
SKIP_PREFIX = ("plugins/ai-sdlc-suite/skills/", "plugins/intel-analysis/skills/",
               "plugins/writing/skills/")

SECRET_PATTERNS = [
    ("aws-access-key", re.compile(r"AKIA[0-9A-Z]{16}")),
    ("github-token", re.compile(r"(ghp_[A-Za-z0-9]{36}|github_pat_[A-Za-z0-9_]{22,})")),
    ("slack-token", re.compile(r"xox[baprs]-[A-Za-z0-9-]{10,}")),
    ("private-key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("jwt", re.compile(r"eyJ[A-Za-z0-9_\-]{10,}\.eyJ[A-Za-z0-9_\-]{10,}\.")),
    ("credential-assignment", re.compile(
        r"(?i)(password|passwd|secret|api[_-]?key|access[_-]?token)\s*[:=]\s*"
        r"['\"][A-Za-z0-9+/_\-]{12,}['\"]")),
]
# 掃描 secrets 的副檔名——不限 .py:金鑰更常出現在設定檔與 workflow
SECRET_SUFFIXES = (".py", ".sh", ".yml", ".yaml", ".json", ".md", ".toml", ".ini", ".cfg")

SEVERITY = {"security": "高", "lint": "中"}


def _is_literal(node) -> bool:
    return isinstance(node, ast.Constant)


def _interpolated(node) -> bool:
    """指令字串是否由變數拼出來(f-string / .format / + 串接)——注入的必要條件。"""
    if isinstance(node, ast.JoinedStr):
        return any(isinstance(v, ast.FormattedValue) for v in node.values)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        return True
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
        return node.func.attr in ("format", "join")
    return False


class Visitor(ast.NodeVisitor):
    def __init__(self, rel: str, is_test: bool):
        self.rel, self.is_test = rel, is_test
        self.found: list[dict] = []
        self.imported: dict[str, int] = {}
        self.used: set[str] = set()

    def add(self, rule, kind, lineno, detail):
        self.found.append({"file": self.rel, "line": lineno, "rule": rule,
                           "kind": kind, "detail": detail})

    # --- 名稱使用追蹤(未使用 import)---
    def visit_Import(self, node):
        for a in node.names:
            self.imported.setdefault((a.asname or a.name.split(".")[0]), node.lineno)
        self.generic_visit(node)

    def visit_ImportFrom(self, node):
        for a in node.names:
            if a.name != "*":
                self.imported.setdefault((a.asname or a.name), node.lineno)
        self.generic_visit(node)

    def visit_Name(self, node):
        self.used.add(node.id)
        self.generic_visit(node)

    def visit_Attribute(self, node):
        if isinstance(node.value, ast.Name):
            self.used.add(node.value.id)
        self.generic_visit(node)

    # --- 安全 ---
    def visit_Call(self, node):
        fn = node.func.attr if isinstance(node.func, ast.Attribute) else getattr(node.func, "id", "")
        mod = (node.func.value.id
               if isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name)
               else "")

        if fn in ("eval", "exec") and not mod:
            self.add("dangerous-eval", "security", node.lineno,
                     f"{fn}() 會執行任意程式碼;改用明確的分派表或 ast.literal_eval")
        if mod in ("pickle", "marshal", "shelve", "dill") and fn in ("load", "loads"):
            self.add("unsafe-deserialization", "security", node.lineno,
                     f"{mod}.{fn} 對不可信輸入等同任意程式碼執行")
        if mod == "yaml" and fn == "load":
            safe = any(k.arg == "Loader" and "Safe" in ast.unparse(k.value)
                       for k in (node.keywords or []))
            if not safe:
                self.add("unsafe-yaml", "security", node.lineno,
                         "yaml.load 未指定 SafeLoader,可建構任意物件")
        if fn == "mktemp" and mod == "tempfile":
            self.add("insecure-temp", "security", node.lineno,
                     "tempfile.mktemp 有 race condition;改用 mkstemp/NamedTemporaryFile")
        if mod == "hashlib" and fn in ("md5", "sha1"):
            self.add("weak-hash", "security", node.lineno,
                     f"hashlib.{fn} 不可用於安全用途(可用於非安全的內容指紋,需列入 allowlist)")

        for kw in node.keywords or []:
            if kw.arg == "shell" and isinstance(kw.value, ast.Constant) and kw.value.value is True:
                arg0 = node.args[0] if node.args else None
                if arg0 is not None and _interpolated(arg0):
                    self.add("shell-injection", "security", node.lineno,
                             "shell=True 且指令由變數拼接——這是命令注入的形態;"
                             "改用 argv 陣列,或確認插入值不來自外部")
                else:
                    self.add("shell-true", "security", node.lineno,
                             "shell=True:指令會經 shell 解譯。若指令來源不是操作者本人,"
                             "這是內容驅動執行。正當用途請列入 allowlist 並寫明理由")
            if kw.arg in ("verify", "check_hostname") and \
                    isinstance(kw.value, ast.Constant) and kw.value.value is False:
                self.add("tls-verification-off", "security", node.lineno,
                         f"{kw.arg}=False 關閉 TLS 驗證,等同接受中間人")
        self.generic_visit(node)

    # --- lint ---
    def visit_ExceptHandler(self, node):
        if node.type is None:
            self.add("bare-except", "lint", node.lineno,
                     "bare except 會吞掉 KeyboardInterrupt 與 SystemExit,也遮蔽真正的錯誤")
        self.generic_visit(node)

    def visit_arguments(self, node):
        for d in list(node.defaults) + [x for x in node.kw_defaults if x]:
            if isinstance(d, (ast.List, ast.Dict, ast.Set)):
                self.add("mutable-default", "lint", getattr(d, "lineno", 0),
                         "可變預設參數在呼叫間共用,是經典的隱性狀態錯誤")
        self.generic_visit(node)


def scan_file(path: Path, rel: str) -> list[dict]:
    found: list[dict] = []
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return found

    if path.suffix in SECRET_SUFFIXES:
        for name, pat in SECRET_PATTERNS:
            for m in pat.finditer(text):
                line = text[:m.start()].count("\n") + 1
                found.append({"file": rel, "line": line, "rule": f"secret:{name}",
                              "kind": "security",
                              "detail": f"疑似 {name}:{m.group(0)[:12]}…"})

    if path.suffix == ".py":
        try:
            tree = ast.parse(text)
        except SyntaxError as e:
            return found + [{"file": rel, "line": e.lineno or 0, "rule": "syntax-error",
                             "kind": "lint", "detail": str(e)}]
        v = Visitor(rel, is_test=path.name.startswith("test_"))
        v.visit(tree)
        found += v.found
        for name, lineno in v.imported.items():
            if name not in v.used and name != "annotations":
                found.append({"file": rel, "line": lineno, "rule": "unused-import",
                              "kind": "lint", "detail": f"未使用的 import:{name}"})
    return found


def load_allowlist(path=None) -> dict:
    p = Path(path) if path else ALLOWLIST
    if not p.is_file():
        return {}
    data = json.loads(p.read_text(encoding="utf-8-sig"))
    return data.get("allow", {})


def allowed(finding: dict, allow: dict) -> str | None:
    """回豁免理由;未豁免回 None。鍵為 `<檔案>::<規則>`——**不含行號**,
    否則每次改動都要重填,而那會逼人把整份 allowlist 當噪音略過。"""
    return allow.get(f"{finding['file']}::{finding['rule']}")


def iter_files(repo: Path, paths: list[str] | None):
    if paths:
        for p in paths:
            f = (repo / p) if not Path(p).is_absolute() else Path(p)
            if f.is_file():
                yield f, Path(p).as_posix()
        return
    for f in sorted(repo.rglob("*")):
        if not f.is_file() or f.suffix not in SECRET_SUFFIXES:
            continue
        relp = f.relative_to(repo)
        rel = relp.as_posix()
        # 比對 **相對於 repo 的** parts,不是絕對路徑的 parts。
        # 實測踩到:本專案的 worktree 位於 `.claude/worktrees/…`,而 `.claude` 在跳過清單裡,
        # 於是絕對路徑比對讓**每一個檔案都被跳過**,檢查器回報「通過」卻什麼都沒看。
        # 這正是 KN-001 的形狀——而且發生在一個為了找這種問題而寫的工具上。
        if any(s in relp.parts for s in SKIP_PARTS) or rel.startswith(SKIP_PREFIX):
            continue
        yield f, rel


def main(argv) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", default=".")
    ap.add_argument("--paths", nargs="*", default=None,
                    help="只檢查這些檔案(供逐 task 對變更檔使用)")
    ap.add_argument("--allowlist", default=None)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--only", choices=["security", "lint"], default=None)
    args = ap.parse_args(argv[1:])

    repo = Path(args.repo).resolve()
    if not repo.is_dir():
        print(f"找不到 repo:{repo}")
        return 2
    try:
        allow = load_allowlist(args.allowlist)
    except (json.JSONDecodeError, OSError) as e:
        print(f"❌ allowlist 讀取失敗({e})——不得因此略過檢查")
        return 2

    findings: list[dict] = []
    exempted: list[dict] = []
    for f, rel in iter_files(repo, args.paths):
        for item in scan_file(f, rel):
            if args.only and item["kind"] != args.only:
                continue
            reason = allowed(item, allow)
            (exempted if reason else findings).append({**item, "reason": reason})

    if args.json:
        print(json.dumps({"findings": findings, "exempted": exempted},
                         ensure_ascii=False, indent=2))
    else:
        if exempted:
            print(f"🔓 已豁免 {len(exempted)} 項(理由記於 allowlist):")
            for ex in exempted[:10]:
                # 變數名不與前面的 except ... as e 重用:Python 3 會在 except 區塊結束時
                # 刪掉那個名字,重用它讓型別檢查器讀成「在 except 之外對 e 賦值」
                print(f"   {ex['file']}::{ex['rule']} — {ex['reason']}")
            if len(exempted) > 10:
                print(f"   …另有 {len(exempted) - 10} 項")
        if findings:
            print(f"\n❌ 靜態/安全檢查發現 {len(findings)} 項:")
            for k in ("security", "lint"):
                group = [f for f in findings if f["kind"] == k]
                if not group:
                    continue
                print(f"\n  [{SEVERITY[k]}風險 · {k}] {len(group)} 項")
                for f in group:
                    print(f"    {f['file']}:{f['line']}  {f['rule']}")
                    print(f"      {f['detail']}")
            print("\n  正當用途請加入 assets/static_allowlist.json 並**寫明理由**——"
                  "豁免要留下署名,不是靜默略過。")
        else:
            print(f"✅ 靜態/安全檢查通過"
                  + (f"(另有 {len(exempted)} 項已具名豁免)" if exempted else ""))
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
