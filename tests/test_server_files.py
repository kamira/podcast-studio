# -*- coding: utf-8 -*-
"""studio_server.py 上傳、檔案服務、工作流程存取的測試(CHG-20261022-01 T8)。

一律以 `create_app(root=tmp_path)` 把 repo 根目錄指到暫存目錄,不碰真正的 `outputs/`、
`workflows/`、`config/`;以 FastAPI TestClient 呼叫 ASGI app,不開網路埠。
"""
from __future__ import annotations

import json
import os
import struct
from pathlib import Path
from urllib.parse import quote

import pytest
from fastapi.testclient import TestClient

import executor
import nodes
import studio_server as ss

BASE = "http://127.0.0.1:7860"
ORIGIN = "http://127.0.0.1:7860"
TOKEN = "測試用權杖-T8-xyz789".encode("utf-8").hex()

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\rIHDR" + b"\x00" * 64
JPG = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00" + b"\x00" * 64
WEBP = b"RIFF" + struct.pack("<I", 80) + b"WEBPVP8 " + b"\x00" * 64
WAV = b"RIFF" + struct.pack("<I", 80) + b"WAVEfmt " + b"\x00" * 64
MP3_ID3 = b"ID3\x04\x00\x00\x00\x00\x00\x00" + b"\x00" * 64
MP3_SYNC = b"\xff\xfb\x90\x64" + b"\x00" * 64
M4A = struct.pack(">I", 24) + b"ftypM4A " + b"\x00\x00\x00\x00M4A isom" + b"\x00" * 64
TXT = "台灣夜市文化,今天來聊聊。\n".encode("utf-8")

GRAPH = {
    "nodes": [
        {"id": "n1", "kind": "text-input", "params": {"text": "台灣夜市文化"}, "pos": [10, 20]},
        {"id": "n2", "kind": "taiwanize", "params": {}},
    ],
    "edges": [{"from": {"node": "n1", "port": "text"}, "to": {"node": "n2", "port": "text"}}],
}


@pytest.fixture
def root(tmp_path):
    r = tmp_path / "repo"
    r.mkdir()
    return r


@pytest.fixture
def client(root):
    app = ss.create_app(runner=lambda g, e, c=None: None, token=TOKEN,
                        static_dir=root / "no-static", root=root)
    return TestClient(app, base_url=BASE)


def auth(**extra):
    return {"Origin": ORIGIN, "X-Studio-Token": TOKEN, **extra}


def upload(client, name, data, headers=None):
    return client.post("/api/upload", params={"name": name}, content=data,
                       headers=headers if headers is not None else auth())


def files_in(d: Path) -> list[str]:
    return sorted(p.name for p in d.iterdir()) if d.exists() else []


# ---------- POST /api/upload ----------

@pytest.mark.parametrize("name,data,kind", [
    ("文稿.txt", TXT, "text"),
    ("筆記.MD", TXT, "text"),
    ("封面.png", PNG, "image"),
    ("封面.jpg", JPG, "image"),
    ("封面.jpeg", JPG, "image"),
    ("封面.webp", WEBP, "image"),
    ("錄音.wav", WAV, "audio"),
    ("錄音.mp3", MP3_ID3, "audio"),
    ("錄音2.mp3", MP3_SYNC, "audio"),
    ("錄音.m4a", M4A, "audio"),
])
def test_upload_whitelisted_types(client, root, name, data, kind):
    r = upload(client, name, data)
    assert r.status_code == 200, r.text
    body = r.json()
    assert set(body) == {"file_id", "kind", "url"}
    assert body["kind"] == kind
    ext = "." + name.rsplit(".", 1)[1].lower()
    fid = body["file_id"]
    assert fid.startswith("uploads/") and fid.endswith(ext)
    stem = fid[len("uploads/"):-len(ext)]
    assert len(stem) == 32 and all(c in "0123456789abcdef" for c in stem)   # uuid4().hex
    assert name.rsplit(".", 1)[0] not in fid                               # 不用用戶端檔名
    assert body["url"] == f"/files/outputs/{fid}"
    saved = root / "outputs" / fid
    assert saved.read_bytes() == data
    assert files_in(root / "outputs" / "uploads") == [saved.name]          # 暫存檔已移除
    # file_id 可直接當 file-input 節點的 file 參數(相對於 outputs/)
    assert executor.validate({"nodes": [{"id": "f", "kind": "file-input",
                                         "params": {"file": fid, "kind": kind}}]}) == []
    # 下載回來內容相同
    got = client.get(body["url"])
    assert got.status_code == 200 and got.content == data


