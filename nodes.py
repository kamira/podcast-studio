"""節點登錄表(CHG-20261022-01 T5)。

每種節點以 `NodeSpec` 描述:輸入埠、輸出埠、參數宣告、是否為重型工作,以及
`run(inputs, params) -> dict`。節點只包住既有的 `taiwan_ai` / `media` 函式,不改其行為;
行為對應舊版 `podcast_studio.py` 的 write_script / revise_script / make_voice / make_cover / make_video。

介面:
  PORT_TYPES                     埠型別只有 text、image、audio、video 四種(Global Constraints)
  Port(type, required, label)    埠宣告;輸入埠 required=False 代表可不接
  Param(type, label, default, minimum, maximum, multiple_of, choices, max_len)
                                 參數宣告;`check(value) -> str | None` 回傳繁中錯誤訊息或 None。
                                 實際驗證由 executor.validate 呼叫 check(T6),不合格不執行
  NodeSpec                       kind、label、inputs、outputs、params、heavy、run;
                                 `output_ports(params)` 回傳該參數下實際產生的輸出埠(file-input 依 kind);
                                 `spec_dict()` 回傳可 JSON 序列化的宣告(不含 run)
  REGISTRY: dict[str, NodeSpec]  十一種節點(CHG-20261022-04 T8 新增「送到素材庫」send-to-library)

約定:
  - 文字輸出是字串;圖片、音訊、影片輸出是 `taiwan_ai.OUT`(outputs/)下檔案的路徑字串。
  - 輸出檔一律經 `taiwan_ai.reserve()` 或既有 `media` 函式排他建立,不覆寫既有檔案。
  - 節點本身**不取**跨行程重型鎖;`heavy=True` 的節點由執行器在 heavy_lock() 內呼叫 run(T6)。
  - `run()` 收到的 params 會先補上宣告的預設值;輸入不合法時丟 ValueError(繁中訊息)。
"""
from __future__ import annotations

import math
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import media
import names
import project as pj
import safe_fs
import taiwan_ai as tai

PORT_TYPES = frozenset({"text", "image", "audio", "video"})
PARAM_TYPES = frozenset({"str", "int", "float", "bool", "enum"})
MAX_TEXT = 20000          # 文字參數上限(Global Constraints)
CHARS_PER_MINUTE = 250    # 台灣華語播報約每分鐘 250 字
STYLES = ("輕鬆聊天", "知識科普", "新聞評論", "故事敘述", "訪談開場")
COVER_SUMMARY_CHARS = 600

# file-input 可接受的副檔名(與 T8 上傳白名單一致)
FILE_KINDS = {
    "text": frozenset({".txt", ".md"}),
    "image": frozenset({".png", ".jpg", ".jpeg", ".webp"}),
    "audio": frozenset({".wav", ".mp3", ".m4a"}),
}


@dataclass(frozen=True)
class Port:
    type: str
    required: bool = True
    label: str = ""

    def __post_init__(self):
        if self.type not in PORT_TYPES:
            raise ValueError(f"未知的埠型別:{self.type}")

    def as_dict(self) -> dict:
        return {"type": self.type, "required": self.required, "label": self.label}


