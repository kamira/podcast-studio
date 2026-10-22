#!/usr/bin/env python3
"""能力偵測:先問這台機器支不支援(CHG-20260807-02 T1)。

**為什麼要有這一層**:repo 裡已經有四個地方各自處理「這個環境做不到」——
`bench.py` 回帶 error 的 dict、`assertion_probe.py` 回 `"not-run"`、
`run_quality.sh` 回 exit 2、`ci_local.sh` 用 shell 變數。四種型別、四種寫法。
於是第五個遇到同一件事的地方(`test_hooks_and_mcp.py` 的 symlink 界線)
**沒有東西可以沿用**,只能在「謊報通過」與「謊報失敗」裡挑一個——它挑了後者,
結果是任何沒開開發者模式的 Windows 上,那道閘永遠紅一項。
而本 repo 自己寫過:**一個永遠紅的閘會被忽略,那與沒有閘是同一件事**。

規則有了(「未涵蓋不是通過」),但套用範圍靠人記得——這是 KN-005。

## 兩條刻意的設計

**一、一律實跑,不看平台名。** `os.name == "nt"` 不能推出「建不出 symlink」:
開了開發者模式就建得出。用平台名判,是把**一台機器的設定**寫死成**整個平台的性質**,
而且錯的方向是靜默的——那台開了開發者模式的機器會被永遠判定為不支援。
同 CHG-20260804-15 的直譯器探針:`command -v` 找得到不等於能用(KN-003)。
`test_capabilities.py` 以 AST 斷言本檔未把 `os.name` / `sys.platform` 用作判準。

**二、探針壞掉一律回 False,而且絕不往外拋。** 這裡的 `except Exception` 是刻意的,
不是偷懶:探針的呼叫端是「要不要跑這條驗證」的分派邏輯,一個從探針拋出的例外
會炸掉整輪驗證——而它想表達的其實只是「這台機器不支援」。
**只 catch 自己預期的型別等於沒 catch**:預期之外的那個才是會炸的那個。

代價要說清楚:探針自己壞掉時,會被讀成「不支援」→ 該驗證降為未涵蓋。
所以未涵蓋**必須恆印且具名**(呼叫端的責任),否則探針的靜默失效沒有人看得見。

## 「不支援」不是終點

本層只回答支不支援。**支不支援 ≠ 那個性質驗不到**——同一個性質常有多種機制
(symlink 與 junction 都能驗「不得走進 repo 外的重解析點」,而且
`os.path.islink()` 對 junction 回 False,所以兩者**測得到的洞不一樣**)。
挑機制、決定要不要降為未涵蓋,是 `mechanisms.py` 的事,不是這裡。
"""
from __future__ import annotations

import importlib
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Callable

MARKER = "marker.txt"
# 刻意用非 ASCII:編碼往返的探針要測的就是這個。純 ASCII 在 cp950 下也會往返成功,
# 那樣的探針恆真——而恆真回報「支援」正是最難發現的失效(KN-001)。
MARKER_TEXT = "能力探針-中文"

_CACHE: dict[str, bool] = {}


def _default_link(link: Path, target: Path) -> None:
    link.symlink_to(target, target_is_directory=True)


def _default_run(args: list[str]) -> int:
    # 非 Windows 上沒有 `cmd`,這裡會 FileNotFoundError → 由呼叫者判為不支援。
    # **這仍然是實跑探針**:失敗是執行出來的,不是從平台名推出來的。
    return subprocess.run(args, capture_output=True, text=True).returncode


def _default_io(text: str) -> str:
    """把 text 以 UTF-8 寫檔,再用**平台預設編碼**讀回來。

    判的是**往返是否一致**,不是編碼名稱叫什麼。理由是實測:
    cp950 讀 UTF-8 位元組多半**不會拋例外**,只是讀成亂碼——
    用 try/except 判會漏掉;用名稱比對則分不出 `utf8` / `UTF-8` / `cp65001`。
    往返比對兩種都涵蓋。
    """
    tmp = Path(tempfile.mkdtemp(prefix="cap-enc-"))
    try:
        p = tmp / "probe.txt"
        p.write_text(text, encoding="utf-8")
        with open(p, encoding=None) as fh:      # noqa: SIM115 — 刻意用平台預設編碼
            return fh.read()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _traversable(link: Path) -> bool:
    """連結建出來之後,真的走進去讀一次。

    只判「呼叫沒拋例外」是不夠的:`mklink /J` 回 0 卻指向不存在的目標時,
    路徑存在而內容讀不到。**建得出來不等於用得了**——同 KN-003 的第三條。
    """
    try:
        return (link / MARKER).read_text(encoding="utf-8") == MARKER_TEXT
    except OSError:
        return False


