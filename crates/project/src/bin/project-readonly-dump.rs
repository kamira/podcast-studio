//! 唯讀比對用的執行檔(CHG-20261128-01 代決 19、20;T4;退出條件 (2))。
//!
//! 用法:`project-readonly-dump --root <資料根目錄>`(使用者 Mac 上為放真實專案的 repo 根目錄,其下為 `projects/`)。
//! 由 `crates/project/py/readonly_check.py` 以 argv 清單啟動,從 stdout 讀結果,只在記憶體中比對。
//!
//! **只呼叫讀取 API**——`project::read` 的 `list_projects`、`load`、`list_history`、`load_version`,以及驗證後的
//! 寫出位元組 `Project::dump_bytes`(只產生記憶體中的位元組,不碰檔案系統)。讀取 API 一律不跟隨連結、以唯讀方式
//! 開檔、不取專案鎖、不建立任何檔案或目錄;本程式不寫任何檔案、不連網,結果只寫到 stdout
//! (`crates/project/tests/readonly_tool.rs` 以前後樹快照與雜湊清單驗證)。
//!
//! 前置(確認閘 Q3):執行前先確認備份可用,無法確認就停下、不要執行;比對期間關閉 Python 網頁與 MCP 伺服器。
//!
//! 輸出(JSON 行,UTF-8,換行一律 `\n`;順序固定):
//! - `{"projects": 結果}`:結果為 `{"value": [專案名稱…]}` 或錯誤;
//! - 每個專案依序:`{"load": 名稱, "result": 結果}`、`{"history": 名稱, "result": {"value": [版本 id…]} 或錯誤}`,
//!   以及每個版本 `{"version": [名稱, 版本 id], "result": 結果}`。
//!
//! 載入結果為 `{"dump": _dump(validate(...)) 字串}`;錯誤為 `{"class", "errors"}`(檔案系統錯誤只有
//! `{"class": "os_error"}`,訊息因語言而異、不比對)。這些內容只交給比對工具,比對工具只印數量。
#![forbid(unsafe_code)]

use std::io::{BufWriter, Write};
use std::path::PathBuf;
use std::process::ExitCode;

use project::Error;
use project::read::{list_history, list_projects, load, load_version};
use serde_json::{Value, json};

const USAGE: &str = "用法:project-readonly-dump --root <資料根目錄>
只以唯讀方式列出並載入 <資料根目錄>/projects/ 下的全部專案與 history 版本,結果以 JSON 行寫到 stdout;
不寫任何檔案、不取專案鎖、不連網。一般由 crates/project/py/readonly_check.py 啟動。
前置:執行前先確認備份可用(無法確認就停下、不要執行);比對期間關閉 Python 網頁與 MCP 伺服器。";

fn error_value(e: &Error) -> Value {
    if e.class() == "os_error" {
        json!({"class": "os_error"})
    } else {
        json!({"class": e.class(), "errors": e.errors()})
    }
}

fn loaded(result: project::Result<project::Project>) -> Value {
    match result.and_then(|p| p.dump_bytes()) {
        Ok(bytes) => match String::from_utf8(bytes) {
            Ok(text) => json!({ "dump": text }),
            Err(_) => json!({"class": "os_error"}),
        },
        Err(e) => error_value(&e),
    }
}

fn parse_args() -> Result<PathBuf, ExitCode> {
    let args: Vec<_> = std::env::args_os().skip(1).collect();
    match args.as_slice() {
        [flag] if flag == "--help" || flag == "-h" => {
            println!("{USAGE}");
            Err(ExitCode::SUCCESS)
        }
        [flag, root] if flag == "--root" => Ok(PathBuf::from(root)),
        _ => {
            eprintln!("{USAGE}");
            Err(ExitCode::from(2))
        }
    }
}

fn dump(root: &std::path::Path, out: &mut impl Write) -> std::io::Result<()> {
    let mut line = |v: Value| writeln!(out, "{v}");
    let names: Vec<String> = match list_projects(root) {
        Ok(entries) => entries.into_iter().map(|p| p.name).collect(),
        Err(e) => {
            line(json!({"projects": error_value(&e)}))?;
            return Ok(());
        }
    };
    line(json!({"projects": {"value": names}}))?;
    for name in &names {
        line(json!({"load": name, "result": loaded(load(root, name))}))?;
        match list_history(root, name) {
            Ok(vids) => {
                line(json!({"history": name, "result": {"value": vids}}))?;
                for vid in &vids {
                    let result = loaded(load_version(root, name, vid));
                    line(json!({"version": [name, vid], "result": result}))?;
                }
            }
            Err(e) => line(json!({"history": name, "result": error_value(&e)}))?,
        }
    }
    Ok(())
}

fn main() -> ExitCode {
    let root = match parse_args() {
        Ok(r) => r,
        Err(code) => return code,
    };
    let stdout = std::io::stdout();
    let mut out = BufWriter::new(stdout.lock());
    match dump(&root, &mut out).and_then(|()| out.flush()) {
        Ok(()) => ExitCode::SUCCESS,
        Err(e) => {
            eprintln!("project-readonly-dump:無法寫出結果:{e}");
            ExitCode::FAILURE
        }
    }
}
