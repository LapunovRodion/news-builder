#!/usr/bin/env python3
"""Tkinter GUI for the standalone news builder."""

from __future__ import annotations

import json
import queue
import threading
import traceback
import webbrowser
from pathlib import Path
from types import SimpleNamespace

import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import news_builder


SETTINGS_PATH = Path.home() / ".news_builder_gui.json"


class NewsBuilderApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("News Builder")
        self.root.geometry("980x760")
        self.root.minsize(900, 680)

        self.message_queue: queue.Queue[tuple[str, str]] = queue.Queue()
        self.worker: threading.Thread | None = None
        self.last_output_path: Path | None = None

        self.input_var = tk.StringVar()
        self.images_dir_var = tk.StringVar()
        self.output_var = tk.StringVar()
        self.title_var = tk.StringVar()
        self.news_slug_var = tk.StringVar()
        self.remote_host_var = tk.StringVar()
        self.remote_user_var = tk.StringVar()
        self.remote_path_var = tk.StringVar()
        self.remote_port_var = tk.StringVar(value="22")
        self.ssh_key_var = tk.StringVar(value=str(Path.home() / ".ssh" / "id_ed25519"))
        self.ssh_password_var = tk.StringVar()
        self.public_base_url_var = tk.StringVar()
        self.style_config_var = tk.StringVar()
        self.keep_temp_var = tk.BooleanVar(value=False)

        self._build_ui()
        self._load_settings()
        self.root.after(100, self._poll_queue)

    def _build_ui(self) -> None:
        container = ttk.Frame(self.root, padding=16)
        container.pack(fill="both", expand=True)
        container.columnconfigure(1, weight=1)
        container.rowconfigure(2, weight=1)

        intro = ttk.Label(
            container,
            text=(
                "Собирает HTML-фрагмент новости, обрабатывает фото и загружает их по SSH. "
                "Маркеры: [image:1], [images:1,2], [image-left:3], [image-right:4]"
            ),
        )
        intro.grid(row=0, column=0, columnspan=3, sticky="w", pady=(0, 14))

        form = ttk.LabelFrame(container, text="Параметры", padding=12)
        form.grid(row=1, column=0, columnspan=3, sticky="nsew")
        form.columnconfigure(1, weight=1)

        self._add_file_row(form, 0, "Файл текста", self.input_var, self._choose_input_file)
        self._add_file_row(form, 1, "Папка с фото", self.images_dir_var, self._choose_images_dir)
        self._add_file_row(form, 2, "Куда сохранить HTML", self.output_var, self._choose_output_file)
        self._add_entry_row(form, 3, "Заголовок", self.title_var)
        self._add_entry_row(form, 4, "Папка новости", self.news_slug_var)
        self._add_entry_row(form, 5, "SSH host", self.remote_host_var)
        self._add_entry_row(form, 6, "SSH user", self.remote_user_var)
        self._add_entry_row(form, 7, "Базовая удалённая папка", self.remote_path_var)
        self._add_entry_row(form, 8, "SSH port", self.remote_port_var, width=12)
        self._add_file_row(form, 9, "SSH key", self.ssh_key_var, self._choose_ssh_key)
        self._add_entry_row(form, 10, "SSH password", self.ssh_password_var, show="*")
        self._add_entry_row(form, 11, "Public base URL", self.public_base_url_var)
        self._add_file_row(form, 12, "Style config", self.style_config_var, self._choose_style_config, optional=True)

        options_row = ttk.Frame(form)
        options_row.grid(row=13, column=0, columnspan=3, sticky="w", pady=(10, 0))
        ttk.Checkbutton(
            options_row,
            text="Сохранять обработанные картинки рядом с HTML",
            variable=self.keep_temp_var,
        ).pack(side="left")
        ttk.Label(
            options_row,
            text="Пароль сохраняется локально в ~/.news_builder_gui.json",
        ).pack(side="left", padx=(18, 0))

        actions = ttk.Frame(container)
        actions.grid(row=2, column=0, columnspan=3, sticky="ew", pady=(14, 10))

        self.run_button = ttk.Button(actions, text="Собрать новость", command=self._start_build)
        self.run_button.pack(side="left")

        ttk.Button(actions, text="Сохранить настройки", command=self._save_settings).pack(
            side="left", padx=(8, 0)
        )
        ttk.Button(actions, text="Открыть HTML", command=self._open_output).pack(side="left", padx=(8, 0))
        ttk.Button(actions, text="Очистить лог", command=self._clear_log).pack(side="left", padx=(8, 0))

        log_frame = ttk.LabelFrame(container, text="Лог", padding=10)
        log_frame.grid(row=3, column=0, columnspan=3, sticky="nsew")
        log_frame.columnconfigure(0, weight=1)
        log_frame.rowconfigure(0, weight=1)
        container.rowconfigure(3, weight=1)

        self.log_text = tk.Text(log_frame, wrap="word", height=20, state="disabled")
        self.log_text.grid(row=0, column=0, sticky="nsew")

        scrollbar = ttk.Scrollbar(log_frame, orient="vertical", command=self.log_text.yview)
        scrollbar.grid(row=0, column=1, sticky="ns")
        self.log_text.configure(yscrollcommand=scrollbar.set)

    def _add_entry_row(
        self,
        parent: ttk.Frame,
        row: int,
        label: str,
        variable: tk.StringVar,
        width: int | None = None,
        show: str | None = None,
    ) -> None:
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", pady=4, padx=(0, 10))
        entry = ttk.Entry(parent, textvariable=variable, width=width, show=show)
        entry.grid(row=row, column=1, sticky="ew", pady=4)

    def _add_file_row(
        self,
        parent: ttk.Frame,
        row: int,
        label: str,
        variable: tk.StringVar,
        command,
        optional: bool = False,
    ) -> None:
        caption = f"{label} (optional)" if optional else label
        ttk.Label(parent, text=caption).grid(row=row, column=0, sticky="w", pady=4, padx=(0, 10))
        ttk.Entry(parent, textvariable=variable).grid(row=row, column=1, sticky="ew", pady=4)
        ttk.Button(parent, text="Выбрать", command=command).grid(row=row, column=2, sticky="w", padx=(8, 0), pady=4)

    def _choose_input_file(self) -> None:
        path = filedialog.askopenfilename(
            title="Выбери текст новости",
            filetypes=[("Documents", "*.docx *.txt *.md"), ("All files", "*.*")],
        )
        if path:
            self.input_var.set(path)
            if not self.output_var.get():
                suggested = str(Path(path).with_suffix(".html"))
                self.output_var.set(suggested)

    def _choose_images_dir(self) -> None:
        path = filedialog.askdirectory(title="Выбери папку с фотографиями")
        if path:
            self.images_dir_var.set(path)

    def _choose_output_file(self) -> None:
        path = filedialog.asksaveasfilename(
            title="Куда сохранить HTML",
            defaultextension=".html",
            filetypes=[("HTML files", "*.html"), ("All files", "*.*")],
        )
        if path:
            self.output_var.set(path)

    def _choose_ssh_key(self) -> None:
        path = filedialog.askopenfilename(title="Выбери SSH-ключ")
        if path:
            self.ssh_key_var.set(path)

    def _choose_style_config(self) -> None:
        path = filedialog.askopenfilename(
            title="Выбери JSON-конфиг стилей",
            filetypes=[("JSON files", "*.json"), ("All files", "*.*")],
        )
        if path:
            self.style_config_var.set(path)

    def _append_log(self, message: str) -> None:
        self.log_text.configure(state="normal")
        self.log_text.insert("end", message.rstrip() + "\n")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def _clear_log(self) -> None:
        self.log_text.configure(state="normal")
        self.log_text.delete("1.0", "end")
        self.log_text.configure(state="disabled")

    def _collect_args(self) -> SimpleNamespace:
        remote_port_text = self.remote_port_var.get().strip() or "22"
        try:
            remote_port = int(remote_port_text)
        except ValueError as exc:
            raise ValueError("SSH port must be an integer.") from exc

        return SimpleNamespace(
            input=self.input_var.get().strip(),
            images_dir=self.images_dir_var.get().strip(),
            output=self.output_var.get().strip(),
            title=self.title_var.get().strip() or None,
            news_slug=self.news_slug_var.get().strip() or None,
            remote_host=self.remote_host_var.get().strip(),
            remote_user=self.remote_user_var.get().strip(),
            remote_path=self.remote_path_var.get().strip(),
            remote_port=remote_port,
            ssh_key=self.ssh_key_var.get().strip(),
            ssh_password=self.ssh_password_var.get(),
            public_base_url=self.public_base_url_var.get().strip(),
            style_config=self.style_config_var.get().strip() or None,
            keep_temp=self.keep_temp_var.get(),
        )

    def _validate_required_fields(self, args: SimpleNamespace) -> None:
        required = {
            "Файл текста": args.input,
            "Папка с фото": args.images_dir,
            "Куда сохранить HTML": args.output,
            "SSH host": args.remote_host,
            "SSH user": args.remote_user,
            "Базовая удалённая папка": args.remote_path,
            "Public base URL": args.public_base_url,
        }
        missing = [label for label, value in required.items() if not value]
        if missing:
            raise ValueError("Заполни обязательные поля: " + ", ".join(missing))
        if not args.ssh_key and not args.ssh_password:
            raise ValueError("Укажи SSH key или SSH password.")

    def _start_build(self) -> None:
        if self.worker and self.worker.is_alive():
            messagebox.showinfo("News Builder", "Сборка уже выполняется.")
            return

        try:
            args = self._collect_args()
            self._validate_required_fields(args)
        except Exception as exc:
            messagebox.showerror("News Builder", str(exc))
            return

        self._save_settings(silent=True)
        self.run_button.configure(state="disabled")
        self._append_log("Starting build...")

        self.worker = threading.Thread(target=self._run_build, args=(args,), daemon=True)
        self.worker.start()

    def _run_build(self, args: SimpleNamespace) -> None:
        def logger(message: str) -> None:
            self.message_queue.put(("log", message))

        try:
            output_path = news_builder.run_builder(args, logger=logger)
        except Exception as exc:
            details = "".join(traceback.format_exception_only(type(exc), exc)).strip()
            self.message_queue.put(("error", details))
            return

        self.message_queue.put(("done", str(output_path)))

    def _poll_queue(self) -> None:
        while True:
            try:
                event_type, payload = self.message_queue.get_nowait()
            except queue.Empty:
                break

            if event_type == "log":
                self._append_log(payload)
            elif event_type == "error":
                self._append_log(f"Error: {payload}")
                self.run_button.configure(state="normal")
                messagebox.showerror("News Builder", payload)
            elif event_type == "done":
                self.last_output_path = Path(payload)
                self._append_log(f"Done: {payload}")
                self.run_button.configure(state="normal")
                messagebox.showinfo("News Builder", f"HTML готов:\n{payload}")

        self.root.after(100, self._poll_queue)

    def _open_output(self) -> None:
        path = self.output_var.get().strip()
        if not path:
            messagebox.showinfo("News Builder", "Сначала укажи путь для HTML.")
            return
        resolved = Path(path).expanduser()
        if not resolved.exists():
            messagebox.showinfo("News Builder", "HTML-файл пока не создан.")
            return
        webbrowser.open(resolved.resolve().as_uri())

    def _load_settings(self) -> None:
        if not SETTINGS_PATH.exists():
            return
        try:
            data = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
        except Exception:
            return

        self.input_var.set(data.get("input", ""))
        self.images_dir_var.set(data.get("images_dir", ""))
        self.output_var.set(data.get("output", ""))
        self.title_var.set(data.get("title", ""))
        self.news_slug_var.set(data.get("news_slug", ""))
        self.remote_host_var.set(data.get("remote_host", ""))
        self.remote_user_var.set(data.get("remote_user", ""))
        self.remote_path_var.set(data.get("remote_path", ""))
        self.remote_port_var.set(str(data.get("remote_port", "22")))
        self.ssh_key_var.set(data.get("ssh_key", self.ssh_key_var.get()))
        self.ssh_password_var.set(data.get("ssh_password", ""))
        self.public_base_url_var.set(data.get("public_base_url", ""))
        self.style_config_var.set(data.get("style_config", ""))
        self.keep_temp_var.set(bool(data.get("keep_temp", False)))

    def _save_settings(self, silent: bool = False) -> None:
        data = {
            "input": self.input_var.get().strip(),
            "images_dir": self.images_dir_var.get().strip(),
            "output": self.output_var.get().strip(),
            "title": self.title_var.get().strip(),
            "news_slug": self.news_slug_var.get().strip(),
            "remote_host": self.remote_host_var.get().strip(),
            "remote_user": self.remote_user_var.get().strip(),
            "remote_path": self.remote_path_var.get().strip(),
            "remote_port": self.remote_port_var.get().strip() or "22",
            "ssh_key": self.ssh_key_var.get().strip(),
            "ssh_password": self.ssh_password_var.get(),
            "public_base_url": self.public_base_url_var.get().strip(),
            "style_config": self.style_config_var.get().strip(),
            "keep_temp": self.keep_temp_var.get(),
        }
        SETTINGS_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        if not silent:
            messagebox.showinfo("News Builder", f"Настройки сохранены в\n{SETTINGS_PATH}")


def main() -> int:
    root = tk.Tk()
    style = ttk.Style()
    if "clam" in style.theme_names():
        style.theme_use("clam")
    app = NewsBuilderApp(root)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
