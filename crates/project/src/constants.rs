//! `project.py` 的常數(第 110~188 行;CHG-20261128-01 T1)。
//!
//! 數值與字串逐一對照 Python 原值,由比對單元 `project.constants` 鎖住;`Decimal` 常數以 Python
//! `str(Decimal)` 的字串保存(錯誤訊息直接引用),四捨五入的步長見 [`crate::round`]。
//! dict 常數以 `(鍵, 值)` 陣列保存,順序即 Python 的建構順序(以 [`crate::pyjson::Entries`] 寫出)。

/// 專案根目錄下的專案目錄名稱。
pub const PROJECTS_NAME: &str = "projects";
/// 回收區目錄名稱(`projects/.trash/`)。
pub const TRASH_NAME: &str = ".trash";
/// 專案檔名稱。
pub const PROJECT_FILE: &str = "project.json";
/// 專案鎖檔名稱。
pub const LOCK_FILE: &str = ".lock";
/// 目前的專案檔版本。
pub const SCHEMA_VERSION: i64 = 2;
/// 載入時自動升級的舊版本。
pub const OLD_SCHEMA_VERSIONS: [i64; 1] = [1];

/// `history/` 保留的版本數。
pub const HISTORY_KEEP: usize = 10;
/// 回收區保留天數。
pub const TRASH_DAYS: u64 = 30;

/// 語速範圍與步長(`str(Decimal)`)。
pub const SPEED_MIN_TEXT: &str = "0.5";
pub const SPEED_MAX_TEXT: &str = "2.0";
pub const SPEED_STEP_TEXT: &str = "0.05";
/// 停頓範圍與步長(`str(Decimal)`)。
pub const PAUSE_MIN_TEXT: &str = "0";
pub const PAUSE_MAX_TEXT: &str = "5";
pub const PAUSE_STEP_TEXT: &str = "0.1";
/// 語調半音範圍與步長(`str(Decimal)`)。
pub const SEMI_MIN_TEXT: &str = "-6";
pub const SEMI_MAX_TEXT: &str = "6";
pub const SEMI_STEP_TEXT: &str = "0.1";
/// 語調位置步長(`str(Decimal)`)。
pub const POS_STEP_TEXT: &str = "0.01";

/// 語速與停頓的預設值(Python `DEFAULTS`,依建構順序)。
pub const DEFAULTS: [(&str, f64); 4] = [
    ("speed", 1.15),
    ("pause_comma", 0.0),
    ("pause_sentence", 0.4),
    ("pause_paragraph", 0.8),
];
/// 切點種類 → 停頓預設的鍵(Python `BREAK_PAUSE`)。
pub const BREAK_PAUSE: [(&str, &str); 3] = [
    ("sentence", "pause_sentence"),
    ("paragraph", "pause_paragraph"),
    ("forced", "pause_comma"),
];
/// 切點種類(Python `BREAKS`)。
pub const BREAKS: [&str; 4] = ["sentence", "paragraph", "forced", "end"];

pub const MAX_SCRIPT: usize = 50000;
pub const MAX_SEGMENT_TEXT: usize = 500;
pub const MAX_SEGMENTS: usize = 2000;
pub const MAX_PROJECT_BYTES: usize = 5 * 1024 * 1024;
pub const MAX_ASSETS: usize = 200;
pub const MAX_ASSET_BYTES: u64 = 2 * 1024 * 1024 * 1024;
pub const MAX_AUDIO_SECONDS: u64 = 60 * 60;
pub const MAX_LABEL: usize = 255;
pub const ZHUYIN_MAX: usize = 5;

/// 連音、清晰度、圓弧的預設百分比(Python `PROSODY_DEFAULTS`,依建構順序)。
pub const PROSODY_DEFAULTS: [(&str, i64); 3] =
    [("connect", 0), ("clarity", 100), ("roundness", 50)];
