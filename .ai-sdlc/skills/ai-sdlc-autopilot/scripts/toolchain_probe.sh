#!/usr/bin/env bash
# 進場時探測工具鏈:直譯器在不在、開發相依裝了沒(CHG-20260811-01 T4)。
#
# ## 為什麼是 shell,不是 Python
#
# 第一版計畫寫的是 `scripts/toolchain_probe.py`——**用 Python 腳本探測 Python 在不在**。
# 獨立審查席位(codex / GPT-5.4)在確認關卡前抓到這個自舉悖論:Python 不存在時,
# 那支腳本根本啟動不了,於是「沒有輸出」與「一切正常」在呼叫端長得一樣。
# 這正是 KN-003 的形狀(退出碼分不出**擋下 / 壞掉 / 根本沒跑**),而我正在寫一個新例子。
#
# ## 為什麼需要它
#
# 這台開發機每個 session 都是重建的映像(2026-08-11 實測:OS InstallDate 與使用者
# profile 都是當天建立,winget 診斷紀錄只回溯到同一個小時)。連三輪進場都發現
# Python 不存在,而前兩輪都把它當成「要診斷的故障」,各自手動裝一次、各自手寫一段筆記。
# 它不是故障,是**保證每一輪都不成立的前置條件**——KN-005:靠記性維持的規則會重複違反,
# 要變成機器判得出來的東西。
#
# ## 三態,而且預設不是通過
#
#   PASS     (exit 0) 直譯器可執行,且宣告的開發相依全部裝好
#   BLOCKED  (exit 3) 明確查出缺什麼——直譯器不可用,或相依缺項(理由會列出來)
#   NOT_RUN  (exit 4) 連判都判不出來(requirements 檔不見了、探測自己壞了)
#
# BLOCKED 與 NOT_RUN 分開是刻意的(KN-003):「查出來缺 X」與「根本沒查成」
# 要走不同的後續動作——前者去裝,後者去修探測本身。
# **狀態變數初值是 NOT_RUN**,只有明確驗過才會變成 PASS:預設拒絕,
# 所以中途崩潰、被 kill、或任何一條路徑忘了設值,都不會掉進通過那一格。
#
# ## 呼叫端注意
#
# 讀「最後一行的 `TOOLCHAIN: <STATE>`」或退出碼,**兩者一致**。
# 不要接 `| tail`——2026-08-08 就踩過一次:退出碼被管線最後一段吃掉,
# 拿到的是 `tail` 的 0,而載具自己印的是失敗(KN-003 的變形)。
set -uo pipefail

# 根目錄由呼叫端決定:第一個參數,否則用當前目錄。
# **不能用 `dirname $0/..`**——這支腳本出貨給消費者,安裝路徑不是本 repo 的相對位置,
# 用腳本自己的位置去推專案根,推出來的是 skill 的安裝目錄。
cd "${1:-$PWD}" || exit 4

STATE="NOT_RUN"      # 預設拒絕:只有明確驗過才升級成 PASS
REASONS=()
HINTS=()

emit() {
    # 理由與指引先印,狀態行**永遠是最後一行**——呼叫端只需讀最後一行。
    #
    # 空陣列一律走 `${VAR+x}` 判斷,**不用 `${#ARR[@]}`**:bash 4.4 之前
    # (macOS 出貨的是 **3.2**,GPLv3 之故)`set -u` 下對空陣列展開會判成 unbound,
    # 而這支腳本的 PASS 路徑 `HINTS` 正好是空的——也就是「一切正常」那條路上崩掉,
    # **而且是在印出狀態行之前**。本機是 bash 5.3,**測不出真正的 3.2 行為**,
    # 所以這裡不押注在「應該沒事」上:改寫成不會有這個問題的寫法(KN-004——
    # 無法評估時往哪邊倒,看失敗代價的方向,而這裡的代價是探測本身失去可信度)。
    for r in ${REASONS+"${REASONS[@]}"}; do echo "  - $r"; done
    if [ -n "${HINTS+x}" ]; then
        echo ""
        echo "  佈建方式:"
        for h in ${HINTS+"${HINTS[@]}"}; do echo "    $h"; done
    fi
    echo "TOOLCHAIN: $STATE"
    case "$STATE" in
        PASS)    exit 0 ;;
        BLOCKED) exit 3 ;;
        *)       exit 4 ;;
    esac
}

