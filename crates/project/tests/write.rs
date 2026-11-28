//! L1:寫入 API(CHG-20261128-01 T6;`project::write`)。
//!
//! 原子寫入(暫存檔命名、`sync_all` 後 `replace`、失敗時暫存檔被刪、暫存檔撞名重試與用盡)、`open_project` /
//! `open_subdir` 的建立分支、`save` 寫出的位元組等於 `dump_bytes`、`project.json` 不是一般檔時拒絕、`update`(change
//! 回錯誤、驗證不過、改名、專案不存在時都不存檔;兩執行緒同時 `update` 不互相覆蓋)、兩執行緒同時 `save`、
//! `create`(片段 id 取自時鐘、已有同名或只差大小寫、名稱與文稿不合格時不建立任何東西),以及 CHG-20261125-01 D-9 交辦:
//! 其他 handle 唯讀開著 `project.json` 時存檔成功(Windows 上 `replace` 不因此共用違規)。
//! 夾具只建在測試自己排他建立的暫存目錄;開頭與結尾確認 repo 工作副本的 `projects/` 不變。

mod common;

use std::io::Read;
use std::sync::{Arc, Barrier};

use common::{BASE_MS, ProjectsGuard, TempTree, TestClock, in_ci};
use project::constants::{HISTORY_KEEP, NAME_HINT};
use project::ids::SystemClock;
use project::model::{Asset, Defaults, Segment};
use project::read::{self, NOT_FOUND, Subdir};
use project::write::{
    self, CASE_EXISTS, EXISTS, NO_TEMP, NOT_REGULAR, RENAME_IN_UPDATE, TEMP_PREFIX, TEMP_SUFFIX,
};
use project::{Error, ErrorKind, Project};

const P: &str = "夜市的味道";

fn setup() -> (ProjectsGuard, TempTree) {
    let guard = ProjectsGuard::new();
    let t = TempTree::new("project-t6-write-");
    (guard, t)
}

fn proj(script: &str) -> Project {
    let mut p = Project::new(P);
    p.script = script.to_string();
    p
}

fn pdir(t: &TempTree) -> std::path::PathBuf {
    t.join(&format!("projects/{P}"))
}

fn temp_files(t: &TempTree) -> Vec<String> {
    std::fs::read_dir(pdir(t))
        .expect("讀取")
        .map(|e| e.expect("目錄項").file_name().to_string_lossy().to_string())
        .filter(|n| n.starts_with(TEMP_PREFIX))
        .collect()
}

#[track_caller]
fn err<T: std::fmt::Debug>(r: project::Result<T>, kind: ErrorKind, msgs: &[&str]) -> Error {
    let e = r.expect_err("預期失敗");
    assert_eq!(e.kind(), kind, "{e}");
    assert_eq!(e.errors(), msgs, "{e}");
    e
}

#[test]
fn save_writes_dump_bytes_and_creates_layout() {
    let (_g, t) = setup();
    let mut p = proj("今天好熱。");
    let mut s = Segment::new("0000000000000001", "今天好熱。");
    s.speed = Some(1.23);
    p.segments.push(s);
    let saved = write::save(&t.path, &p, &TestClock::at(BASE_MS)).expect("存檔");
    assert_eq!(saved.version, None);
    assert!(saved.warnings.is_empty());
    let bytes = std::fs::read(pdir(&t).join("project.json")).expect("讀取");
    assert_eq!(
        bytes,
        p.dump_bytes().expect("寫出"),
        "與 _dump(validate(to_dict())) 相同"
    );
    let got = read::load(&t.path, P).expect("載入");
    assert_eq!(got.segments[0].speed, Some(1.25));
    assert_eq!(got.dump_bytes().expect("寫出"), bytes);
    // 版面:projects/<名稱>/{.lock(長度 0), history/, project.json},沒有殘留的暫存檔
    let snap = t.snapshot();
    let keys: Vec<String> = snap.keys().cloned().collect();
    assert_eq!(
        keys,
        [
            "projects".to_string(),
            format!("projects/{P}"),
            format!("projects/{P}/.lock"),
            format!("projects/{P}/history"),
            format!("projects/{P}/project.json"),
        ]
    );
    assert_eq!(
        snap[&format!("projects/{P}/.lock")].2.as_deref(),
        Some(&[][..])
    );
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        let mode = std::fs::metadata(pdir(&t).join("project.json"))
            .expect("metadata")
            .permissions()
            .mode();
        assert_eq!(
            mode & 0o777 & !0o644,
            0,
            "暫存檔以 0o644 建立(受 umask 影響),不會多出其他權限位元:{mode:o}"
        );
    }
}

