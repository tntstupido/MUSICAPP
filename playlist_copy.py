from __future__ import annotations

import argparse
import configparser
import shutil
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


def copy_tracks(
    tracks: Iterable[Path],
    destination: Path,
    dry_run: bool = False,
    progress_callback: Optional[ProgressCallback] = None,
    cancel_event: Optional[threading.Event] = None,
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

        shutil.copy2(track, target)
        copied += 1
        print(f"[OK] {track} -> {target}")
        if progress_callback:
            progress_callback(index, total, track, target, "ok")

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


def main() -> None:
    args = parse_args()
    tracks = read_playlist(args.playlist)

    print(f"Pronađeno fajlova: {len(tracks)}")
    copied, missing = copy_tracks(tracks, args.destination, dry_run=args.dry_run)

    print(f"Uspešno: {copied}")
    print(f"Nedostaje: {missing}")


if __name__ == "__main__":
    main()