# --- interpreter-resolution:start (CHG-20260804-15)
# 候選要**實跑探針**,不只 `command -v`:Windows 的 Microsoft Store 有一個 `python3`
# 假替身,找得到、執行起來只會印安裝提示。找得到不等於能用——這台機器上它天天出現。
PY=""
for _cand in ${PYTHON:-} ./.venv/bin/python3 python3 python; do
    [ -n "$_cand" ] || continue
    if command -v "$_cand" >/dev/null 2>&1 && "$_cand" -c "import sys" >/dev/null 2>&1; then
        PY="$_cand"
        break
    fi
done
# --- interpreter-resolution:end

if [ -z "$PY" ]; then
    STATE="BLOCKED"
    # 訊息裡**不手抄候選清單**,兩個理由:①手抄的清單會跟上面那個共用區塊漂移
    # (與本 repo 記過的「文件裡手抄的數字」同一族);②`test_gates_wired.py` 有一道閘
    # 擋「解析區塊外出現 bare python/python3」,而它分不出那是呼叫還是散文——
    # 把閘放寬到豁免字串,就是判準過鬆,會漏掉真正的 bare 呼叫。
    REASONS+=("找不到可執行的 Python 直譯器(候選順序見本檔的 interpreter-resolution 區塊)")
    REASONS+=("覆寫候選:設 PYTHON 環境變數指到直譯器;覆寫值同樣要通過實跑探針")
    REASONS+=("注意「找得到」不等於「能用」:Store 別名殘根 command -v 找得到,執行只印安裝提示")
    HINTS+=("winget install --id Python.Python.3.11 --scope user \\")
    HINTS+=("    --custom \"InstallLauncherAllUsers=0 Include_launcher=0\"   # 免 UAC 提權")
    HINTS+=("<直譯器> -m pip install -r requirements-dev.txt")
    HINTS+=("或指定路徑:PYTHON=<直譯器路徑> bash skills/ai-sdlc-autopilot/scripts/toolchain_probe.sh")
    emit
fi

echo "直譯器:$PY ($("$PY" -c 'import sys;print(sys.version.split()[0])' 2>/dev/null || echo '版本讀不到'))"

# --- declaration-sources:start (CHG-20260822-01)
# 開發相依有兩種合法宣告方式,兩種都要讀。只認 requirements-dev.txt 的話,用 pyproject
# 宣告的消費者每一輪進場都拿到 NOT_RUN——實際發生過(下游 ai-sdlc-runner,刻意零執行期相依)。
#
# **聯集,不是擇一**:兩個檔都在就兩個都驗。讓其中一個蓋掉另一個,等於讓比較寬鬆的那份
# 永久遮住另一份的宣告。
REQ="requirements-dev.txt"
PYPROJECT="pyproject.toml"

SPECS=""
FOUND=""

if [ -f "$REQ" ]; then
    FOUND="yes"
    SPECS="$(cat "$REQ")"
fi

if [ -f "$PYPROJECT" ]; then
    FOUND="yes"
    # 用 `$PY` + tomllib 讀,不在 shell 裡解析 TOML——同一條「不自己實作、不猜」的判準。
    # 這裡動用 Python 是安全的:此刻直譯器**已經通過實跑探針**(見上方 interpreter-resolution),
    # 自舉悖論約束的是「偵測 Python 在不在」,不是「確認它能跑之後再用它」。
    #
    # 只讀 `[project.optional-dependencies]`。**不讀** `[project.dependencies]`(那是執行期相依,
    # 不在開發工具鏈探測的宣稱範圍內),也**還不讀** PEP 735 `[dependency-groups]`——後者是正式
    # 標準沒錯,但它支援 `{include-group = ...}`,那是一個解析步驟;在聯集語意下,一個只能部分
    # 解析的來源會讓今天靠 requirements 就綠的 repo 掉成 NOT_RUN。等 include 解析做完整再加。
    _pp_prog='import sys, tomllib
