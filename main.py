import io
import locale
import os
import queue
import shutil
import subprocess
import sys
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from PIL import Image
from PIL.PngImagePlugin import PngInfo

SCALES = [100, 90, 80, 70, 60, 50, 40, 30]
COLORS = [256, 128, 64, 32]



def detect_lang():
    try:
        if sys.platform == "win32":
            import ctypes
            lang_id = ctypes.windll.kernel32.GetUserDefaultUILanguage()
            return "ru" if (lang_id & 0xFF) == 0x19 else "en"
        if sys.platform == "darwin":
            out = subprocess.run(["defaults", "read", "-g", "AppleLocale"],
                                 capture_output=True, text=True, timeout=2).stdout
            if out.strip():
                return "ru" if out.strip().lower().startswith("ru") else "en"
    except Exception:
        pass
    candidates = [os.environ.get(v) for v in ("LC_ALL", "LC_MESSAGES", "LANG", "LANGUAGE")]
    try:
        candidates.append(locale.getlocale()[0])
    except Exception:
        pass
    for c in candidates:
        if c:
            return "ru" if c.lower().startswith("ru") else "en"
    return "en"


LANG = detect_lang()

STRINGS = {
    "ru": {
        "title": "PNG Shrinker",
        "folder": "Папка:",
        "browse": "Обзор…",
        "settings": "Настройки",
        "limit": "Лимит размера, КБ:",
        "recursive": "Включая вложенные папки",
        "strip": "Удалять метаданные (EXIF, текстовые чанки, ICC-профиль, DPI)",
        "backup": "Делать резервную копию (.png.bak) перед заменой",
        "start": "Старт",
        "stop": "Стоп",
        "ready": "Готово к работе",
        "stopping": "Останавливаю…",
        "err_title": "Ошибка",
        "err_folder": "Выберите существующую папку",
        "err_limit": "Некорректный лимит",
        "found": "Найдено PNG: {n}",
        "processed": "Обработано: {i} / {n}",
        "done": "Готово. Сэкономлено: {kb:.1f} КБ",
        "stopped": "Остановлено пользователем",
        "skip": "SKIP {path}: уже в пределах лимита",
        "meta": "META {path}: {a:.1f} КБ -> {b:.1f} КБ (метаданные удалены)",
        "ok": "OK   {path}: {a:.1f} КБ -> {b:.1f} КБ",
        "fail": "FAIL {path}: не удалось уложиться в {limit} КБ",
        "err": "ERR  {path}: {e}",
    },
    "en": {
        "title": "PNG Shrinker",
        "folder": "Folder:",
        "browse": "Browse…",
        "settings": "Settings",
        "limit": "Size limit, KB:",
        "recursive": "Include subfolders",
        "strip": "Remove metadata (EXIF, text chunks, ICC profile, DPI)",
        "backup": "Make a backup (.png.bak) before replacing",
        "start": "Start",
        "stop": "Stop",
        "ready": "Ready",
        "stopping": "Stopping…",
        "err_title": "Error",
        "err_folder": "Please choose an existing folder",
        "err_limit": "Invalid size limit",
        "found": "PNG files found: {n}",
        "processed": "Processed: {i} / {n}",
        "done": "Done. Saved: {kb:.1f} KB",
        "stopped": "Stopped by user",
        "skip": "SKIP {path}: already within the limit",
        "meta": "META {path}: {a:.1f} KB -> {b:.1f} KB (metadata removed)",
        "ok": "OK   {path}: {a:.1f} KB -> {b:.1f} KB",
        "fail": "FAIL {path}: could not fit into {limit} KB",
        "err": "ERR  {path}: {e}",
    },
}


def T(key, **kw):
    text = STRINGS[LANG][key]
    return text.format(**kw) if kw else text

DEFAULT_DIR = os.path.dirname(os.path.abspath(__file__))


class PlaceholderEntry(ttk.Entry):
    PLACEHOLDER_FG = "#9a9a9a"

    def __init__(self, master, placeholder, **kw):
        super().__init__(master, **kw)
        self.placeholder = placeholder
        self.showing = False
        self.bind("<FocusIn>", self._focus_in)
        self.bind("<FocusOut>", self._focus_out)
        self._show_placeholder()

    def _show_placeholder(self):
        self.delete(0, "end")
        self.insert(0, self.placeholder)
        self.configure(foreground=self.PLACEHOLDER_FG)
        self.showing = True

    def _hide_placeholder(self):
        self.delete(0, "end")
        self.configure(foreground="")
        self.showing = False

    def _focus_in(self, _=None):
        if self.showing:
            self._hide_placeholder()

    def _focus_out(self, _=None):
        if not self.get():
            self._show_placeholder()

    def value(self):
        return "" if self.showing else self.get().strip()

    def set_value(self, text):
        if self.showing:
            self._hide_placeholder()
        self.delete(0, "end")
        self.insert(0, text)

