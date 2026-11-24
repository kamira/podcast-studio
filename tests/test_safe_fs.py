"""safe_fs 單元測試(CHG-20261022-04 DIR-5 修正 4):dir_fd 逐層、O_NOFOLLOW、fd 遞迴刪除。"""
from __future__ import annotations

import os
import stat
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import safe_fs  # noqa: E402


@pytest.fixture
def top(tmp_path):
    t = (tmp_path / "top").resolve()
    (t / "a" / "b").mkdir(parents=True)
    (t / "a" / "b" / "f.txt").write_text("內容", encoding="utf-8")
    return t


@pytest.fixture
def outside(tmp_path):
    o = tmp_path / "outside"
    (o / "sub").mkdir(parents=True)
    (o / "keep.txt").write_text("不可動", encoding="utf-8")
    (o / "sub" / "deep.txt").write_text("深層", encoding="utf-8")
    (o / "f.txt").write_text("SECRET", encoding="utf-8")
    return o


def snapshot(d: Path) -> dict:
    return {str(p.relative_to(d)): (p.read_bytes() if p.is_file() else None) for p in sorted(d.rglob("*"))}


def read_fd(fd: int) -> bytes:
    with os.fdopen(fd, "rb") as f:
        return f.read()


def test_open_path_reads_regular_file(top):
    assert read_fd(safe_fs.open_path(top, ["a", "b", "f.txt"])) == "內容".encode("utf-8")


def test_open_dir_rejects_symlink_at_any_level(top, outside):
    (top / "link").symlink_to(outside, target_is_directory=True)
    (top / "a" / "mid").symlink_to(outside, target_is_directory=True)
    for parts in (["link"], ["a", "mid"], ["link", "sub"]):
        with pytest.raises(safe_fs.UnsafePathError):
            safe_fs.open_dir(top, parts)
    with pytest.raises(safe_fs.UnsafePathError):
        safe_fs.open_path(top, ["a", "mid", "f.txt"])
    with pytest.raises(safe_fs.UnsafePathError):   # 指向內部的連結也拒絕
        (top / "a" / "inner").symlink_to(top / "a" / "b", target_is_directory=True)
        safe_fs.open_path(top, ["a", "inner", "f.txt"])


def test_open_dir_missing_and_create(top):
    with pytest.raises(FileNotFoundError):
        safe_fs.open_dir(top, ["new", "deeper"])
    assert not (top / "new").exists()
    fd = safe_fs.open_dir(top, ["new", "deeper"], create=True)
    os.close(fd)
    assert (top / "new" / "deeper").is_dir()


def test_open_dir_create_does_not_follow_existing_symlink(top, outside):
    (top / "made").symlink_to(outside, target_is_directory=True)
    with pytest.raises(safe_fs.UnsafePathError):
        safe_fs.open_dir(top, ["made", "x"], create=True)
    assert not (outside / "x").exists()


def test_open_file_rejects_symlink_and_non_regular(top, outside):
    (top / "a" / "ln.txt").symlink_to(outside / "f.txt")
    with safe_fs.closing(safe_fs.open_dir(top, ["a"])) as d:
        with pytest.raises(safe_fs.UnsafePathError):
            safe_fs.open_file(d, "ln.txt")
        with pytest.raises(safe_fs.UnsafePathError):   # 目錄不是一般檔
            safe_fs.open_file(d, "b")
        with pytest.raises(FileNotFoundError):
            safe_fs.open_file(d, "沒有.txt")


@pytest.mark.parametrize("bad", ["", ".", "..", "a/b", "x\x00y"])
def test_names_must_be_single_component(top, bad):
    with pytest.raises(ValueError):
        safe_fs.open_dir(top, [bad])
    with safe_fs.closing(safe_fs.open_dir(top)) as d:
        with pytest.raises(ValueError):
            safe_fs.unlink(d, bad)


def test_create_excl_does_not_follow_or_overwrite(top, outside):
    with safe_fs.closing(safe_fs.open_dir(top, ["a"])) as d:
        (top / "a" / "dangling").symlink_to(outside / "new.txt")
        with pytest.raises(FileExistsError):
            safe_fs.create_excl(d, "dangling")
        with pytest.raises(FileExistsError):
            safe_fs.create_excl(d, "b")
        fd = safe_fs.create_excl(d, "fresh.txt", 0o600)
        os.write(fd, b"x")
        os.close(fd)
    assert not (outside / "new.txt").exists()
    assert stat.S_IMODE((top / "a" / "fresh.txt").stat().st_mode) == 0o600