@dataclass(frozen=True)
class Param:
    type: str
    label: str = ""
    default: Any = None
    minimum: float | None = None
    maximum: float | None = None
    multiple_of: float | None = None
    choices: tuple = ()
    max_len: int | None = None

    def __post_init__(self):
        if self.type not in PARAM_TYPES:
            raise ValueError(f"未知的參數型別:{self.type}")

    def check(self, value) -> str | None:
        """回傳錯誤訊息(繁中);合格回 None。bool 不算整數或小數。"""
        name = self.label or "參數"
        if self.type == "str":
            if not isinstance(value, str):
                return f"{name}必須是文字"
            if self.max_len is not None and len(value) > self.max_len:
                return f"{name}超過 {self.max_len} 字"
            return None
        if self.type == "bool":
            return None if isinstance(value, bool) else f"{name}必須是是/否"
        if self.type == "enum":
            return None if value in self.choices else f"{name}必須是 {'、'.join(map(str, self.choices))} 之一"
        if isinstance(value, bool):
            return f"{name}必須是數字"
        if self.type == "int" and not isinstance(value, int):
            return f"{name}必須是整數"
        if self.type == "float" and not isinstance(value, (int, float)):
            return f"{name}必須是數字"
        if isinstance(value, float) and not math.isfinite(value):
            return f"{name}必須是有限的數字"
        if self.minimum is not None and value < self.minimum:
            return f"{name}不得小於 {self.minimum:g}"
        if self.maximum is not None and value > self.maximum:
            return f"{name}不得大於 {self.maximum:g}"
        if self.multiple_of is not None:
            q = value / self.multiple_of
            if abs(q - round(q)) > 1e-9:
                return f"{name}必須是 {self.multiple_of:g} 的倍數"
        return None

    def as_dict(self) -> dict:
        d = {"type": self.type, "label": self.label, "default": self.default}
        for k in ("minimum", "maximum", "multiple_of", "max_len"):
            if getattr(self, k) is not None:
                d[k] = getattr(self, k)
        if self.choices:
            d["choices"] = list(self.choices)
        return d


@dataclass(frozen=True)
class NodeSpec:
    kind: str
    label: str
    inputs: dict[str, Port]
    outputs: dict[str, Port]
    params: dict[str, Param]
    heavy: bool
    fn: Callable[[dict, dict], dict] = field(repr=False)
    dynamic_outputs: Callable[[dict], tuple[str, ...]] | None = field(default=None, repr=False)

    def __post_init__(self):
        for p in self.params.values():
            if p.default is not None and p.check(p.default) is not None:
                raise ValueError(f"{self.kind} 的參數預設值不合法:{p.label}")

    def with_defaults(self, params: dict | None) -> dict:
        return {**{k: p.default for k, p in self.params.items()}, **(params or {})}

    def run(self, inputs: dict, params: dict) -> dict:
        """執行節點;params 先補預設值。不取重型鎖(由執行器負責)。"""
        return self.fn(inputs or {}, self.with_defaults(params))

    def output_ports(self, params: dict | None = None) -> dict[str, Port]:
        """該參數下實際產生的輸出埠;多數節點等於 outputs,file-input 只有 kind 對應的一個。"""
        if self.dynamic_outputs is None:
            return dict(self.outputs)
        keep = self.dynamic_outputs(self.with_defaults(params))
        return {k: p for k, p in self.outputs.items() if k in keep}

    def spec_dict(self) -> dict:
        return {"kind": self.kind, "label": self.label, "heavy": self.heavy,
                "inputs": {k: p.as_dict() for k, p in self.inputs.items()},
                "outputs": {k: p.as_dict() for k, p in self.outputs.items()},
                "params": {k: p.as_dict() for k, p in self.params.items()}}


def _text(inputs, key):
    v = inputs.get(key)
    return v if isinstance(v, str) else ""


# ---------- 輸入節點 ----------

def _text_input(inputs, params):
    return {"text": params["text"]}


def _output_parts(value, outside_msg: str) -> list[str]:
    """outputs/ 之下的路徑 → 相對 outputs/ 的路徑元件(DIR-5 修正 4:不 resolve,交給 safe_fs 逐層開啟)。

    相對路徑直接切元件;絕對路徑須以 outputs/(原樣或解析後的真實路徑)開頭。含 `..`、空路徑一律拒絕。"""
    p = Path(value)
    rel = None
    if p.is_absolute():
        for base in (tai.OUT, tai.OUT.resolve()):
            try:
                rel = p.relative_to(base)
                break
            except ValueError:
                continue
        if rel is None:
            raise ValueError(outside_msg)
    else:
        rel = p
    parts = list(rel.parts)
    if not parts or any(x in (".", "..") for x in parts):
        raise ValueError(outside_msg)
    return parts


def _open_output_file(value, outside_msg: str, missing_msg: str, link_msg: str) -> tuple[int, list[str]]:
    """自 outputs/ 以 dir_fd 逐層 O_NOFOLLOW 開啟一般檔,回傳 (fd, 路徑元件);任一層是符號連結丟 ValueError(link_msg)。"""
    parts = _output_parts(value, outside_msg)
    try:
        return safe_fs.open_path(tai.OUT, parts), parts
    except safe_fs.UnsafePathError:
        raise ValueError(link_msg) from None
    except (OSError, ValueError):
        raise ValueError(missing_msg) from None


