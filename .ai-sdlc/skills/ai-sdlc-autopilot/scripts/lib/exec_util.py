#!/usr/bin/env python3
"""執行輔助:shell/agent 呼叫、簡報組裝、live handshake、退出碼訊息。"""
from __future__ import annotations
import contextlib
import json
import os
import re
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
from datetime import datetime, timezone
from pathlib import Path


def die(msg: str, code: int) -> int:
    print(f"{'HALT' if code == 3 else 'ERROR' if code == 1 else 'INVALID-PLAN'}: {msg}")
    return code


# ── 共用啟動器(CHG-20260926-01 T2)──────────────────────────────────────────
# runner 派出的每一個子行程都走這裡:施工者、審查席、測試與驗收指令、靜態檢查。
# 無人值守時,一個卡住的子行程會讓整個執行永遠停在那裡,而沒有人看得到它在等什麼——
# 所以四件事一起做,而且 run_shell 與 run_argv **共用同一個內部函式**,兩條路不會只剩一條有上限:
#   · 上限:預設 3600 秒(--process-timeout)。它是給卡死行程的上限,不是預估;
#   · stdin 是 DEVNULL:提問讀不到任何回答,立即 EOF,不會等一個不存在的人;
#   · 自己的行程群組:上限到期或 runner 被中斷時,連同孫行程整組收掉,不留孤兒;
#   · 不設上限必須明示傳 NO_LIMIT(以 `is` 判斷)——`None`、0 或負數都不是「不設上限」。
DEFAULT_PROCESS_TIMEOUT = 3600
KILL_GRACE = 5            # SIGTERM 之後等多久才 SIGKILL(秒)
TIMEOUT_RC = 124          # 與 coreutils `timeout` 同一個慣例
# 逾時時附在兩個輸出串流尾端的註記開頭。panel.retryable 以它辨認「stdout 只有 runner 自己的註記」
# (代決 R1-D:這種逾時算行程層級失敗,重試同一席一次)。
TIMEOUT_NOTE_PREFIX = "[runner] 逾時"
# 無人值守訊號(代決 B6):只由 runner 設給它派出的 agent;plugin 的 hooks 讀同一個名字。
UNATTENDED_ENV = "AI_SDLC_UNATTENDED"


class _NoLimit:
    """「不設上限」的唯一物件。用一個專屬物件而不是 None / 0:
    `timeout=None` 的意思是「用全域上限」,把它讀成「不設上限」正是上一版草稿的退步。"""

    def __repr__(self) -> str:
        return "NO_LIMIT"


NO_LIMIT = _NoLimit()
_process_timeout: float = DEFAULT_PROCESS_TIMEOUT


def _check_limit(seconds) -> float:
    if isinstance(seconds, bool) or not isinstance(seconds, (int, float)) or not seconds > 0:
        raise ValueError(f"行程上限必須是正數秒(得 {seconds!r});不設上限請明示傳 NO_LIMIT")
    return float(seconds)


def set_process_timeout(seconds) -> None:
    """設定全域行程上限(--process-timeout)。0 與負數不接受——它們不是「不設上限」。"""
    global _process_timeout
    _process_timeout = _check_limit(seconds)


def process_timeout() -> float:
    """讀回目前的全域行程上限(秒)。"""
    return _process_timeout


class ProcResult(subprocess.CompletedProcess):
    """`CompletedProcess` 加上 `timed_out`:既有呼叫端照舊讀 returncode / stdout / stderr。"""

    def __init__(self, args, returncode, stdout=None, stderr=None, timed_out=False):
        super().__init__(args, returncode, stdout, stderr)
        self.timed_out = timed_out