#[test]
fn second_save_archives_the_old_file_as_a_hard_link_copy() {
    let (_g, t) = setup();
    let clock = TestClock::at(BASE_MS);
    write::save(&t.path, &proj("舊"), &clock).expect("存檔");
    let old = std::fs::read(pdir(&t).join("project.json")).expect("讀取");
    let v = write::save(&t.path, &proj("新"), &clock)
        .expect("存檔")
        .version
        .expect("版本");
    assert_eq!(v, "20261027-120000-000-abcd");
    assert_eq!(
        std::fs::read(pdir(&t).join(format!("history/{v}.json"))).expect("讀取"),
        old
    );
    assert_eq!(read::load(&t.path, P).expect("載入").script, "新");
    assert_eq!(
        read::load_version(&t.path, P, &v).expect("載入版本").script,
        "舊"
    );
    assert!(temp_files(&t).is_empty());
}

/// 暫存檔建立後、`replace` 前讓 `project.json` 變成非空目錄(以時鐘的掛勾模擬外部改動)→ `replace` 失敗、
/// 暫存檔被刪、錯誤為 `Fs`;已封存的版本保留(與 Python 相同,不還原)。
#[test]
fn atomic_write_failure_removes_the_temp_file() {
    let (_g, t) = setup();
    let clock = TestClock::at(BASE_MS);
    write::save(&t.path, &proj("舊"), &clock).expect("存檔");
    let target = pdir(&t).join("project.json");
    clock.before_next_hex8(move || {
        std::fs::remove_file(&target).expect("刪除");
        std::fs::create_dir(&target).expect("建立目錄");
        std::fs::write(target.join("inner"), b"x").expect("寫入");
    });
    let e = write::save(&t.path, &proj("新"), &clock).expect_err("取代失敗");
    assert!(matches!(e.kind(), ErrorKind::Fs(_)), "{e:?}");
    assert_eq!(e.class(), "os_error");
    assert!(
        temp_files(&t).is_empty(),
        "暫存檔須被刪除:{:?}",
        temp_files(&t)
    );
    assert!(pdir(&t).join("project.json/inner").is_file());
    assert_eq!(
        read::list_history(&t.path, P).expect("列出"),
        ["20261027-120000-000-abcd"]
    );
}

/// 暫存檔名撞名時換下一個隨機碼;重試 16 次仍撞名 → `Invalid`「無法建立暫存檔」(既有的同名檔不變)。
#[test]
fn temp_name_collisions_retry_then_give_up() {
    let (_g, t) = setup();
    let clock = TestClock::at(BASE_MS);
    write::save(&t.path, &proj("舊"), &clock).expect("存檔"); // 用掉 0000000000000001
    let next = format!("{TEMP_PREFIX}{:016x}{TEMP_SUFFIX}", 2);
    t.file(&format!("projects/{P}/{next}"), "保留".as_bytes());
    write::save(&t.path, &proj("新"), &clock).expect("撞名重試後存檔");
    assert_eq!(read::load(&t.path, P).expect("載入").script, "新");
    assert_eq!(
        std::fs::read(pdir(&t).join(&next)).expect("讀取"),
        "保留".as_bytes()
    );
    assert_eq!(temp_files(&t), std::slice::from_ref(&next));

    clock.fix_hex8(&format!("{:016x}", 2));
    err(
        write::save(&t.path, &proj("更新"), &clock),
        ErrorKind::Invalid,
        &[NO_TEMP],
    );
    assert_eq!(
        read::load(&t.path, P).expect("載入").script,
        "新",
        "project.json 不變"
    );
    assert_eq!(temp_files(&t), [next]);
}

#[test]
fn project_file_that_is_not_regular_is_refused() {
    let (_g, t) = setup();
    t.dir(&format!("projects/{P}/project.json"));
    let before = t.snapshot();
    err(
        write::save(&t.path, &proj("新"), &TestClock::at(BASE_MS)),
        ErrorKind::UnsafePath,
        &[NOT_REGULAR],
    );
    let after = t.snapshot();
    assert!(after.contains_key(&format!("projects/{P}/.lock")));
    for (k, v) in &before {
        assert_eq!(after.get(k).map(|e| e.0), Some(v.0), "{k}");
    }
    assert!(temp_files(&t).is_empty());
}

