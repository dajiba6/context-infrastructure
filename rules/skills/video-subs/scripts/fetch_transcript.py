#!/usr/bin/env python3
"""Download one YouTube or Bilibili subtitle track.

Chrome must be the logged-in browser. The process inherits the current proxy
environment. Cookie values are never printed.
"""

from __future__ import annotations

import argparse
import html
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from http.cookiejar import MozillaCookieJar
from pathlib import Path


class QuietLogger:
    def debug(self, message):
        return None

    def info(self, message):
        return None

    def warning(self, message, only_once=False):
        print(message, file=sys.stderr)

    def error(self, message):
        print(message, file=sys.stderr)


def chrome_cookie_dbs() -> list[Path]:
    root = Path.home() / "Library/Application Support/Google/Chrome"
    if not root.is_dir():
        return []
    found = []
    for pattern in ("*/Cookies", "*/Network/Cookies"):
        found.extend(root.glob(pattern))
    return sorted({path.resolve() for path in found if path.is_file()})


def snapshot_cookie_db(src: Path, dest_root: Path) -> Path:
    profile = dest_root / "Default"
    profile.mkdir(parents=True, exist_ok=True)
    for suffix in ("", "-wal", "-shm"):
        extra = Path(str(src) + suffix)
        if extra.is_file():
            shutil.copy2(extra, profile / f"Cookies{suffix}")
    db = profile / "Cookies"
    connection = sqlite3.connect(db)
    try:
        connection.execute("PRAGMA wal_checkpoint(FULL)")
        connection.commit()
    finally:
        connection.close()
    for suffix in ("-wal", "-shm"):
        leftover = profile / f"Cookies{suffix}"
        if leftover.exists():
            leftover.unlink()
    return dest_root


def platform_of(url: str) -> str:
    lowered = url.strip().lower()
    if "bilibili.com" in lowered or "b23.tv" in lowered or re.search(r"(^|/)bv[0-9a-z]+", lowered) or re.search(r"(^|/)av\d+", lowered):
        return "bilibili"
    if "youtube.com" in lowered or "youtu.be" in lowered:
        return "youtube"
    raise SystemExit("只支持 YouTube 和 Bilibili 链接，或裸的 BV / av 号。")


def watch_url(url: str, site: str) -> str:
    text = url.strip()
    if site == "bilibili" and re.fullmatch(r"(BV[0-9A-Za-z]+|av\d+|AV\d+)", text):
        return f"https://www.bilibili.com/video/{text}"
    return text


def export_chrome_cookies(cookie_path: Path, site: str) -> None:
    from yt_dlp.cookies import extract_cookies_from_browser

    databases = chrome_cookie_dbs()
    if not databases:
        raise SystemExit("找不到 Chrome cookie 数据库。先在 Chrome 里登录对应网站。")

    if site == "youtube":
        def score(cookie) -> int:
            domain = cookie.domain or ""
            if "youtube.com" not in domain:
                return 0
            return 1000 if cookie.name == "LOGIN_INFO" else 1

        def keep(cookie) -> bool:
            domain = cookie.domain or ""
            return domain.endswith("youtube.com") or domain.endswith("google.com")

        missing = (
            "Chrome cookie 已读取，但没有 youtube.com 的 LOGIN_INFO。"
            "若弹出钥匙串授权，允许读取 Chrome Safe Storage 后重试。"
            "不要改用 --cookies-from-browser：Chrome 开着时主库不含 WAL，登录态会不完整。"
        )
    else:
        def score(cookie) -> int:
            domain = cookie.domain or ""
            if "bilibili.com" not in domain:
                return 0
            return 1000 if cookie.name in {"SESSDATA", "DedeUserID"} else 1

        def keep(cookie) -> bool:
            domain = cookie.domain or ""
            return domain.endswith("bilibili.com")

        missing = (
            "Chrome cookie 已读取，但没有 bilibili.com 的 SESSDATA 或 DedeUserID。"
            "若弹出钥匙串授权，允许读取 Chrome Safe Storage 后重试。"
            "不要改用 --cookies-from-browser：Chrome 开着时主库不含 WAL，登录态会不完整。"
        )

    best_jar = None
    best_score = -1
    with tempfile.TemporaryDirectory(prefix="video-subs-chrome-") as tmp:
        for index, src in enumerate(databases):
            snapshot = snapshot_cookie_db(src, Path(tmp) / f"profile-{index}")
            jar = extract_cookies_from_browser("chrome", profile=str(snapshot), logger=QuietLogger())
            total = sum(score(cookie) for cookie in jar)
            if total > best_score:
                best_score = total
                best_jar = jar

    if best_jar is None or best_score < 1000:
        raise SystemExit(missing)

    jar = MozillaCookieJar(str(cookie_path))
    saved = 0
    for cookie in best_jar:
        if keep(cookie):
            jar.set_cookie(cookie)
            saved += 1
    cookie_path.parent.mkdir(parents=True, exist_ok=True)
    jar.save(ignore_discard=True, ignore_expires=True)
    os.chmod(cookie_path, 0o600)
    print(f"exported {saved} {site} cookies", file=sys.stderr)