with open(sys.argv[1], "rb") as f:
    _d = tomllib.load(f)
for _g in (_d.get("project", {}).get("optional-dependencies") or {}).values():
    for _spec in _g:
        print(_spec)'
    if _pp_out="$("$PY" -c "$_pp_prog" "$PYPROJECT" 2>/dev/null)"; then
        [ -n "$_pp_out" ] && SPECS="$SPECS
$_pp_out"
    else
        # 讀不動就是**沒查成**,不是「沒有相依」。最常見的原因是 Python < 3.11(tomllib 是
        # 3.11 才進 stdlib);其次是 TOML 本身壞掉。兩種都具名,不靜默略過。
        UNPARSED_EARLY="$PYPROJECT(讀不動——需要 Python 3.11+ 的 tomllib,或該檔語法有誤)"
    fi
fi

if [ -z "$FOUND" ]; then
    STATE="NOT_RUN"
    REASONS+=("找不到任何相依宣告——判不出該裝什麼。這不是「沒有相依」,是沒有查成")
    REASONS+=("找過的宣告來源:$REQ、$PYPROJECT(皆不存在)")
    REASONS+=("「沒宣告」與「路徑給錯 / 檔名寫錯」在這裡長得一樣,所以不回 PASS")
    emit
fi
# --- declaration-sources:end

# 用 **發行版名稱** 查,不用 import 名:`pip-audit` 的模組叫 `pip_audit`,
# 而 requirements 檔列的是發行版名。`importlib.metadata` 認的正好是後者,
# 所以不需要維護一張「套件名 → 模組名」的對照表。
#
# ## 版本釘選要驗,而第一版沒驗——那是這支腳本自己的假綠
#
# 第一版把 `${name%%[<>=!~]*}` 當成「去掉版本釘選」就結束了,然後只問「有沒有裝」。
# 於是裝了 `mypy==1.0` 的機器,對著釘死 `mypy==2.3.0` 的 requirements **回報 PASS**。
# 那正是 KN-001:恆真回報等同無訊號——而它出現在一支專門用來擋假綠的探測裡。
# 獨立審查席位(codex / GPT-5.4)在末端 review 抓到,判 `quality: fail`。
#
# ## 而解不動的那些行,一律算「沒查成」,不算「沒問題」
#
# requirements.txt 的合法語法遠比 `name==version` 多:`-r other.txt`、`-e .`、
# `foo[extra]`、`foo @ https://…`、`foo; python_version < "3.12"`、`--hash=…`、
# 行尾續接 `\`、`--index-url` …。在 shell 裡實作一個完整的 PEP 508 解析器是錯的方向,
# 而**猜**更錯:猜錯的方向會是「看起來驗過了」。
# 所以判準是三態——解得動就驗,解不動就**具名列為未涵蓋並回 NOT_RUN**。
# 「這一行我看不懂」與「這一行沒問題」必須分得開(KN-003)。
# 範圍比對委派給 `$PY` 的程式。定義在迴圈外:每圈重新賦值毫無意義,而且**擺在行首**
# 讓 test_gates_wired.py 的「內嵌程式必須是合法 Python」那道閘掃得到它
#(那道閘是本輪自己踩了引號寫壞、Gherkin 抓不到之後補的)。
# 慣例:外層單引號、內部只用雙引號。
_cmp_prog='import sys
from packaging.specifiers import SpecifierSet
from packaging.version import Version
print("SATISFIED" if Version(sys.argv[2]) in SpecifierSet(sys.argv[1]) else "UNSATISFIED")'

