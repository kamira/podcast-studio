//! 啟動 Python 轉接器 `py/adapter.py` 並以 JSON 行協定交換資料(CHG-20261123-01 代決 6)。
//!
//! - 直譯器取得順序:`--python <路徑>` → 環境變數 `PARITY_PYTHON` → 預設值
//!   (Windows 為 `python`,其他平台為 `python3`;Windows 的 `python3` 可能是商店別名)。
//! - 子程序一律以 argv 清單啟動、不經 shell;設 `PYTHONUTF8=1`、`PYTHONDONTWRITEBYTECODE=1`。
//! - 一次寫入全部請求後關閉 stdin,讀回全部回應;超過逾時即終止子程序。
//! - 回應須逐行合法、筆數相同、id 依序對應請求。

use std::ffi::OsString;
use std::fmt;
use std::io::{Read, Write};
use std::path::PathBuf;
use std::process::{Child, Command, ExitStatus, Stdio};
use std::thread::{self, JoinHandle};
use std::time::{Duration, Instant};

use crate::{Case, Outcome, ProtocolError, encode_request, match_responses, parse_response};

/// 預設逾時(秒)。
pub const DEFAULT_TIMEOUT_SECS: u64 = 60;

/// 錯誤訊息中保留的 stderr 尾端長度(字元)。
const STDERR_TAIL_CHARS: usize = 2000;

/// 依序取 `--python`、`PARITY_PYTHON`、平台預設值;空字串視為未提供。
pub fn resolve_python(cli: Option<OsString>, env: Option<OsString>) -> OsString {
    cli.filter(|v| !v.is_empty())
        .or_else(|| env.filter(|v| !v.is_empty()))
        .unwrap_or_else(|| OsString::from(if cfg!(windows) { "python" } else { "python3" }))
}

/// 轉接器腳本的位置(本 crate 目錄下的 `py/adapter.py`,建置時決定)。
pub fn adapter_path() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .join("py")
        .join("adapter.py")
}

/// 一次轉接器呼叫的設定。
#[derive(Debug, Clone)]
pub struct Adapter {
    pub python: OsString,
    pub script: PathBuf,
    pub timeout: Duration,
}

/// 轉接器呼叫失敗(命令列對應 exit 2)。
#[derive(Debug)]
pub enum AdapterError {
    /// 直譯器無法啟動(不存在、沒有執行權限等)。
    Spawn { python: OsString, reason: String },
    /// 超過逾時仍未結束;子程序已終止。
    Timeout(Duration),
    /// 轉接器以非 0 結束(例:未知單元 exit 2)。
    Exit { status: ExitStatus, stderr: String },
    /// 輸出不是 UTF-8。
    Encoding,
    /// 回應不合協定。
    Protocol(ProtocolError),
    /// 管線讀寫失敗。
    Io(String),
}

impl fmt::Display for AdapterError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            AdapterError::Spawn { python, reason } => write!(
                f,
                "無法啟動 Python 直譯器 {}:{reason}(可用 --python 或環境變數 PARITY_PYTHON 指定)",
                python.to_string_lossy()
            ),
            AdapterError::Timeout(t) => write!(
                f,
                "Python 轉接器逾時:{} 秒內未完成,已終止子程序",
                t.as_secs()
            ),
            AdapterError::Exit { status, stderr } => {
                write!(f, "Python 轉接器異常結束({status})")?;
                if !stderr.trim().is_empty() {
                    write!(f, ":\n{}", stderr.trim_end())?;
                }
                Ok(())
            }
            AdapterError::Encoding => write!(f, "Python 轉接器的輸出不是 UTF-8"),
            AdapterError::Protocol(e) => write!(f, "Python 轉接器{e}"),
            AdapterError::Io(e) => write!(f, "與 Python 轉接器通訊失敗:{e}"),
        }
    }
}

impl std::error::Error for AdapterError {}

fn read_all<R: Read + Send + 'static>(mut r: R) -> JoinHandle<std::io::Result<Vec<u8>>> {
    thread::spawn(move || {
        let mut buf = Vec::new();
        r.read_to_end(&mut buf)?;
        Ok(buf)
    })
}

