from __future__ import annotations

import argparse
import configparser
import shutil
import subprocess
import threading
from pathlib import Path
from typing import Callable, Iterable, List, Optional, Tuple


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Kopira sve pesme iz M3U ili PLS playliste u zadati folder."
    )
    parser.add_argument(
        "playlist",
        type=Path,
        help="Putanja do .m3u ili .pls fajla",
    )
    parser.add_argument(
        "destination",
        type=Path,
        help="Folder u koji će se kopirati fajlovi",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Prođi kroz listu bez kopiranja fajlova (prikazuje šta bi uradio)",
    )
    parser.add_argument(
        "--normalize-lufs",
        type=float,
        default=None,
        help="Normalizuj glasnoću na zadati LUFS (zahteva ffmpeg, npr. -14)",
    )
    parser.add_argument(
        "--codec-preset",
        choices=list(CODEC_PRESETS.keys()),
        default="auto",
        help="Codec/bitrate preset za izlaz (primenjuje se pri normalizaciji)",
    )
    return parser.parse_args()


def read_playlist(playlist_path: Path) -> List[Path]:
    if not playlist_path.exists():
        raise FileNotFoundError(f"Fajl {playlist_path} ne postoji")

    suffix = playlist_path.suffix.lower()
    if suffix == ".m3u" or suffix == ".m3u8":
        tracks = _parse_m3u(playlist_path)
    elif suffix == ".pls":
        tracks = _parse_pls(playlist_path)
    else:
        raise ValueError("Podržane su samo M3U/M3U8 i PLS liste")

    return tracks


def _parse_m3u(path: Path) -> List[Path]:
    base_dir = path.parent
    tracks: List[Path] = []
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue

        track_path = Path(line)
        if not track_path.is_absolute():
            track_path = base_dir / track_path

        tracks.append(track_path.resolve())

    return tracks


def _parse_pls(path: Path) -> List[Path]:
    parser = configparser.ConfigParser()
    parser.read(path, encoding="utf-8")

    if "playlist" not in parser:
        raise ValueError("Neispravan PLS format: nedostaje [playlist] sekcija")

    base_dir = path.parent
    tracks: List[Tuple[int, Path]] = []

    for key, value in parser["playlist"].items():
        if not key.lower().startswith("file"):
            continue

        try:
            index = int(key[4:])  # File1 -> 1
        except ValueError:
            index = 0

        track_path = Path(value)
        if not track_path.is_absolute():
            track_path = base_dir / track_path
        tracks.append((index, track_path.resolve()))

    # Sort by broj u nazivu (File1, File2...) da bi redosled bio stabilan
    tracks.sort(key=lambda item: item[0])

    return [track for _, track in tracks]


ProgressCallback = Callable[[int, int, Path, Optional[Path], str], None]

CODEC_PRESETS: dict[str, Optional[list[str]]] = {
    "auto": None,  # bira se prema ekstenziji fajla
    "mp3-192": ["-c:a", "libmp3lame", "-b:a", "192k"],
    "mp3-256": ["-c:a", "libmp3lame", "-b:a", "256k"],
    "aac-192": ["-c:a", "aac", "-b:a", "192k"],
    "aac-256": ["-c:a", "aac", "-b:a", "256k"],
    "flac": ["-c:a", "flac"],
    "wav": ["-c:a", "pcm_s16le"],
}