pub const PITCH_POINTS_MIN: usize = 2;
pub const PITCH_POINTS_MAX: usize = 16;
/// worker 實際套用的語調內部點上限與半音界限。
pub const INNER_POINTS_MAX: usize = 30;
pub const INNER_SEMI_LIMIT: f64 = 7.2;
pub const MAX_WORD_CLARITY: usize = 32;
pub const MAX_FUSIONS: usize = 16;
pub const FUSION_MIN: usize = 2;
pub const FUSION_MAX: usize = 4;
pub const MAX_FUSION_OFF: usize = 32;
pub const FUSION_DISPLAY_MAX: usize = 4;
pub const FUSION_SOURCES: [&str; 3] = ["rule", "lexicon", "manual"];
pub const POST_VERSION: i64 = 1;
/// 後製演算法標記(Python `POST_ALGO`,依建構順序)。
pub const POST_ALGO: [(&str, i64); 2] = [("clarity", 2), ("pitch", 2)];
/// 裁切參數(Python `TRIM_PARAMS`,依建構順序)。
pub const TRIM_PARAMS: [(&str, i64); 3] = [("threshold_db", -40), ("keep_ms", 30), ("fade_ms", 10)];
/// 素材種類 → 副檔名白名單(Python `ASSET_EXTS`,依建構順序)。
pub const ASSET_EXTS: [(&str, &[&str]); 3] = [
    ("image", &["png", "jpg", "jpeg", "webp"]),
    ("audio", &["wav", "mp3", "m4a"]),
    ("text", &["txt", "md"]),
];

/// 聲調符號與 BreezyVoice 數字聲調(`breezy_worker.TONE_MARKS`;一聲沒有符號,數字為 1)。
pub const TONE_MARKS: [(char, char); 4] = [('ˊ', '2'), ('ˇ', '3'), ('ˋ', '4'), ('˙', '5')];
/// Python `ZHUYIN_CHARS`(依碼位排序):聲調符號 ˇ ˊ ˋ ˙ 與注音符號 ㄅ(U+3105)~ㄩ(U+3129)。
pub const ZHUYIN_CHARS: &str = "\u{2c7}\u{2ca}\u{2cb}\u{2d9}\u{3105}\u{3106}\u{3107}\u{3108}\u{3109}\u{310a}\u{310b}\u{310c}\u{310d}\u{310e}\u{310f}\u{3110}\u{3111}\u{3112}\u{3113}\u{3114}\u{3115}\u{3116}\u{3117}\u{3118}\u{3119}\u{311a}\u{311b}\u{311c}\u{311d}\u{311e}\u{311f}\u{3120}\u{3121}\u{3122}\u{3123}\u{3124}\u{3125}\u{3126}\u{3127}\u{3128}\u{3129}";
/// Python `SENTENCE_ENDERS`(依碼位排序):`!;?。！；？`。
pub const SENTENCE_ENDERS: &str = "!;?\u{3002}\u{ff01}\u{ff1b}\u{ff1f}";
/// Python `CLOSERS`(依碼位排序)。
pub const CLOSERS: &str =
    "\"')\u{2019}\u{201d}\u{3009}\u{300b}\u{300d}\u{300f}\u{3011}\u{3015}\u{ff09}";
/// Python `NEWLINES`。
pub const NEWLINES: &str = "\n\r";
/// 漢字範圍(Python `HANZI_RANGES`,原順序)。
pub const HANZI_RANGES: [(u32, u32); 4] = [
    (0x4E00, 0x9FFF),
    (0x3400, 0x4DBF),
    (0xF900, 0xFAFF),
    (0x20000, 0x323AF),
];
/// 專案名稱不合格時的訊息(Python `NAME_HINT`)。
pub const NAME_HINT: &str =
    "專案名稱只能用中文、日文假名、英文字母、數字、底線、連字號,長度 1 到 64 字";
/// 硬連結封存撞名時的重試次數(Python `LINK_TRIES`)。
pub const LINK_TRIES: usize = 16;

pub const TOP_KEYS: [&str; 7] = [
    "schema_version",
    "name",
    "script",
    "defaults",
    "segments",
    "images",
    "assets",
];
pub const SEGMENT_KEYS_V1: [&str; 7] = [
    "id",
    "text",
    "break",
    "pause_after",
    "speed",
    "annotations",
    "audio",
];
pub const SEGMENT_KEYS: [&str; 14] = [
    "id",
    "text",
    "break",
    "pause_after",
    "speed",
    "annotations",
    "audio",
    "connect",
    "clarity",
    "word_clarity",
    "pitch",
    "post",
    "fusions",
    "fusion_off",
];
pub const POST_KEYS: [&str; 4] = ["id", "key", "seconds", "trim"];
pub const POST_OPTIONAL: [&str; 1] = ["pitch_points"];
pub const IMAGE_KEYS: [&str; 4] = ["id", "asset", "start", "end"];
pub const ASSET_KEYS: [&str; 5] = ["id", "kind", "ext", "bytes", "label"];