#[test]
fn open_project_create_and_open_subdir_create() {
    let (_g, t) = setup();
    t.dir("root");
    let root = t.join("root");
    let dir = write::open_project_create(&root, P).expect("建立");
    drop(dir);
    assert!(root.join(format!("projects/{P}")).is_dir());
    for sub in [Subdir::Audio, Subdir::Assets, Subdir::Tmp] {
        write::open_subdir_create(&root, P, sub).expect("建立子目錄");
        assert!(root.join(format!("projects/{P}/{}", sub.as_str())).is_dir());
        read::open_subdir(&root, P, sub).expect("可讀取開啟");
    }
    // 已存在時照常開啟、不改內容
    std::fs::write(root.join(format!("projects/{P}/audio/a.wav")), b"1").expect("寫入");
    write::open_subdir_create(&root, P, Subdir::Audio).expect("開啟");
    assert!(root.join(format!("projects/{P}/audio/a.wav")).is_file());
    // 名稱、只差大小寫、不在預期位置
    err(
        write::open_project_create(&root, "a/b"),
        ErrorKind::Invalid,
        &[NAME_HINT],
    );
    write::open_project_create(&root, "Ep1").expect("建立");
    err(
        write::open_project_create(&root, "ep1"),
        ErrorKind::Exists,
        &[CASE_EXISTS],
    );
    std::fs::write(root.join("projects/檔案"), b"x").expect("寫入");
    err(
        write::open_project_create(&root, "檔案"),
        ErrorKind::UnsafePath,
        &["資料目錄「專案」不在預期位置"],
    );
    // 資料根目錄不存在 → NotFound(不建立根目錄)
    err(
        write::open_project_create(&t.join("missing"), P),
        ErrorKind::NotFound,
        &[NOT_FOUND],
    );
    assert!(!t.join("missing").exists());
    // 連結一律拒絕、目標不變
    t.file("outside/keep.txt", b"keep");
    std::fs::remove_dir_all(root.join(format!("projects/{P}/tmp"))).expect("刪除");
    if t.symlink(&format!("root/projects/{P}/tmp"), &t.join("outside"), true) {
        err(
            write::open_subdir_create(&root, P, Subdir::Tmp),
            ErrorKind::UnsafePath,
            &["資料目錄「tmp」是符號連結,不接受"],
        );
        assert_eq!(
            std::fs::read_dir(t.join("outside")).expect("讀取").count(),
            1
        );
    } else {
        assert!(!in_ci(), "CI 上無法建立符號連結,判失敗");
        println!("略過(Windows 無權限建立符號連結):子目錄是連結");
    }
}

#[test]
fn update_saves_and_returns_the_change_result() {
    let (_g, t) = setup();
    let clock = TestClock::at(BASE_MS);
    write::create(&t.path, P, "第一句。", None, &clock).expect("建立");
    let u = write::update(&t.path, P, &clock, |p| {
        p.defaults.speed = 1.3;
        Ok("改好了")
    })
    .expect("修改");
    assert_eq!(u.extra, "改好了");
    let vid = u.version.expect("版本");
    assert_eq!(u.project.defaults.speed, 1.3);
    assert_eq!(read::load(&t.path, P).expect("載入").defaults.speed, 1.3);
    assert!(read::list_history(&t.path, P).expect("列出").contains(&vid));
    assert_eq!(u.project, read::load(&t.path, P).expect("載入"));
}

