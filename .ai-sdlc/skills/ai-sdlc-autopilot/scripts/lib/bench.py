#!/usr/bin/env python3
"""效能基準引擎(CHG-20260804-05;量測方法於 CHG-20260804-18 重做)。stdlib `time`,不引入相依。

**為什麼量比值而不是秒數。** 共用的 CI runner 速度會差好幾倍,而且同一台機器在
不同時段也不一樣。拿絕對秒數設門檻,結果是這道閘三不五時無故轉紅——
而**會無故轉紅的閘,人會在第一時間關掉它**。那與沒有閘是同一件事。

所以每一輪都在同一個行程裡先跑一段固定的**校準負載**,再把每個目標的時間
除以校準時間。機器快慢會同時作用在分子與分母上,約掉。

**為什麼量 CPU 時間而不是牆鐘。** 上面那段只約掉了「這台機器多快」,約不掉
「這一刻有多少人在搶 CPU」——被別的行程搶走的時間算在**牆鐘**上,不算在本行程的
**CPU 時間**上。實測(2 核機器開滿背景競爭,同一份工作重複量 10–12 輪,最大/最小):

    perf_counter min/min(舊法)      1.82–2.10×
    perf_counter 配對 min(w/c)        3.27×   ← 更差
    perf_counter 視窗拉到 100/200 ms  2.10× / 1.78×   ← 無效
    process_time(CPU 時間)          1.05–1.07×

中位比值幾乎不變(0.211 → 0.203):量的是同一件事,散布掉到約 1/17。
配對與拉長視窗這兩條路已被實測否定,不要再走一次——競爭是持續的而非尖峰,平均不掉。

**為什麼要自動調整視窗。** `process_time` 在 Linux 實測有效粒度約 1.2 µs,
但在 Windows 走 `GetProcessTimes`,粒度是排程 tick(常見 ~15.6 ms)。校準負載只有
數毫秒,直接量會變成 0 或跳階。所以把待測函式重複 k 次塞進同一個計時視窗,
直到視窗遠大於粒度,再除以 k(`timeit` 的作法)。粒度以**實測**為準,不信任
`get_clock_info().resolution`——那回報的是宣稱值:Linux 上兩個時鐘都宣稱 1 ns,
實測差 6 倍。

**已知限制**:CPU 時間不含 I/O 等待與 sleep。現行目標全是 CPU 密集的閘本身,
所以這是對的時鐘;若日後要量 I/O 密集的東西,這個引擎會低估它。

門檻抓**倍數**(預設 2.0)而不是百分比:要抓的是演算法級退步(O(n) → O(n²)),
不是 10% 的排程雜訊。
"""
from __future__ import annotations
import time

DEFAULT_REPEAT = 7          # 取最小值:雜訊只會加時間,不會減——見 measure()
DEFAULT_TOLERANCE = 2.0     # 倍數門檻:抓演算法退步,不抓雜訊
CALIBRATION_N = 60000

CLOCK_NAME = "process_time"
_CLOCK = time.process_time
# 視窗至少要是粒度的這麼多倍,量化誤差才壓得住(10× → ≤10%,遠小於 1.25 的穩定性上限)
WINDOW_GRANULARITY_FACTOR = 10
MIN_WINDOW_S = 0.001        # 粒度極細時的地板,避免 k 被推到天上
MAX_WINDOW_S = 0.25         # 視窗上限;需要更大代表粒度粗到不堪用 → 回報未涵蓋
_MAX_INNER = 5_000_000      # k 的硬上限,防呆
# 自動調整視窗的**牆鐘**預算。k 的上限不夠:若時鐘的讀數不隨工作量成比例前進
# (粒度粗到單次工作量不出來),k 會一路翻倍直到硬上限,而那可能是幾千萬次呼叫。
# 閘卡住與閘變紅一樣糟,而且更難診斷——所以用真實時間封頂,超過就回報量不出來。
AUTORANGE_BUDGET_S = 2.0


class Unmeasurable(Exception):
    """時鐘與工作量對不上,無法在預算內湊出一個有意義的視窗。"""


def _median(xs: list) -> float:
    s = sorted(xs)
    n = len(s)
    return s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2


def calibration_workload() -> int:
    """固定的純 Python 負載。內容不重要,重要的是它每一輪都一樣。"""
    total = 0
    for i in range(CALIBRATION_N):
        total += (i ^ (i >> 3)) & 0xFF
    return total


def effective_granularity(clock=None, probes: int = 5) -> float:
    """實測時鐘的有效粒度:忙等到讀數改變,取最小的那個變化量。

    不用 `time.get_clock_info().resolution`——那是宣稱值。Linux 上 `perf_counter`
    與 `process_time` 都宣稱 1 ns,實測分別是 0.04 µs 與 1.2 µs。
    """
    clock = clock or _CLOCK
    deltas = []
    for _ in range(probes):
        t0 = clock()
        while True:
            t1 = clock()
            if t1 != t0:
                deltas.append(t1 - t0)
                break
    return min(deltas) if deltas else MIN_WINDOW_S


def _call(fn, arg):
    try:
        fn(arg) if arg is not None else fn()
    except Exception:                       # noqa: BLE001 —— 基準不負責驗正確性
        pass