def _with_fixture(fn) -> bool:
    """備好 target(內含 marker)與 link 路徑,交給 fn 去建連結,然後驗可穿越。"""
    tmp = None
    try:
        tmp = Path(tempfile.mkdtemp(prefix="cap-"))
        target = tmp / "target"
        target.mkdir()
        (target / MARKER).write_text(MARKER_TEXT, encoding="utf-8")
        link = tmp / "link"
        fn(link, target)
        return _traversable(link)
    except Exception:
        return False
    finally:
        if tmp is not None:
            # junction 用 shutil.rmtree 刪會**跟著刪掉目標裡的東西**,
            # 所以先讓 OS 自己拆(rmdir 對 junction 只拆連結),失敗再退回 rmtree。
            #
            # **`check=False` 擋不住 `FileNotFoundError`**(CHG-20260901-02):那是
            # 「程式不存在」,不是「程式回非零」。非 Windows 機器上沒有 `cmd`,於是這一行
            # 從 `finally` 裡拋出去、**繞過上面那個 `except Exception`**——連
            # `probe_symlink` 的成功路徑都會炸。實測後果:Linux 上 `supports('symlink')`
            # 恆為壞的,而它是「要不要跑這條驗證」的分派判準,於是涵蓋面無聲縮小成
            # 「這台機器不支援」(KN-001 的形狀:沒有人會去追查一句「不支援」)。
            try:
                subprocess.run(["cmd", "/c", "rmdir", "/S", "/Q", str(tmp)],
                               capture_output=True, check=False)
            except Exception:
                # 清理**不是探針的答案**,所以它吞得比別處寬:`OSError` 只擋得住
                # 「沒有 cmd」這一種,而任何從 finally 逃出去的例外都會蓋掉
                # 上面剛算好的 True/False,把「支援」變成一次崩潰。
                pass
            shutil.rmtree(tmp, ignore_errors=True)


def probe_symlink(_link=None) -> bool:
    """這台機器建得出可穿越的符號連結嗎?"""
    link_fn = _link or _default_link
    return _with_fixture(lambda link, target: link_fn(link, target))


def probe_junction(_run=None) -> bool:
    """這台機器建得出可穿越的 junction 嗎?(Windows 的目錄重解析點,免權限)"""
    run = _run or _default_run

    def build(link: Path, target: Path) -> None:
        rc = run(["cmd", "/c", "mklink", "/J", str(link), str(target)])
        if rc != 0:
            raise OSError(f"mklink /J 回 {rc}")

    return _with_fixture(build)


def probe_utf8_locale(_io=None) -> bool:
    """平台預設編碼讀得回 UTF-8 檔案的原內容嗎?"""
    io_fn = _io or _default_io
    try:
        return io_fn(MARKER_TEXT) == MARKER_TEXT
    except Exception:
        return False


def probe_tool(name: str, _import=None) -> bool:
    """那個套件真的 import 得起來嗎?

    **不查檔案在不在**:半裝好的套件、殼層別名都會讓「找得到」與「能用」分家
    (`run_quality.sh` 已經照這個做法寫,這裡只是把它收進同一層)。
    """
    imp = _import or importlib.import_module
    try:
        imp(name)
        return True
    except Exception:
        return False


# 型別標成 `Callable[..., bool]`:每個探針的注入參數名不同(`_link` / `_run` / `_io`),
# 那是**刻意**的——注入點要說出它替換掉的是哪一個底層動作,叫 `_inject` 就看不出來了。
# 代價是查表呼叫時型別檢查器看不到具體簽章,所以在這裡明說「任何參數,回 bool」。
PROBES: dict[str, Callable[..., bool]] = {
    "symlink": probe_symlink,
    "junction": probe_junction,
    "utf8_locale": probe_utf8_locale,
}


def supports(name: str, probes=None, cache=None) -> bool:
    """這台機器支不支援 `name`。同名只探一次。

    **未知的能力名直接 KeyError**,不回 False:打錯字的能力名若被靜默讀成
    「不支援」,對應的驗證會降為未涵蓋——而且沒有人會去追查一個未涵蓋項。
    這是 KN-001 的鏡像(恆真回報「沒問題」比恆真回報「有問題」難發現)。
    """
    table = PROBES if probes is None else probes
    store = _CACHE if cache is None else cache
    if name in store:
        return store[name]
    probe = table[name]          # 查表在 try 之外:KeyError 必須往外拋
    try:
        result = bool(probe())
    except Exception:
        result = False
    store[name] = result
    return result


def report(probes=None, cache=None) -> dict[str, bool]:
    """整台機器的能力表。**單一探針壞掉不得讓整份報告消失**——
    那會把「有一項測不出來」放大成「什麼都不知道」。"""
    table = PROBES if probes is None else probes
    return {name: supports(name, probes=table, cache=cache if cache is not None else {})
            for name in table}