#[test]
fn update_does_not_save_on_error_invalid_or_rename() {
    let (_g, t) = setup();
    let clock = TestClock::at(BASE_MS);
    write::create(&t.path, P, "第一句。", None, &clock).expect("建立");
    let before = t.snapshot();
    err(
        write::update(&t.path, P, &clock, |p| {
            p.defaults.speed = 1.9;
            Err::<(), _>(Error::invalid("中途失敗"))
        }),
        ErrorKind::Invalid,
        &["中途失敗"],
    );
    err(
        write::update(&t.path, P, &clock, |p| {
            p.defaults.speed = 5.0;
            Ok(())
        }),
        ErrorKind::Invalid,
        &["defaults.speed必須在 0.5~2.0 倍之間"],
    );
    err(
        write::update(&t.path, P, &clock, |p| {
            p.name = "別的名字".to_string();
            Ok(())
        }),
        ErrorKind::Invalid,
        &[RENAME_IN_UPDATE],
    );
    assert_eq!(t.snapshot(), before, "都不存檔");
    assert_eq!(
        read::load(&t.path, P).expect("載入").defaults.speed,
        Defaults::default().speed
    );
    err(
        write::update(&t.path, "沒有這個", &clock, |_| Ok(())),
        ErrorKind::NotFound,
        &[NOT_FOUND],
    );
    assert!(!t.join("projects/沒有這個").exists(), "update 不建立專案");
}

/// Python test_兩個執行緒同時update不會互相覆蓋:8 個執行緒各加一個素材,全部保留。
#[test]
fn concurrent_updates_do_not_overwrite_each_other() {
    let (_g, t) = setup();
    write::create(&t.path, P, "第一句。", None, &SystemClock).expect("建立");
    let n = 8u64;
    let barrier = Arc::new(Barrier::new(n as usize));
    let root = Arc::new(t.path.clone());
    let handles: Vec<_> = (0..n)
        .map(|i| {
            let (barrier, root) = (Arc::clone(&barrier), Arc::clone(&root));
            std::thread::spawn(move || {
                barrier.wait();
                write::update(&root, P, &SystemClock, |p| {
                    p.assets.push(Asset {
                        id: format!("{i:08x}{}", "0".repeat(8)),
                        kind: "text".to_string(),
                        ext: "txt".to_string(),
                        bytes: i,
                        label: None,
                    });
                    Ok(())
                })
                .map(|_| ())
            })
        })
        .collect();
    for h in handles {
        h.join().expect("執行緒").expect("修改");
    }
    let mut bytes: Vec<u64> = read::load(&t.path, P)
        .expect("載入")
        .assets
        .iter()
        .map(|a| a.bytes)
        .collect();
    bytes.sort_unstable();
    assert_eq!(bytes, (0..n).collect::<Vec<_>>());
}

/// Python test_concurrent_saves_from_two_threads:兩執行緒各存 15 次,版本 id 不重複、history 10 個、沒有殘留暫存檔。
#[test]
fn concurrent_saves_from_two_threads() {
    let (_g, t) = setup();
    write::save(&t.path, &proj(""), &SystemClock).expect("存檔");
    let rounds = 15;
    let barrier = Arc::new(Barrier::new(2));
    let root = Arc::new(t.path.clone());
    let handles: Vec<_> = ["甲", "乙"]
        .into_iter()
        .map(|tag| {
            let (barrier, root) = (Arc::clone(&barrier), Arc::clone(&root));
            std::thread::spawn(move || {
                barrier.wait();
                (0..rounds)
                    .map(|i| {
                        let mut p = proj(&format!("{tag}-{i}").repeat(200));
                        p.segments
                            .push(Segment::new("0000000000000001", format!("{tag}第{i}句。")));
                        write::save(&root, &p, &SystemClock)
                            .expect("存檔")
                            .version
                            .expect("版本")
                    })
                    .collect::<Vec<String>>()
            })
        })
        .collect();
    let mut all = Vec::new();
    for h in handles {
        all.extend(h.join().expect("執行緒"));
    }
    let unique: std::collections::BTreeSet<&String> = all.iter().collect();
    assert_eq!(all.len(), 2 * rounds);
    assert_eq!(unique.len(), 2 * rounds, "history 檔名不重複");
    let got = read::load(&t.path, P).expect("不是壞檔");
    assert!(["甲", "乙"].contains(&got.script.split('-').next().unwrap_or_default()));
    let hist: Vec<_> = std::fs::read_dir(pdir(&t).join("history"))
        .expect("讀取")
        .collect();
    assert_eq!(hist.len(), HISTORY_KEEP);
    for v in read::list_history(&t.path, P).expect("列出") {
        read::load_version(&t.path, P, &v).expect("每個版本都可載入");
    }
    assert!(temp_files(&t).is_empty());
}