def test_open_or_create(top, outside):
    with safe_fs.closing(safe_fs.open_dir(top, ["a"])) as d:
        os.close(safe_fs.open_or_create(d, "lock"))
        os.close(safe_fs.open_or_create(d, "lock"))   # 已存在:直接開
        (top / "a" / "ln").symlink_to(outside / "f.txt")
        with pytest.raises(safe_fs.UnsafePathError):
            safe_fs.open_or_create(d, "ln")
    assert (top / "a" / "lock").is_file()


def test_entries_lists_with_lstat(top, outside):
    (top / "a" / "ln").symlink_to(outside, target_is_directory=True)
    with safe_fs.closing(safe_fs.open_dir(top, ["a"])) as d:
        got = {n: st for n, st in safe_fs.entries(d)}
    assert sorted(got) == ["b", "ln"]
    assert stat.S_ISLNK(got["ln"].st_mode) and stat.S_ISDIR(got["b"].st_mode)


def test_rmtree_removes_tree_but_never_follows_links(top, outside):
    victim = top / "a" / "victim"
    (victim / "x" / "y").mkdir(parents=True)
    (victim / "x" / "y" / "z.txt").write_text("z", encoding="utf-8")
    (victim / "out-dir").symlink_to(outside, target_is_directory=True)
    (victim / "x" / "out-file").symlink_to(outside / "keep.txt")
    before = snapshot(outside)
    with safe_fs.closing(safe_fs.open_dir(top, ["a"])) as d:
        safe_fs.rmtree(d, "victim")
        safe_fs.rmtree(d, "victim", missing_ok=True)
        with pytest.raises(FileNotFoundError):
            safe_fs.rmtree(d, "victim")
    assert not victim.exists()
    assert snapshot(outside) == before


def test_rmtree_on_symlink_removes_only_the_link(top, outside):
    (top / "a" / "ln").symlink_to(outside, target_is_directory=True)
    before = snapshot(outside)
    with safe_fs.closing(safe_fs.open_dir(top, ["a"])) as d:
        safe_fs.rmtree(d, "ln")
    assert not os.path.lexists(top / "a" / "ln")
    assert snapshot(outside) == before


def test_rmtree_dir_swapped_to_symlink_after_lstat(top, outside, monkeypatch):
    """lstat 判定為目錄之後、開啟之前被換成符號連結:只刪連結本身,不刪連結目標內容。"""
    victim = top / "a" / "victim"
    victim.mkdir()
    before = snapshot(outside)
    real = safe_fs.lstat

    def swapping(dirfd, name):
        st = real(dirfd, name)
        if name == "victim" and victim.is_dir() and not victim.is_symlink():
            victim.rename(top / "a" / "moved")
            victim.symlink_to(outside, target_is_directory=True)
        return st
    monkeypatch.setattr(safe_fs, "lstat", swapping)
    with safe_fs.closing(safe_fs.open_dir(top, ["a"])) as d:
        safe_fs.rmtree(d, "victim")
    assert not os.path.lexists(victim)
    assert snapshot(outside) == before


def test_dir_fd_survives_swap_of_its_path(top, outside):
    """取得目錄 fd 之後把路徑換成符號連結:之後的寫入與刪除仍落在原目錄,不會寫到連結目標。"""
    before = snapshot(outside)
    with safe_fs.closing(safe_fs.open_dir(top, ["a", "b"])) as d:
        (top / "a" / "b").rename(top / "a" / "b-moved")
        (top / "a" / "b").symlink_to(outside, target_is_directory=True)
        os.close(safe_fs.create_excl(d, "new.txt"))
        safe_fs.unlink(d, "f.txt")
    assert (top / "a" / "b-moved" / "new.txt").is_file()
    assert not (top / "a" / "b-moved" / "f.txt").exists()
    assert snapshot(outside) == before


def test_rename_replace_link(top):
    with safe_fs.closing(safe_fs.open_dir(top, ["a"])) as a, safe_fs.closing(safe_fs.open_dir(top, ["a", "b"])) as b:
        safe_fs.link(b, "f.txt", a, "hard.txt")
        with pytest.raises(FileExistsError):
            safe_fs.link(b, "f.txt", a, "hard.txt")
        safe_fs.rename(a, "hard.txt", a, "renamed.txt")
        os.close(safe_fs.create_excl(a, "tmp"))
        safe_fs.replace(a, "tmp", a, "renamed.txt")
    assert (top / "a" / "renamed.txt").read_bytes() == b""
    assert (top / "a" / "b" / "f.txt").is_file()
