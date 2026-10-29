"""settings.py 的測試(CHG-20261022-01 T1;T3 修訂 2 改寫為 SQLite 儲存層)。

每個測試把 settings.ROOT / CONFIG_PATH 指到暫存目錄(`DB_PATH` 相對於 ROOT),
不動 repo 內的 config/;跨行程測試以子行程執行同一份 settings.py。
"""
from __future__ import annotations

import ast
import copy
import hashlib
import json
import os
import sqlite3
import stat
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest

import settings

ALL = {
    settings.DEFAULTS["roles"][r]["model"] for r in settings.LLM_ROLES
} | {v for v in settings.DEFAULTS["review_seats"].values() if v != settings.CODEX}

REPO = Path(settings.__file__).resolve().parent


@pytest.fixture
def env(tmp_path, monkeypatch):
    root = tmp_path.resolve()
    monkeypatch.setattr(settings, "ROOT", root)
    monkeypatch.setattr(settings, "CONFIG_PATH", root / "config" / "models.json")
    (root / "tools/BreezyVoice/data").mkdir(parents=True)
    (root / "outputs/uploads").mkdir(parents=True)
    return root


def cfg():
    return copy.deepcopy(settings.DEFAULTS)


def db(root: Path) -> Path:
    return root / "config" / "studio.db"


def sidecars(root: Path) -> list[Path]:
    return [root / "config" / "studio.db-wal", root / "config" / "studio.db-shm"]


def write_json(root: Path, c) -> None:
    (root / "config").mkdir(exist_ok=True)
    settings.CONFIG_PATH.write_text(json.dumps(c, ensure_ascii=False, indent=2), encoding="utf-8")


def query(root: Path, sql: str, params=()) -> list[tuple]:
    con = sqlite3.connect(db(root))
    try:
        return con.execute(sql, params).fetchall()
    finally:
        con.close()


def table_rows(root: Path) -> tuple[list, list]:
    return (query(root, "SELECT role, config FROM roles ORDER BY role"),
            query(root, "SELECT seat, model FROM review_seats ORDER BY seat"))


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def changed(**kw) -> dict:
    c = cfg()
    c["roles"]["reason"]["model"] = kw.get("reason", "qwen3:8b")
    return c


# ---- validate(T1,不變) ----

def test_預設值在模型都已下載時合格(env):
    assert settings.validate(cfg(), ALL) == []


def test_未下載的模型回錯誤(env):
    c = cfg()
    c["roles"]["reason"]["model"] = "not-a-model:1b"
    errs = settings.validate(c, ALL)
    assert any(e.startswith("roles.reason.model") and "尚未下載" in e for e in errs)
    c = cfg()
    c["review_seats"]["drift"] = "not-a-model:1b"
    assert any(e.startswith("review_seats.drift") for e in settings.validate(c, ALL))


def test_結構檢查不看模型清單(env):
    c = cfg()
    c["roles"]["reason"]["model"] = "not-a-model:1b"
    assert settings.validate(c, None) == []


@pytest.mark.parametrize("seat", ["risk", "security", "compliance"])
def test_否決席設glm4回錯誤(env, seat):
    c = cfg()
    c["review_seats"][seat] = "glm4:latest"
    errs = settings.validate(c, ALL | {"glm4:latest"})
    assert any(e.startswith(f"review_seats.{seat}") and "glm4" in e for e in errs)


def test_非否決席可用glm4_但不可用codex(env):
    c = cfg()
    c["review_seats"]["impact"] = "glm4:latest"
    assert settings.validate(c, ALL) == []
    c["review_seats"]["impact"] = settings.CODEX
    assert any(e.startswith("review_seats.impact") for e in settings.validate(c, ALL))


def test_否決席可設codex且不需在模型清單(env):
    c = cfg()
    for s in settings.VETO_SEATS:
        c["review_seats"][s] = settings.CODEX
    assert settings.validate(c, ALL) == []


@pytest.mark.parametrize("wav", [
    "/etc/passwd",
    "outputs/other/a.wav",
    "tools/BreezyVoice/data/../../../x.wav",
    "outputs/uploads",
    "",
])
def test_允許目錄外的聲線路徑回錯誤(env, wav):
    c = cfg()
    c["roles"]["voice"]["wav"] = wav
    errs = settings.validate(c, ALL)
    assert any(e.startswith("roles.voice.wav") for e in errs), errs


def test_允許目錄內的聲線路徑合格(env):
    c = cfg()
    c["roles"]["voice"]["wav"] = "outputs/uploads/me.wav"
    assert settings.validate(c, ALL) == []
    c["roles"]["voice"]["wav"] = str(env / "tools/BreezyVoice/data/example.wav")
    assert settings.validate(c, ALL) == []


def test_符號連結逐層拒絕(env, tmp_path_factory):
    outside = tmp_path_factory.mktemp("outside").resolve()
    (outside / "x.wav").write_bytes(b"RIFF")
    (env / "outputs/uploads/evil.wav").symlink_to(outside / "x.wav")
    (env / "outputs/uploads/sub").symlink_to(outside)
    (env / "outputs/alias").symlink_to(env / "outputs/uploads")
    (env / "outputs/uploads/ok.wav").write_bytes(b"RIFF")
    (env / "outputs/uploads/link.wav").symlink_to(env / "outputs/uploads/ok.wav")
    for wav in ("outputs/uploads/evil.wav", "outputs/uploads/sub/x.wav",
                "outputs/alias/ok.wav", "outputs/uploads/link.wav"):
        c = cfg()
        c["roles"]["voice"]["wav"] = wav
        errs = settings.validate(c, ALL)
        assert any(e.startswith("roles.voice.wav") for e in errs), (wav, errs)


def test_允許目錄本身是符號連結時拒絕(env, tmp_path_factory):
    outside = tmp_path_factory.mktemp("outside2").resolve()
    (env / "outputs/uploads").rmdir()
    (env / "outputs/uploads").symlink_to(outside)
    c = cfg()
    c["roles"]["voice"]["wav"] = "outputs/uploads/x.wav"
    errs = settings.validate(c, ALL)
    assert any(e.startswith("roles.voice.wav") for e in errs), errs


@pytest.mark.parametrize("path,value", [
    (("roles", "image", "steps"), 0),
    (("roles", "image", "steps"), 9),
    (("roles", "image", "steps"), True),
    (("roles", "reason", "num_ctx"), 1024),
    (("roles", "reason", "num_ctx"), 131072),
    (("roles", "reason", "num_ctx"), "16384"),
    (("roles", "reason", "think"), "yes"),
    (("roles", "voice", "text"), ""),
])
def test_數值與型別範圍(env, path, value):
    c = cfg()
    c[path[0]][path[1]][path[2]] = value
    errs = settings.validate(c, ALL)
    assert any(e.startswith(".".join(path)) for e in errs), errs


def test_邊界值合格(env):
    c = cfg()
    c["roles"]["image"]["steps"] = 8
    c["roles"]["reason"]["num_ctx"] = 65536
    c["roles"]["taiwan"]["num_ctx"] = 2048
    assert settings.validate(c, ALL) == []


def test_未知鍵與缺鍵回錯誤(env):
    c = cfg()
    c["extra"] = 1
    c["roles"]["reason"]["temperature"] = 0.5
    c["review_seats"]["judge"] = "qwen3:8b"
    del c["roles"]["image"]["steps"]
    errs = settings.validate(c, ALL)
    for prefix in ("設定.extra", "roles.reason.temperature", "review_seats.judge", "roles.image.steps"):
        assert any(e.startswith(prefix) for e in errs), (prefix, errs)


def test_非物件回錯誤(env):
    assert settings.validate([], ALL)
    assert settings.validate({"roles": [], "review_seats": {}}, ALL)


HEAVY = "hf.co/HauhauCS/Qwen3.8-27B-Uncensored-HauhauCS-Aggressive-MTP-GGUF:IQ3_M"