def _resolve_output_file(rel):
    """相對於 outputs/ 的路徑 → outputs/ 之下的一般檔案路徑;絕對路徑、跳出 outputs/、任一層是符號連結一律拒絕。

    (DIR-5 修正 4)以 safe_fs 逐層開啟確認後關閉;回傳的路徑供下游節點(外部程式)使用。"""
    if not isinstance(rel, str) or not rel or "\x00" in rel or Path(rel).is_absolute():
        raise ValueError("檔案路徑必須是 outputs/ 之下的相對路徑")
    fd, parts = _open_output_file(rel, "檔案路徑不在 outputs/ 之下", "找不到檔案",
                                  "檔案路徑中有符號連結,不接受")
    os.close(fd)
    return tai.OUT.resolve().joinpath(*parts)


def _read_output_text(rel) -> str:
    """file-input 文字檔:同樣逐層開啟後直接讀這個 fd(不以路徑重開)。"""
    fd, _ = _open_output_file(rel, "檔案路徑不在 outputs/ 之下", "找不到檔案", "檔案路徑中有符號連結,不接受")
    with os.fdopen(fd, "rb") as f:
        raw = f.read(MAX_TEXT * 4 + 4)
    return raw.decode("utf-8-sig")


def _file_input(inputs, params):
    kind = params["kind"]
    path = _resolve_output_file(params["file"])
    if path.suffix.lower() not in FILE_KINDS[kind]:
        raise ValueError(f"副檔名 {path.suffix} 與檔案種類 {kind} 不符")
    if kind != "text":
        return {kind: str(path)}
    try:
        text = _read_output_text(params["file"])
    except UnicodeDecodeError as e:
        raise ValueError("文字檔必須是 UTF-8 編碼") from e
    if len(text) > MAX_TEXT:
        raise ValueError(f"文字檔超過 {MAX_TEXT} 字")
    return {"text": text}


# ---------- 文字節點(LLM) ----------

def _write_script(inputs, params):
    """原 podcast_studio.write_script 的第一段(reason 起稿);台灣用語潤飾拆成 taiwanize 節點。"""
    topic = _text(inputs, "topic")
    if not topic.strip():
        raise ValueError("請先輸入主題")
    chars = int(params["minutes"] * CHARS_PER_MINUTE)
    prompt = (f"請寫一篇單人主持的 podcast 口播稿。\n主題:{topic}\n風格:{params['style']}\n"
              f"長度:約 {chars} 字\n補充要求:{params['notes'] or '無'}\n\n"
              "規則:只輸出要念出來的文字,不要標題、不要 Markdown、不要 [音樂] 之類的舞台指示;"
              "句子短、口語化,適合用耳朵聽;開頭先打招呼並點出主題,結尾簡短收尾。")
    return {"text": tai.chat("reason", prompt, system=f"你是台灣的 podcast 編劇。{tai.TW}", think=False)}


def _revise(inputs, params):
    script, instruction = _text(inputs, "text"), params["instruction"]
    if not script.strip() or not instruction.strip():
        raise ValueError("需要文案和修改指示")
    out = tai.chat("reason", f"依照指示修改這篇口播稿,只輸出修改後的全文。\n\n指示:{instruction}\n\n原稿:\n{script}",
                   system=f"你是台灣的 podcast 編輯。{tai.TW}", think=False)
    return {"text": tai.to_tw(out)}


def _taiwanize(inputs, params):
    """原 podcast_studio.write_script 的第二段:以 taiwan 角色潤飾成台灣口語,再轉台灣繁體與用語。"""
    draft = _text(inputs, "text")
    if not draft.strip():
        raise ValueError("文案是空的")
    polished = tai.chat("taiwan", "請把以下口播稿潤飾成自然的台灣口語,保留內容與長度,只輸出改寫結果:\n\n" + draft,
                        system=tai.TW)
    return {"text": tai.to_tw(polished)}


