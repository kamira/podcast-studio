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
    assert meta["schema_version"] == "1"
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
