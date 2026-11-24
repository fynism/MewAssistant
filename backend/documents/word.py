"""Convert Word originals once so indexing and preview share the same pages."""

import os
import shutil
import signal
import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Lock

from backend.documents.cleaning import ProcessingError
from backend.documents.pdf import pdf_page_count

WORD_SUFFIXES = {".doc", ".docx"}
_conversion_lock = Lock()
_cache_write_lock = Lock()
_profile_config = """<?xml version="1.0" encoding="UTF-8"?>
<oor:items xmlns:oor="http://openoffice.org/2001/registry">
 <item oor:path="/org.openoffice.Office.Common/Security/Scripting">
  <prop oor:name="DisableMacrosExecution" oor:op="fuse"><value>true</value></prop>
  <prop oor:name="BlockUntrustedRefererLinks" oor:op="fuse"><value>true</value></prop>
 </item>
 <item oor:path="/org.openoffice.Office.Writer/Content/Update">
  <prop oor:name="Link" oor:op="fuse"><value>0</value></prop>
 </item>
</oor:items>
"""


class WordConversionBusy(ProcessingError):
    """The preview can be retried after the active conversion finishes."""


def _cached_pdf(source: Path) -> Path:
    return source.parent / ".previews" / f"{source.name}.pdf"


def _run_conversion(source: Path, work_dir: Path) -> None:
    if os.name == "nt":
        executable = shutil.which("soffice.com")
        if executable is None:
            installed = Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "LibreOffice/program/soffice.com"
            if installed.is_file():
                executable = str(installed)
    else:
        executable = shutil.which("soffice")
    if executable is None:
        raise ProcessingError("服务端未安装 LibreOffice，暂时无法处理 Word 文件")

    profile = work_dir / "profile"
    (profile / "user").mkdir(parents=True)
    (profile / "user" / "registrymodifications.xcu").write_text(_profile_config, encoding="utf-8")
    # The app environment contains credentials that the Office parser does not need.
    env = {key: os.environ[key] for key in ("PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "LANG", "LC_ALL")
           if key in os.environ}
    command = [executable, f"-env:UserInstallation={profile.as_uri()}", "--headless", "--norestore",
               "--convert-to", "pdf:writer_pdf_Export", "--outdir", str(work_dir), str(source)]
    with subprocess.Popen(command, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                          stderr=subprocess.DEVNULL, start_new_session=os.name != "nt",
                          creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0) as process:
        try:
            return_code = process.wait(timeout=60)
        except subprocess.TimeoutExpired as exc:
            # The launcher can spawn soffice.bin, which must stop before deleting its profile.
            if os.name == "nt":
                subprocess.run(["taskkill.exe", "/F", "/T", "/PID", str(process.pid)], check=False,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                               creationflags=subprocess.CREATE_NO_WINDOW)
                process.kill()
            else:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            raise ProcessingError("Word 转换超时，请拆分文件后重试") from exc
    if return_code != 0:
        raise ProcessingError("无法转换 Word 文件，请确认文件完整且已解除密码保护")


def word_pdf(source: Path, *, wait: bool = True) -> Path:
    source = source.resolve()
    cached = _cached_pdf(source)
    if cached.is_file():
        return cached
    # Indexing can wait, but a burst of previews must leave web threads free.
    if not _conversion_lock.acquire(blocking=wait):
        raise WordConversionBusy("Word 转换繁忙，请稍后重试")
    try:
        if cached.is_file():
            return cached
        if not source.is_file():
            raise FileNotFoundError(source)
        cached.parent.mkdir(exist_ok=True)
        with TemporaryDirectory(prefix="word-", dir=cached.parent) as tmp:
            work_dir = Path(tmp)
            _run_conversion(source, work_dir)
            rendered = work_dir / f"{source.stem}.pdf"
            if not rendered.is_file():
                raise ProcessingError("无法转换 Word 文件，请确认文件完整且已解除密码保护")
            if not pdf_page_count(str(rendered)):
                raise ProcessingError("Word 文件没有可预览页面")
            with _cache_write_lock:
                if not source.is_file():
                    raise FileNotFoundError(source)
                rendered.replace(cached)
        return cached
    finally:
        _conversion_lock.release()


def remove_word_pdf(source: Path) -> None:
    if source.suffix.lower() in WORD_SUFFIXES:
        with _cache_write_lock:
            _cached_pdf(source.resolve()).unlink(missing_ok=True)