@pytest.mark.parametrize("seat", settings.SEATS)
def test_任一席位設27B回錯誤(env, seat):
    assert HEAVY in settings.PAUSED_REVIEW_MODELS
    c = cfg()
    c["review_seats"][seat] = HEAVY
    for avail in (ALL | {HEAVY}, None):
        errs = settings.validate(c, avail)
        assert any(e.startswith(f"review_seats.{seat}") and "DIR-1 第 10 條" in e for e in errs), errs


def test_DEFAULTS仍通過且reason_heavy不受影響(env):
    assert settings.DEFAULTS["roles"]["reason_heavy"]["model"] == HEAVY
    assert settings.validate(cfg(), ALL) == []
    assert settings.validate(cfg(), None) == []


# ---- 介面常數 ----

def test_資料庫路徑與新警告常數():
    assert Path(settings.DB_PATH) == Path("config") / "studio.db"
    assert settings.WARN_DB_UNREADABLE == "設定資料庫無法讀取,已使用預設值"
    assert settings.WARN_DB_VERSION == "設定資料庫版本不明,已使用預設值"


# ---- load:資料庫與 JSON 都不存在 / 一次性匯入 ----

def test_資料庫與JSON都不存在回預設值且不建立資料庫(env):
    got = settings.load()
    assert got == settings.DEFAULTS
    assert "_warnings" not in got
    got["roles"]["reason"]["model"] = "x"  # 回傳的是副本
    assert settings.DEFAULTS["roles"]["reason"]["model"] != "x"
    assert not db(env).exists()
    assert not any(p.exists() for p in sidecars(env))


def test_只有合法JSON時第一次load匯入且之後只讀資料庫(env):
    c = changed()
    write_json(env, c)
    json_before = settings.CONFIG_PATH.read_bytes()
    got = settings.load()
    assert got == c and "_warnings" not in got
    assert db(env).is_file()
    meta = dict(query(env, "SELECT key, value FROM meta"))
    assert meta["schema_version"] == "3"   # CHG-20261022-04 T8(修訂 3):一次性匯入直接建為 v2
    assert meta["imported_from"] == "config/models.json"
    assert meta["imported_at"]
    assert settings.CONFIG_PATH.read_bytes() == json_before  # 原檔不刪不改
    # 之後改 JSON 不影響 load()
    write_json(env, changed(reason="glm4:latest"))
    assert settings.load() == c
    assert dict(query(env, "SELECT key, value FROM meta"))["imported_at"] == meta["imported_at"]


@pytest.mark.parametrize("content,warning", [
    ("{ 不是 JSON", "WARN_BAD_JSON"),
    ("INVALID", "WARN_INVALID"),
])
def test_壞JSON回預設值帶警告且不建立資料庫(env, capsys, content, warning):
    (env / "config").mkdir()
    if content == "INVALID":
        bad = cfg()
        bad["roles"]["image"]["steps"] = 99
        content = json.dumps(bad)
    settings.CONFIG_PATH.write_text(content, encoding="utf-8")
    before = settings.CONFIG_PATH.read_bytes()
    got = settings.load()
    assert got.pop("_warnings") == [getattr(settings, warning)]
    assert got == settings.DEFAULTS
    assert getattr(settings, warning) in capsys.readouterr().err
    assert not db(env).exists()
    assert not any(p.exists() for p in sidecars(env))
    assert settings.CONFIG_PATH.read_bytes() == before


def test_匯入交易失敗時rollback且不留資料庫檔(env, monkeypatch):
    write_json(env, changed())
    original = settings._seat_rows

    def boom(c):
        raise RuntimeError("模擬匯入中途失敗")

    monkeypatch.setattr(settings, "_seat_rows", boom)
    got = settings.load()
    assert got.pop("_warnings") == [settings.WARN_DB_UNREADABLE]
    assert got == settings.DEFAULTS
    assert not db(env).exists()
    assert not any(p.exists() for p in sidecars(env))
    monkeypatch.setattr(settings, "_seat_rows", original)
    assert settings.load() == changed()  # 下次 load() 再試


# ---- save ----

def test_save後load相等(env):
    c = changed()
    c["roles"]["image"]["steps"] = 2
    c["roles"]["taiwan"]["think"] = False
    settings.save(c)
    assert settings.load() == c
    assert not settings.CONFIG_PATH.exists()  # 不寫 JSON
    c2 = changed(reason="glm4:latest")
    settings.save(c2)
    assert settings.load() == c2


def test_save去除警告鍵(env):
    c = cfg()
    c["_warnings"] = ["x"]
    settings.save(c)
    assert "_warnings" not in settings.load()


def test_save拒絕結構不合格且不動資料庫(env):
    settings.save(cfg())
    before = table_rows(env)
    bad = cfg()
    bad["roles"]["image"]["steps"] = 100
    with pytest.raises(ValueError):
        settings.save(bad)
    assert table_rows(env) == before


def test_save交易中途丟例外時兩表都不變(env, monkeypatch):
    settings.save(cfg())
    before = table_rows(env)
    calls = []

    def boom(c):
        calls.append(1)
        raise RuntimeError("模擬寫到一半中斷")

    # roles 已在交易內刪除並重寫之後,review_seats 寫入前丟例外
    monkeypatch.setattr(settings, "_seat_rows", boom)
    with pytest.raises(RuntimeError):
        settings.save(changed())
    assert calls
    assert table_rows(env) == before
    assert settings.load() == settings.DEFAULTS


def test_config目錄不存在時save在repo內建立(env):
    assert not (env / "config").exists()
    settings.save(cfg())
    assert (env / "config").is_dir() and not (env / "config").is_symlink()
    assert settings.load() == settings.DEFAULTS


def test_新建資料庫權限0600且journal_mode為wal(env):
    settings.save(cfg())
    assert stat.S_IMODE(db(env).stat().st_mode) == 0o600
    assert query(env, "PRAGMA journal_mode") == [("wal",)]


def test_匯入建立的資料庫權限0600且journal_mode為wal(env):
    write_json(env, changed())
    settings.load()
    assert stat.S_IMODE(db(env).stat().st_mode) == 0o600
    assert query(env, "PRAGMA journal_mode") == [("wal",)]


def test_版本已知但以rollback_journal建立的資料庫開啟後改為wal(env):
    settings.save(changed())
    con = sqlite3.connect(db(env))
    con.execute("PRAGMA journal_mode=DELETE")
    con.close()
    assert query(env, "PRAGMA journal_mode") == [("delete",)]
    assert settings.load() == changed()
    assert query(env, "PRAGMA journal_mode") == [("wal",)]


# ---- 資料庫內容被改寫:讀出的值一律再經 validate ----

def test_資料庫否決席被改成glm4時回預設值(env, capsys):
    settings.save(changed())
    con = sqlite3.connect(db(env))
    with con:
        con.execute("UPDATE review_seats SET model = ? WHERE seat = ?", ("glm4:latest", "security"))
    con.close()
    before = table_rows(env)
    got = settings.load()
    assert got.pop("_warnings")
    assert got == settings.DEFAULTS
    assert table_rows(env) == before  # 不修改資料庫


def test_資料庫聲線路徑指向允許目錄外時回預設值(env):
    settings.save(changed())
    voice = dict(cfg()["roles"]["voice"], wav="/etc/passwd")
    con = sqlite3.connect(db(env))
    with con:
        con.execute("UPDATE roles SET config = ? WHERE role = ?",
                    (json.dumps(voice, ensure_ascii=False), "voice"))
    con.close()
    got = settings.load()
    assert got.pop("_warnings")
    assert got == settings.DEFAULTS


def test_資料庫角色設定不是JSON時回預設值(env):
    settings.save(changed())
    con = sqlite3.connect(db(env))
    with con:
        con.execute("UPDATE roles SET config = ? WHERE role = ?", ("{壞掉", "reason"))
    con.close()
    got = settings.load()
    assert got.pop("_warnings")
    assert got == settings.DEFAULTS