def test_upload_whitelist_matches_file_input_kinds():
    assert ss.UPLOAD_KINDS == {ext: k for k, exts in nodes.FILE_KINDS.items() for ext in exts}
    assert set(ss.UPLOAD_KINDS) == {".txt", ".md", ".png", ".jpg", ".jpeg", ".webp", ".wav", ".mp3", ".m4a"}


@pytest.mark.parametrize("name", ["setup.exe", "a.html", "a.svg", "noext", "a.png.exe", "a.", ".png"])
def test_upload_rejects_non_whitelisted_extension(client, root, name):
    r = upload(client, name, PNG)
    assert r.status_code == 400
    assert files_in(root / "outputs" / "uploads") == []


def test_upload_requires_name(client):
    r = client.post("/api/upload", content=PNG, headers=auth())
    assert r.status_code == 400


@pytest.mark.parametrize("name,data", [
    ("封面.png", "這是文字,不是圖片".encode("utf-8")),
    ("封面.png", JPG),                      # 副檔名與內容種類不符(JPEG 冒充 PNG)
    ("封面.jpg", PNG),
    ("封面.webp", WAV),                     # RIFF 但不是 WEBP
    ("錄音.wav", WEBP),
    ("錄音.mp3", "ID 不對的文字".encode("utf-8")),
    ("錄音.m4a", b"\x00\x00\x00\x18ftypqt  " + b"\x00" * 32),   # QuickTime 不是 m4a
    ("文稿.txt", PNG),                      # 二進位不是 UTF-8
    ("文稿.txt", "有\x00空字元".encode("utf-8")),
    ("文稿.txt", "臺灣".encode("big5")),     # 非 UTF-8 編碼
    ("文稿.txt", b""),
    ("封面.png", b""),
])
def test_upload_content_sniffing_mismatch_is_400(client, root, name, data):
    r = upload(client, name, data)
    assert r.status_code == 400, r.text
    assert files_in(root / "outputs" / "uploads") == []


def test_upload_ignores_client_content_type(client, root):
    r = upload(client, "封面.png", b"plain text", headers=auth(**{"Content-Type": "image/png"}))
    assert r.status_code == 400
    r = upload(client, "封面.png", PNG, headers=auth(**{"Content-Type": "text/plain"}))
    assert r.status_code == 200
    assert r.json()["kind"] == "image"


def test_upload_text_over_char_limit_is_400(client, root):
    r = upload(client, "長.txt", ("字" * (nodes.MAX_TEXT + 1)).encode("utf-8"))
    assert r.status_code == 400
    r = upload(client, "剛好.txt", ("字" * nodes.MAX_TEXT).encode("utf-8"))
    assert r.status_code == 200
    assert files_in(root / "outputs" / "uploads") == [Path(r.json()["file_id"]).name]


def test_upload_text_with_bom_is_ok(client):
    assert upload(client, "bom.txt", b"\xef\xbb\xbf" + TXT).status_code == 200


def test_upload_limit_is_200_mb():
    assert ss.MAX_UPLOAD == 200 * 1024 * 1024


def test_upload_over_limit_declared_length_is_413(client, root, monkeypatch):
    monkeypatch.setattr(ss, "MAX_UPLOAD", 1000)
    r = upload(client, "封面.png", PNG + b"\x00" * 2000)
    assert r.status_code == 413
    assert files_in(root / "outputs" / "uploads") == []


def test_upload_over_limit_streamed_is_413_and_temp_removed(client, root, monkeypatch):
    """沒有 Content-Length(chunked)時以累計位元組計數中止,暫存檔刪除。"""
    monkeypatch.setattr(ss, "MAX_UPLOAD", 1000)
    seen = []

    def gen():
        yield PNG
        for _ in range(50):
            # 每送一塊就看一次 uploads/:中止前暫存檔只能是點開頭的 .part
            seen.extend(files_in(root / "outputs" / "uploads"))
            yield b"\x00" * 100

    r = client.post("/api/upload", params={"name": "封面.png"}, content=gen(), headers=auth())
    assert r.status_code == 413
    assert files_in(root / "outputs" / "uploads") == []
    assert all(n.startswith(".") for n in seen)


