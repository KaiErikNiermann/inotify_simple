import errno
import os
import struct
import subprocess
import tomllib
from pathlib import Path

import pytest

import inotify_simple
from inotify_simple import Event, INotify, flags, masks, parse_events


def _raw_event(wd: int, mask: int, cookie: int, name: bytes) -> bytes:
    padded = name.ljust(len(name) // 16 * 16 + 16, b"\x00")
    return struct.pack("iIII", wd, mask, cookie, len(padded)) + padded


class TestParseEvents:
    def test_empty(self) -> None:
        assert parse_events(b"") == []

    def test_single_named_event(self) -> None:
        data = _raw_event(3, flags.CREATE, 0, b"file.txt")
        assert parse_events(data) == [Event(3, flags.CREATE, 0, "file.txt")]

    def test_nameless_event(self) -> None:
        data = struct.pack("iIII", 1, flags.DELETE_SELF, 0, 0)
        assert parse_events(data) == [Event(1, flags.DELETE_SELF, 0, "")]

    def test_multiple_events_and_padding_stripped(self) -> None:
        data = _raw_event(1, flags.MOVED_FROM, 7, b"a") + _raw_event(
            1, flags.MOVED_TO, 7, b"b"
        )
        assert parse_events(data) == [
            Event(1, flags.MOVED_FROM, 7, "a"),
            Event(1, flags.MOVED_TO, 7, "b"),
        ]

    def test_non_ascii_name_uses_fsdecode(self) -> None:
        name = "grüße".encode()
        assert parse_events(_raw_event(1, flags.CREATE, 0, name))[0].name == "grüße"


class TestFlags:
    def test_from_mask(self) -> None:
        mask = flags.CREATE | flags.ISDIR
        assert flags.from_mask(mask) == [flags.CREATE, flags.ISDIR]

    def test_from_mask_zero(self) -> None:
        assert flags.from_mask(0) == []

    def test_composite_masks(self) -> None:
        assert masks.CLOSE == flags.CLOSE_WRITE | flags.CLOSE_NOWRITE
        assert masks.MOVE == flags.MOVED_FROM | flags.MOVED_TO

    def test_all_events_excludes_option_flags(self) -> None:
        for option in (flags.ONLYDIR, flags.DONT_FOLLOW, flags.ONESHOT, flags.ISDIR):
            assert not masks.ALL_EVENTS & option


class TestINotify:
    def test_create_and_delete_events(self, inotify: INotify, tmp_path: Path) -> None:
        inotify.add_watch(tmp_path, flags.CREATE | flags.DELETE)
        target = tmp_path / "new.txt"
        target.write_text("x")
        target.unlink()
        events = inotify.read(timeout=1000)
        assert [(e.mask, e.name) for e in events] == [
            (flags.CREATE, "new.txt"),
            (flags.DELETE, "new.txt"),
        ]

    def test_watch_single_file(self, inotify: INotify, tmp_path: Path) -> None:
        target = tmp_path / "config.yaml"
        target.write_text("a")
        wd = inotify.add_watch(target, flags.MODIFY)
        target.write_text("b")
        (event, *_) = inotify.read(timeout=1000)
        assert (event.wd, event.mask, event.name) == (wd, flags.MODIFY, "")

    @pytest.mark.parametrize("kind", [str, bytes, Path])
    def test_add_watch_path_types(
        self, inotify: INotify, tmp_path: Path, kind: type
    ) -> None:
        path = kind(os.fsencode(tmp_path) if kind is bytes else tmp_path)
        assert inotify.add_watch(path, flags.CREATE) >= 1

    def test_add_watch_missing_path_raises(self, inotify: INotify) -> None:
        with pytest.raises(FileNotFoundError):
            inotify.add_watch("/nonexistent/for/inotify", flags.CREATE)

    def test_rm_watch_generates_ignored(self, inotify: INotify, tmp_path: Path) -> None:
        wd = inotify.add_watch(tmp_path, flags.CREATE)
        inotify.rm_watch(wd)
        assert [e.mask for e in inotify.read(timeout=1000)] == [flags.IGNORED]

    def test_rm_watch_invalid_wd_raises(self, inotify: INotify) -> None:
        with pytest.raises(OSError, match="Invalid argument") as info:
            inotify.rm_watch(12345)
        assert info.value.errno == errno.EINVAL

    def test_read_timeout_zero_returns_immediately(self, inotify: INotify) -> None:
        assert inotify.read(timeout=0) == []

    def test_read_timeout_expires(self, inotify: INotify, tmp_path: Path) -> None:
        inotify.add_watch(tmp_path, flags.CREATE)
        assert inotify.read(timeout=20) == []

    def test_read_delay_still_returns_events(
        self, inotify: INotify, tmp_path: Path
    ) -> None:
        inotify.add_watch(tmp_path, flags.MODIFY)
        target = tmp_path / "f"
        target.write_text("")
        with target.open("a") as handle:
            handle.write("1")
            handle.flush()
            first = inotify.read(timeout=1000, read_delay=50)
            handle.write("2")
            handle.flush()
            second = inotify.read(timeout=1000, read_delay=50)
        assert first
        assert second

    def test_isdir_flag(self, inotify: INotify, tmp_path: Path) -> None:
        inotify.add_watch(tmp_path, flags.CREATE)
        (tmp_path / "sub").mkdir()
        (event,) = inotify.read(timeout=1000)
        assert flags.from_mask(event.mask) == [flags.CREATE, flags.ISDIR]

    def test_nonblocking_raw_read_raises(self, tmp_path: Path) -> None:
        with INotify(nonblocking=True) as instance, pytest.raises(BlockingIOError):
            os.read(instance.fileno(), 1024)

    def test_inheritable(self) -> None:
        with INotify() as default, INotify(inheritable=True) as inherited:
            assert not os.get_inheritable(default.fileno())
            assert os.get_inheritable(inherited.fileno())

    def test_fd_property_matches_fileno(self, inotify: INotify) -> None:
        assert inotify.fd == inotify.fileno()

    def test_context_manager_closes(self) -> None:
        with INotify() as instance:
            fd = instance.fileno()
        assert instance.closed
        with pytest.raises(OSError, match="Bad file descriptor"):
            os.fstat(fd)

    def test_closefd_false_leaves_descriptor_open(self) -> None:
        instance = INotify(closefd=False)
        fd = instance.fileno()
        try:
            instance.close()
            os.fstat(fd)
        finally:
            os.close(fd)


def test_version_matches_pyproject() -> None:
    pyproject = Path(__file__).parents[1] / "pyproject.toml"
    declared = tomllib.loads(pyproject.read_text())["project"]["version"]
    assert inotify_simple.__version__ == declared


def test_first_instance_spawns_no_subprocess(monkeypatch: pytest.MonkeyPatch) -> None:
    """Loading libc must not shell out to ``ldconfig`` (ctypes.util.find_library)."""

    def forbidden(*args: object, **kwargs: object) -> None:
        msg = "a subprocess was started while creating INotify"
        raise AssertionError(msg)

    monkeypatch.setattr(inotify_simple, "_libc", None)
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    with INotify() as instance:
        assert instance.fileno() >= 0