COVER_SYSTEM = (
    "Write one English prompt for a wide 16:9 podcast cover background image that fits this episode. "
    "Bold, clean composition, strong central visual, keep the bottom quarter simple for a title. "
    "No text, no letters, no words in the image. Output only the prompt.")


def _cover_prompt(inputs, params):
    """原 podcast_studio.make_cover 的 prompt 產生段。"""
    title = _text(inputs, "title")
    if not title.strip():
        raise ValueError("請輸入節目標題(會印在封面上)")
    script = _text(inputs, "script")
    prompt = tai.chat("reason", f"節目標題:{title}\n內容摘要:{script[:COVER_SUMMARY_CHARS]}\n"
                                f"額外要求:{params['extra'] or '無'}", think=False, system=COVER_SYSTEM)
    return {"text": prompt}


# ---------- 媒體節點 ----------

def _speak(inputs, params):
    script = _text(inputs, "text")
    if not script.strip():
        raise ValueError("文案是空的")
    return {"audio": str(tai.speak(script))}


def _generate_image(inputs, params):
    prompt = _text(inputs, "prompt")
    if not prompt.strip():
        raise ValueError("生圖 prompt 是空的")
    return {"image": str(media.generate_image(prompt, params["width"], params["height"]))}


def _add_title(inputs, params):
    """原 media.make_cover 的後半段:裁成 16:9 並疊上標題,另存新檔(不改原圖)。"""
    image, title = _text(inputs, "image"), _text(inputs, "title")
    if not image or not title.strip():
        raise ValueError("需要圖片和標題")
    path = tai.reserve("cover", ".png")
    with tai.discard_on_error(path):
        media.save_image(media.add_title(media.to_16x9(image), title), path)   # 經 outputs/ 目錄 fd 寫入
    return {"image": str(path)}


def _make_video(inputs, params):
    image, audio = _text(inputs, "image"), _text(inputs, "audio")
    if not image or not audio:
        raise ValueError("需要先產生封面和語音")
    script = _text(inputs, "script") if params["subtitles"] else ""
    return {"video": str(media.make_video(image, audio, script))}


# ---------- 送到素材庫(CHG-20261022-04 T8) ----------
#
# 把接進來的文字、圖片、音訊登記成專案素材(`projects/<名稱>/assets/<新 id>.<副檔名>` + project.json 的
# assets),供精靈與完整模式的素材庫使用。安全:
#   - 專案名稱以 names.py 的中英日白名單檢查;專案必須已存在(不建立專案)。
#   - 圖片與音訊只接受 outputs/ 之下的一般檔案:檔案本身與其在 outputs/ 之下的每一層目錄都不得是
#     符號連結(DIR-5 統整 M-4;指向 outputs/ 內的連結也拒絕)。DIR-5 修正 4 起不 resolve:自 outputs/
#     以 dir_fd 逐層 O_NOFOLLOW 開啟(safe_fs),之後的檢查與複製都讀同一個 fd,副檔名在專案素材白名單(project.ASSET_EXTS)內,單檔不超過 LIBRARY_MAX_BYTES,
#     檔頭須與副檔名相符(media.sniff_binary,與 /api/upload 同一套嗅探規則,DIR-5 統整 M-5)。
#   - 複製到 assets/ 一律排他建立新檔(不覆寫),專案只記素材 id、不記路徑;
#     登記經 project.update() 在專案鎖內完成,驗證(素材數量與總量上限)失敗時刪除本次複製的檔案。

LIBRARY_MAX_BYTES = 200 * 1024 * 1024   # 同 studio_server.MAX_UPLOAD
LIBRARY_TEXT_LABEL = "文字(由流程送入)"
COPY_CHUNK = 1 << 20


