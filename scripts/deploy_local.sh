#!/bin/sh
# deploy_local.sh — 把 main 部署到本機(CHG-20261022-03 T6)
#
# 流程:確認在 main 且工作樹乾淨 → git pull --ff-only → 跑 scripts/check.sh(存在時)
#       → 依 pidfile 停止舊的本專案網頁伺服器 → 重新啟動 → 驗證本專案行程的
#       所有 LISTEN socket 只綁 127.0.0.1 / [::1],否則停止伺服器並 exit 1。
#
# 安全約束(CHG-20261022-03 安全影響 (d)(f)(m)):
#   - 只部署本 repo 的 main;不 reset、不刪檔、不 force。
#   - 工作樹不乾淨(含未追蹤檔,.gitignore 排除者除外)就中止。
#   - 「本專案行程」以 pidfile(outputs/.studio.pid)識別;pidfile 指向的行程若不是
#     本 repo 的 studio_server.py,一律不碰並 exit 1。
#   - 只寫入 outputs/.studio.pid 與 outputs/studio_server.log(outputs/ 已被 .gitignore 排除)。
#     寫入前確認 outputs 與這兩個檔都不是符號連結、兩檔硬連結數為 1、outputs 實際路徑在 repo 內,否則中止。
#   - LISTEN 驗證 fail-closed:lsof 失敗或查無 socket 一律停止伺服器並 exit 1。
#
# 以下環境變數**只供測試使用**(tests/ci/test_deploy_local.py),正式部署不要設定:
#   DEPLOY_REPO        repo 路徑(預設為本腳本所在 repo)
#   DEPLOY_SERVER_CMD  取代 uv 啟動指令(測試以假伺服器代替),以空白分詞
#   DEPLOY_PORT        網頁伺服器埠(預設 7860;測試用隨機高位埠)
#   DEPLOY_SKIP_PULL   設為 1 時略過 git pull(測試不連網)
set -eu

SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd -P)
REPO=${DEPLOY_REPO:-"$SCRIPT_DIR/.."}
REPO=$(cd "$REPO" && pwd -P)
PORT=${DEPLOY_PORT:-7860}
SERVER_CMD=${DEPLOY_SERVER_CMD:-"$HOME/.local/bin/uv run --script studio_server.py"}
PIDFILE="$REPO/outputs/.studio.pid"
LOGFILE="$REPO/outputs/studio_server.log"
MCP_MSG="MCP 伺服器由 client 啟動,請重新啟動 Claude Code / Codex 以載入新版 local-models"

die() {
	echo "錯誤:$*" >&2
	exit 1
}

cd "$REPO"

# 1. 只部署 main
branch=$(git symbolic-ref --short -q HEAD || true)
if [ "$branch" != "main" ]; then
	die "目前分支為「${branch:-(detached HEAD)}」,只能在 main 上部署;請先 git switch main"
fi

# 2. 工作樹必須乾淨(含未追蹤檔;.gitignore 排除者不算)
if [ -n "$(git status --porcelain --untracked-files=all)" ]; then
	git status --short --untracked-files=all >&2
	die "工作樹不乾淨,請先 commit 或自行整理後再部署(本腳本不會 reset 或刪檔)"
fi

# 3. 只允許 fast-forward 更新
if [ "${DEPLOY_SKIP_PULL:-0}" = "1" ]; then
	echo "略過 git pull(DEPLOY_SKIP_PULL=1,僅供測試)"
else
	git pull --ff-only || die "git pull --ff-only 失敗,請手動處理後再部署"
fi

# 4. 本機檢查(存在時)
if [ -f scripts/check.sh ]; then
	echo "執行 scripts/check.sh"
	sh scripts/check.sh || die "scripts/check.sh 失敗,不重啟伺服器"
fi

# 5. 網頁伺服器尚未建立時到此為止
if [ ! -f studio_server.py ]; then
	echo "網頁伺服器尚未建立(CHG-20261022-01)"
	echo "$MCP_MSG"
	exit 0
fi