# ---- 版本不明與非 SQLite:不讀、不寫、位元組不變 ----

def _rollback_journal_db(path: Path, version: str) -> None:
    path.parent.mkdir(exist_ok=True)
    con = sqlite3.connect(path)
    con.execute("PRAGMA journal_mode=DELETE")
    with con:
        con.execute("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        con.execute("INSERT INTO meta (key, value) VALUES (?, ?)", ("schema_version", version))
        con.execute("CREATE TABLE roles (role TEXT PRIMARY KEY, config TEXT NOT NULL)")
        con.execute("CREATE TABLE review_seats (seat TEXT PRIMARY KEY, model TEXT NOT NULL)")
    con.close()


def test_版本不明時load回預設值_save丟錯_檔案不變(env, capsys):
    _rollback_journal_db(db(env), "99")
    before_bytes, before_sha = db(env).read_bytes(), sha(db(env))
    write_json(env, changed())  # 有 JSON 也不得匯入
    got = settings.load()
    assert got.pop("_warnings") == [settings.WARN_DB_VERSION]
    assert got == settings.DEFAULTS
    assert settings.WARN_DB_VERSION in capsys.readouterr().err
    with pytest.raises(ValueError):
        settings.save(changed())
    assert db(env).read_bytes() == before_bytes
    assert sha(db(env)) == before_sha
    assert not any(p.exists() for p in sidecars(env))
    assert not (env / "config" / "studio.db-journal").exists()


def test_有表但沒有meta表視為版本不明(env):
    db(env).parent.mkdir()
    con = sqlite3.connect(db(env))
    con.execute("PRAGMA journal_mode=DELETE")
    with con:
        con.execute("CREATE TABLE other (x)")
    con.close()
    before = sha(db(env))
    got = settings.load()
    assert got.pop("_warnings") == [settings.WARN_DB_VERSION]
    with pytest.raises(ValueError):
        settings.save(cfg())
    assert sha(db(env)) == before
    assert not any(p.exists() for p in sidecars(env))


@pytest.mark.parametrize("content", [
    b"this is not a sqlite database at all " * 200,
    b"SQLite format 3\x00" + b"\xff" * 4000,
], ids=["純文字", "檔頭正確但內容損壞"])
def test_非SQLite內容時load回預設值_save丟錯不覆寫不重建(env, capsys, content):
    db(env).parent.mkdir()
    db(env).write_bytes(content)
    before_sha = sha(db(env))
    got = settings.load()
    assert got.pop("_warnings") == [settings.WARN_DB_UNREADABLE]
    assert got == settings.DEFAULTS
    assert settings.WARN_DB_UNREADABLE in capsys.readouterr().err
    with pytest.raises(ValueError):
        settings.save(cfg())
    assert db(env).read_bytes() == content
    assert sha(db(env)) == before_sha
    assert not any(p.exists() for p in sidecars(env))


# ---- 路徑規則:符號連結與 repo 外 ----

def _outside_db(tmp_path_factory, name) -> tuple[Path, Path]:
    outside = tmp_path_factory.mktemp(name).resolve()
    target = outside / "studio.db"
    con = sqlite3.connect(target)
    con.execute("PRAGMA journal_mode=DELETE")
    with con:
        con.execute("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        con.execute("INSERT INTO meta (key, value) VALUES (?, ?)", ("schema_version", "1"))
    con.close()
    return outside, target


def _assert_unsafe(env, outside, watched: dict) -> None:
    got = settings.load()
    assert got.pop("_warnings") == [settings.WARN_UNSAFE_PATH]
    assert got == settings.DEFAULTS
    with pytest.raises(ValueError):
        settings.save(cfg())
    for p, digest in watched.items():
        assert sha(p) == digest, p
    assert sorted(x.name for x in outside.iterdir()) == sorted(p.name for p in watched)


def test_studio_db是符號連結時不讀不寫(env, tmp_path_factory):
    outside, target = _outside_db(tmp_path_factory, "outside_db")
    (env / "config").mkdir()
    db(env).symlink_to(target)
    _assert_unsafe(env, outside, {target: sha(target)})
    assert db(env).is_symlink()


def test_wal是符號連結時不讀不寫(env, tmp_path_factory):
    outside = tmp_path_factory.mktemp("outside_wal").resolve()
    target = outside / "victim.txt"
    target.write_bytes(b"do not touch")
    settings.save(changed())
    db_before = sha(db(env))
    for p in sidecars(env):
        if p.exists():
            p.unlink()
    (env / "config" / "studio.db-wal").symlink_to(target)
    _assert_unsafe(env, outside, {target: sha(target)})
    assert sha(db(env)) == db_before
    assert (env / "config" / "studio.db-wal").is_symlink()


def test_shm是符號連結時不讀不寫(env, tmp_path_factory):
    outside = tmp_path_factory.mktemp("outside_shm").resolve()
    target = outside / "victim.txt"
    target.write_bytes(b"do not touch")
    (env / "config").mkdir()
    (env / "config" / "studio.db-shm").symlink_to(target)
    _assert_unsafe(env, outside, {target: sha(target)})
    assert not db(env).exists()


def test_config目錄是符號連結時不讀不寫(env, tmp_path_factory):
    outside, target = _outside_db(tmp_path_factory, "outside_cfg")
    (outside / "models.json").write_text(json.dumps(changed()), encoding="utf-8")
    (env / "config").symlink_to(outside)
    _assert_unsafe(env, outside, {target: sha(target),
                                  outside / "models.json": sha(outside / "models.json")})


def test_models_json是符號連結時不匯入(env, tmp_path_factory):
    outside = tmp_path_factory.mktemp("outside_json").resolve()
    (outside / "models.json").write_text(json.dumps(changed()), encoding="utf-8")
    (env / "config").mkdir()
    settings.CONFIG_PATH.symlink_to(outside / "models.json")
    got = settings.load()
    assert got.pop("_warnings") == [settings.WARN_UNSAFE_PATH]
    assert got == settings.DEFAULTS
    assert not db(env).exists()


def test_DB_PATH不在repo內時拒絕(env, monkeypatch, tmp_path_factory):
    outside, target = _outside_db(tmp_path_factory, "outside_path")
    monkeypatch.setattr(settings, "DB_PATH", target)
    _assert_unsafe(env, outside, {target: sha(target)})


# ---- 不以 f-string 組 SQL(CHG 的 ast 檢查) ----

def test_settings不以fstring組SQL():
    t = ast.parse((REPO / "settings.py").read_text(encoding="utf-8"))
    assert not any(isinstance(n, ast.JoinedStr) and "SELECT" in ast.unparse(n).upper()
                   for n in ast.walk(t))


def test_config_models_json不在git追蹤內():
    if not (REPO / ".git").exists():
        pytest.skip("不在 git 工作樹內")
    out = subprocess.run(["git", "ls-files", "config/models.json"], cwd=REPO,
                         capture_output=True, text=True, check=True).stdout
    assert out == ""


# ---- 跨行程:兩個子行程同時操作同一個資料庫 ----

CHILD_PRELUDE = textwrap.dedent("""
    import json, sys, time
    from pathlib import Path
    sys.path.insert(0, {repo!r})
    import settings
    root = Path({root!r})
    settings.ROOT = root
    settings.CONFIG_PATH = root / "config" / "models.json"
    go = root / "go"
    deadline = time.time() + 30
    while not go.exists():
        if time.time() > deadline:
            sys.exit("等不到開始訊號")
        time.sleep(0.001)
""")

CHILD_LOAD_ONCE = CHILD_PRELUDE + textwrap.dedent("""
    print(json.dumps(settings.load(), ensure_ascii=False))
""")

CHILD_SAVE_MANY = CHILD_PRELUDE + textwrap.dedent("""
    configs = json.loads({configs!r})
    for i in range(200):
        settings.save(configs[i % 2])
    print("ok")
""")

CHILD_LOAD_MANY = CHILD_PRELUDE + textwrap.dedent("""
    configs = json.loads({configs!r})
    bad = []
    for i in range(200):
        got = settings.load()
        if got not in configs:
            bad.append(got)
    print(json.dumps({{"bad": bad}}, ensure_ascii=False))
""")


def _spawn(code: str, cwd: Path) -> subprocess.Popen:
    return subprocess.Popen([sys.executable, "-c", code], cwd=cwd, text=True,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            env=dict(os.environ, PYTHONUTF8="1", PYTHONDONTWRITEBYTECODE="1"))


def _run_pair(env: Path, codes: list[str]) -> list[tuple[int, str, str]]:
    procs = [_spawn(c, env) for c in codes]
    time.sleep(0.5)  # 讓兩個子行程都進入等待
    (env / "go").write_text("go")
    results = []
    for p in procs:
        out, err = p.communicate(timeout=180)
        results.append((p.returncode, out, err))
    return results


@pytest.mark.parametrize("round_", range(3))
def test_兩個子行程同時第一次load只匯入一次(env, round_):
    c = changed()
    write_json(env, c)
    code = CHILD_LOAD_ONCE.format(repo=str(REPO), root=str(env))
    results = _run_pair(env, [code, code])
    for rc, out, err in results:
        assert rc == 0, err
        assert "database is locked" not in err
        assert json.loads(out) == c  # 皆回相同設定,且沒有 _warnings
    rows = query(env, "SELECT value FROM meta WHERE key = ?", ("imported_at",))
    assert len(rows) == 1
    assert query(env, "SELECT value FROM meta WHERE key = ?", ("imported_from",)) == [
        ("config/models.json",)]


def test_一行程連續save200次另一行程load200次皆成功且完整(env):
    a, b = changed(reason="qwen3:8b"), changed(reason="glm4:latest")
    b["review_seats"]["drift"] = "glm4:latest"
    b["roles"]["image"]["steps"] = 2
    settings.save(a)
    configs = json.dumps([a, b], ensure_ascii=False)
    saver = CHILD_SAVE_MANY.format(repo=str(REPO), root=str(env), configs=configs)
    loader = CHILD_LOAD_MANY.format(repo=str(REPO), root=str(env), configs=configs)
    (rc_s, out_s, err_s), (rc_l, out_l, err_l) = _run_pair(env, [saver, loader])
    assert rc_s == 0, err_s
    assert out_s.strip() == "ok"
    assert rc_l == 0, err_l
    assert json.loads(out_l) == {"bad": []}
    for err in (err_s, err_l):
        assert "database is locked" not in err
        assert "[settings]" not in err  # 沒有任何回退警告
    assert settings.load() == b  # 最後一次 save 是 configs[199 % 2]



# ---- _discard 與初始化鎖(依 PR #36、PR #37 審議修正) ----

def _fail_first_open(monkeypatch, before_fail):
    """第一次 _open_rw(建立者自己的匯入或 save)先執行 before_fail(),再模擬失敗;之後照常。"""
    real = settings._open_rw
    calls = []

    def fake(path):
        calls.append(path)
        if len(calls) == 1:
            before_fail()
            raise sqlite3.OperationalError("database is locked(模擬)")
        return real(path)
    monkeypatch.setattr(settings, "_open_rw", fake)
    return real


def _ident(p: Path) -> tuple[int, int]:
    st = p.stat()
    return st.st_dev, st.st_ino


def _wait_for(path: Path, proc: subprocess.Popen, seconds: float = 60) -> None:
    deadline = time.monotonic() + seconds
    while not path.exists():
        if proc.poll() is not None:
            pytest.fail("子行程提早結束:" + proc.stderr.read())
        if time.monotonic() > deadline:
            proc.kill()
            pytest.fail(f"子行程 {seconds} 秒內沒有產生 {path.name}")
        time.sleep(0.01)


# 子行程:load() 後保持一條讀取連線(讓 -wal、-shm 存在),等 exit 訊號才結束
CHILD_LOAD_AND_HOLD = CHILD_PRELUDE + textwrap.dedent("""
    import sqlite3
    got = settings.load()
    con = sqlite3.connect(str(root / "config" / "studio.db"))
    con.execute("SELECT count(*) FROM roles").fetchone()
    (root / "done").write_text(json.dumps(got, ensure_ascii=False), encoding="utf-8")
    deadline = time.time() + 60
    while not (root / "exit").exists() and time.time() < deadline:
        time.sleep(0.01)
    con.close()
""")


def test_建立者清理時另一行程正要建立_另一行程的資料庫與附屬檔不被刪(env, monkeypatch):
    """建立者匯入失敗、刪掉自己的空檔後正要刪 -wal、-shm 時,另一行程開始第一次 load()。

    舊做法(只在刪附屬檔前看主檔是否存在)下,另一行程會在這個窗口建立新資料庫與附屬檔,
    接著附屬檔被建立者刪掉;初始化鎖下另一行程必須等清理結束才能建立。
    """
    c = changed()
    write_json(env, c)
    (env / "go").write_text("go")
    state = {}
    real_sidecars = settings._sidecars

    def window(p):
        if state.pop("armed", False):
            proc = _spawn(CHILD_LOAD_AND_HOLD.format(repo=str(REPO), root=str(env)), env)
            state["proc"] = proc
            deadline = time.monotonic() + 1.5
            while (not (env / "done").exists() and proc.poll() is None
                   and time.monotonic() < deadline):
                time.sleep(0.01)
            state["other_done_during_cleanup"] = (env / "done").exists()
        return real_sidecars(p)

    def arm():
        state["armed"] = True

    _fail_first_open(monkeypatch, arm)
    monkeypatch.setattr(settings, "_sidecars", window)
    got = settings.load()  # 建立者自己的匯入失敗 → 本次回預設值
    assert got.pop("_warnings") == [settings.WARN_DB_UNREADABLE]
    proc = state["proc"]
    try:
        assert state["other_done_during_cleanup"] is False  # 另一行程在鎖外等待
        _wait_for(env / "done", proc)
        assert json.loads((env / "done").read_text(encoding="utf-8")) == c
        # 另一行程建立的資料庫與附屬檔都在
        assert db(env).exists()
        assert all(p.exists() for p in sidecars(env))
        assert len(query(env, "SELECT value FROM meta WHERE key = ?", ("imported_at",))) == 1
        assert settings.load() == c
    finally:
        (env / "exit").write_text("exit")
        out, err = proc.communicate(timeout=60)
    assert proc.returncode == 0, err
    assert settings.load() == c


def test_discard不刪已有資料表的檔(env):
    """同一個檔已完成匯入或存入(有 meta 與資料)時,即使身分相同也保留(PR #36)。"""
    (env / "config").mkdir()
    path = db(env)
    ident = settings._create_db_file(path)
    con = settings._open_rw(path)
    try:
        settings._immediate(con, settings._save_body(changed()))
    finally:
        con.close()
    settings._discard(path, ident)
    assert path.exists() and _ident(path) == ident
    assert settings.load() == changed()


def test_建立者失敗時檔案已被換成另一個檔_不刪除(env, monkeypatch):
    write_json(env, changed())
    replacement = env / "config" / "replacement"

    def swap():
        replacement.write_bytes(b"")
        os.replace(replacement, db(env))  # 同一路徑換成另一個 inode

    _fail_first_open(monkeypatch, swap)
    got = settings.load()
    assert got.pop("_warnings") == [settings.WARN_DB_UNREADABLE]
    assert db(env).exists()  # 不是本次建立的那一個檔,保留
    assert db(env).stat().st_size == 0


def test_建立者失敗且檔案仍是自己建立的空檔_照舊刪除(env, monkeypatch):
    write_json(env, changed())
    real = _fail_first_open(monkeypatch, lambda: None)
    got = settings.load()
    assert got.pop("_warnings") == [settings.WARN_DB_UNREADABLE]
    assert not db(env).exists()
    assert not any(p.exists() for p in sidecars(env))
    monkeypatch.setattr(settings, "_open_rw", real)
    assert settings.load() == changed()  # 下次 load() 再試


def test_save建立者失敗時另一行程等鎖後才存入(env, monkeypatch):
    other = changed(reason="glm4:latest")
    (env / "go").write_text("go")
    state = {}

    def start_other_then_fail():
        code = CHILD_PRELUDE.format(repo=str(REPO), root=str(env)) + (
            f"settings.save(json.loads({json.dumps(json.dumps(other))}))\nprint('ok')\n")
        proc = _spawn(code, env)
        state["proc"] = proc
        time.sleep(0.8)
        assert proc.poll() is None  # 另一行程在初始化鎖外等待
        assert db(env).stat().st_size == 0

    real = _fail_first_open(monkeypatch, start_other_then_fail)
    with pytest.raises(sqlite3.OperationalError):
        settings.save(changed())
    out, err = state["proc"].communicate(timeout=60)
    assert state["proc"].returncode == 0, err
    assert out.strip() == "ok"
    monkeypatch.setattr(settings, "_open_rw", real)
    assert settings.load() == other


def test_初始化鎖檔權限0600且不刪除(env):
    write_json(env, changed())
    assert settings.load() == changed()
    lock = env / "config" / ".studio.db.lock"
    assert lock.is_file() and not lock.is_symlink()
    assert stat.S_IMODE(lock.stat().st_mode) == 0o600


def test_初始化鎖檔是符號連結時不讀不寫(env, tmp_path_factory):
    write_json(env, changed())
    outside = tmp_path_factory.mktemp("outside_lock")
    target = outside / "target"
    target.write_bytes(b"keep")
    (env / "config" / ".studio.db.lock").symlink_to(target)
    got = settings.load()
    assert got.pop("_warnings") == [settings.WARN_UNSAFE_PATH]
    assert got == settings.DEFAULTS
    with pytest.raises(ValueError):
        settings.save(cfg())
    assert target.read_bytes() == b"keep"
    assert not db(env).exists()
    assert sorted(x.name for x in outside.iterdir()) == ["target"]


# ---- CHG-20261022-04 T8:語音預設(voice_defaults 表)與 schema 1 → 2 升級 ----

VOICE_DEFAULTS = {"speed": 1.15, "pause_comma": 0.0, "pause_sentence": 0.4, "pause_paragraph": 0.8}


def _v1_db(path: Path, c: dict) -> None:
    """建立 T8 以前格式的 v1 資料庫(只有 meta、roles、review_seats),內容為 c。"""
    path.parent.mkdir(exist_ok=True)
    con = sqlite3.connect(path)
    with con:
        con.execute("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        con.execute("CREATE TABLE roles (role TEXT PRIMARY KEY, config TEXT NOT NULL)")
        con.execute("CREATE TABLE review_seats (seat TEXT PRIMARY KEY, model TEXT NOT NULL)")
        con.executemany("INSERT INTO meta (key, value) VALUES (?, ?)", [
            ("schema_version", "1"), ("imported_from", "config/models.json"),
            ("imported_at", "2026-10-26T00:00:00+00:00")])
        con.executemany("INSERT INTO roles (role, config) VALUES (?, ?)",
                        [(r, json.dumps(v, ensure_ascii=False, sort_keys=True)) for r, v in c["roles"].items()])
        con.executemany("INSERT INTO review_seats (seat, model) VALUES (?, ?)", list(c["review_seats"].items()))
    con.close()


def _tables(root: Path) -> set[str]:
    return {r[0] for r in query(root, "SELECT name FROM sqlite_master WHERE type = ?", ("table",))}


def _voice_rows(root: Path) -> dict:
    return dict(query(root, "SELECT key, value FROM voice_defaults"))


def test_DEFAULTS含語音預設且與專案模組一致(env):
    import project
    assert settings.DEFAULTS["voice_defaults"] == VOICE_DEFAULTS == project.DEFAULTS
    assert settings.SCHEMA_VERSION == "3"


def test_v1資料庫第一次load升為v2且既有列不變_語音預設為預設值(env):
    c = changed()
    c["review_seats"]["drift"] = "glm4:latest"
    _v1_db(db(env), c)
    before = table_rows(env)
    got = settings.load()
    assert "_warnings" not in got
    assert got == {**c, "voice_defaults": VOICE_DEFAULTS}
    meta = dict(query(env, "SELECT key, value FROM meta"))
    assert meta["schema_version"] == "3"
    assert meta["imported_at"] == "2026-10-26T00:00:00+00:00"   # 其他 meta 不動
    assert table_rows(env) == before   # roles、review_seats 內容不變
    assert _voice_rows(env) == VOICE_DEFAULTS
    assert settings.load_meta() == {"imported_from": "config/models.json",
                                    "imported_at": "2026-10-26T00:00:00+00:00"}


def test_已是v2時不再升級(env, monkeypatch):
    _v1_db(db(env), changed())
    settings.load()
    con = sqlite3.connect(db(env))
    with con:
        con.execute("UPDATE voice_defaults SET value = ? WHERE key = ?", (1.5, "speed"))
    con.close()

    def boom(con):
        raise AssertionError("v2 資料庫不得再升級")

    monkeypatch.setattr(settings, "_upgrade_v1_v2", boom)
    for _ in range(3):
        got = settings.load()
        assert "_warnings" not in got and got["voice_defaults"]["speed"] == 1.5


@pytest.mark.parametrize("fail_at", ["_write_voice", "_voice_rows"])
def test_升級交易中途丟例外時仍為v1且回傳帶警告(env, monkeypatch, capsys, fail_at):
    c = changed()
    _v1_db(db(env), c)
    before = table_rows(env)
    original = getattr(settings, fail_at)

    def boom(*a):
        raise RuntimeError("模擬升級中途失敗")

    monkeypatch.setattr(settings, fail_at, boom)
    got = settings.load()
    assert got.pop("_warnings") == [settings.WARN_DB_UPGRADE]
    assert got == {**c, "voice_defaults": VOICE_DEFAULTS}   # v1 內容 + 程式內語音預設
    assert settings.WARN_DB_UPGRADE in capsys.readouterr().err
    assert dict(query(env, "SELECT key, value FROM meta"))["schema_version"] == "1"
    assert "voice_defaults" not in _tables(env)   # CREATE TABLE 也一併 rollback
    assert table_rows(env) == before
    # 下次 load() 再試即升級成功
    monkeypatch.setattr(settings, fail_at, original)
    got = settings.load()
    assert "_warnings" not in got and got["voice_defaults"] == VOICE_DEFAULTS
    assert dict(query(env, "SELECT key, value FROM meta"))["schema_version"] == "3"


def test_版本3的資料庫照舊不讀不寫(env):
    _rollback_journal_db(db(env), "4")
    before = sha(db(env))
    got = settings.load()
    assert got.pop("_warnings") == [settings.WARN_DB_VERSION]
    assert got == settings.DEFAULTS
    with pytest.raises(ValueError):
        settings.save(changed())
    assert sha(db(env)) == before
    assert not any(p.exists() for p in sidecars(env))


def test_v1資料庫上save在同一交易內升級並存入(env):
    _v1_db(db(env), cfg())
    c = changed()
    c["voice_defaults"] = {"speed": 1.3, "pause_comma": 0.2, "pause_sentence": 0.5, "pause_paragraph": 1.0}
    settings.save(c)
    assert dict(query(env, "SELECT key, value FROM meta"))["schema_version"] == "3"
    assert settings.load() == c


def test_v1資料庫上save失敗時仍為v1且兩表不變(env, monkeypatch):
    _v1_db(db(env), cfg())
    before = table_rows(env)

    def boom(c):
        raise RuntimeError("模擬存入中途失敗")

    monkeypatch.setattr(settings, "_seat_rows", boom)
    with pytest.raises(RuntimeError):
        settings.save(changed())
    assert dict(query(env, "SELECT key, value FROM meta"))["schema_version"] == "1"
    assert "voice_defaults" not in _tables(env)
    assert table_rows(env) == before


@pytest.mark.parametrize("key,value", [
    ("speed", 2.5), ("speed", 0.45), ("speed", 2.05), ("pause_sentence", 5.5), ("pause_comma", -0.1),
    ("pause_paragraph", 5.06), ("speed", True), ("speed", "1.2"), ("speed", None), ("speed", float("nan")),
])
def test_語速或停頓超出範圍的save丟ValueError且不動資料庫(env, key, value):
    settings.save(changed())
    before = (table_rows(env), _voice_rows(env))
    c = changed()
    c["voice_defaults"] = dict(VOICE_DEFAULTS, **{key: value})
    with pytest.raises(ValueError) as e:
        settings.save(c)
    assert any(m.startswith(f"voice_defaults.{key}:") for m in e.value.args[0]), e.value.args
    assert (table_rows(env), _voice_rows(env)) == before


def test_語速取到0點05_停頓取到0點1(env):
    c = changed()
    c["voice_defaults"] = {"speed": 1.23, "pause_comma": 0.04, "pause_sentence": 5.04, "pause_paragraph": 2}
    assert settings.validate(c, None) == []
    settings.save(c)
    assert settings.load()["voice_defaults"] == {"speed": 1.25, "pause_comma": 0.0, "pause_sentence": 5.0,
                                                 "pause_paragraph": 2.0}


def test_語音預設的鍵必須恰為四個(env):
    c = changed()
    c["voice_defaults"] = {"speed": 1.2, "pause_comma": 0.0, "pause_sentence": 0.4, "volume": 1}
    errs = settings.validate(c, None)
    assert any(e.startswith("voice_defaults.volume") for e in errs)
    assert any(e.startswith("voice_defaults.pause_paragraph") for e in errs)
    c["voice_defaults"] = [1.2]
    assert settings.validate(c, None)


def test_save省略語音預設時保留資料庫內的值(env):
    c = changed()
    c["voice_defaults"] = dict(VOICE_DEFAULTS, speed=1.4)
    settings.save(c)
    without = {k: v for k, v in changed(reason="glm4:latest").items() if k != "voice_defaults"}
    settings.save(without)
    got = settings.load()
    assert got["roles"]["reason"]["model"] == "glm4:latest"
    assert got["voice_defaults"]["speed"] == 1.4


def test_新建資料庫沒帶語音預設時寫入預設值(env):
    settings.save({k: v for k, v in changed().items() if k != "voice_defaults"})
    assert dict(query(env, "SELECT key, value FROM meta"))["schema_version"] == "3"
    assert _voice_rows(env) == VOICE_DEFAULTS


def test_資料庫語音預設不合法時回預設值(env):
    settings.save(changed())
    con = sqlite3.connect(db(env))
    with con:
        con.execute("UPDATE voice_defaults SET value = ? WHERE key = ?", (9.0, "speed"))
    con.close()
    got = settings.load()
    assert got.pop("_warnings") == [settings.WARN_INVALID]
    assert got == settings.DEFAULTS


def test_T8以前格式的models_json匯入為v2且語音預設為預設值(env):
    old = {k: v for k, v in changed().items() if k != "voice_defaults"}
    write_json(env, old)
    got = settings.load()
    assert "_warnings" not in got
    assert got == {**old, "voice_defaults": VOICE_DEFAULTS}
    assert dict(query(env, "SELECT key, value FROM meta"))["schema_version"] == "3"
    assert _voice_rows(env) == VOICE_DEFAULTS


def test_models_json帶語音預設時一併匯入(env):
    c = changed()
    c["voice_defaults"] = dict(VOICE_DEFAULTS, pause_sentence=0.6)
    write_json(env, c)
    assert settings.load() == c
    assert _voice_rows(env)["pause_sentence"] == 0.6


CHILD_UPGRADE_ONCE = CHILD_PRELUDE + textwrap.dedent("""
    import os
    real = settings._upgrade_v1_v2
    def counted(con):
        real(con)
        fd = os.open(str(root / "upgrades.log"), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        os.write(fd, b"x\\n")
        os.close(fd)
    settings._upgrade_v1_v2 = counted
    print(json.dumps(settings.load(), ensure_ascii=False))
""")


@pytest.mark.parametrize("round_", range(3))
def test_兩個子行程同時觸發升級只升一次(env, round_):
    c = changed()
    _v1_db(db(env), c)
    before = table_rows(env)
    code = CHILD_UPGRADE_ONCE.format(repo=str(REPO), root=str(env))
    results = _run_pair(env, [code, code])
    for rc, out, err in results:
        assert rc == 0, err
        assert "database is locked" not in err and "[settings]" not in err
        assert json.loads(out) == {**c, "voice_defaults": VOICE_DEFAULTS}   # 皆沒有 _warnings
    assert (env / "upgrades.log").read_text().splitlines() == ["x"]
    assert dict(query(env, "SELECT key, value FROM meta"))["schema_version"] == "3"
    assert table_rows(env) == before
    assert _voice_rows(env) == VOICE_DEFAULTS


def test_升級時初始化鎖檔是符號連結則不讀不寫(env, tmp_path_factory):
    _v1_db(db(env), changed())
    before = sha(db(env))
    outside = tmp_path_factory.mktemp("outside_lock_v1")
    target = outside / "target"
    target.write_bytes(b"keep")
    (env / "config" / ".studio.db.lock").symlink_to(target)
    got = settings.load()
    assert got.pop("_warnings") == [settings.WARN_UNSAFE_PATH]
    assert got == settings.DEFAULTS
    assert sha(db(env)) == before and target.read_bytes() == b"keep"


def test_voice_speed_error_message_says_0_5_to_2_0(env):
    """DIR-5 統整 RS-32:語速範圍訊息寫成 0.5–2.0 倍(與規格一致)。"""
    c = cfg()
    c["voice_defaults"] = {**c["voice_defaults"], "speed": 2.5}
    errs = settings.validate(c, None)
    assert any("0.5–2.0 倍" in e for e in errs), errs


# ---- DIR-5 修正 4:路徑檢查後、讀取前換成符號連結 ----

def test_匯入來源在檢查後被換成連結_不讀外部內容(env, tmp_path, monkeypatch):
    write_json(env, changed())
    secret = tmp_path / "secret.json"
    secret.write_text(json.dumps(changed(reason="外部:不該被讀到"), ensure_ascii=False), encoding="utf-8")
    real = settings._config_path_error

    def swapping():
        msg = real()
        src = env / "config" / "models.json"
        if msg is None and not src.is_symlink():
            src.unlink()
            src.symlink_to(secret)
        return msg
    monkeypatch.setattr(settings, "_config_path_error", swapping)
    got = settings.load()
    assert "外部:不該被讀到" not in json.dumps(got, ensure_ascii=False)
    assert got.get("_warnings")
    assert not db(env).exists()


def test_save時config目錄在檢查後被換成連結_不建立也不寫外部(env, tmp_path, monkeypatch):
    """DIR-5 修正 5:config/ 以 mkdir(dir_fd) + O_NOFOLLOW 開啟,第一次路徑檢查之後換成連結也拒絕。"""
    outside = tmp_path / "outside-config"
    outside.mkdir()
    real = settings._db_path_error
    calls = []

    def swapping():
        calls.append(1)
        msg = real()
        if len(calls) == 1 and not (env / "config").exists():
            (env / "config").symlink_to(outside, target_is_directory=True)
        return msg
    monkeypatch.setattr(settings, "_db_path_error", swapping)
    with pytest.raises(ValueError):
        settings.save(changed())
    assert list(outside.iterdir()) == []


# ---- CHG-20261029-01 T8:合音詞庫(schema 3、匯入差異預覽與套用) ----

def _v2_db(root: Path, c: dict) -> None:
    """建立 CHG-20261022-04 T8 格式的 v2 資料庫(meta、roles、review_seats、voice_defaults)。"""
    _v1_db(db(root), c)
    con = sqlite3.connect(db(root))
    with con:
        con.execute("CREATE TABLE voice_defaults (key TEXT PRIMARY KEY, value REAL NOT NULL)")
        con.executemany("INSERT INTO voice_defaults (key, value) VALUES (?, ?)",
                        list(dict(VOICE_DEFAULTS, speed=1.3).items()))
        con.execute("UPDATE meta SET value = ? WHERE key = ?", ("2", "schema_version"))
    con.close()


def _all_rows(root: Path) -> dict:
    return {t: query(root, f"SELECT * FROM {t} ORDER BY 1") for t in ("roles", "review_seats", "voice_defaults")}


def _fusion_rows(root: Path) -> list:
    return query(root, "SELECT word, zhuyin, enabled, source FROM fusion_words ORDER BY word")


def test_v2資料庫開啟後升為3_既有三表逐列不變_預設詞6筆(env):
    c = changed()
    _v2_db(env, c)
    before = _all_rows(env)
    got = settings.load()
    assert "_warnings" not in got and got["voice_defaults"]["speed"] == 1.3
    assert dict(query(env, "SELECT key, value FROM meta"))["schema_version"] == "3"
    assert _all_rows(env) == before
    rows = _fusion_rows(env)
    assert len(rows) == len(settings.FUSION_DEFAULTS) == 11 and {r[0] for r in rows} == {w for w, _, _ in settings.FUSION_DEFAULTS}
    assert ("不要", "ㄅㄧㄠˋ", 0, "default") in rows and ("知道", "ㄗㄠˋ", 1, "default") in rows


def test_v2升級中途丟例外時維持v2_回預設詞庫帶警告(env, monkeypatch, capsys):
    _v2_db(env, changed())
    before = _all_rows(env)
    real = settings._write_fusion_defaults

    def boom(con):
        real(con)
        raise RuntimeError("模擬升級中途失敗")
    monkeypatch.setattr(settings, "_write_fusion_defaults", boom)
    got = settings.load()
    assert got.pop("_warnings") == [settings.WARN_DB_UPGRADE3]
    assert dict(query(env, "SELECT key, value FROM meta"))["schema_version"] == "2"
    assert "fusion_words" not in _tables(env) and _all_rows(env) == before
    fz = settings.load_fusions()
    assert fz["_warnings"] and len(fz["words"]) == len(settings.FUSION_DEFAULTS)
    monkeypatch.setattr(settings, "_write_fusion_defaults", real)
    assert "_warnings" not in settings.load()
    assert dict(query(env, "SELECT key, value FROM meta"))["schema_version"] == "3"


def test_版本4的資料庫合音詞庫不讀不寫(env):
    _rollback_journal_db(db(env), "4")
    before = sha(db(env))
    assert settings.load_fusions()["_warnings"]
    with pytest.raises(settings.FusionError):
        settings.save_fusion("然後", "ㄖㄢˊ", True)
    assert sha(db(env)) == before


def test_資料庫不存在時讀預設詞_寫入時建立資料庫(env):
    got = settings.load_fusions()
    assert [r["word"] for r in got["words"]] == sorted(w for w, _, _ in settings.FUSION_DEFAULTS)
    assert not db(env).exists()
    settings.save_fusion("然後", "ㄖㄢˊ", True)
    assert ("然後", "ㄖㄢˊ", 1, "custom") in _fusion_rows(env)
    assert settings.load() == settings.DEFAULTS            # 建庫時寫入程式內預設設定


def test_CRUD_預設詞可編輯停用還原不可刪_自訂詞可刪(env):
    settings.save(changed())
    settings.save_fusion("知道", "ㄓㄠˋ", False)
    assert ("知道", "ㄓㄠˋ", 0, "default") in _fusion_rows(env)
    with pytest.raises(settings.FusionError) as e:
        settings.delete_fusion("知道")
    assert e.value.status == 422
    settings.restore_fusion("知道")
    assert ("知道", "ㄗㄠˋ", 1, "default") in _fusion_rows(env)
    settings.save_fusion("然後", "ㄖㄢˊ", True)
    settings.delete_fusion("然後")
    assert "然後" not in {r[0] for r in _fusion_rows(env)}
    with pytest.raises(settings.FusionError) as e:
        settings.delete_fusion("然後")
    assert e.value.status == 404
    for bad in (("一二三四五", "ㄖㄢˊ", True), ("然後", "ㄓㄧ", True), ("然後", "ㄖㄢˊ", "true"), ("然,", "ㄖㄢˊ", True),
                ("不知道", "ㄅㄨˋ ㄓ ㄉㄠˋ", True), ("然後", "ㄖㄢˊ ㄏㄡˋ", True), ("不知道", "ㄅㄨˋ  ㄗㄠˋ", True)):
        with pytest.raises(settings.FusionError):
            settings.save_fusion(*bad)


def _export(fmt="json") -> bytes:
    return settings.export_fusions(fmt)


def test_匯出JSON再預覽_一致且零寫入(env):
    settings.save(changed())
    settings.save_fusion("然後", "ㄖㄢˊ", True)
    raw = _export()
    body = json.loads(raw)
    assert body["format"] == "podcast-studio-fusions" and body["version"] == 1 and len(body["words"]) == len(settings.FUSION_DEFAULTS) + 1
    before = (db(env).read_bytes(), db(env).stat().st_mtime_ns)
    for fmt in ("json", "csv"):
        p = settings.preview_import(_export(fmt))
        assert p["status"] == "same" and p["changes"] == [] and p["rejected"] == [] and p["unchanged"] == len(settings.FUSION_DEFAULTS) + 1
    assert (db(env).read_bytes(), db(env).stat().st_mtime_ns) == before


def test_改一筆後預覽只有一筆update(env):
    settings.save(changed())
    raw = _export()
    settings.save_fusion("那樣", "ㄋㄧㄤˋ", False)
    p = settings.preview_import(raw)
    assert p["status"] == "diff"
    assert p["changes"] == [{"id": "那樣", "word": "那樣", "kind": "update",
                             "old": {"zhuyin": "ㄋㄧㄤˋ", "enabled": False},
                             "new": {"zhuyin": "ㄋㄧㄤˋ", "enabled": True}}]
    raw_csv = _export("csv")
    settings.save_fusion("那樣", "ㄋㄧㄤˋ", True)
    p = settings.preview_import(raw_csv)
    assert [(c["word"], c["kind"], c["new"]["enabled"]) for c in p["changes"]] == [("那樣", "update", False)]


def _file(words: list[dict]) -> bytes:
    return json.dumps({"format": "podcast-studio-fusions", "version": 1, "words": words},
                      ensure_ascii=False).encode("utf-8")


def _words_without(*drop) -> list[dict]:
    return [w for w in json.loads(_export())["words"] if w["word"] not in drop]


def test_檔案缺自訂詞與預設詞_刪除與停用_預覽不寫_套用全部(env):
    settings.save(changed())
    settings.save_fusion("然後", "ㄖㄢˊ", True)
    raw = _file(_words_without("然後", "我們"))
    before = _fusion_rows(env)
    p = settings.preview_import(raw)
    kinds = {(c["word"], c["kind"]) for c in p["changes"]}
    assert kinds == {("然後", "delete"), ("我們", "disable")}
    assert _fusion_rows(env) == before
    out = settings.apply_import(p["changes"], p["base_version"], True)
    assert sorted(out["applied"]) == ["我們", "然後"] and out["kept"] == []
    rows = {r[0]: r for r in _fusion_rows(env)}
    assert "然後" not in rows and rows["我們"][2] == 0


def test_keep的詞維持原值_其他照寫(env):
    settings.save(changed())
    settings.save_fusion("然後", "ㄖㄢˊ", True)
    words = _words_without("然後", "我們") + [{"word": "好像", "zhuyin": "ㄏㄠˇ", "enabled": True}]
    before = {r[0]: r for r in _fusion_rows(env)}
    p = settings.preview_import(_file(words))
    out = settings.apply_import(p["changes"], p["base_version"], False, ["然後", "我們"])
    assert out["applied"] == ["好像"] and sorted(out["kept"]) == ["我們", "然後"]
    rows = {r[0]: r for r in _fusion_rows(env)}
    assert rows["然後"] == before["然後"] and rows["我們"] == before["我們"]
    assert rows["好像"] == ("好像", "ㄏㄠˇ", 1, "custom")


def test_預覽後詞庫被改_套用丟FusionsChanged且不變(env):
    settings.save(changed())
    p = settings.preview_import(_file(_words_without("我們")))
    settings.save_fusion("知道", "ㄓㄠˋ", True)
    before = _fusion_rows(env)
    with pytest.raises(settings.FusionsChanged):
        settings.apply_import(p["changes"], p["base_version"], True)
    assert _fusion_rows(env) == before


def test_被拒絕的詞不產生更動_原資料保留(env):
    settings.save(changed())
    settings.save_fusion("然後", "ㄖㄢˊ", True)
    words = _words_without("然後") + [{"word": "然後", "zhuyin": "ㄓㄧ", "enabled": True},
                                      {"word": "一二三四五", "zhuyin": "ㄗㄤˇ", "enabled": True}]
    p = settings.preview_import(_file(words))
    assert p["status"] == "same" and p["changes"] == []
    assert [(r["word"], r["reason"]) for r in p["rejected"]] == [("然後", "注音不是合法音節(多個音節以半形空白分隔)"),
                                                                 ("一二三四五", "詞須為 2~4 個漢字")]
    assert all(set(r) == {"row", "word", "reason"} for r in p["rejected"])


@pytest.mark.parametrize("raw,status", [
    (b"x" * (256 * 1024 + 1), 413),
    ("{\"format\": \"podcast-studio-fusions\", \"version\": 1, \"words\": []}".encode("utf-16"), 422),
    (b"{\"format\": 1", 422),
    (b"{\"format\": \"other\", \"version\": 1, \"words\": []}", 422),
    (b"{\"format\": \"podcast-studio-fusions\", \"version\": 2, \"words\": []}", 422),
    (b"{\"format\": \"podcast-studio-fusions\", \"version\": 1, \"words\": {}}", 422),
    (b"{\"format\": \"podcast-studio-fusions\", \"version\": 1, \"words\": [[1]]}", 422),
    ("word,zhuyin\n這樣,ㄐㄧㄤˋ\n".encode("utf-8"), 422),
    ("word,zhuyin,enabled,source\n這樣,ㄐㄧㄤˋ,true\n".encode("utf-8"), 422),
])
def test_整檔不合法一律拒絕(env, raw, status):
    settings.save(changed())
    before = _fusion_rows(env)
    with pytest.raises(settings.FusionError) as e:
        settings.preview_import(raw)
    assert e.value.status == status
    assert _fusion_rows(env) == before


def test_1001筆整檔拒絕_1000筆可以(env):
    words = [{"word": chr(0x4E00 + i) + chr(0x5000 + i), "zhuyin": "ㄏㄠˇ", "enabled": True} for i in range(1001)]
    with pytest.raises(settings.FusionError) as e:
        settings.preview_import(_file(words))
    assert e.value.status == 413 and str(e.value) == "超過筆數上限(1000 筆)"
    assert len(settings.preview_import(_file(words[:1000]))["changes"]) >= 1000


def test_apply中途丟例外時rollback(env, monkeypatch):
    settings.save(changed())
    settings.save_fusion("然後", "ㄖㄢˊ", True)
    p = settings.preview_import(_file(_words_without("然後", "我們")))
    before = _fusion_rows(env)
    real_txn = settings._fusion_txn

    def txn(op):
        def wrapped(con):
            op(con)
            assert "然後" not in {r["word"] for r in settings._fusion_rows(con)}   # 交易內已刪
            raise RuntimeError("模擬寫入後失敗")
        return real_txn(wrapped)
    monkeypatch.setattr(settings, "_fusion_txn", txn)
    with pytest.raises(RuntimeError):
        settings.apply_import(p["changes"], p["base_version"], True)
    assert _fusion_rows(env) == before


def test_fusion_lexicon只含啟用者(env):
    lex = settings.fusion_lexicon()
    assert lex["知道"] == "ㄗㄠˋ" and "不要" not in lex


def test_只差被拒詞條時為一致(env):
    settings.save(changed())
    words = json.loads(_export())["words"] + [{"word": "好像", "zhuyin": "ㄓㄧ", "enabled": True}]
    before = (db(env).read_bytes(), db(env).stat().st_mtime_ns)
    p = settings.preview_import(_file(words))
    assert p["status"] == "same" and p["changes"] == [] and [r["word"] for r in p["rejected"]] == ["好像"]
    assert (db(env).read_bytes(), db(env).stat().st_mtime_ns) == before


def test_匯入上限預設值與範圍(env):
    settings.save(changed())
    assert dict(query(env, "SELECT key, value FROM fusion_settings")) == {"import_max_kb": 256,
                                                                         "import_max_rows": 1000}
    assert settings.load_import_limits() == {"max_kb": 256, "max_rows": 1000}
    for bad in ((15, 1000), (4097, 1000), (256, 9), (256, 10001), ("256", 1000), (256.0, 1000), (True, 1000)):
        with pytest.raises(settings.FusionError) as e:
            settings.save_import_limits(*bad)
        assert e.value.status == 422
        assert settings.load_import_limits() == {"max_kb": 256, "max_rows": 1000}
    for ok in ((16, 10), (4096, 10000)):
        settings.save_import_limits(*ok)
        assert settings.load_import_limits() == {"max_kb": ok[0], "max_rows": ok[1]}


def test_改上限後超過大小與筆數回413與訊息(env):
    settings.save(changed())
    settings.save_import_limits(32, 20)
    with pytest.raises(settings.FusionError) as e:
        settings.preview_import(b" " * (33 * 1024))
    assert (e.value.status, str(e.value)) == (413, "超過允許大小上限(32 KB)")
    words = [{"word": chr(0x4E00 + i) + "好", "zhuyin": "ㄏㄠˇ", "enabled": True} for i in range(21)]
    with pytest.raises(settings.FusionError) as e:
        settings.preview_import(_file(words))
    assert (e.value.status, str(e.value)) == (413, "超過筆數上限(20 筆)")
    assert settings.preview_import(_file(words[:20]))["status"] == "diff"
    settings.save_import_limits(256, 1000)
    with pytest.raises(settings.FusionError) as e:
        settings.preview_import(b" " * (257 * 1024))
    assert str(e.value) == "超過允許大小上限(256 KB)"


def test_v2升級時同時建立匯入上限表(env):
    _v2_db(env, changed())
    settings.load()
    assert dict(query(env, "SELECT key, value FROM fusion_settings")) == {"import_max_kb": 256,
                                                                         "import_max_rows": 1000}


def test_三四字詞可存入_CSV多音節往返(env):
    settings.save(changed())
    settings.save_fusion("我也不知", "ㄨㄛˇ ㄧㄝˇ ㄅㄨˋ", True)
    settings.save_fusion("怎麼辦", "ㄗㄣˇ ㄅㄢˋ", True)
    p = settings.preview_import(_export("csv"))
    assert p["status"] == "same" and p["rejected"] == []
    assert "不知道,ㄅㄨˋ ㄗㄠˋ,true,default" in _export("csv").decode("utf-8")