def _kill_group(proc, grace: float = KILL_GRACE) -> None:
    """結束子行程所在的整個行程群組:先請它結束,`grace` 秒後強制結束(0 = 立即強制結束)。"""
    if sys.platform == "win32":
        if proc.poll() is None:  # diffcov-exempt: platform — Windows 分支(CTRL_BREAK_EVENT / taskkill / PATHEXT 逐目錄搜尋);本量測環境是 Linux,由 T15 的 tests-manual.yml 完整矩陣實跑 [signoff: self(T15 實作者自審,待實作後審查席複核) @ CHG-20260926-01]
            try:  # diffcov-exempt: platform — Windows 分支(CTRL_BREAK_EVENT / taskkill / PATHEXT 逐目錄搜尋);本量測環境是 Linux,由 T15 的 tests-manual.yml 完整矩陣實跑 [signoff: self(T15 實作者自審,待實作後審查席複核) @ CHG-20260926-01]
                proc.send_signal(signal.CTRL_BREAK_EVENT)  # diffcov-exempt: platform — Windows 分支(CTRL_BREAK_EVENT / taskkill / PATHEXT 逐目錄搜尋);本量測環境是 Linux,由 T15 的 tests-manual.yml 完整矩陣實跑 [signoff: self(T15 實作者自審,待實作後審查席複核) @ CHG-20260926-01]
                proc.wait(timeout=grace)  # diffcov-exempt: platform — Windows 分支(CTRL_BREAK_EVENT / taskkill / PATHEXT 逐目錄搜尋);本量測環境是 Linux,由 T15 的 tests-manual.yml 完整矩陣實跑 [signoff: self(T15 實作者自審,待實作後審查席複核) @ CHG-20260926-01]
            except (OSError, subprocess.TimeoutExpired):  # diffcov-exempt: platform — Windows 分支(CTRL_BREAK_EVENT / taskkill / PATHEXT 逐目錄搜尋);本量測環境是 Linux,由 T15 的 tests-manual.yml 完整矩陣實跑 [signoff: self(T15 實作者自審,待實作後審查席複核) @ CHG-20260926-01]
                pass  # diffcov-exempt: platform — Windows 分支(CTRL_BREAK_EVENT / taskkill / PATHEXT 逐目錄搜尋);本量測環境是 Linux,由 T15 的 tests-manual.yml 完整矩陣實跑 [signoff: self(T15 實作者自審,待實作後審查席複核) @ CHG-20260926-01]
        # 不經 shell 時 %SystemRoot% 不會被展開,所以組成路徑而不是寫成字串
        taskkill = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "taskkill.exe"  # diffcov-exempt: platform — Windows 分支(CTRL_BREAK_EVENT / taskkill / PATHEXT 逐目錄搜尋);本量測環境是 Linux,由 T15 的 tests-manual.yml 完整矩陣實跑 [signoff: self(T15 實作者自審,待實作後審查席複核) @ CHG-20260926-01]
        subprocess.run([str(taskkill), "/F", "/T", "/PID", str(proc.pid)],  # diffcov-exempt: platform — Windows 分支(CTRL_BREAK_EVENT / taskkill / PATHEXT 逐目錄搜尋);本量測環境是 Linux,由 T15 的 tests-manual.yml 完整矩陣實跑 [signoff: self(T15 實作者自審,待實作後審查席複核) @ CHG-20260926-01]
                       stdin=subprocess.DEVNULL, capture_output=True)
    else:
        pgid = proc.pid           # start_new_session:子行程是自己群組的組長
        try:
            os.killpg(pgid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            return
        deadline = time.monotonic() + grace
        while time.monotonic() < deadline:
            proc.poll()           # 收掉組長的殭屍,否則群組看起來永遠還在
            try:
                os.killpg(pgid, 0)
            except (ProcessLookupError, PermissionError):
                return
            time.sleep(0.05)
        try:
            os.killpg(pgid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass


# 收掉群組的期間延後處理的訊號(Windows 沒有 SIGHUP)
_DEFERRED_SIGNALS = tuple(getattr(signal, n) for n in ("SIGINT", "SIGTERM", "SIGHUP")
                          if hasattr(signal, n))


@contextlib.contextmanager
def signals_deferred():
    """區塊執行期間,SIGINT / SIGTERM / SIGHUP 只記下、不處理;離開時還原原本的處理,再補送第一個。

    收掉行程群組要 SIGTERM、等寬限、再 SIGKILL。runner 的訊號處理是拋 SystemExit:寬限期間的第二次
    中斷(使用者連按兩次 Ctrl-C、或 SIGTERM 之後又一個)會從等待迴圈裡拋出,SIGKILL 永遠沒送出,
    忽略 SIGTERM 的施工者就成了孤兒(實作後審查,四席)。所以收群組的整段期間把訊號延後,收完再補送:
    中斷照樣生效,只是晚幾秒。已被設為忽略的訊號(nohup)與 C 層裝的處理函式不接手。不在主執行緒時什麼都不改——
    Python 只在主執行緒處理訊號,也只准在那裡換處理函式。
    """
    got: list[int] = []
    saved: dict = {}
    if threading.current_thread() is threading.main_thread():
        for sig in _DEFERRED_SIGNALS:
            cur = signal.getsignal(sig)
            # C 層裝的處理函式 getsignal 回 None,接手之後還原不回去(TypeError),跟忽略的一樣不接手
            if cur is not None and cur != signal.SIG_IGN:
                saved[sig] = signal.signal(sig, lambda n, _f: got.append(n))
    try:
        yield
    finally:
        for sig, handler in saved.items():
            signal.signal(sig, handler)
        if got:
            signal.raise_signal(got[0])


def _launch(cmd, cwd: Path, shell: bool, timeout, env, kill_grace=None) -> ProcResult:
    """run_shell 與 run_argv 共用的啟動器:上限、空 stdin、行程群組、中斷處理都在這裡。

    `kill_grace`:收掉群組時 SIGTERM 之後等幾秒才 SIGKILL(None = KILL_GRACE)。變異 harness 傳 0:
    正在跑的是變異體的測試,目標檔由 harness 自己還原,測試不需要清理時間;而呼叫 harness 的
    啟動器給它的寬限也只有 KILL_GRACE 秒(CHG-20260926-01 T11)。
    """
    if timeout is NO_LIMIT:
        limit: float | None = None
    elif timeout is None:
        limit = _process_timeout
    else:
        limit = _check_limit(timeout)
    if kill_grace is None:
        grace = float(KILL_GRACE)
    elif isinstance(kill_grace, bool) or not isinstance(kill_grace, (int, float)) or kill_grace < 0:
        raise ValueError(f"kill_grace 必須是 0 以上的秒數(得 {kill_grace!r})")
    else:
        grace = float(kill_grace)
    kw: dict = {}
    if sys.platform == "win32":
        kw["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP  # diffcov-exempt: platform — Windows 分支(CTRL_BREAK_EVENT / taskkill / PATHEXT 逐目錄搜尋);本量測環境是 Linux,由 T15 的 tests-manual.yml 完整矩陣實跑 [signoff: self(T15 實作者自審,待實作後審查席複核) @ CHG-20260926-01]
    else:
        kw["start_new_session"] = True
    # encoding 明示(CHG-20260803-01 T2):text=True 不帶 encoding 會用 locale 編碼,
    # 非 UTF-8 locale(如 Windows cp932)讀到 CJK 輸出會 UnicodeDecodeError。
    proc = subprocess.Popen(cmd, shell=shell, cwd=str(cwd), env=env,
                            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, text=True, encoding="utf-8",
                            errors="replace", **kw)
    try:
        out, err = proc.communicate(timeout=limit)
    except subprocess.TimeoutExpired:
        with signals_deferred():     # 寬限期間的中斷延後:SIGKILL 一定送得出去
            _kill_group(proc, grace)
        try:
            out, err = proc.communicate(timeout=KILL_GRACE)
        except subprocess.TimeoutExpired:
            out, err = "", ""   # 有行程逃出群組還握著輸出管道;不再等它
        note = (f"\n{TIMEOUT_NOTE_PREFIX}:超過 {limit:g} 秒(--process-timeout)仍未結束,"
                f"已終止它的整個行程群組")
        return ProcResult(cmd, TIMEOUT_RC, (out or "") + note, (err or "") + note,
                          timed_out=True)
    except BaseException:
        # runner 被中斷(SIGINT / SIGTERM / SIGHUP)也要收掉整組,否則派出去的行程在
        # runner 結束後照樣寫檔——那是「中斷之後還在改工作樹」的孤兒。寬限期間的第二次中斷
        # 延後到收完之後(signals_deferred),否則它會在 SIGKILL 之前把 runner 帶走。
        with signals_deferred():
            _kill_group(proc, grace)
        raise
    return ProcResult(cmd, proc.returncode, out, err)


def run_shell(cmd: str, cwd: Path, timeout=None, env=None, *, kill_grace=None) -> ProcResult:
    """經 shell 執行**操作者自己給的**指令模板(--test-cmd、--verify-cmd…)。

    帶 CHG 文字或施工者自取名稱的呼叫一律走 run_argv / run_git,不經這裡。
    `timeout=None` = 用 --process-timeout 的值;不設上限須明示傳 NO_LIMIT。
    `kill_grace`(秒,None = KILL_GRACE):逾時或被中斷時,SIGTERM 之後等多久才 SIGKILL。
    """
    return _launch(cmd, cwd, shell=True, timeout=timeout, env=env, kill_grace=kill_grace)


def _resolve_exe(exe: str) -> str | None:
    """解析 argv[0] 成絕對路徑(Windows 的 PATHEXT:`git.bat`、`gh.cmd`,見 ci_gate.query_cmd)。

    搜尋的 PATH 去掉目前目錄:Windows 上 repo 根目錄裡的 `git.bat` 否則可能被選中。
    `shutil.which` 在 Windows 上不論傳入的 PATH 為何都會先找目前目錄,
    所以 Windows 改為逐目錄比對 PATHEXT;有路徑分隔的 argv[0] 照原樣解析。
    """
    if os.sep in exe or (os.altsep and os.altsep in exe):
        return shutil.which(exe)
    dirs = [p for p in os.environ.get("PATH", "").split(os.pathsep) if p and p != os.curdir]
    if sys.platform != "win32":
        return shutil.which(exe, path=os.pathsep.join(dirs))
    exts = ([""] if os.path.splitext(exe)[1] else  # diffcov-exempt: platform — Windows 分支(CTRL_BREAK_EVENT / taskkill / PATHEXT 逐目錄搜尋);本量測環境是 Linux,由 T15 的 tests-manual.yml 完整矩陣實跑 [signoff: self(T15 實作者自審,待實作後審查席複核) @ CHG-20260926-01]
            [e for e in os.environ.get("PATHEXT", ".COM;.EXE;.BAT;.CMD").split(os.pathsep) if e])
    for d in dirs:  # diffcov-exempt: platform — Windows 分支(CTRL_BREAK_EVENT / taskkill / PATHEXT 逐目錄搜尋);本量測環境是 Linux,由 T15 的 tests-manual.yml 完整矩陣實跑 [signoff: self(T15 實作者自審,待實作後審查席複核) @ CHG-20260926-01]
        for ext in exts:  # diffcov-exempt: platform — Windows 分支(CTRL_BREAK_EVENT / taskkill / PATHEXT 逐目錄搜尋);本量測環境是 Linux,由 T15 的 tests-manual.yml 完整矩陣實跑 [signoff: self(T15 實作者自審,待實作後審查席複核) @ CHG-20260926-01]
            cand = os.path.join(d, exe + ext)  # diffcov-exempt: platform — Windows 分支(CTRL_BREAK_EVENT / taskkill / PATHEXT 逐目錄搜尋);本量測環境是 Linux,由 T15 的 tests-manual.yml 完整矩陣實跑 [signoff: self(T15 實作者自審,待實作後審查席複核) @ CHG-20260926-01]
            if os.path.isfile(cand):  # diffcov-exempt: platform — Windows 分支(CTRL_BREAK_EVENT / taskkill / PATHEXT 逐目錄搜尋);本量測環境是 Linux,由 T15 的 tests-manual.yml 完整矩陣實跑 [signoff: self(T15 實作者自審,待實作後審查席複核) @ CHG-20260926-01]
                return cand  # diffcov-exempt: platform — Windows 分支(CTRL_BREAK_EVENT / taskkill / PATHEXT 逐目錄搜尋);本量測環境是 Linux,由 T15 的 tests-manual.yml 完整矩陣實跑 [signoff: self(T15 實作者自審,待實作後審查席複核) @ CHG-20260926-01]
    return None  # diffcov-exempt: platform — Windows 分支(CTRL_BREAK_EVENT / taskkill / PATHEXT 逐目錄搜尋);本量測環境是 Linux,由 T15 的 tests-manual.yml 完整矩陣實跑 [signoff: self(T15 實作者自審,待實作後審查席複核) @ CHG-20260926-01]


def run_argv(argv, cwd: Path, timeout=None, env=None) -> ProcResult:
    """不經 shell 執行一個 argv 陣列;與 run_shell 共用同一個啟動器與上限。

    找不到執行檔回 127(與 shell 同一個碼),不拋 OSError——崩潰與停下要分得開(KN-003)。
    """
    argv = [str(a) for a in argv]
    exe = _resolve_exe(argv[0]) if argv else None
    if not exe:
        msg = f"找不到執行檔:{argv[0] if argv else '(空的 argv)'}"
        return ProcResult(argv, 127, "", msg)
    return _launch([exe, *argv[1:]], cwd, shell=False, timeout=timeout, env=env)


def run_git(args, cwd: Path, timeout=None) -> ProcResult:
    """以 argv 執行 git——commit 訊息與檔名不經 shell 解譯。"""
    return run_argv(["git", *args], cwd, timeout=timeout)


# cmd.exe 的內建指令:沒有對應的執行檔,解析不到不代表找不到指令。
_CMD_BUILTINS = frozenset(
    "assoc break call cd chdir cls color copy date del dir echo endlocal erase exit for ftype "
    "goto if md mkdir mklink move path pause popd prompt pushd rd rem ren rename rmdir set "
    "setlocal shift start time title type ver verify vol".split())


def cmd_exe_missing(cmd: str, cwd, path: str | None = None, pathext: str | None = None) -> str | None:
    """依 cmd.exe 的查找規則確認指令的第一個 token 找不到:回那個 token;找得到或判不出來回 None。

    查找:含路徑者相對 `cwd`;否則先 `cwd`、再 PATH 逐目錄;每個候選照原樣與加上 PATHEXT 各試一次。
    判不出來(內建指令、帶 `%` `!` `^` 等展開或運算字元、引號不成對)一律回 None——
    呼叫端把 None 當「照原樣回報」,所以這裡寧可少說找不到。
    """
    try:
        toks = shlex.split(cmd, posix=False)
    except ValueError:
        return None
    tok = toks[0].strip('"') if toks else ""
    if not tok or tok.lower() in _CMD_BUILTINS or re.search(r'[%!^&|<>()@=,;"]', tok):
        return None
    exts = [e for e in (pathext if pathext is not None
                        else os.environ.get("PATHEXT", ".COM;.EXE;.BAT;.CMD")).split(";") if e]
    if "/" in tok or "\\" in tok or ":" in tok:
        bases = [os.path.join(str(cwd), tok)]
    else:
        dirs = (path if path is not None else os.environ.get("PATH", "")).split(os.pathsep)
        bases = [os.path.join(str(cwd), tok)] + [os.path.join(d, tok) for d in dirs if d]
    for base in bases:
        for cand in [base] + [base + e for e in exts] + [base + e.lower() for e in exts]:
            if os.path.exists(cand):
                return None
    return tok


def shell_not_found(r: ProcResult, cmd: str, cwd, platform: str | None = None) -> ProcResult:
    """經 shell 執行之後,把「找不到指令」統一成 127(與 run_argv、POSIX sh 同一個碼;KN-003)。

    POSIX 的 sh 本來就回 127。Windows 的 cmd.exe 找不到指令時回 1(或 9009),與「指令跑了、回 1」
    無法以退出碼區分(見 autopilot_sentinels 的 LAUNCH_FAIL 表)——所以在**已經失敗之後**以
    cmd_exe_missing 解析第一個 token,確定找不到才改記 127。只在失敗後判斷:判錯的代價是換一句
    停下訊息,不會擋掉一個跑得起來的指令。其餘平台與其餘情形原樣回傳。
    """
    if (platform or sys.platform) != "win32" or r.returncode not in (1, 9009) or r.timed_out:
        return r
    missing = cmd_exe_missing(cmd, cwd)
    if missing is None:
        return r
    note = f"找不到執行檔:{missing}(cmd.exe 以退出碼 {r.returncode} 回報;視同 127)"
    err = (r.stderr or "").rstrip("\n")
    return ProcResult(r.args, 127, r.stdout, f"{err}\n{note}" if err else note)


def agent_call(tmpl: str, brief: str, cwd: Path):
    with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False, encoding="utf-8") as f:
        f.write(brief)
        brief_path = f.name
    # 只設給 agent 子行程(代決 B6);Popen(env=) 會整個取代環境,所以合併 os.environ
    env = {**os.environ, UNATTENDED_ENV: "1"}
    cmd = tmpl.replace("{brief}", brief_path)
    # Windows:cmd.exe 找不到 --agent-cmd 時回 1,不改記 127 就會被當成施工者停下(要人補答案)
    return shell_not_found(run_shell(cmd, cwd, env=env), cmd, cwd)


# ── 回修迴圈(CHG-20260803-07)────────────────────────────────────────────
# 回修那一輪必須看得到上一輪為什麼失敗,否則它只是再擲一次骰子。
# 回饋一律**原文擷取**:runner 是 no-LLM 狀態機,這條界線不為了回饋而破——
# 讓模型摘要上一輪會遺失決定性細節,還多一層「模型轉述模型」的失真。
# ratchet / flaky 自 CHG-20260803-08 起加入:兩道閘的失敗同樣要能回送給下一輪,
# 否則「測試被刪掉」與「重跑不一致」只會得到一句沒有下文的 halt。
FAILURE_SOURCES = ("test", "ratchet", "static", "flaky", "mutation", "review")
NOTE_LIMIT = 800
# 實際操作驗收時發現:Python 3.13+ 的彩色 traceback 會把 ANSI 逃脫序列一起送進回饋。
# 那是給終端機看的,不是給讀 brief 的人或模型看的——而且它會吃掉截斷上限的額度。
ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def failure_note(source: str, payload: str, limit: int = NOTE_LIMIT) -> str:
    """把一次閘失敗擷取成帶來源標籤的段落。原文,不轉述(僅去除 ANSI 色碼)。"""
    if source not in FAILURE_SOURCES:
        raise ValueError(f"未知的失敗來源:{source!r}(可用:{', '.join(FAILURE_SOURCES)})")
    body = ANSI_RE.sub("", payload or "").strip()
    if not body:
        # 沒有輸出也是一種證據,但**不能**寫成任何像「通過」的字眼——
        # 回饋若誤導下一輪,比沒有回饋更糟。
        body = "(該閘失敗但未產生任何輸出)"
    tail = ""
    if len(body) > limit:
        body, tail = body[:limit], "\n…(已截斷,完整輸出見執行紀錄)"
    return f"[failed-gate: {source}]\n{body}{tail}"


def compose_extra(notes) -> str:
    """把上一輪的 failure notes 組成 brief 的附加段;第一輪(無 notes)為空字串。"""
    kept = [n for n in (notes or []) if n]
    if not kept:
        return ""
    return ("\n## 上一輪失敗原因(原文擷取,請逐項修正後再交)\n\n"
            + "\n\n".join(kept) + "\n")


def compose_review_extra(notes) -> str:
    """複審用的附加段:把上一輪的 findings 交給審查者逐項確認(scoped re-review)。"""
    kept = [n for n in (notes or []) if n]
    if not kept:
        return ""
    return ("\n## 本輪須確認的 findings(上一輪的失敗,請逐項回覆已解 / 未解)\n\n"
            + "\n\n".join(kept) + "\n")


def mutation_enabled(args) -> tuple[bool, str | None]:
    """變異閘是否執行。回 (是否執行, 留痕訊息 or None)。

    自 v1.10.0 起**預設開啟**:缺 `--test-cmd` 會 halt(存在性是強制的),
    而唯一檢驗「測試夠不夠強」的閘卻是選配的——這個排序反了。
    `--mutation` 保留為相容 no-op,`--no-mutation` 是明示且留痕的逃生口。
    """
    if getattr(args, "no_mutation", False):
        return False, ("⚠️ 變異閘已以 --no-mutation 明示關閉:本輪未驗測試強度,"
                       "須記入 ACC 的未涵蓋欄")
    return True, None


MAX_FIX_ROUNDS = 3


def next_round(round_idx: int, max_rounds: int) -> tuple[bool, str]:
    """這一輪失敗後還有沒有下一輪。回 (是否續作, 裁決 ∈ {'retry','halt'})。

    達上限一律 halt,不得靜默放行——build 迴圈的失敗代價是把壞碼往下游送(KN-004)。
    """
    if round_idx < max_rounds:
        return True, "retry"
    return False, "halt"


def agent_for_round(round_idx: int, max_rounds: int, agent_cmd: str,
                    escalate_cmd: str | None) -> tuple[str, str | None]:
    """這一輪要用哪個施工指令。回 (指令, 警語 or None)。

    最後一輪改用升階指令。未給升階指令時**沿用原指令而不是 halt**:
    硬性要求第二個模型會讓沒有多模型配置的人完全跑不動。但沿用必須說出來——
    同模型升階只換提示、不換盲點,那是折扣不是等價。
    """
    if round_idx < max_rounds:
        return agent_cmd, None
    if escalate_cmd:
        return escalate_cmd, None
    return agent_cmd, ("⚠️ 最後一輪未給 --escalate-cmd:沿用 --agent-cmd。"
                       "同模型升階=只換提示不換盲點,獨立性有折扣。")


def unresolved_report(entries) -> str:
    """達上限時的未解項清單。entries: [(輪次, 來源, 摘要)]。

    只印最後一行會讓人以為問題只有一個;debug 需要的正是
    「這幾輪各卡在哪、有沒有在收斂」。
    """
    # 同樣清掉 ANSI:這份是印給人看的,夾帶逃脫序列會讓輪次之間的界線讀不出來
    rows = [f"  · 第 {rnd} 輪 [{src}] {ANSI_RE.sub('', summary or '').strip()[:200]}"
            for rnd, src, summary in (entries or [])]
    if not rows:
        return "(無未解項紀錄)"
    return f"回修 {len(rows)} 輪仍未通過,各輪未解項:\n" + "\n".join(rows)


# ── 施工者的 brief 與回報管道(CHG-20260926-01 T5、T6;#4、#9)──────────────────────
# 無人值守的施工者照字面讀它拿到的 brief,而沒有人能回覆它。c5af2cc 的 brief 只有
# Global Constraints 與 `T1. <標題>`:沒有 `test:` 行、沒有 CHG 路徑、沒有範圍,也沒有回報管道。
#
# 行首規則(三條共用:`[blocked]` 回報、判定行 T7、交叉讀 T8):行首可以是空白、`>`、`*`、`_`、
# 反引號、`-`,可再加一個列表編號 `\d{1,3}[.)]`。**不含 `|`**——`| ` 是 runner 的引用前綴,
# 加了它的行三條規則都讀不到(quote_lines)。
LINE_START = r"[ \t>*_`\-]*(?:\d{1,3}[.)][ \t>*_`\-]*)?"
_MARKER_LINE_RE = re.compile(r"^" + LINE_START + r"\[(?:blocked|task-review|cross)\]", re.IGNORECASE)
QUOTE_PREFIX = "| "
# 施工者的回報:行首 `[blocked]`、點名一個 task、再一個 `|`。標記與 task 編號前後都可帶強調符號
# (`**[BLOCKED]** T1 |`、`[blocked] **T1** |`)——讀不到強調過的停下,施工者的提問就被當成完成
# (fail open;實作後審查,四席)。
_EMPH = r"[ \t*_`]*"
BLOCKED_RE = re.compile(r"^" + LINE_START + r"\[blocked\]" + _EMPH + r"(?P<tid>T\d+)" + _EMPH + r"\|",
                        re.IGNORECASE | re.MULTILINE)
REPORT_LIMIT = 2000
# 施工者原文寫進交接檔時的前綴:帶前綴的行,讀 `doing:` / `escape-hatches:` 的程式與 BLOCKED_RE 都讀不到
REPORT_PREFIX = "  | "
_C0_RE = re.compile(r"[\x00-\x08\x0a-\x1f\x7f]")
ENTRY_FALLBACK_NOTE = "(這個 task 沒有條目文字;只有標題)"
FIRST_ROUND_HEAD = ("依 TDD(紅→綠→重構)完成下列 task:先從 `test:` 行寫失敗測試,"
                    "確認它紅,再實作到綠。")
REWORK_HEAD = ("這是回修輪:從上一輪的失敗原因出發(見文末「上一輪失敗原因」),逐項修正;"
               "修正仍依 TDD(紅→綠→重構)。")
SKILL_DIR = Path(__file__).resolve().parents[2]
HALT_POLICY = Path(__file__).resolve().parents[2] / "assets" / "halt_policy.json"
REFERENCES = ("tdd-loop.zh-tw.md", "autonomy.zh-tw.md", "task-review.zh-tw.md")


class PolicyUnavailable(RuntimeError):
    """讀不到 `assets/halt_policy.json`:runner 以 exit 1 結束,不以較短的清單代替。"""


def quote_lines(text: str) -> str:
    """行首會被讀成回報、判定或交叉讀的行,在最前面加上引用前綴 `| `;其餘的行原樣。

    來自 CHG 的文字、回修輪帶進來的失敗原文都先經這裡再放進 brief:被 agent 回顯、
    或被照抄(連同前綴)都不算回報或判定(代決 B3、R6)。行中提到的標記不動。
    """
    if not text:
        return text or ""
    return "".join(QUOTE_PREFIX + ln if _MARKER_LINE_RE.match(ln) else ln
                   for ln in text.splitlines(keepends=True))


def global_constraints(chg_text: str) -> str:
    """`### Global Constraints` 一節:截到下一個 `#`–`###` 標題(代決 B9);沒有這一節回空字串。

    照舊截到 `### Tasks` 會把「本機閘」「驗收標準」「Acceptance operation」一起交給施工者與
    conformance 席,而契約叫施工者遵守它拿到的約束。圍欄裡的 `# 註解` 不算標題。
    """
    lines = (chg_text or "").splitlines()
    start = next((i for i, ln in enumerate(lines)
                  if re.match(r"###\s*Global Constraints", ln)), None)
    if start is None:
        return ""
    out, fence = [lines[start]], False
    for ln in lines[start + 1:]:
        if ln.lstrip().startswith("```"):
            fence = not fence
        elif not fence and re.match(r"#{1,3}\s", ln):
            break
        out.append(ln)
    return "\n".join(out).rstrip()


def _task_entry(task) -> str:
    if isinstance(task, dict):
        return task.get("entry") or f"{task.get('tid', '?')}. {task.get('title', '')}"
    return str(task)


def build_contract(tid: str, chg_path, test_cmd, timeout) -> str:
    """施工者契約(T5):無人值守、範圍、先前的成果、時間、永遠停點、優先序、回報管道、參考。

    永遠停點清單在執行期取自 `assets/halt_policy.json` 的 `always_halt_actions`——`halt_gate`
    比對的同一份。讀不到就拋 PolicyUnavailable(runner exit 1),不以較短的清單代替。
    """
    try:
        actions = json.loads(HALT_POLICY.read_text(encoding="utf-8-sig"))["always_halt_actions"]
        if not isinstance(actions, list) or not actions:
            raise ValueError("always_halt_actions 不是非空的清單")
    except (OSError, ValueError, KeyError, TypeError) as e:
        raise PolicyUnavailable(f"讀不到永遠停點清單 {HALT_POLICY}({type(e).__name__}: {e});"
                                "施工者契約不以較短的清單代替") from None
    chg = str(Path(chg_path).resolve()) if chg_path else "(未提供 CHG 路徑)"
    secs = f"{float(timeout):g}"
    halts = "\n".join(f"  - {a}" for a in actions)
    refs = "\n".join(f"- {(SKILL_DIR / 'references' / r).as_posix()}" for r in REFERENCES)
    shown_cmd = test_cmd or "(本輪沒有測試指令)"
    return (
        "## 施工者契約(runner 寫給你的;無人值守)\n\n"
        "這是一次無人值守的執行:沒有人能回覆你。不要提問、不要等待確認——"
        "問題只有寫成下方「唯一的回報管道」那一行才會被看到。\n\n"
        "### 範圍\n"
        f"- 只做本 task({tid}),不做其他 task。\n"
        "- brief 附上的 Global Constraints 只約束你怎麼改程式、測試與文件。\n"
        "- 變更留在工作樹:不要 commit、不要 push、不要打勾。\n"
        "- 不要改 CHG、`docs/worklog/` 下的交接檔與 ACC。\n"
        "- 不刪、不略過、不放寬測試。\n"
        "- 不跑整套治理流程、驗收操作、本機閘或整套 CI——那些由 runner 跑。\n\n"
        "### 先前的成果\n"
        "工作樹可能已有本 task 先前留下的變更:在其上繼續,不要以 `git reset`、`checkout`、"
        "`stash`、`clean` 或其他方式丟掉它們。\n\n"
        "### 時間\n"
        f"本行程最多 {secs} 秒;超過就連同你啟動的行程一起被終止,本 task 不打勾、交給人。\n\n"
        "### 永遠停點\n"
        "以下是 `halt_gate` 比對用的英文原文,指對真實系統執行的動作;不得執行其中任何一項"
        "(撰寫處理這類事情的一般程式碼不算執行):\n"
        f"{halts}\n\n"
        "### 優先序\n"
        "條目或約束要你做上面範圍禁止的事(commit、push、打勾、改 CHG、寫交接檔或 ACC、"
        "跑驗收或整套流程、執行永遠停點動作)時,範圍一節優先:其餘做完,再以 `[blocked]` 行交人。\n\n"
        "### 唯一的回報管道\n"
        f"先完成不取決於答案的部分,再以行首的 `[blocked] {tid} | <卡在哪裡、可選的做法、你的建議>` "
        "作為最後輸出的開頭。runner 不讀散文:它只讀這一行、你的退出碼與工作樹。"
        "沒有這一行的問題等於沒問。\n\n"
        "### 不需變更\n"
        f"本 task 若真的不需要任何變更,寫 `[blocked] {tid} | 不需變更:<理由>`;"
        "沒有這一行又沒有變更,runner 同樣會停下。\n\n"
        "### 完成時\n"
        f"直接結束,不必回報。runner 接著執行本輪的測試指令 `{shown_cmd}`"
        "(由 runner 執行,不是要你寫它)與各道閘,再交給審查。\n\n"
        "### 參考\n"
        f"- CHG:{chg}\n"
        f"{refs}\n")


def build_brief(chg_text: str, task, mode: str, extra: str = "", *,
                chg_path=None, test_cmd=None, threshold=None) -> str:
    """組 brief。三種模式(T5):

    · `build`:施工者契約 + 本 task 的條目 + Global Constraints(截到下一個標題);
      回修輪(`extra` 帶上一輪失敗)從失敗原因出發。來自 CHG 的文字與失敗原文先經 quote_lines;
    · `manual`:人在迴圈(沒有 --agent-cmd),只附 task 條目與 CHG 路徑,沒有契約;
    · `review`:task 條目與 diff 的位置在前,回答契約(panel.answer_contract,含 `threshold`)
      放在最後(T7);複審附加段經 quote_lines(T8)。

    新參數一律 keyword-only:呼叫端以關鍵字傳,既有的位置參數不變。
    """
    tid = task["tid"] if isinstance(task, dict) else str(task)
    entry = quote_lines(_task_entry(task))
    gc = quote_lines(global_constraints(chg_text))
    chg = str(Path(chg_path).resolve()) if chg_path else None
    if mode == "manual":
        where = f"CHG:{chg}\n" if chg else ""
        return (f"人在迴圈模式:依下列 task 條目人工完成(TDD:先寫失敗測試),完成後打勾重跑。\n"
                f"{where}\n## Task 條目\n{entry}\n")
    if mode == "build":
        head = REWORK_HEAD if extra else FIRST_ROUND_HEAD
        contract = build_contract(tid, chg_path, test_cmd, process_timeout())
        return (f"{head}\n\n{contract}\n## Task 條目(取自 CHG,原文)\n{entry}\n\n"
                f"## Global Constraints(取自 CHG)\n{gc}\n{quote_lines(extra)}\n")
    # review(T7、T8):diff 的位置在前,回答契約放在最後;複審附加段經 quote_lines——
    # 它引用上一輪的判定行,席位回顯或照抄都不能被讀成它沒寫的判定(代決 R6)。
    from . import panel as _PAN     # panel 在模組層 import 本模組,這裡延後以免循環
    where = f"CHG 檔案:{chg}\n" if chg else ""
    return (f"唯讀審查以下 task 的 diff:spec 與 quality 各判一次;回答的格式在本簡報最後。\n"
            f"{where}{_PAN.diff_location(test_cmd)}\n"
            f"## Task 條目(取自 CHG,原文)\n{entry}\n\n"
            f"## Global Constraints(取自 CHG)\n{gc}\n{quote_lines(extra)}"
            + _PAN.answer_contract(tid, None, threshold or _PAN.DEFAULT_THRESHOLD))


def _clean_lines(text: str) -> list[str]:
    """以 `str.splitlines()` 切行(所有斷行字元都被消掉),再去掉 `\\t` 以外的 C0 控制字元(V2)。"""
    return [_C0_RE.sub("", ln) for ln in ANSI_RE.sub("", text or "").splitlines()]


def _cap(text: str, limit: int = REPORT_LIMIT) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n…(已截斷:共 {len(text)} 字,這裡只附前 {limit} 字)"


def blocked_report(stdout, stderr, tid: str) -> str | None:
    """施工者點名本 task 的 `[blocked]` 回報(T6;代決 B3)。先查 stdout 再查 stderr。

    只認行首(LINE_START)的 `[blocked] <tid> |`;行中提到的、點名別的 task 的、帶引用前綴的都不算。
    回報從那一行起到該輸出結尾;去除 ANSI 碼與交接檔的區塊標記,切行並清掉控制字元,上限 2000 字。
    """
    want = str(tid).upper()
    for text in (stdout, stderr):
        if not isinstance(text, str) or not text:
            continue
        clean = ANSI_RE.sub("", text).replace(HANDSHAKE_BEGIN, "").replace(HANDSHAKE_END, "")
        for m in BLOCKED_RE.finditer(clean):
            if m.group("tid").upper() == want:
                return _cap("\n".join(_clean_lines(clean[m.start():])).strip())
    return None


def output_tail(stdout, stderr, limit: int = REPORT_LIMIT) -> str:
    """施工者輸出的尾段(stdout 與 stderr 各取最後一段),切行並清掉控制字元。"""
    parts = []
    for name, text in (("stdout", stdout), ("stderr", stderr)):
        body = "\n".join(_clean_lines(text if isinstance(text, str) else "")).strip()
        if len(body) > limit // 2:
            body = "…" + body[-(limit // 2):]
        parts.append(f"[{name} 尾段]\n{body or '(沒有輸出)'}")
    return "\n".join(parts)


def builder_outcome(returncode, stdout, stderr, timed_out, changed, head_moved, tid,
                    tampered=()) -> str | None:
    """施工者結束之後怎麼判定(純函式;T6)。回停點種類,或 None(照常進測試與各道閘)。

    依序:改了交接檔或 CHG(`tampered`,V3)→ 行首 `[blocked]` 本 task(不論退出碼)→ 逾時 →
    非零退出(126 / 127 是 `--agent-cmd` 設定錯誤,KN-003)→ HEAD 移動(施工者自行 commit)→
    退出 0 但零變更。`changed is None` 表示 git 不可用,零變更停點不生效。
    """
    if tampered:
        return "tampered"
    if blocked_report(stdout, stderr, tid) is not None:
        return "blocked"
    if timed_out:
        return "timeout"
    if returncode in (126, 127):
        return "config"
    if returncode != 0:
        return "nonzero"
    if head_moved:
        return "self-commit"
    if changed is not None and not changed:
        return "no-change"
    return None


def quote_report(text: str) -> str:
    """施工者原文寫進交接檔之前:`str.splitlines()` 切行、去掉 `\\t` 以外的 C0 字元,逐行加 `  | `。

    寫入端與讀者用同一套切行規則(V2):以 `"\\n"` 切的話,一行 `foo\\rescape-hatches: …` 對以
    `splitlines()` 切的讀者就是一行不帶前綴的紀錄行。交接檔的區塊標記一併中和(neutralize_markers):
    前綴擋不住子字串比對,帶前綴的 `<!-- autopilot:end -->` 一樣會讓機器區塊在錯的地方切開。
    """
    return ("\n".join(REPORT_PREFIX + ln for ln in _clean_lines(neutralize_markers(text)))
            or REPORT_PREFIX.rstrip())


# 交接文件是**人與機器共寫**的一份檔:機器寫進度,人寫交接判斷。
# 原本的實作整份覆寫,會把人寫的交接內容清掉——而那正是 handshake 協定
# 要求下一棒先讀的東西。改為標記包夾的區塊,機器只覆寫自己那一段。
HANDSHAKE_BEGIN = "<!-- autopilot:begin -->"
HANDSHAKE_END = "<!-- autopilot:end -->"
# runner 自己寫的交接檔(相對 repo 根目錄)。它不是施工者的產出:`verify.changed_files`
# 以這個常數把它排除在「本 task 的變更」之外(CHG-20260926-01 T3,代決 S5)。
HANDSHAKE_REL = Path("docs") / "worklog" / "handshake-autopilot.md"
# 原文裡的區塊標記換成這兩個字樣(不含 `<!--`,不會再被讀成標記)
_MARKER_STANDINS = ((HANDSHAKE_BEGIN, "‹autopilot:begin(原文裡的標記,已中和)›"),
                    (HANDSHAKE_END, "‹autopilot:end(原文裡的標記,已中和)›"))


def neutralize_markers(text: str) -> str:
    """把原文裡的交接檔區塊標記換成不會被讀成標記的字樣(實作後審查,四席)。

    施工者或審查者的原文(`report:`、以及組進 `doing:` / `next:` 的字)只要含 `<!-- autopilot:end -->`,
    handshake_block 與 carried_hatches 就會在錯的地方切開機器區塊:區塊被截斷、原文殘留在區塊外,
    下一次寫入再多一份。重複替換到沒有標記為止——巢狀的 `<!-- autopilot:<!-- autopilot:end -->end -->`
    替換一次之後才露出來的標記也要換掉。
    """
    out = text or ""
    while HANDSHAKE_BEGIN in out or HANDSHAKE_END in out:
        for marker, standin in _MARKER_STANDINS:
            out = out.replace(marker, standin)
    return out


def handshake_block(existing: str, body: str) -> str:
    """把機器區塊寫進既有內容:有標記換內容,無標記則附加,既有內容一字不動。"""
    block = f"{HANDSHAKE_BEGIN}\n{body}\n{HANDSHAKE_END}"
    if HANDSHAKE_BEGIN in existing and HANDSHAKE_END in existing:
        pre = existing[:existing.index(HANDSHAKE_BEGIN)]
        post = existing[existing.index(HANDSHAKE_END) + len(HANDSHAKE_END):]
        return pre + block + post
    if existing.strip():
        # 無標記代表這是人手寫的交接文件。為了機器格式而擋住整條流程,
        # 代價方向錯了(KN-004)——最壞情況只是多一個區塊,不會遺失內容。
        return existing.rstrip("\n") + "\n\n" + block + "\n"
    return block + "\n"


# 逃生口的留痕(CHG-20260926-01 T10,NEW-13,代決 M2):機器區塊裡一行不帶前綴的
# `escape-hatches: <a>; <b>`。c5af2cc 只在執行開始時寫一筆「使用逃生口」,第一個 task 的寫入
# 就把它蓋掉;寫 ACC 的往往是另一個 session,它讀的是收尾之後的交接檔,不是 runner 的 stdout。
SCOPE_KEY = "branch/role/scope:"
HATCHES_KEY = "escape-hatches:"
HATCH_SEP = "; "


def carried_hatches(existing: str, chg_id: str) -> list[str]:
    """交接檔機器區塊裡、屬於 `chg_id` 的逃生口清單;別的 CHG 或沒有這一行回 []。

    只認不帶前綴的整行(施工者或審查者原文在 `report:` 底下,每行帶 `  | `,讀不到),
    以 `str.splitlines()` 切行——與寫入端同一套切法(V2)。字串操作,不用正規式。
    """
    text = existing or ""
    begin = text.find(HANDSHAKE_BEGIN)
    end = text.find(HANDSHAKE_END, begin + 1) if begin >= 0 else -1
    if begin < 0 or end < 0:
        return []
    scope, found = None, None
    for ln in text[begin + len(HANDSHAKE_BEGIN):end].splitlines():
        if scope is None and ln.startswith(SCOPE_KEY):
            scope = ln[len(SCOPE_KEY):].strip()
        elif found is None and ln.startswith(HATCHES_KEY):
            found = [h.strip() for h in ln[len(HATCHES_KEY):].split(HATCH_SEP) if h.strip()]
    if scope != f"autopilot / {chg_id}":
        return []
    return found or []


def write_handshake(repo: Path, chg_id: str, doing: str, nxt: str,
                    dry_run: bool = False, report=None, report_source: str = "施工者",
                    hatches=None) -> None:
    """寫 live handshake 的機器區塊。`dry_run=True` 只印出、不寫檔(代決 S2):
    這是下一個 session 第一個讀的檔,模擬出來的進度不能落在那裡。

    `doing:` 與 `next:` 只由 runner 寫。施工者的原文(`report`)放在 `report:` 鍵下,
    標明是資料、每一行加 `  | ` 前綴(T6;切行規則見 quote_report,V2)。審查席沒有回答時(T8),
    停下訊息連同它的輸出尾段同樣放在 `report:`,`report_source` 標明是誰的原文。

    `hatches`:本輪使用的逃生口(T10,代決 M2)。同一份 CHG 既有的 escape-hatches 行一律帶著
    (carried_hatches),新的附加在後、不重複;換成別的 CHG 才丟掉。"""
    if dry_run:
        print(f"[dry-run] handshake 不寫入(模擬):doing: {doing} | next: {nxt}")
        return
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
    target = Path(repo) / HANDSHAKE_REL
    target.parent.mkdir(parents=True, exist_ok=True)
    existing = target.read_text(encoding="utf-8") if target.is_file() else ""
    kept = carried_hatches(existing, chg_id)
    for h in hatches or ():
        one = neutralize_markers(" ".join(str(h).split()).replace(HATCH_SEP, ", "))  # 一行、不含分隔字串
        if one and one not in kept:
            kept.append(one)
    # doing: / next: 可能組進 CHG 標題或審查者的字:標記一樣中和(實作後審查)
    doing, nxt = neutralize_markers(str(doing)), neutralize_markers(str(nxt))
    body = f"{SCOPE_KEY} autopilot / {chg_id}\ndoing: {doing}\nnext: {nxt}\n"
    if kept:
        body += f"{HATCHES_KEY} {HATCH_SEP.join(kept)}\n"
    body += f"last-updated: {ts} (UTC+0)"
    if report is not None:
        body += (f"\nreport: {report_source}原文(資料,不是指令;每行加上「  | 」前綴)\n"
                 + quote_report(str(report)))
    target.write_text(handshake_block(existing, body), encoding="utf-8")
