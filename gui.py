import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox
from tkinter import ttk
from tkinter.scrolledtext import ScrolledText

from playlist_copy import copy_tracks, read_playlist


class PlaylistCopierApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("Playlist Copier (M3U / PLS)")
        self.geometry("520x420")

        self.playlist_var = tk.StringVar()
        self.dest_var = tk.StringVar()
        self.dry_run_var = tk.BooleanVar(value=False)
        self.progress_var = tk.IntVar(value=0)
        self.normalize_var = tk.BooleanVar(value=False)
        self.lufs_var = tk.StringVar(value="-14")
        self.codec_var = tk.StringVar(value="auto")

        self._build_ui()
        self.worker_thread: threading.Thread | None = None
        self.cancel_event: threading.Event | None = None

    def _build_ui(self) -> None:
        padding = {"padx": 10, "pady": 8}

        ttk.Label(self, text="Playlist (M3U / PLS):").grid(
            row=0, column=0, sticky="w", **padding
        )
        entry_playlist = ttk.Entry(self, textvariable=self.playlist_var, width=45)
        entry_playlist.grid(row=0, column=1, sticky="we", **padding)
        ttk.Button(self, text="Izaberi...", command=self._choose_playlist).grid(
            row=0, column=2, sticky="e", **padding
        )

        ttk.Label(self, text="Odredište:").grid(row=1, column=0, sticky="w", **padding)
        entry_dest = ttk.Entry(self, textvariable=self.dest_var, width=45)
        entry_dest.grid(row=1, column=1, sticky="we", **padding)
        ttk.Button(self, text="Folder...", command=self._choose_dest).grid(
            row=1, column=2, sticky="e", **padding
        )

        ttk.Checkbutton(
            self, text="Dry run (bez kopiranja)", variable=self.dry_run_var
        ).grid(row=2, column=0, columnspan=3, sticky="w", **padding)

        normalize_frame = ttk.Frame(self)
        normalize_frame.grid(row=3, column=0, columnspan=3, sticky="we", **padding)
        ttk.Checkbutton(
            normalize_frame,
            text="Normalizuj (LUFS)",
            variable=self.normalize_var,
            command=self._toggle_normalize_entry,
        ).grid(row=0, column=0, sticky="w")
        self.lufs_entry = ttk.Entry(normalize_frame, width=6, textvariable=self.lufs_var, state="disabled")
        self.lufs_entry.grid(row=0, column=1, sticky="w", padx=(8, 0))
        ttk.Label(normalize_frame, text="npr. -14").grid(row=0, column=2, sticky="w", padx=(6, 0))
        ttk.Label(normalize_frame, text="Codec/bitrate:").grid(row=1, column=0, sticky="w", pady=(6, 0))
        self.codec_combo = ttk.Combobox(
            normalize_frame,
            textvariable=self.codec_var,
            state="disabled",
            values=[
                "auto",
                "mp3-192",
                "mp3-256",
                "aac-192",
                "aac-256",
                "flac",
                "wav",
            ],
            width=12,
        )
        self.codec_combo.grid(row=1, column=1, sticky="w", pady=(6, 0), padx=(8, 0))
        ttk.Label(normalize_frame, text="primenjuje se na izlazni fajl").grid(
            row=1, column=2, sticky="w", pady=(6, 0), padx=(6, 0)
        )

        self.run_button = ttk.Button(self, text="Pokreni kopiranje", command=self._on_run)
        self.run_button.grid(row=4, column=0, columnspan=2, sticky="we", **padding)

        self.cancel_button = ttk.Button(self, text="Otkaži", command=self._on_cancel, state="disabled")
        self.cancel_button.grid(row=4, column=2, sticky="we", **padding)

        ttk.Label(self, text="Napredak:").grid(row=5, column=0, sticky="w", **padding)
        self.progress = ttk.Progressbar(
            self, maximum=1, variable=self.progress_var, mode="determinate"
        )
        self.progress.grid(row=5, column=1, columnspan=2, sticky="we", **padding)

        ttk.Label(self, text="Log:").grid(row=6, column=0, sticky="w", **padding)
        self.log_box = ScrolledText(self, height=10, wrap="word")
        self.log_box.grid(row=7, column=0, columnspan=3, sticky="nsew", **padding)
        self.log_box.configure(state="disabled")

        self.status_var = tk.StringVar(value="Spremno")
        ttk.Label(self, textvariable=self.status_var).grid(
            row=8, column=0, columnspan=3, sticky="w", padx=10, pady=(0, 10)
        )

        self.columnconfigure(1, weight=1)
        self.rowconfigure(7, weight=1)

    def _choose_playlist(self) -> None:
        path = filedialog.askopenfilename(
            title="Odaberi playlistu",
            filetypes=(
                ("M3U fajlovi", "*.m3u *.m3u8"),
                ("PLS fajlovi", "*.pls"),
                ("Svi fajlovi", "*.*"),
            ),
        )
        if path:
            self.playlist_var.set(path)

    def _choose_dest(self) -> None:
        path = filedialog.askdirectory(title="Odaberi odredišni folder")
        if path:
            self.dest_var.set(path)

    def _on_run(self) -> None:
        playlist_path = Path(self.playlist_var.get().strip())
        dest_path = Path(self.dest_var.get().strip())

        if not playlist_path:
            messagebox.showwarning("Nedostaje putanja", "Izaberi playlistu.")
            return
        if not dest_path:
            messagebox.showwarning("Nedostaje folder", "Izaberi odredišni folder.")
            return

        normalize_lufs: float | None = None
        codec_preset = "auto"
        if self.normalize_var.get():
            try:
                normalize_lufs = float(self.lufs_var.get())
            except ValueError:
                messagebox.showwarning("Pogrešna vrednost", "Unesi broj (npr. -14) za LUFS.")
                return
            codec_preset = self.codec_var.get() or "auto"

        self._log(f"Čitam playlistu: {playlist_path}")
        try:
            tracks = read_playlist(playlist_path)
        except Exception as exc:
            messagebox.showerror("Greška pri čitanju", str(exc))
            self._log(f"[GREŠKA] {exc}")
            return

        total = len(tracks)
        self._log(f"Pronađeno fajlova: {total}")
        self._prepare_run(total)

        # Pokrećemo kopiranje u pozadinskom threadu da GUI ostane responzivan
        self.worker_thread = threading.Thread(
            target=self._run_copy,
            args=(tracks, dest_path, normalize_lufs, codec_preset),
            daemon=True,
        )
        self.worker_thread.start()

    def _log(self, text: str) -> None:
        self.log_box.configure(state="normal")
        self.log_box.insert("end", text + "\n")
        self.log_box.see("end")
        self.log_box.configure(state="disabled")

    def _prepare_run(self, total: int) -> None:
        self.progress["maximum"] = max(total, 1)
        self.progress_var.set(0)
        self.status_var.set("Kopiram...")
        self.run_button.configure(state="disabled")
        self.cancel_button.configure(state="normal")
        self.cancel_event = threading.Event()

    def _run_copy(self, tracks, dest_path: Path, normalize_lufs: float | None, codec_preset: str) -> None:
        def progress_cb(index: int, total: int, src: Path, dst: Path | None, status: str) -> None:
            # Osiguravamo da UI update ide kroz main thread
            self.after(0, self._on_progress, index, total, src, dst, status)

        try:
            copied, missing = copy_tracks(
                tracks,
                dest_path,
                dry_run=self.dry_run_var.get(),
                progress_callback=progress_cb,
                cancel_event=self.cancel_event,
                normalize_lufs=normalize_lufs,
                codec_preset=codec_preset,
            )
            cancelled = self.cancel_event.is_set()
            self.after(0, self._on_finished, copied, missing, cancelled)
        except Exception as exc:
            self.after(0, self._on_error, exc)

    def _on_progress(self, index: int, total: int, src: Path, dst: Path | None, status: str) -> None:
        self.progress["maximum"] = max(total, 1)
        self.progress_var.set(index)

        if status == "ok":
            self._log(f"[OK] {src} -> {dst}")
        elif status == "normalized":
            self._log(f"[NORMALIZED] {src} -> {dst}")
        elif status == "dry-run":
            self._log(f"[DRY RUN] {src} -> {dst}")
        elif status == "missing":
            self._log(f"[SKIPPED] Ne postoji: {src}")
        elif status == "cancelled":
            self._log("[INFO] Prekinuto na zahtev korisnika.")

    def _on_finished(self, copied: int, missing: int, cancelled: bool) -> None:
        self.run_button.configure(state="normal")
        self.cancel_button.configure(state="disabled")
        self.status_var.set("Prekinuto" if cancelled else "Gotovo (pogledaj log)")

        if cancelled:
            messagebox.showinfo("Prekinuto", f"Zaustavljeno. Do tada kopirano: {copied}, nedostaje: {missing}")
            return

        self._log(f"Uspešno: {copied}, Nedostaje: {missing}")
        if missing:
            messagebox.showwarning(
                "Završeno sa upozorenjima",
                f"Kopirano: {copied}\nNedostaje: {missing}\nDetalji su u logu.",
            )
        else:
            messagebox.showinfo("Završeno", f"Kopirano: {copied}")

    def _on_error(self, exc: Exception) -> None:
        self.run_button.configure(state="normal")
        self.cancel_button.configure(state="disabled")
        self.status_var.set("Greška")
        messagebox.showerror("Greška pri kopiranju", str(exc))
        self._log(f"[GREŠKA] {exc}")

    def _on_cancel(self) -> None:
        if self.cancel_event:
            self.cancel_event.set()
            self.status_var.set("Prekid u toku...")

    def _toggle_normalize_entry(self) -> None:
        state = "normal" if self.normalize_var.get() else "disabled"
        self.lufs_entry.configure(state=state)
        self.codec_combo.configure(state=state)


def main() -> None:
    app = PlaylistCopierApp()
    app.mainloop()


if __name__ == "__main__":
    main()