def run_yt_dlp(url: str, work: Path, cookie_path: Path, site: str, extra: list[str]) -> subprocess.CompletedProcess[str]:
    yt_dlp = shutil.which("yt-dlp")
    if not yt_dlp:
        raise SystemExit("PATH 里没有 yt-dlp。")
    if site == "youtube":
        sub_langs = "en.*"
        sub_format = "vtt/json3"
    else:
        sub_langs = "ai-zh,zh-Hans,zh-CN,zh,zh-Hant,en"
        sub_format = "srt/vtt"
    command = [
        yt_dlp,
        "--no-update",
        "--no-playlist",
        "--cookies",
        str(cookie_path),
        "--skip-download",
        "--write-auto-sub",
        "--write-sub",
        "--sub-langs",
        sub_langs,
        "--sub-format",
        sub_format,
        "--write-info-json",
        "-o",
        str(work / "%(id)s"),
        *extra,
        url,
    ]
    return subprocess.run(
        command,
        cwd=work,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=120,
        check=False,
    )


def english_caption(work: Path) -> Path | None:
    candidates = [path for path in work.glob("*.vtt") if ".en" in path.name]
    if not candidates:
        json3 = [path for path in work.glob("*.json3") if ".en" in path.name]
        if not json3:
            return None
        source = max(json3, key=lambda path: path.stat().st_size)
        target = source.with_suffix(".vtt")
        target.write_text(json3_to_vtt(source.read_text(encoding="utf-8")))
        return target

    def rank(path: Path) -> tuple[int, int]:
        exact = 0 if path.name.endswith(".en.vtt") else 1
        return (exact, -path.stat().st_size)

    return sorted(candidates, key=rank)[0]


BILI_LANGS = ("ai-zh", "zh-hans", "zh-cn", "zh", "zh-hant", "en")


def bilibili_caption(work: Path) -> Path | None:
    files = [
        path
        for path in work.iterdir()
        if path.suffix.lower() in {".srt", ".vtt"} and "danmaku" not in path.name.lower()
    ]
    if not files:
        return None

    def rank(path: Path) -> tuple[int, int]:
        name = path.name.lower()
        for index, lang in enumerate(BILI_LANGS):
            if f".{lang}." in name:
                return (index, -path.stat().st_size)
        return (len(BILI_LANGS), -path.stat().st_size)

    return sorted(files, key=rank)[0]


def json3_to_vtt(raw: str) -> str:
    payload = json.loads(raw)
    lines = ["WEBVTT", ""]
    for event in payload.get("events") or []:
        text = "".join(seg.get("utf8", "") for seg in event.get("segs") or [])
        text = re.sub(r"\s+", " ", text).strip()
        if not text or text == "\n":
            continue
        start = int(event.get("tStartMs") or 0)
        end = start + int(event.get("dDurationMs") or 0)
        lines.append(f"{ms_timestamp(start)} --> {ms_timestamp(end)}")
        lines.append(text)
        lines.append("")
    return "\n".join(lines) + "\n"


def ms_timestamp(value: int) -> str:
    millis = max(0, value)
    hours, rem = divmod(millis, 3_600_000)
    minutes, rem = divmod(rem, 60_000)
    seconds, millis = divmod(rem, 1000)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}.{millis:03d}"


def clean_cue(body: str) -> str:
    body = re.sub(r"<[^>]+>", "", body)
    body = html.unescape(body)
    return re.sub(r"\s+", " ", body).strip()


def parse_vtt(raw: str) -> list[tuple[str, str, str]]:
    cues = []
    pattern = re.compile(
        r"(\d{2}:\d{2}:\d{2}\.\d{3}) --> (\d{2}:\d{2}:\d{2}\.\d{3})[^\n]*\n(.*?)(?=\n\n|\Z)",
        re.S,
    )
    for match in pattern.finditer(raw):
        body = clean_cue(match.group(3))
        if body:
            cues.append((match.group(1), match.group(2), body))
    return cues