def _one_window(fn, arg, clock, window: float) -> float:
    """把 fn 重複 k 次塞進同一個計時視窗,直到視窗 ≥ window。回**每次呼叫**的秒數。

    以牆鐘設預算:見 `AUTORANGE_BUDGET_S`。時間到了還湊不出視窗就拋 `Unmeasurable`,
    由呼叫端轉成「未涵蓋」——不得靜默回一個湊出來的數字。
    """
    k = 1
    deadline = time.perf_counter() + AUTORANGE_BUDGET_S
    while True:
        t0 = clock()
        for _ in range(k):
            _call(fn, arg)
        dt = clock() - t0
        if dt >= window:
            return dt / k
        if k >= _MAX_INNER or time.perf_counter() > deadline:
            raise Unmeasurable(
                f"在 {AUTORANGE_BUDGET_S} 秒內湊不出 {window * 1e3:.0f} ms 的量測視窗"
                f"(k={k},最後量到 {dt * 1e3:.3f} ms)")
        k = max(k * 2, int(k * window / dt) + 1) if dt > 0 else k * 8


def measure(fn, arg, repeat: int = DEFAULT_REPEAT, clock=None, window: float = 0.0) -> float:
    """回**最小**的每次呼叫秒數。先跑一次暖身,讓 import 與快取的成本不算進來。

    取最小而非中位數:雜訊只會讓時間變長、不會變短,所以最小值才是「這段程式碼本身
    有多貴」的最佳估計。實測在有背景負載時,中位數擋得住單一次尖峰,擋不住**持續**的競爭。
    """
    clock = clock or _CLOCK
    if window <= 0:
        window = _window_for(effective_granularity(clock))
    _call(fn, arg)
    return min(_one_window(fn, arg, clock, window) for _ in range(max(1, repeat)))


def _window_for(granularity: float) -> float:
    return max(granularity * WINDOW_GRANULARITY_FACTOR, MIN_WINDOW_S)


def run_bench(targets, repeat: int = DEFAULT_REPEAT, clock=None,
              window: float = 0.0) -> dict:
    """targets: [(名稱, 函式, 參數)]。回含比值的報告。

    校準**每個目標前都重跑一次**:機器負載會在一輪之內變化,那時分子與分母
    若不來自同一個時刻就約不掉。
    """
    clock = clock or _CLOCK
    gran = effective_granularity(clock)
    # 呼叫端可以要求更大的視窗。視窗越大,單次排程雜訊佔比越小——
    # 穩定性斷言用得上(CHG-20260804-19)。
    window = max(_window_for(gran), window)
    if window > MAX_WINDOW_S:
        # 粒度粗到量不出東西。這是**未涵蓋**,不是「沒有退步」——
        # 硬擠出一份數字比不量更糟,因為它看起來像通過。
        return {"version": 1, "clock": CLOCK_NAME, "granularity_s": gran,
                "error": f"時鐘粒度 {gran * 1e3:.2f} ms 過粗:所需視窗 "
                         f"{window * 1e3:.0f} ms 超過上限 {MAX_WINDOW_S * 1e3:.0f} ms，"
                         f"本環境量不出可用的效能比值(未涵蓋,不等於沒有退步)",
                "targets": {}}
    # 視窗越大,重複次數要收斂,否則粗粒度平台上這一步會慢到沒人願意跑
    reps = max(3, min(repeat, int(0.5 / window))) if window > MIN_WINDOW_S else repeat
    try:
        cal = measure(calibration_workload, None, reps, clock, window)
    except Unmeasurable as e:
        return {"version": 1, "clock": CLOCK_NAME, "granularity_s": gran,
                "error": f"量不出可用的效能比值:{e}(未涵蓋,不等於沒有退步)",
                "targets": {}}
    if cal <= 0:
        return {"version": 1, "clock": CLOCK_NAME, "granularity_s": gran,
                "error": "校準負載量到 0 秒:計時器解析度不足", "targets": {}}
    out = {}
    for name, fn, arg in targets:
        try:
            near_cal = measure(calibration_workload, None, max(3, reps // 2), clock, window)
            secs = measure(fn, arg, reps, clock, window)
        except Unmeasurable as e:
            return {"version": 1, "clock": CLOCK_NAME, "granularity_s": gran,
                    "error": f"目標 {name} 量不出可用的效能比值:{e}"
                             f"(未涵蓋,不等於沒有退步)", "targets": {}}
        base = min(cal, near_cal) if near_cal > 0 else cal
        out[name] = {"min_s": round(secs, 9), "ratio": round(secs / base, 6)}
    return {"version": 1, "repeat": reps, "clock": CLOCK_NAME,
            "granularity_s": round(gran, 12), "window_s": round(window, 9),
            "calibration_s": round(cal, 9),
            "_ratio_doc": "ratio = 本目標的每次呼叫 CPU 秒數 / 同一輪校準負載的每次呼叫 CPU 秒數。"
                          "機器快慢在分子分母上約掉;用 CPU 時間而非牆鐘,"
                          "所以被別的行程搶走的時間不算進來(見檔頭)。",
            "targets": out}