/// CHG-20261125-01 D-9 交辦:其他 handle 唯讀開著 `project.json`(與封存的版本檔)時存檔成功;
/// 開著的 handle 讀到的仍是舊內容(取代的是目錄項,不改寫舊檔)。
#[test]
fn save_succeeds_while_another_handle_reads_project_json() {
    let (_g, t) = setup();
    let clock = TestClock::at(BASE_MS);
    write::save(&t.path, &proj("舊"), &clock).expect("存檔");
    let old = std::fs::read(pdir(&t).join("project.json")).expect("讀取");
    let mut reader = std::fs::File::open(pdir(&t).join("project.json")).expect("唯讀開啟");
    let v = write::save(&t.path, &proj("新"), &clock)
        .expect("開著時存檔")
        .version
        .expect("版本");
    let mut reader2 =
        std::fs::File::open(pdir(&t).join(format!("history/{v}.json"))).expect("開啟版本");
    write::save(&t.path, &proj("更新"), &clock).expect("再存檔");
    let mut buf = Vec::new();
    reader.read_to_end(&mut buf).expect("讀取");
    assert_eq!(buf, old, "開著的 handle 讀到舊內容");
    let mut buf2 = Vec::new();
    reader2.read_to_end(&mut buf2).expect("讀取");
    assert_eq!(buf2, old);
    assert_eq!(read::load(&t.path, P).expect("載入").script, "更新");
    println!(
        "其他 handle 唯讀開著 project.json 時存檔:成功({})",
        std::env::consts::OS
    );
}

#[test]
fn create_splits_script_with_ids_from_the_clock() {
    let (_g, t) = setup();
    let clock = TestClock::at(BASE_MS);
    let p = write::create(&t.path, P, "大家好。今天天氣很好!\n第二段", None, &clock).expect("建立");
    let ids: Vec<&str> = p.segments.iter().map(|s| s.id.as_str()).collect();
    assert_eq!(
        ids,
        ["0000000000000001", "0000000000000002", "0000000000000003"]
    );
    let brk: Vec<&str> = p.segments.iter().map(|s| s.brk.as_str()).collect();
    assert_eq!(brk, ["sentence", "paragraph", "end"]);
    assert_eq!(read::load(&t.path, P).expect("載入"), p);
    assert_eq!(
        read::list_history(&t.path, P).expect("列出"),
        Vec::<String>::new()
    );
    // 建立時的暫存檔用下一個隨機碼,沒有殘留
    assert!(temp_files(&t).is_empty());
    let d = Defaults {
        speed: 1.3,
        ..Defaults::default()
    };
    let q = write::create(&t.path, "其他", "", Some(d), &clock).expect("建立");
    assert_eq!(q.defaults.speed, 1.3);
    assert!(q.segments.is_empty());
}

#[test]
fn create_refuses_existing_and_creates_nothing_when_invalid() {
    let (_g, t) = setup();
    let clock = TestClock::at(BASE_MS);
    for bad in ["a/b", "", ".."] {
        err(
            write::create(&t.path, bad, "一。", None, &clock),
            ErrorKind::Invalid,
            &[NAME_HINT],
        );
    }
    err(
        write::create(&t.path, P, &"字".repeat(50001), None, &clock),
        ErrorKind::Invalid,
        &["文稿超過 50000 字"],
    );
    err(
        write::create(&t.path, P, &"字".repeat(501), None, &clock),
        ErrorKind::Invalid,
        &["第 1 句超過 500 字,請加句尾標點或強制停頓"],
    );
    let bad_defaults = Defaults {
        speed: 5.0,
        ..Defaults::default()
    };
    err(
        write::create(&t.path, P, "一。", Some(bad_defaults), &clock),
        ErrorKind::Invalid,
        &["defaults.speed必須在 0.5~2.0 倍之間"],
    );
    assert!(
        t.snapshot().is_empty(),
        "不合格時不建立任何東西(含 projects/)"
    );
    write::create(&t.path, P, "一。", None, &clock).expect("建立");
    err(
        write::create(&t.path, P, "", None, &clock),
        ErrorKind::Exists,
        &[EXISTS],
    );
    t.dir("projects/ep1");
    err(
        write::create(&t.path, "Ep1", "", None, &clock),
        ErrorKind::Exists,
        &[EXISTS],
    );
    t.file("projects/檔案", b"x");
    err(
        write::create(&t.path, "檔案", "", None, &clock),
        ErrorKind::Exists,
        &[EXISTS],
    );
}