def _library_source(value, kind: str) -> tuple[int, str, str]:
    """把節點輸入的檔案路徑解析成 outputs/ 之下的一般檔案;回傳 (已開啟的 fd, 副檔名, 顯示名稱)。

    (DIR-5 修正 4)不再 resolve() 後以路徑開檔:自 outputs/ 以 dir_fd 逐層 O_NOFOLLOW 開啟,任一層(含指向
    outputs/ 內者)是符號連結即拒絕;之後的檢查與複製都讀這個 fd。"""
    if not isinstance(value, str) or not value or "\x00" in value:
        raise ValueError("輸入的檔案路徑不正確")
    outside = "只能把 outputs/ 之下的檔案送到素材庫"
    parts = _output_parts(value, outside)
    ext = Path(parts[-1]).suffix.lower().lstrip(".")
    if ext not in pj.ASSET_EXTS[kind]:
        raise ValueError(f"副檔名 .{ext} 不能當成{'圖片' if kind == 'image' else '音訊'}素材")
    fd, _ = _open_output_file(value, outside, "找不到要送到素材庫的檔案",
                              "只能送出 outputs/ 之下的一般檔案(不接受符號連結)")
    st = os.fstat(fd)
    if st.st_size > LIBRARY_MAX_BYTES:
        os.close(fd)
        raise ValueError(f"只能送出 {LIBRARY_MAX_BYTES // 1024 ** 2} MB 以內的一般檔案")
    if not media.sniff_binary(f".{ext}", os.pread(fd, media.SNIFF_BYTES, 0)):
        os.close(fd)
        raise ValueError(f"檔案內容與副檔名 .{ext} 不符")
    label = "".join(c for c in parts[-1] if ord(c) >= 32 and ord(c) != 127)[:pj.MAX_LABEL] or None
    return fd, ext, label


def _copy_into(assets: int, ext: str, write) -> tuple[str, str, int]:
    """在 assets/(目錄 fd)內排他建立 `<新 id>.<ext>` 並以 write(f) 寫入;回傳 (id, 檔名, 位元組數)。"""
    for _ in range(8):
        aid = pj.new_id()
        fn = f"{aid}.{ext}"
        try:
            fd = safe_fs.create_excl(assets, fn, 0o644)
            break
        except FileExistsError:
            continue
    else:  # pragma: no cover - 隨機 id 連續撞名
        raise ValueError("無法建立不重複的素材檔名")
    try:
        with os.fdopen(fd, "wb") as f:
            write(f)
            size = f.tell()
    except BaseException:
        safe_fs.unlink(assets, fn, missing_ok=True)
        raise
    return aid, fn, size


def _send_to_library(inputs, params):
    name = params["project"]
    if names.normalize_name(name) is None:
        raise ValueError("請輸入要送入的專案名稱(" + pj.NAME_HINT + ")")
    text = inputs.get("text")
    if text is not None and not isinstance(text, str):
        raise ValueError("文字輸入不正確")
    text = text if text and text.strip() else None
    if text is not None and len(text) > MAX_TEXT:
        raise ValueError(f"文字超過 {MAX_TEXT} 字")
    files = [(kind, inputs.get(kind)) for kind in ("image", "audio") if inputs.get(kind)]
    if text is None and not files:
        raise ValueError("請至少接上一個要送到素材庫的輸入(文字、圖片或音訊)")
    pj.load(name)   # 專案必須已存在(不存在丟 ProjectNotFound,為 ValueError)
    opened = []
    try:
        for kind, value in files:
            opened.append((kind, *_library_source(value, kind)))
        created: list[str] = []   # assets/ 內本次建立的檔名;失敗時相對目錄 fd 刪除

        payload = text.encode("utf-8") if text is not None else b""

        def change(proj):
            # 專案鎖內、複製前檢查素材與自錄音檔的共同磁碟配額(DIR-5 修正 3);超過丟 QuotaExceeded,不留新檔
            pj.check_quota(name, sum(os.fstat(fd).st_size for _, fd, _, _ in opened) + len(payload))
            added = []
            with safe_fs.closing(pj.open_subdir(name, "assets", create=True)) as assets:
                for kind, fd, ext, label in opened:
                    def write(f, fd=fd):
                        os.lseek(fd, 0, os.SEEK_SET)
                        while chunk := os.read(fd, COPY_CHUNK):
                            f.write(chunk)
                    aid, fn, size = _copy_into(assets, ext, write)
                    created.append(fn)
                    added.append({"id": aid, "kind": kind, "ext": ext, "bytes": size,
                                  **({"label": label} if label else {})})
                if text is not None:
                    aid, fn, size = _copy_into(assets, "txt", lambda f: f.write(payload))
                    created.append(fn)
                    added.append({"id": aid, "kind": "text", "ext": "txt", "bytes": size,
                                  "label": LIBRARY_TEXT_LABEL})
            proj.assets = [*proj.assets, *added]
            return added

        try:
            _, _, added = pj.update(name, change)
        except BaseException:
            for fn in created:
                pj.unlink_in(name, "assets", fn)
            raise
    finally:
        for _, fd, _, _ in opened:
            os.close(fd)
    kinds = "、".join(dict.fromkeys({"image": "圖片", "audio": "音訊", "text": "文字"}[a["kind"]] for a in added))
    return {"text": f"已把 {len(added)} 個素材({kinds})送到專案「{name}」的素材庫"}