def test_upload_exactly_at_limit_is_ok(client, root, monkeypatch):
    data = PNG + b"\x00" * 100
    monkeypatch.setattr(ss, "MAX_UPLOAD", len(data))
    assert upload(client, "封面.png", data).status_code == 200


def test_upload_requires_origin_and_token(client, root):
    assert upload(client, "封面.png", PNG, headers={"Origin": ORIGIN}).status_code == 403
    assert upload(client, "封面.png", PNG, headers={"X-Studio-Token": TOKEN}).status_code == 403
    assert upload(client, "封面.png", PNG,
                  headers={"Origin": "http://evil.example", "X-Studio-Token": TOKEN}).status_code == 403
    assert not (root / "outputs").exists()


def test_upload_never_overwrites_existing(client, root, monkeypatch):
    """uuid 撞名時重取,既有檔不被覆寫。"""
    up = root / "outputs" / "uploads"
    up.mkdir(parents=True)
    (up / ("a" * 32 + ".png")).write_bytes(b"original")
    ids = iter(["a" * 32, "b" * 32])

    class FakeUUID:
        def __init__(self, h):
            self.hex = h

    monkeypatch.setattr(ss.uuid, "uuid4", lambda: FakeUUID(next(ids, "c" * 32)))
    r = upload(client, "封面.png", PNG)
    assert r.status_code == 200
    assert (up / ("a" * 32 + ".png")).read_bytes() == b"original"
    assert (root / "outputs" / r.json()["file_id"]).read_bytes() == PNG


def test_upload_rejects_symlinked_outputs(client, root, tmp_path):
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (root / "outputs").symlink_to(elsewhere, target_is_directory=True)
    r = upload(client, "封面.png", PNG)
    assert r.status_code == 500
    assert str(tmp_path) not in r.text and str(root) not in r.text
    assert list(elsewhere.iterdir()) == []


def test_upload_rejects_symlinked_uploads_dir(client, root, tmp_path):
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (root / "outputs").mkdir()
    (root / "outputs" / "uploads").symlink_to(elsewhere, target_is_directory=True)
    r = upload(client, "封面.png", PNG)
    assert r.status_code == 500
    assert str(tmp_path) not in r.text
    assert list(elsewhere.iterdir()) == []


# ---------- GET /files/{path} ----------

@pytest.fixture
def served(root, tmp_path):
    out = root / "outputs"
    (out / "uploads").mkdir(parents=True)
    (out / "podcast-1.srt").write_text("1\n00:00:00,000 --> 00:00:01,000\n你好\n", encoding="utf-8")
    (out / "cover.png").write_bytes(PNG)
    (out / "page.html").write_text("<script>alert(1)</script>", encoding="utf-8")
    (out / ".heavy.lock").write_text("", encoding="utf-8")
    (out / "uploads" / ".upload-x.part").write_bytes(b"partial")
    (root / "config").mkdir()
    (root / "config" / "studio.db").write_bytes(b"SQLite format 3\x00 secret")
    (root / "secret.txt").write_text("機密", encoding="utf-8")
    sib = root / "outputs_evil"
    sib.mkdir()
    (sib / "x.txt").write_text("機密", encoding="utf-8")
    outside = tmp_path / "outside.txt"
    outside.write_text("機密", encoding="utf-8")
    (out / "link-out.txt").symlink_to(outside)
    (out / "link-db").symlink_to(root / "config" / "studio.db")
    (out / "link-in.png").symlink_to(out / "cover.png")
    # 指向兄弟目錄 outputs_evil/ 的連結:解析後的字串以「…/outputs」開頭,只有以路徑元件判斷才擋得住
    (out / "link-sib").symlink_to(sib, target_is_directory=True)
    (out / "link-sib.txt").symlink_to(sib / "x.txt")
    return root