# 寫入 outputs/ 前的檢查:outputs 目錄與 pidfile、log 都不得是符號連結,
# 且 outputs 的實際路徑必須在本 repo 實際路徑之下;任一不符就中止、不寫入。
# 參數 create:outputs 不存在時建立(建立後再檢查一次實際路徑)。
check_outputs() {
	out_dir="$REPO/outputs"
	if [ -L "$out_dir" ]; then
		die "$out_dir 是符號連結,拒絕寫入"
	fi
	if [ "${1:-}" = "create" ] && [ ! -e "$out_dir" ]; then
		mkdir "$out_dir"
	fi
	if [ -e "$out_dir" ]; then
		if [ ! -d "$out_dir" ] || [ -L "$out_dir" ]; then
			die "$out_dir 不是一般目錄,拒絕寫入"
		fi
		real_out=$(cd "$out_dir" && pwd -P) || die "無法取得 $out_dir 的實際路徑,拒絕寫入"
		case "$real_out" in
		"$REPO"/*) ;;
		*) die "$out_dir 的實際路徑 $real_out 不在 repo 內,拒絕寫入" ;;
		esac
	fi
	for f in "$PIDFILE" "$LOGFILE"; do
		if [ -L "$f" ]; then
			die "$f 是符號連結,拒絕寫入"
		fi
		# 硬連結:已存在的檔案連結數必須為 1,否則可能指向 repo 外的同一個檔案
		if [ -e "$f" ]; then
			if [ ! -f "$f" ]; then
				die "$f 不是一般檔案,拒絕寫入"
			fi
			nlink=$(link_count "$f" 2>/dev/null || true)
			if [ "$nlink" != "1" ]; then
				die "$f 的硬連結數為「${nlink:-未知}」(必須為 1),拒絕寫入"
			fi
		fi
	done
}

# 檔案的硬連結數(macOS 與 Linux 的 stat 參數不同)
link_count() {
	case "$(uname)" in
	Darwin | *BSD) stat -f %l "$1" ;;
	*) stat -c %h "$1" ;;
	esac
}

# 先檢查一次,有問題就在動到任何行程前中止
check_outputs

command -v lsof >/dev/null 2>&1 || die "找不到 lsof,無法驗證伺服器只綁本機,中止部署"

# 列出 pid 與其所有子孫行程(逗號分隔,供 lsof -p 使用)
proc_tree() {
	ps -A -o pid= -o ppid= | awk -v root="$1" '
		{ parent[$1] = $2; pids[NR] = $1 }
		END {
			keep[root] = 1; changed = 1
			while (changed) {
				changed = 0
				for (i in pids) {
					p = pids[i]
					if (!(p in keep) && (parent[p] in keep)) { keep[p] = 1; changed = 1 }
				}
			}
			out = ""
			for (p in keep) out = (out == "" ? p : out "," p)
			print out
		}'
}

# 行程存在且不是殭屍(已結束但尚未被回收的子行程不算存活)
alive() {
	st=$(ps -p "$1" -o stat= 2>/dev/null | tr -d " " || true)
	case "$st" in
	'' | Z*) return 1 ;;
	*) return 0 ;;
	esac
}

# 判斷 pid 是否為本 repo 的 studio_server.py 行程(指令列含 studio_server.py 且工作目錄為本 repo)
is_ours() {
	cmdline=$(ps -p "$1" -o command= 2>/dev/null || true)
	case "$cmdline" in
	*studio_server.py*) ;;
	*) return 1 ;;
	esac
	cwd=$(lsof -a -p "$1" -d cwd -Fn 2>/dev/null | sed -n 's/^n//p' | head -n 1)
	[ "$cwd" = "$REPO" ]
}

# 停止行程樹:先 TERM,10 秒後仍在才 KILL
stop_tree() {
	tree=$(proc_tree "$1")
	echo "停止伺服器行程:$tree"
	for p in $(echo "$tree" | tr ',' ' '); do
		kill -TERM "$p" 2>/dev/null || true
	done
	i=0
	while [ "$i" -lt 10 ]; do
		remaining=""
		for p in $(echo "$tree" | tr ',' ' '); do
			if alive "$p"; then remaining="$remaining $p"; fi
		done
		[ -z "$remaining" ] && break
		sleep 1
		i=$((i + 1))
	done
	for p in $(echo "$tree" | tr ',' ' '); do
		if alive "$p"; then
			echo "行程 $p 10 秒內未結束,送出 KILL"
			kill -KILL "$p" 2>/dev/null || true
		fi
	done
	# 清空 pidfile(不刪檔),避免日後 pid 被重複使用時誤判
	check_outputs
	: >"$PIDFILE"
}

# 6. 依 pidfile 停止舊行程
if [ -s "$PIDFILE" ]; then
	old_pid=$(head -n 1 "$PIDFILE" | tr -cd '0-9')
	if [ -n "$old_pid" ] && alive "$old_pid"; then
		if is_ours "$old_pid"; then
			stop_tree "$old_pid"
		else
			die "pidfile 的行程 $old_pid 不是本 repo 的 studio_server.py,不予處理;請人工確認 $PIDFILE"
		fi
	fi
fi

# 7. 埠若已被其他行程佔用就不啟動(不碰非本專案行程)
if lsof -nP -iTCP:"$PORT" -sTCP:LISTEN >/dev/null 2>&1; then
	lsof -nP -iTCP:"$PORT" -sTCP:LISTEN >&2 || true
	die "埠 $PORT 已被其他行程佔用,不啟動;請人工確認"
fi

# 8. 背景啟動
check_outputs create
echo "啟動網頁伺服器:$SERVER_CMD(輸出寫入 $LOGFILE)"
# shellcheck disable=SC2086
(cd "$REPO" && exec nohup $SERVER_CMD) >"$LOGFILE" 2>&1 </dev/null &
server_pid=$!
echo "$server_pid" >"$PIDFILE"

# 9. 等待最多 60 秒直到埠 LISTEN(fail-closed:lsof 查詢失敗等同未 LISTEN,逾時即停止伺服器)
listening=0
i=0
while [ "$i" -lt 60 ]; do
	if ! alive "$server_pid"; then
		tail -n 20 "$LOGFILE" >&2 || true
		check_outputs
		: >"$PIDFILE"
		die "伺服器啟動後即結束,請查看 $LOGFILE"
	fi
	if lsof -nP -a -p "$(proc_tree "$server_pid")" -iTCP:"$PORT" -sTCP:LISTEN >/dev/null 2>&1; then
		listening=1
		break
	fi
	sleep 1
	i=$((i + 1))
done
if [ "$listening" != 1 ]; then
	stop_tree "$server_pid"
	die "伺服器 60 秒內未在埠 $PORT LISTEN(或無法驗證監聽位址),已停止;請查看 $LOGFILE"
fi

# 10. 驗證本專案行程的全部 LISTEN socket 只綁 127.0.0.1 / [::1](安全影響 (f)(m))
#     fail-closed:lsof 查詢失敗,或行程應在 LISTEN 卻查不到任何 socket,一律視為驗證失敗
tree=$(proc_tree "$server_pid")
if ! raw=$(lsof -nP -a -p "$tree" -iTCP -sTCP:LISTEN -Fn 2>/dev/null); then
	stop_tree "$server_pid"
	die "無法驗證監聽位址(lsof 查詢失敗),已停止伺服器"
fi
sockets=$(printf '%s\n' "$raw" | sed -n 's/^n//p')
if [ -z "$sockets" ]; then
	stop_tree "$server_pid"
	die "無法驗證監聽位址(查不到任何 LISTEN socket),已停止伺服器"
fi
bad=""
for s in $sockets; do
	case "$s" in
	127.0.0.1:* | \[::1\]:*) ;;
	*) bad="$bad $s" ;;
	esac
done
if [ -n "$bad" ]; then
	echo "發現非本機的 LISTEN socket:$bad" >&2
	stop_tree "$server_pid"
	die "伺服器綁定了非本機位址,已停止伺服器"
fi

echo "網頁伺服器已啟動(pid $server_pid),LISTEN:$(echo $sockets)"
echo "$MCP_MSG"