MISSING=""
UNPARSED="${UNPARSED_EARLY:+|${UNPARSED_EARLY}}"
while IFS= read -r line; do
    spec="${line%%#*}"                                   # 去掉行內註解
    spec="$(echo "$spec" | tr -d '[:space:]')"
    [ -n "$spec" ] || continue
    case "$spec" in
        # 選項行 / URL / VCS / extras / 環境標記 / 續接行 / hash——一律解不動。
        -*|*@*|*://*|*\[*|*\;*|*\\) UNPARSED="$UNPARSED|$spec"; continue ;;
    esac
    name="${spec%%[<>=!~]*}"
    rest="${spec#"$name"}"
    if [ -z "$name" ]; then UNPARSED="$UNPARSED|$spec"; continue; fi
    got="$("$PY" -c "import importlib.metadata as m; print(m.version('$name'))" 2>/dev/null)"
    if [ -z "$got" ]; then
        MISSING="$MISSING|未安裝:$name"
        continue
    fi
    case "$rest" in
        "")        : ;;                                   # 沒釘版本,存在即可
        "=="*)     want="${rest#==}"
                   [ "$got" = "$want" ] || \
                       MISSING="$MISSING|版本不符:$name 要 $want,實得 $got" ;;
        # `>=` / `~=` / `!=` / `<` 這些要比較大小,而版本比較的規則(PEP 440)不是字串比大小。
        # 仍然**不在 shell 裡自己實作**——改為委派給那支已經證明可執行的 `$PY`(CHG-20260822-01)。
        #
        # 之前這裡直接放棄(具名「只驗了存在」→ NOT_RUN)。那對 requirements 還算堪用,因為那邊
        # 釘死版本是常態;但 pyproject 裡**範圍才是常態**,照舊放棄等於把 NOT_RUN 換個地方發生。
        #
        # `packaging` 不是 stdlib,消費者機器不保證有。**沒有它就往 NOT_RUN 倒,不往 PASS 倒**:
        # 把範圍悄悄降級成「有裝就算」正是第一版犯過、被獨立審查席位抓到的那個假綠(KN-001)。
        *)         _verdict="$("$PY" -c "$_cmp_prog" "$rest" "$got" 2>/dev/null)"
                   case "$_verdict" in
                       SATISFIED)   : ;;
                       UNSATISFIED) MISSING="$MISSING|版本不符:$name 要 $rest,實得 $got" ;;
                       *)           UNPARSED="$UNPARSED|$name$rest(比不動範圍:$PY 沒有可用的 packaging,只驗到有裝 $got)" ;;
                   esac ;;
    esac
done <<< "$SPECS"

if [ -n "$MISSING" ]; then
    STATE="BLOCKED"
    _old_ifs="$IFS"; IFS="|"
    for m in $MISSING; do [ -n "$m" ] && REASONS+=("開發相依 $m"); done
    IFS="$_old_ifs"
    [ -f "$REQ" ] && HINTS+=("$PY -m pip install -r $REQ")
    [ -f "$PYPROJECT" ] && HINTS+=("$PY -m pip install -e \".[<extra>]\"   # 見 $PYPROJECT 的 optional-dependencies")
    emit
fi

if [ -n "$UNPARSED" ]; then
    STATE="NOT_RUN"
    REASONS+=("相依宣告有解不動的項目——**未涵蓋,不是通過**:")
    _old_ifs="$IFS"; IFS="|"
    for u in $UNPARSED; do [ -n "$u" ] && REASONS+=("  · $u"); done
    IFS="$_old_ifs"
    REASONS+=("這些請人工確認,或改用 pip 自己驗(它有完整的 PEP 440 實作):")
    [ -f "$REQ" ] && REASONS+=("  $PY -m pip install --dry-run -r $REQ")
    [ -f "$PYPROJECT" ] && REASONS+=("  $PY -m pip install --dry-run -e \".[<extra>]\"")
    emit
fi

FOUND_LABEL=""
[ -f "$REQ" ] && FOUND_LABEL="$REQ"
[ -f "$PYPROJECT" ] && FOUND_LABEL="${FOUND_LABEL:+$FOUND_LABEL + }$PYPROJECT"
STATE="PASS"
echo "開發相依:${FOUND_LABEL} 宣告的項目全數已安裝,版本全數符合"
emit