def test_files_serves_outputs(client, served):
    r = client.get("/files/outputs/cover.png")
    assert r.status_code == 200 and r.content == PNG
    assert r.headers["content-type"] == "image/png"
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["content-disposition"].startswith("inline")


def test_files_text_is_plain_utf8(client, served):
    r = client.get("/files/outputs/podcast-1.srt")
    assert r.status_code == 200
    assert r.headers["content-type"] == "text/plain; charset=utf-8"
    assert "你好" in r.content.decode("utf-8")


def test_files_unknown_type_is_attachment_octet_stream(client, served):
    r = client.get("/files/outputs/page.html")
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/octet-stream"
    assert r.headers["content-disposition"].startswith("attachment")


def test_files_symlink_inside_outputs_to_inside_is_404(client, served):
    """DIR-5 修正 4(使用者 2026-10-28「every level」):outputs/ 內的符號連結一律不跟隨,指向內部也 404。"""
    r = client.get("/files/outputs/link-in.png")
    assert r.status_code == 404 and PNG not in r.content


@pytest.mark.parametrize("path", [
    "/files/config/studio.db",
    "/files/outputs/../config/studio.db",
    "/files/%2e%2e/config/studio.db",
    "/files/outputs/%2e%2e/config/studio.db",
    "/files/outputs/..%2fconfig%2fstudio.db",
    "/files/outputs%2f..%2fconfig%2fstudio.db",
    "/files/outputs/%2e%2e%2fsecret.txt",
    "/files/secret.txt",
    "/files//etc/passwd",
    "/files/%2Fetc%2Fpasswd",
    "/files/%2F" + "x",
    "/files/outputs_evil/x.txt",
    "/files/outputs/../outputs_evil/x.txt",
    "/files/outputs/%2e%2e/outputs_evil/x.txt",
    "/files/outputs/link-out.txt",
    "/files/outputs/link-db",
    "/files/outputs/link-sib/x.txt",
    "/files/outputs/link-sib.txt",
    "/files/outputs/.heavy.lock",
    "/files/outputs/uploads/.upload-x.part",
    "/files/outputs",
    "/files/outputs/",
    "/files/outputs/uploads",
    "/files/outputs/nope.png",
    "/files/outputs/cover.png%00.txt",
    "/files/",
])
def test_files_outside_outputs_is_404(client, served, path):
    r = client.get(path)
    assert r.status_code == 404, (path, r.status_code)
    assert "機密" not in r.text and "secret" not in r.text
    assert str(served) not in r.text


def test_files_absolute_path_of_real_outputs_file_is_404(client, served):
    """絕對路徑(即使指向 outputs/ 內的真實檔案)也一律 404。"""
    abs_path = str((served / "outputs" / "cover.png").resolve())
    assert client.get("/files/" + abs_path).status_code == 404
    assert client.get("/files" + abs_path).status_code == 404


def test_resolve_served_file_uses_component_containment(served):
    """直接呼叫內部函式:沒有 URL 正規化干擾時,`..` 與兄弟目錄仍被拒絕。"""
    f = ss._served_file
    assert f(served, "outputs/cover.png") == (served / "outputs" / "cover.png").resolve()
    for rel in ["outputs/../config/studio.db", "outputs/../outputs_evil/x.txt", "../repo/outputs/cover.png",
                str(served / "outputs" / "cover.png"), "outputs\\..\\secret.txt", "outputs/./cover.png/..",
                "outputs/link-out.txt", "outputs/.heavy.lock", "outputs_evil/x.txt", "outputs",
                "outputs/link-sib/x.txt", "outputs/link-sib.txt"]:
        assert f(served, rel) is None, rel


def test_files_404_when_outputs_is_symlink(client, root, tmp_path):
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / "a.png").write_bytes(PNG)
    (root / "outputs").symlink_to(elsewhere, target_is_directory=True)
    assert client.get("/files/outputs/a.png").status_code == 404


def test_files_wrong_host_is_403(client, served):
    r = client.get("/files/outputs/cover.png", headers={"Host": "evil.example:7860"})
    assert r.status_code == 403


# ---------- GET/PUT /api/workflows/{name} ----------