# ---------- 登錄表 ----------

def _p_text(label, max_len=MAX_TEXT, default=""):
    return Param("str", label, default=default, max_len=max_len)


TEXT_OUT = {"text": Port("text", label="文字")}

_SPECS = [
    NodeSpec("text-input", "文字", {}, TEXT_OUT,
             {"text": _p_text("文字")}, False, _text_input),
    NodeSpec("file-input", "檔案", {},
             {"text": Port("text", label="文字"), "image": Port("image", label="圖片"),
              "audio": Port("audio", label="音訊")},
             {"file": _p_text("檔案路徑", max_len=512),
              "kind": Param("enum", "檔案種類", default="text", choices=tuple(FILE_KINDS))},
             False, _file_input, dynamic_outputs=lambda p: (p["kind"],)),
    NodeSpec("write-script", "寫文案", {"topic": Port("text", label="主題")}, TEXT_OUT,
             {"style": Param("enum", "風格", default=STYLES[0], choices=STYLES),
              "minutes": Param("float", "長度(分鐘)", default=2, minimum=0.5, maximum=10, multiple_of=0.5),
              "notes": _p_text("補充要求", max_len=2000)},
             True, _write_script),
    NodeSpec("revise", "AI 修改", {"text": Port("text", label="文案")}, TEXT_OUT,
             {"instruction": _p_text("修改指示", max_len=2000)}, True, _revise),
    NodeSpec("taiwanize", "台灣用語", {"text": Port("text", label="文案")}, TEXT_OUT, {}, True, _taiwanize),
    NodeSpec("speak", "語音", {"text": Port("text", label="文案")},
             {"audio": Port("audio", label="語音")}, {}, True, _speak),
    NodeSpec("cover-prompt", "封面 prompt",
             {"title": Port("text", label="標題"), "script": Port("text", required=False, label="文案")},
             TEXT_OUT, {"extra": _p_text("畫面要求", max_len=2000)}, True, _cover_prompt),
    NodeSpec("generate-image", "生圖", {"prompt": Port("text", label="prompt")},
             {"image": Port("image", label="圖片")},
             {"width": Param("int", "寬", default=media.FLUX_W, minimum=256, maximum=2048, multiple_of=16),
              "height": Param("int", "高", default=media.FLUX_H, minimum=256, maximum=2048, multiple_of=16)},
             True, _generate_image),
    NodeSpec("add-title", "疊標題",
             {"image": Port("image", label="圖片"), "title": Port("text", label="標題")},
             {"image": Port("image", label="封面")}, {}, False, _add_title),
    NodeSpec("make-video", "合成影片",
             {"image": Port("image", label="封面"), "audio": Port("audio", label="語音"),
              "script": Port("text", required=False, label="字幕文案")},
             {"video": Port("video", label="影片")},
             {"subtitles": Param("bool", "加入字幕", default=True)}, True, _make_video),
    NodeSpec("send-to-library", "送到素材庫",
             {"text": Port("text", required=False, label="文字"),
              "image": Port("image", required=False, label="圖片"),
              "audio": Port("audio", required=False, label="音訊")},
             {"text": Port("text", label="結果")},
             {"project": Param("str", "專案名稱", default="", max_len=names.MAX_NAME)},
             False, _send_to_library),
]

REGISTRY: dict[str, NodeSpec] = {s.kind: s for s in _SPECS}
