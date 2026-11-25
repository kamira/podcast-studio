//! `parity` 執行檔(CHG-20261123-01 T2,代決 6)。
//!
//! ```text
//! parity run --unit <單元名> --fixture <檔案> [--python <路徑>] [--timeout <秒>] [--format text|json]
//! parity list
//! ```
//!
//! 退出碼:0 = 全部一致;1 = 至少一個不一致;2 = 參數錯誤、fixture 格式錯誤、
//! Python 轉接器啟動失敗 / 逾時 / 回應格式錯誤 / 回應 id 與請求不符。
//! 錯誤原因以繁體中文印到 stderr,此時不輸出結論。只有 `safe_fs.*` 單元會在系統暫存目錄建立並刪除合成檔案樹,不寫其他任何位置。
#![forbid(unsafe_code)]

use std::ffi::OsString;
use std::path::PathBuf;
use std::process::ExitCode;
use std::time::Duration;

use parity::py::{self, Adapter, DEFAULT_TIMEOUT_SECS};
use parity::{Fixture, Outcome, Registry, compare};

const USAGE: &str = "用法:
  parity run --unit <單元名> --fixture <檔案> [--python <路徑>] [--timeout <秒>] [--format text|json]
  parity list";

#[derive(Debug, Clone, Copy, PartialEq)]
enum Format {
    Text,
    Json,
}

#[derive(Debug)]
struct RunArgs {
    unit: String,
    fixture: PathBuf,
    python: Option<OsString>,
    timeout: Duration,
    format: Format,
}

/// 失敗原因:印到 stderr、exit 2;參數錯誤另附用法。
enum Fail {
    Usage(String),
    Run(String),
}

fn value_of(args: &mut impl Iterator<Item = OsString>, flag: &str) -> Result<OsString, Fail> {
    args.next()
        .ok_or_else(|| Fail::Usage(format!("參數 {flag} 缺少值")))
}

fn utf8(v: OsString, flag: &str) -> Result<String, Fail> {
    v.into_string()
        .map_err(|_| Fail::Usage(format!("參數 {flag} 的值不是合法的 UTF-8")))
}

fn parse_run(mut args: impl Iterator<Item = OsString>) -> Result<RunArgs, Fail> {
    let mut unit = None;
    let mut fixture = None;
    let mut python = None;
    let mut timeout = Duration::from_secs(DEFAULT_TIMEOUT_SECS);
    let mut format = Format::Text;
    while let Some(arg) = args.next() {
        let flag = arg.to_string_lossy().into_owned();
        match flag.as_str() {
            "--unit" => unit = Some(utf8(value_of(&mut args, &flag)?, &flag)?),
            "--fixture" => fixture = Some(PathBuf::from(value_of(&mut args, &flag)?)),
            "--python" => python = Some(value_of(&mut args, &flag)?),
            "--timeout" => {
                let v = utf8(value_of(&mut args, &flag)?, &flag)?;
                let secs: u64 =
                    v.parse().ok().filter(|s| *s > 0).ok_or_else(|| {
                        Fail::Usage(format!("--timeout 須為正整數秒數,收到 {v:?}"))
                    })?;
                timeout = Duration::from_secs(secs);
            }
            "--format" => {
                format = match utf8(value_of(&mut args, &flag)?, &flag)?.as_str() {
                    "text" => Format::Text,
                    "json" => Format::Json,
                    other => {
                        return Err(Fail::Usage(format!(
                            "--format 只能是 text 或 json,收到 {other:?}"
                        )));
                    }
                }
            }
            _ => return Err(Fail::Usage(format!("未知的參數 {flag:?}"))),
        }
    }
    Ok(RunArgs {
        unit: unit.ok_or_else(|| Fail::Usage("缺少 --unit".to_string()))?,
        fixture: fixture.ok_or_else(|| Fail::Usage("缺少 --fixture".to_string()))?,
        python,
        timeout,
        format,
    })
}

fn cmd_list(mut args: impl Iterator<Item = OsString>) -> Result<ExitCode, Fail> {
    if let Some(extra) = args.next() {
        return Err(Fail::Usage(format!(
            "list 不接受參數,收到 {:?}",
            extra.to_string_lossy()
        )));
    }
    for name in Registry::builtin().names() {
        println!("{name}");
    }
    Ok(ExitCode::SUCCESS)
}

fn cmd_run(args: impl Iterator<Item = OsString>) -> Result<ExitCode, Fail> {
    let args = parse_run(args)?;
    let registry = Registry::builtin();
    if !registry.contains(&args.unit) {
        return Err(Fail::Run(format!(
            "Rust 端沒有登錄單元 {:?}(以 parity list 查看已登錄的單元)",
            args.unit
        )));
    }
    let shown = args.fixture.display().to_string();
    let text = std::fs::read_to_string(&args.fixture)
        .map_err(|e| Fail::Run(format!("無法讀取 fixture {shown}:{e}")))?;
    let fixture = Fixture::parse(&text).map_err(|e| Fail::Run(format!("{e}({shown})")))?;
    if fixture.unit != args.unit {
        return Err(Fail::Run(format!(
            "fixture 的 unit 為 {:?},與 --unit {:?} 不同({shown})",
            fixture.unit, args.unit
        )));
    }

    let rust: Vec<Outcome> = fixture
        .cases
        .iter()
        .map(|c| registry.run(&args.unit, &c.input).expect("單元已確認登錄"))
        .collect();
    let adapter = Adapter {
        python: py::resolve_python(args.python, std::env::var_os("PARITY_PYTHON")),
        script: py::adapter_path(),
        timeout: args.timeout,
    };
    let python =
        py::run(&adapter, &args.unit, &fixture.cases).map_err(|e| Fail::Run(e.to_string()))?;

    let mut report = compare(&fixture, &rust, &python);
    report.fixture = shown;
    match args.format {
        Format::Text => print!("{}", report.to_text()),
        Format::Json => println!("{}", report.to_json()),
    }
    Ok(if report.is_consistent() {
        ExitCode::SUCCESS
    } else {
        ExitCode::from(1)
    })
}

fn main() -> ExitCode {
    let mut args = std::env::args_os().skip(1);
    let sub = args.next().map(|s| s.to_string_lossy().into_owned());
    let result = match sub.as_deref() {
        Some("run") => cmd_run(args),
        Some("list") => cmd_list(args),
        Some("-h" | "--help" | "help") => {
            println!("{USAGE}");
            return ExitCode::SUCCESS;
        }
        Some(other) => Err(Fail::Usage(format!("未知的子命令 {other:?}"))),
        None => Err(Fail::Usage("缺少子命令".to_string())),
    };
    match result {
        Ok(code) => code,
        Err(Fail::Usage(msg)) => {
            eprintln!("parity:{msg}\n{USAGE}");
            ExitCode::from(2)
        }
        Err(Fail::Run(msg)) => {
            eprintln!("parity:{msg}");
            ExitCode::from(2)
        }
    }
}