def put_wf(client, name, graph=GRAPH, overwrite=None, headers=None):
    body = {"graph": graph}
    if overwrite is not None:
        body["overwrite"] = overwrite
    return client.put(f"/api/workflows/{name}", json=body,
                      headers=headers if headers is not None else auth())


def test_workflow_put_then_get(client, root):
    r = put_wf(client, "my-flow_1")
    assert r.status_code == 200, r.text
    assert r.json() == {"name": "my-flow_1", "saved": True}
    stored = root / "workflows" / "my-flow_1.json"
    assert json.loads(stored.read_text(encoding="utf-8")) == GRAPH
    g = client.get("/api/workflows/my-flow_1")
    assert g.status_code == 200
    assert g.json() == {"name": "my-flow_1", "graph": GRAPH}
    assert files_in(root / "workflows") == ["my-flow_1.json"]     # 無殘留暫存檔


def test_workflow_same_name_without_overwrite_is_409(client, root):
    assert put_wf(client, "flow").status_code == 200
    other = {"nodes": [{"id": "x", "kind": "text-input", "params": {"text": "新"}}], "edges": []}
    for ow in (None, False, "true", 1):
        r = put_wf(client, "flow", graph=other, overwrite=ow)
        assert r.status_code in (409, 400), ow
        if ow in (None, False):
            assert r.status_code == 409
    assert json.loads((root / "workflows" / "flow.json").read_text(encoding="utf-8")) == GRAPH
    r = put_wf(client, "flow", graph=other, overwrite=True)
    assert r.status_code == 200
    assert client.get("/api/workflows/flow").json()["graph"] == other
    assert files_in(root / "workflows") == ["flow.json"]


def test_workflow_overwrite_is_atomic_replace(client, root, monkeypatch):
    """寫入中途失敗時原檔不變、不留暫存檔。"""
    assert put_wf(client, "flow").status_code == 200
    real_replace = os.replace

    def boom(src, dst):
        raise OSError("磁碟已滿 " + str(root))

    monkeypatch.setattr(ss.os, "replace", boom)
    other = {"nodes": [{"id": "x", "kind": "text-input", "params": {}}], "edges": []}
    r = put_wf(client, "flow", graph=other, overwrite=True)
    assert r.status_code == 500
    assert str(root) not in r.text
    monkeypatch.setattr(ss.os, "replace", real_replace)
    assert json.loads((root / "workflows" / "flow.json").read_text(encoding="utf-8")) == GRAPH
    assert files_in(root / "workflows") == ["flow.json"]


@pytest.mark.parametrize("name", [
    "..", "a..b", "a.json", "a b", "%2e%2e", "a%2fb", "a" * 65, "a%0a", "-" * 65,
    "夜市%2F流程", "夜市%2f..%2f流程", "夜市 流程", "夜市.流程", "%E5%A4%9C%00", "夜" * 65,
])
def test_workflow_bad_names_rejected(client, root, name):
    r = put_wf(client, name)
    assert r.status_code in (400, 404, 405), (name, r.status_code)
    assert r.status_code != 200
    g = client.get(f"/api/workflows/{name}")
    assert g.status_code in (400, 404), name
    wf = root / "workflows"
    assert files_in(wf) == []
    assert not (root / "a.json").exists()


# ---------- 工作流程名稱:中英日白名單(使用者 2026-10-26 指示) ----------

GOOD_NAMES = [
    "A-z_09", "a" * 64, "夜市流程", "第1集_夜市-初稿", "夜" * 64, "ポッドキャスト", "ひらがな", "々", "ラーメン",
    "東京タワー", "人々", "Podcast2026", "\u3400\u4dbf", "\u3041\u3096", "\u30a1\u30fa", "-_",
]
BAD_NAMES = [
    "", "a" * 65, "夜" * 65, "a b", "a.b", "a/b", "a\\b", "..", ".", "a\x00", "a\n", "\x07", "a\x7f",
    "\u0301", "a\u200bb", "a\u200db", "\ufeffa", "a\uff0fb", "a\u202eb", "a\u3000b", "a\u203fb",
    "夜市。流程", "a:b", "a*b", "a\U0001f600", "\U00020000", None, 123,
    # 縮減範圍後不再允許(使用者 2026-10-26:「只採最低限度 中英日即可」)
    "한국어", "\u0661\u0662", "ＡＢＣ", "１２３", "\u216b", "東京\u30fbタワー", "café", "Ünïcödé", "straße",
    "\u3040", "\u3097", "\u30a0", "\u30fb", "\u30fd", "\u30ff", "\u3006", "\u3007", "\u9fff\ua000",
    "\u33ff", "\u4dc0", "\uff66", "\u0391", "\u0430",
]