def quantize(img, colors):
    for method in (Image.Quantize.LIBIMAGEQUANT,
                   Image.Quantize.FASTOCTREE if img.mode == "RGBA" else Image.Quantize.MEDIANCUT):
        try:
            return img.quantize(colors=colors, method=method)
        except Exception:
            continue
    return img


def encode(img, orig, strip_meta):
    buf = io.BytesIO()
    kwargs = {"optimize": True}
    if strip_meta:
        # icc_profile=None явно запрещает Pillow подтягивать профиль из img.info
        kwargs["icc_profile"] = None
    else:
        if orig.info.get("icc_profile"):
            kwargs["icc_profile"] = orig.info["icc_profile"]
        if orig.info.get("dpi"):
            kwargs["dpi"] = orig.info["dpi"]
        if orig.info.get("exif"):
            kwargs["exif"] = orig.info["exif"]
        text = getattr(orig, "text", None)
        if text:
            pnginfo = PngInfo()
            for k, v in text.items():
                try:
                    pnginfo.add_text(k, v)
                except Exception:
                    pass
            kwargs["pnginfo"] = pnginfo
    img.save(buf, "PNG", **kwargs)
    return buf.getvalue()


def shrink(path, limit, strip_meta, backup):
    orig = Image.open(path)
    orig.load()
    has_alpha = orig.mode in ("RGBA", "LA") or "transparency" in orig.info
    base = orig.convert("RGBA" if has_alpha else "RGB")

    for scale in SCALES:
        if scale == 100:
            img = base
        else:
            w = max(1, base.width * scale // 100)
            h = max(1, base.height * scale // 100)
            img = base.resize((w, h), Image.LANCZOS)
        for colors in COLORS:
            data = encode(quantize(img, colors), orig, strip_meta)
            if len(data) <= limit:
                if backup:
                    shutil.copy2(path, path + ".bak")
                with open(path, "wb") as f:
                    f.write(data)
                return True
    return False


def strip_only(path, backup):
    orig = Image.open(path)
    orig.load()
    data = encode(orig, orig, True)
    if len(data) < os.path.getsize(path):
        if backup:
            shutil.copy2(path, path + ".bak")
        with open(path, "wb") as f:
            f.write(data)
        return True
    return False


def collect_pngs(root, recursive):
    result = []
    if recursive:
        for dirpath, _, files in os.walk(root):
            for name in files:
                if name.lower().endswith(".png"):
                    result.append(os.path.join(dirpath, name))
    else:
        for name in os.listdir(root):
            p = os.path.join(root, name)
            if os.path.isfile(p) and name.lower().endswith(".png"):
                result.append(p)
    return result


# ---------- GUI ----------

class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(T("title"))
        self.geometry("700x520")
        self.minsize(560, 420)

        self.q = queue.Queue()
        self.worker = None
        self.stop_flag = threading.Event()

        self.var_limit = tk.IntVar(value=64)
        self.var_recursive = tk.BooleanVar(value=True)
        self.var_strip = tk.BooleanVar(value=False)
        self.var_backup = tk.BooleanVar(value=True)

        self._build()
        self.after(100, self._poll)

    def _build(self):
        pad = {"padx": 10, "pady": 5}

        f1 = ttk.Frame(self)
        f1.pack(fill="x", **pad)
        ttk.Label(f1, text=T("folder")).pack(side="left")
        self.entry_path = PlaceholderEntry(f1, DEFAULT_DIR)
        self.entry_path.pack(side="left", fill="x", expand=True, padx=6)
        ttk.Button(f1, text=T("browse"), command=self._browse).pack(side="left")
        f2 = ttk.LabelFrame(self, text=T("settings"))
        f2.pack(fill="x", **pad)
        row = ttk.Frame(f2)
        row.pack(fill="x", padx=8, pady=4)
        ttk.Label(row, text=T("limit")).pack(side="left")
        ttk.Spinbox(row, from_=1, to=100000, textvariable=self.var_limit, width=8).pack(side="left", padx=6)
        ttk.Checkbutton(f2, text=T("recursive"), variable=self.var_recursive).pack(anchor="w", padx=8)
        ttk.Checkbutton(f2, text=T("strip"),
                        variable=self.var_strip).pack(anchor="w", padx=8)
        ttk.Checkbutton(f2, text=T("backup"),
                        variable=self.var_backup).pack(anchor="w", padx=8, pady=(0, 6))

        f3 = ttk.Frame(self)
        f3.pack(fill="x", **pad)
        self.btn_start = ttk.Button(f3, text=T("start"), command=self._start)
        self.btn_start.pack(side="left")
        self.btn_stop = ttk.Button(f3, text=T("stop"), command=self._stop, state="disabled")
        self.btn_stop.pack(side="left", padx=6)

        self.progress = ttk.Progressbar(self, mode="determinate")
        self.progress.pack(fill="x", **pad)
        self.lbl_status = ttk.Label(self, text=T("ready"))
        self.lbl_status.pack(anchor="w", padx=10)
        f4 = ttk.Frame(self)
        f4.pack(fill="both", expand=True, **pad)
        self.log = tk.Text(f4, height=10, state="disabled", wrap="none")
        sb = ttk.Scrollbar(f4, command=self.log.yview)
        self.log.configure(yscrollcommand=sb.set)
        self.log.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        self.log.tag_config("ok", foreground="#1a7f37")
        self.log.tag_config("fail", foreground="#cf222e")
        self.log.tag_config("skip", foreground="#6e7781")

    def _browse(self):
        d = filedialog.askdirectory()
        if d:
            self.entry_path.set_value(d)

    def _write(self, text, tag=None):
        self.log.configure(state="normal")
        self.log.insert("end", text + "\n", tag)
        self.log.see("end")
        self.log.configure(state="disabled")

    def _start(self):
        root = self.entry_path.value() or DEFAULT_DIR
        if not os.path.isdir(root):
            messagebox.showerror(T("err_title"), T("err_folder"))
            return
        try:
            limit = int(self.var_limit.get()) * 1024
            if limit <= 0:
                raise ValueError
        except Exception:
            messagebox.showerror(T("err_title"), T("err_limit"))
            return

        self.log.configure(state="normal")
        self.log.delete("1.0", "end")
        self.log.configure(state="disabled")

        self.stop_flag.clear()
        self.btn_start.configure(state="disabled")
        self.btn_stop.configure(state="normal")

        args = (root, limit, self.var_recursive.get(), self.var_strip.get(), self.var_backup.get())
        self.worker = threading.Thread(target=self._run, args=args, daemon=True)
        self.worker.start()

    def _stop(self):
        self.stop_flag.set()
        self.lbl_status.configure(text=T("stopping"))

    def _run(self, root, limit, recursive, strip, backup):
        q = self.q
        files = collect_pngs(root, recursive)
        q.put(("total", len(files)))
        saved_total = 0
        for i, path in enumerate(files, 1):
            if self.stop_flag.is_set():
                q.put(("log", T("stopped"), "skip"))
                break
            try:
                before = os.path.getsize(path)
                if before <= limit:
                    if strip and strip_only(path, backup):
                        after = os.path.getsize(path)
                        saved_total += before - after
                        q.put(("log", T("meta", path=path, a=before / 1024, b=after / 1024), "ok"))
                    else:
                        q.put(("log", T("skip", path=path), "skip"))
                else:
                    ok = shrink(path, limit, strip, backup)
                    if ok:
                        after = os.path.getsize(path)
                        saved_total += before - after
                        q.put(("log", T("ok", path=path, a=before / 1024, b=after / 1024), "ok"))
                    else:
                        q.put(("log", T("fail", path=path, limit=limit // 1024), "fail"))
            except Exception as e:
                q.put(("log", T("err", path=path, e=e), "fail"))
            q.put(("progress", i))
        q.put(("done", saved_total))

    def _poll(self):
        try:
            while True:
                kind, *rest = self.q.get_nowait()
                if kind == "total":
                    self.progress.configure(maximum=max(rest[0], 1), value=0)
                    self.lbl_status.configure(text=T("found", n=rest[0]))
                elif kind == "progress":
                    self.progress.configure(value=rest[0])
                    self.lbl_status.configure(text=T("processed", i=rest[0], n=int(self.progress["maximum"])))
                elif kind == "log":
                    self._write(rest[0], rest[1])
                elif kind == "done":
                    self.btn_start.configure(state="normal")
                    self.btn_stop.configure(state="disabled")
                    self.lbl_status.configure(text=T("done", kb=rest[0] / 1024))
        except queue.Empty:
            pass
        self.after(100, self._poll)


if __name__ == "__main__":
    App().mainloop()