def parse_srt(raw: str) -> list[tuple[str, str, str]]:
    cues = []
    pattern = re.compile(
        r"(\d{2}:\d{2}:\d{2},\d{3}) --> (\d{2}:\d{2}:\d{2},\d{3})[^\n]*\n(.*?)(?=\n\n|\Z)",
        re.S,
    )
    for match in pattern.finditer(raw):
        body = clean_cue(match.group(3))
        if body:
            cues.append((match.group(1), match.group(2), body))
    return cues


def output_lang(path: Path, site: str) -> str:
    if site == "youtube":
        return "en"
    stem = path.name[: -len(path.suffix)]
    part = stem.rsplit(".", 1)[-1]
    if part.lower() == "ai-zh":
        return "zh"
    return part


def write_transcripts(caption: Path, output_dir: Path, lang: str) -> dict:
    raw = caption.read_text(encoding="utf-8")
    cues = parse_srt(raw) if caption.suffix.lower() == ".srt" else parse_vtt(raw)
    if not cues:
        raise SystemExit(f"字幕文件没有可用文本：{caption}")
    output_dir.mkdir(parents=True, exist_ok=True)
    original = output_dir / f"original.{lang}{caption.suffix.lower()}"
    plain = output_dir / f"transcript.{lang}.txt"
    stamped = output_dir / f"transcript.timestamped.{lang}.txt"
    shutil.copyfile(caption, original)
    plain.write_text("\n".join(cue[2] for cue in cues) + "\n", encoding="utf-8")
    stamped.write_text(
        "\n".join(f"[{start} --> {end}] {text}" for start, end, text in cues) + "\n",
        encoding="utf-8",
    )
    return {
        "language": lang,
        "cues": len(cues),
        "start": cues[0][0],
        "end": cues[-1][1],
        "words": sum(len(cue[2].split()) for cue in cues),
        "files": {
            "original": str(original),
            "text": str(plain),
            "timestamped": str(stamped),
        },
    }


def load_info(work: Path) -> dict:
    files = list(work.glob("*.info.json"))
    if not files:
        return {}
    info = json.loads(files[0].read_text(encoding="utf-8"))
    chapters = []
    for chapter in info.get("chapters") or []:
        chapters.append({
            "start_time": chapter.get("start_time"),
            "end_time": chapter.get("end_time"),
            "title": chapter.get("title"),
        })
    return {
        "id": info.get("id"),
        "title": info.get("title"),
        "duration": info.get("duration"),
        "upload_date": info.get("upload_date"),
        "channel": info.get("channel") or info.get("uploader"),
        "chapters": chapters,
    }


def fetch(url: str, output_dir: Path) -> dict:
    site = platform_of(url)
    url = watch_url(url, site)
    work = Path(tempfile.mkdtemp(prefix="video-subs-"))
    cookie_path = work / "cookies.txt"
    try:
        export_chrome_cookies(cookie_path, site)
        attempts = [[]]
        if site == "youtube":
            attempts.append(["--extractor-args", "youtube:player_client=tv"])
        captured = ""
        for extra in attempts:
            try:
                result = run_yt_dlp(url, work, cookie_path, site, extra)
            except subprocess.TimeoutExpired as error:
                captured = ((error.stderr or "") if isinstance(error.stderr, str) else "") + (
                    (error.stdout or "") if isinstance(error.stdout, str) else ""
                )
                continue
            captured = (result.stderr or "") + (result.stdout or "")
            found = english_caption(work) if site == "youtube" else bilibili_caption(work)
            if found:
                break
        caption = english_caption(work) if site == "youtube" else bilibili_caption(work)
        if caption is None:
            tail = "\n".join(captured.strip().splitlines()[-30:])
            if site == "youtube":
                reason = "没有下载到英文字幕。PO token 警告可以忽略，但必须已经写出 vtt。不要安装 bgutil，也不要清掉当前代理。"
            else:
                reason = "没有下载到 B 站字幕。弹幕 danmaku 不算字幕。不要清掉当前代理，也不要改去下载视频。"
            raise SystemExit(reason + "\n" + tail)
        summary = load_info(work)
        summary["platform"] = site
        summary.update(write_transcripts(caption, output_dir, output_lang(caption, site)))
        return summary
    finally:
        shutil.rmtree(work, ignore_errors=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="Download a YouTube or Bilibili subtitle track")
    parser.add_argument("url")
    parser.add_argument("output_dir", type=Path)
    args = parser.parse_args()
    if sys.platform != "darwin":
        raise SystemExit("video-subs 目前只覆盖 macOS 上的 Chrome。")
    summary = fetch(args.url, args.output_dir)
    json.dump(summary, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