@pytest.mark.parametrize("name", GOOD_NAMES)
def test_workflow_name_cjk_whitelist_allowed(name):
    assert ss._normalize_name(name) == name


@pytest.mark.parametrize("name", BAD_NAMES)
def test_workflow_name_other_chars_rejected(name):
    assert ss._normalize_name(name) is None


def test_workflow_name_not_using_backslash_w_or_categories():
    """不用 `\\w`,也不用 Unicode 類別:只認明確的中英日白名單。"""
    assert not hasattr(ss, "WORKFLOW_NAME")
    for ch in ["\u203f", "\u2040", "\ud55c", "\u00e9", "\u0661"]:
        assert ss._normalize_name("a" + ch + "b") is None, ch


def test_workflow_chinese_name_round_trip_percent_encoded(client, root):
    name = "夜市流程"
    encoded = quote(name, safe="")
    assert encoded.startswith("%E5")
    r = client.put(f"/api/workflows/{encoded}", json={"graph": GRAPH}, headers=auth())
    assert r.status_code == 200, r.text
    assert r.json() == {"name": name, "saved": True}
    assert files_in(root / "workflows") == ["夜市流程.json"]
    g = client.get(f"/api/workflows/{encoded}")
    assert g.status_code == 200
    assert g.json() == {"name": name, "graph": GRAPH}


@pytest.mark.parametrize("first,second", [("Flow", "flow"), ("夜市Flow", "夜市FLOW"), ("ラーメンA1", "ラーメンa1")])
def test_workflow_case_variant_name_is_409_even_with_overwrite(client, root, first, second):
    """只差大小寫的名稱在 macOS(預設不分大小寫)會指到同一檔;一律拒絕,請用原名稱覆寫。"""
    assert put_wf(client, quote(first, safe="")).status_code == 200
    other = {"nodes": [{"id": "x", "kind": "text-input", "params": {"text": "新"}}], "edges": []}
    for ow in (None, False, True):
        r = put_wf(client, quote(second, safe=""), graph=other, overwrite=ow)
        assert r.status_code == 409, (ow, r.text)
        msg = r.json()["error"]
        assert "大小寫" in msg and first in msg
        assert str(root) not in r.text
    assert files_in(root / "workflows") == [first + ".json"]
    assert client.get(f"/api/workflows/{quote(first, safe='')}").json()["graph"] == GRAPH
    assert put_wf(client, quote(first, safe=""), graph=other, overwrite=True).status_code == 200
    assert client.get(f"/api/workflows/{quote(first, safe='')}").json()["graph"] == other


def test_workflow_sharp_s_rejected_not_case_variant(client, root):
    """ß 不在白名單:直接 400,不會走到大小寫比對。"""
    assert put_wf(client, "STRASSE").status_code == 200
    r = put_wf(client, quote("straße", safe=""), overwrite=True)
    assert r.status_code == 400
    assert "工作流程名稱" in r.json()["error"]
    assert files_in(root / "workflows") == ["STRASSE.json"]


def test_workflow_bad_name_error_is_chinese_without_paths(client, root):
    for name in ["夜市 流程", "a%E2%80%8Bb", "%EF%BC%8F", "%E2%80%AE", "%ED%95%9C", "%E3%83%BB", "%EF%BC%A1"]:
        r = client.put(f"/api/workflows/{name}", json={"graph": GRAPH}, headers=auth())
        assert r.status_code == 400, name
        msg = r.json()["error"]
        assert "工作流程名稱" in msg and "64" in msg
        assert str(root) not in r.text
        g = client.get(f"/api/workflows/{name}")
        assert g.status_code == 400 and g.json()["error"] == msg
    assert files_in(root / "workflows") == []