def copy_tracks(
    tracks: Iterable[Path],
    destination: Path,
    dry_run: bool = False,
    progress_callback: Optional[ProgressCallback] = None,
    cancel_event: Optional[threading.Event] = None,
    normalize_lufs: Optional[float] = None,
    codec_preset: str = "auto",
) -> Tuple[int, int]:
    destination.mkdir(parents=True, exist_ok=True)

    copied = 0
    missing = 0

    tracks_list = list(tracks)
    total = len(tracks_list)

    for index, track in enumerate(tracks_list, start=1):
        if cancel_event and cancel_event.is_set():
            if progress_callback:
                progress_callback(index, total, track, None, "cancelled")
            break

        if not track.exists():
            missing += 1
            print(f"[SKIPPED] Ne postoji: {track}")
            if progress_callback:
                progress_callback(index, total, track, None, "missing")
            continue

        target = destination / track.name
        target = _ensure_unique_name(target)

        if dry_run:
            print(f"[DRY RUN] Kopirao bih {track} -> {target}")
            copied += 1
            if progress_callback:
                progress_callback(index, total, track, target, "dry-run")
            continue

        if normalize_lufs is not None:
            codec_args = _codec_from_preset(codec_preset, target.suffix.lower())
            _normalize_track(track, target, normalize_lufs, codec_args)
            status = "normalized"
        else:
            shutil.copy2(track, target)
            status = "ok"

        copied += 1
        print(f"[{status.upper()}] {track} -> {target}")
        if progress_callback:
            progress_callback(index, total, track, target, status)

    return copied, missing


def _ensure_unique_name(target: Path) -> Path:
    # Ako fajl već postoji, dodaje suffix _1, _2...
    if not target.exists():
        return target

    stem = target.stem
    suffix = target.suffix
    parent = target.parent
    counter = 1

    while True:
        candidate = parent / f"{stem}_{counter}{suffix}"
        if not candidate.exists():
            return candidate
        counter += 1


def _normalize_track(src: Path, dst: Path, lufs: float, codec_args: Optional[list[str]]) -> None:
    """
    Normalizuje glasnoću koristeći ffmpeg loudnorm filter.
    Re-enkodira audio pa može potrajati i promeniti veličinu fajla.
    Zahteva instaliran ffmpeg u PATH.
    """
    codec_args = codec_args or _codec_for_ext(dst.suffix.lower())

    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        str(src),
        "-af",
        f"loudnorm=I={lufs}:TP=-1.5:LRA=11",
        *codec_args,
        str(dst),
    ]

    try:
        subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    except FileNotFoundError as exc:
        raise RuntimeError(
            "ffmpeg nije pronađen. Instaliraj ffmpeg da bi radio normalize."
        ) from exc
    except subprocess.CalledProcessError as exc:
        msg = exc.stderr.decode("utf-8", errors="ignore")
        raise RuntimeError(f"ffmpeg greška: {msg}") from exc


def _codec_for_ext(ext: str) -> list[str]:
    """
    Bira odgovarajući audio codec za izlazni format.
    """
    if ext == ".mp3":
        return ["-c:a", "libmp3lame", "-b:a", "192k"]
    if ext in {".m4a", ".aac"}:
        return ["-c:a", "aac", "-b:a", "192k"]
    if ext == ".flac":
        return ["-c:a", "flac"]
    if ext == ".wav":
        return ["-c:a", "pcm_s16le"]
    # Default na AAC ako ne prepoznamo
    return ["-c:a", "aac", "-b:a", "192k"]


def _codec_from_preset(preset: str, ext: str) -> list[str]:
    preset_lower = (preset or "auto").lower()
    if preset_lower in CODEC_PRESETS and CODEC_PRESETS[preset_lower] is not None:
        return CODEC_PRESETS[preset_lower] or _codec_for_ext(ext)
    # auto ili nepoznato -> prema ekstenziji
    return _codec_for_ext(ext)


def main() -> None:
    args = parse_args()
    tracks = read_playlist(args.playlist)

    print(f"Pronađeno fajlova: {len(tracks)}")
    copied, missing = copy_tracks(
        tracks,
        args.destination,
        dry_run=args.dry_run,
        normalize_lufs=args.normalize_lufs,
        codec_preset=args.codec_preset,
    )

    print(f"Uspešno: {copied}")
    print(f"Nedostaje: {missing}")


if __name__ == "__main__":
    main()