fn join_bytes(h: JoinHandle<std::io::Result<Vec<u8>>>) -> Result<Vec<u8>, AdapterError> {
    h.join()
        .map_err(|_| AdapterError::Io("讀取執行緒異常結束".to_string()))?
        .map_err(|e| AdapterError::Io(e.to_string()))
}

fn tail(text: &str) -> String {
    let n = text.chars().count();
    if n <= STDERR_TAIL_CHARS {
        text.to_string()
    } else {
        let s: String = text.chars().skip(n - STDERR_TAIL_CHARS).collect();
        format!("…{s}")
    }
}

/// 等子程序結束;超過期限即終止並回 `None`。
fn wait_with_deadline(
    child: &mut Child,
    timeout: Duration,
) -> Result<Option<ExitStatus>, AdapterError> {
    let deadline = Instant::now() + timeout;
    loop {
        if let Some(status) = child
            .try_wait()
            .map_err(|e| AdapterError::Io(e.to_string()))?
        {
            return Ok(Some(status));
        }
        if Instant::now() >= deadline {
            // 子程序可能恰好在此時結束,kill 失敗不影響結果。
            let _ = child.kill();
            let _ = child.wait();
            return Ok(None);
        }
        thread::sleep(Duration::from_millis(10));
    }
}

/// 以轉接器執行單元 `unit` 的全部案例,回傳與 `cases` 同序的結果。
pub fn run(adapter: &Adapter, unit: &str, cases: &[Case]) -> Result<Vec<Outcome>, AdapterError> {
    let mut child = Command::new(&adapter.python)
        .arg(&adapter.script)
        .arg("--unit")
        .arg(unit)
        .env("PYTHONUTF8", "1")
        .env("PYTHONDONTWRITEBYTECODE", "1")
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .map_err(|e| AdapterError::Spawn {
            python: adapter.python.clone(),
            reason: e.to_string(),
        })?;

    let mut payload = String::new();
    for case in cases {
        payload.push_str(&encode_request(case));
        payload.push('\n');
    }
    let mut stdin = child.stdin.take().expect("stdin 已設為 piped");
    // 寫入失敗(例:轉接器提早結束造成管線中斷)以退出碼判斷,這裡不另報。
    let writer = thread::spawn(move || {
        let _ = stdin.write_all(payload.as_bytes());
    });
    let stdout = read_all(child.stdout.take().expect("stdout 已設為 piped"));
    let stderr = read_all(child.stderr.take().expect("stderr 已設為 piped"));

    let status = wait_with_deadline(&mut child, adapter.timeout)?;
    let _ = writer.join();
    let stdout = join_bytes(stdout)?;
    let stderr = join_bytes(stderr)?;

    let Some(status) = status else {
        return Err(AdapterError::Timeout(adapter.timeout));
    };
    if !status.success() {
        return Err(AdapterError::Exit {
            status,
            stderr: tail(&String::from_utf8_lossy(&stderr)),
        });
    }
    let text = String::from_utf8(stdout).map_err(|_| AdapterError::Encoding)?;
    let responses = text
        .lines()
        .map(parse_response)
        .collect::<Result<Vec<_>, _>>()
        .map_err(AdapterError::Protocol)?;
    match_responses(cases, responses).map_err(AdapterError::Protocol)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn python_resolution_order() {
        let cli = Some(OsString::from("a"));
        let env = Some(OsString::from("b"));
        assert_eq!(resolve_python(cli.clone(), env.clone()), "a");
        assert_eq!(resolve_python(None, env.clone()), "b");
        assert_eq!(resolve_python(Some(OsString::new()), env), "b");
        let default = resolve_python(None, Some(OsString::new()));
        if cfg!(windows) {
            assert_eq!(default, "python");
        } else {
            assert_eq!(default, "python3");
        }
    }

    #[test]
    fn adapter_script_exists() {
        assert!(adapter_path().is_file(), "{}", adapter_path().display());
    }

    #[test]
    fn stderr_tail_is_bounded() {
        let long = "字".repeat(STDERR_TAIL_CHARS + 10);
        assert_eq!(tail(&long).chars().count(), STDERR_TAIL_CHARS + 1);
        assert_eq!(tail("短"), "短");
    }
}