def test_workflow_chinese_name_invalid_graph_still_400(client, root):
    """草稿仍需通過 executor.validate 才能存。"""
    bad = {"nodes": [{"id": "a", "kind": "no-such-kind"}], "edges": []}
    r = put_wf(client, quote("夜市草稿", safe=""), graph=bad, overwrite=True)
    assert r.status_code == 400
    assert r.json()["errors"][0]["node_id"] == "a"
    assert files_in(root / "workflows") == []


def test_workflow_temp_file_name_has_no_name(client, root, monkeypatch):
    """暫存檔名為 `.wf-<uuid>.tmp`,不含工作流程名稱;寫完不留暫存檔。"""
    seen = []
    real = ss._open_excl

    def spy(dirfd, name, mode=0o600):   # DIR-5 修正 4:相對目錄 fd 排他建立
        seen.append(name)
        return real(dirfd, name, mode)

    monkeypatch.setattr(ss, "_open_excl", spy)
    name = "夜" * 64
    assert put_wf(client, quote(name, safe="")).status_code == 200
    assert seen and all(n.startswith(".wf-") and n.endswith(".tmp") and "夜" not in n for n in seen)
    assert files_in(root / "workflows") == [name + ".json"]


def test_workflow_slash_name_does_not_escape(client, root):
    r = client.put("/api/workflows/..%2f..%2fevil", json={"graph": GRAPH}, headers=auth())
    assert r.status_code in (400, 404, 405)
    assert not (root.parent / "evil.json").exists()
    assert not (root / "evil.json").exists()


def test_workflow_get_unknown_is_404(client):
    assert client.get("/api/workflows/nope").status_code == 404


def test_workflow_invalid_graph_is_400(client, root):
    bad = {"nodes": [{"id": "a", "kind": "no-such-kind"}], "edges": []}
    r = put_wf(client, "bad", graph=bad)
    assert r.status_code == 400
    assert r.json()["errors"][0]["node_id"] == "a"
    assert not (root / "workflows" / "bad.json").exists()


@pytest.mark.parametrize("body", [b"not json", b"[]", b'{"overwrite": true}', b'{"graph": [], "overwrite": true}',
                                  "\ud800".encode("utf-8", "surrogatepass")])
def test_workflow_bad_body_is_400(client, root, body):
    r = client.put("/api/workflows/x", content=body, headers=auth(**{"Content-Type": "application/json"}))
    assert r.status_code == 400
    assert not (root / "workflows" / "x.json").exists()


def test_workflow_body_size_limit_is_413(client, root, monkeypatch):
    monkeypatch.setattr(ss, "MAX_BODY", 200)
    big = {"nodes": [{"id": "a", "kind": "text-input", "params": {"text": "字" * 500}}], "edges": []}
    assert put_wf(client, "big", graph=big).status_code == 413
    assert not (root / "workflows" / "big.json").exists()


def test_workflow_put_requires_origin_and_token(client, root):
    assert put_wf(client, "flow", headers={"Origin": ORIGIN}).status_code == 403
    assert put_wf(client, "flow", headers={"X-Studio-Token": TOKEN}).status_code == 403
    assert put_wf(client, "flow", headers={"Origin": "null", "X-Studio-Token": TOKEN}).status_code == 403
    assert not (root / "workflows").exists()


def test_workflow_get_wrong_host_is_403(client, root):
    assert put_wf(client, "flow").status_code == 200
    assert client.get("/api/workflows/flow", headers={"Host": "evil.example"}).status_code == 403


def test_workflow_symlinked_dir_is_refused(client, root, tmp_path):
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / "flow.json").write_text(json.dumps(GRAPH), encoding="utf-8")
    (root / "workflows").symlink_to(elsewhere, target_is_directory=True)
    r = put_wf(client, "new", overwrite=True)
    assert r.status_code == 500
    assert str(tmp_path) not in r.text
    assert not (elsewhere / "new.json").exists()
    assert client.get("/api/workflows/flow").status_code == 404


def test_workflow_symlinked_file_not_followed(client, root, tmp_path):
    target = tmp_path / "target.json"
    target.write_text("原本", encoding="utf-8")
    (root / "workflows").mkdir()
    (root / "workflows" / "flow.json").symlink_to(target)
    assert client.get("/api/workflows/flow").status_code == 404
    assert put_wf(client, "flow").status_code == 409
    assert put_wf(client, "flow", overwrite=True).status_code == 200
    assert target.read_text(encoding="utf-8") == "原本"                 # 連結目標不被寫入
    assert not (root / "workflows" / "flow.json").is_symlink()


def test_workflow_corrupt_file_get_is_error_without_paths(client, root):
    (root / "workflows").mkdir()
    (root / "workflows" / "broken.json").write_text("{壞掉", encoding="utf-8")
    r = client.get("/api/workflows/broken")
    assert r.status_code == 422
    assert str(root) not in r.text


# ---------- DIR-5 修正 4:檢查後、操作前換成符號連結的競態 ----------

def _swap_dir(path: Path, target: Path) -> None:
    path.rename(path.with_name(path.name + "-moved"))
    path.symlink_to(target, target_is_directory=True)


@pytest.fixture
def elsewhere(tmp_path):
    e = tmp_path / "elsewhere"
    e.mkdir()
    (e / "a.png").write_bytes(b"SECRET" + PNG)
    (e / "x.json").write_text('{"nodes": [], "edges": [], "secret": 1}', encoding="utf-8")
    return e


def _after_rel_parts(monkeypatch, action):
    real = ss._rel_parts

    def wrapped(rel):
        parts = real(rel)
        action()
        return parts
    monkeypatch.setattr(ss, "_rel_parts", wrapped)


def test_race_files_dir_swapped_after_check_is_404(client, root, elsewhere, monkeypatch):
    sub = root / "outputs" / "sub"
    sub.mkdir(parents=True)
    (sub / "a.png").write_bytes(PNG)
    assert client.get("/files/outputs/sub/a.png").status_code == 200
    _after_rel_parts(monkeypatch, lambda: sub.is_symlink() or _swap_dir(sub, elsewhere))
    r = client.get("/files/outputs/sub/a.png")
    assert r.status_code == 404 and b"SECRET" not in r.content


def test_race_static_dir_swapped_after_check_is_404(root, elsewhere, monkeypatch):
    static = root / "static"
    (static / "css").mkdir(parents=True)
    (static / "css" / "a.png").write_bytes(PNG)
    app = ss.create_app(runner=lambda g, e, c=None: None, token=TOKEN, static_dir=static, root=root)
    c = TestClient(app, base_url=BASE)
    assert c.get("/static/css/a.png").content == PNG
    _after_rel_parts(monkeypatch, lambda: (static / "css").is_symlink() or _swap_dir(static / "css", elsewhere))
    r = c.get("/static/css/a.png")
    assert r.status_code == 404 and b"SECRET" not in r.content


def test_race_upload_dir_swapped_after_open_writes_stay_inside(client, root, elsewhere, monkeypatch):
    before = sorted(p.name for p in elsewhere.iterdir())
    updir = root / "outputs" / "uploads"
    real = ss._open_dir

    def wrapped(r, *parts, create):
        fd = real(r, *parts, create=create)
        if parts == ("outputs", "uploads") and not updir.is_symlink():
            _swap_dir(updir, elsewhere)
        return fd
    monkeypatch.setattr(ss, "_open_dir", wrapped)
    r = upload(client, "封面.png", PNG)
    assert r.status_code == 200, r.text
    assert sorted(p.name for p in elsewhere.iterdir()) == before          # 沒寫進連結目標
    assert files_in(root / "outputs" / "uploads-moved") == [r.json()["file_id"].split("/")[1]]


def test_race_workflow_file_swapped_after_dir_open_is_404(client, root, elsewhere, monkeypatch):
    assert put_wf(client, quote("夜市", safe="")).status_code == 200
    wf = root / "workflows" / "夜市.json"
    real = ss.safe_fs.open_file

    def wrapped(dirfd, name, flags=os.O_RDONLY):
        if name == "夜市.json" and not wf.is_symlink():
            wf.unlink()
            wf.symlink_to(elsewhere / "x.json")
        return real(dirfd, name, flags)
    monkeypatch.setattr(ss.safe_fs, "open_file", wrapped)
    r = client.get("/api/workflows/" + quote("夜市", safe=""))
    assert r.status_code == 404 and "secret" not in r.text
