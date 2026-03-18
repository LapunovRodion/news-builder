#!/usr/bin/env python3
"""Tkinter GUI for the standalone news builder."""

from __future__ import annotations

import json
import os
import queue
import subprocess
import sys
import threading
import traceback
import webbrowser
from pathlib import Path
from types import SimpleNamespace

import tkinter as tk
from tkinter import filedialog, messagebox, ttk

try:
    from tkhtmlview import HTMLScrolledText  # type: ignore
except ImportError:
    HTMLScrolledText = None

import news_builder


SETTINGS_PATH = Path.home() / ".news_builder_gui.json"


class NewsBuilderApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("News Builder")
        self.root.geometry("1200x920")
        self.root.minsize(1040, 760)

        self.message_queue: queue.Queue[tuple[str, object]] = queue.Queue()
        self.worker: threading.Thread | None = None
        self.last_build_result: news_builder.BuildResult | None = None
        self.profiles: dict[str, dict[str, object]] = {}

        self.profile_var = tk.StringVar()
        self.input_var = tk.StringVar()
        self.images_dir_var = tk.StringVar()
        self.output_var = tk.StringVar()
        self.full_output_var = tk.StringVar()
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
        self.save_full_page_var = tk.BooleanVar(value=True)

        self.preview_html = ""

        self._build_ui()
        self._load_settings()
        self.root.after(100, self._poll_queue)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    def _build_ui(self) -> None:
        container = ttk.Frame(self.root, padding=16)
        container.pack(fill="both", expand=True)
        container.columnconfigure(0, weight=1)
        container.rowconfigure(4, weight=1)

        intro = ttk.Label(
            container,
            text=(
                "Собирает HTML новости, загружает фото по SSH, умеет ряды, обтекание, "
                "профили серверов и локальный предпросмотр."
            ),
        )
        intro.grid(row=0, column=0, sticky="w", pady=(0, 12))

        profile_row = ttk.LabelFrame(container, text="Профили серверов", padding=10)
        profile_row.grid(row=1, column=0, sticky="ew", pady=(0, 10))
        profile_row.columnconfigure(0, weight=1)

        self.profile_combo = ttk.Combobox(
            profile_row,
            textvariable=self.profile_var,
            state="normal",
        )
        self.profile_combo.grid(row=0, column=0, sticky="ew")
        ttk.Button(profile_row, text="Сохранить профиль", command=self._save_profile).grid(
            row=0, column=1, padx=(8, 0)
        )
        ttk.Button(profile_row, text="Загрузить профиль", command=self._load_selected_profile).grid(
            row=0, column=2, padx=(8, 0)
        )
        ttk.Button(profile_row, text="Удалить профиль", command=self._delete_profile).grid(
            row=0, column=3, padx=(8, 0)
        )

        form = ttk.LabelFrame(container, text="Параметры", padding=12)
        form.grid(row=2, column=0, sticky="ew")
        form.columnconfigure(1, weight=1)

        self._add_file_row(form, 0, "Файл текста", self.input_var, self._choose_input_file, optional=True)
        self._add_file_row(form, 1, "Папка с фото", self.images_dir_var, self._choose_images_dir)
        self._add_file_row(form, 2, "HTML fragment", self.output_var, self._choose_output_file)
        self._add_file_row(form, 3, "Full HTML page", self.full_output_var, self._choose_full_output_file, optional=True)
        self._add_entry_row(form, 4, "Заголовок", self.title_var)
        self._add_entry_row(form, 5, "Папка новости", self.news_slug_var)
        self._add_entry_row(form, 6, "SSH host", self.remote_host_var)
        self._add_entry_row(form, 7, "SSH user", self.remote_user_var)
        self._add_entry_row(form, 8, "Базовая удалённая папка", self.remote_path_var)
        self._add_entry_row(form, 9, "SSH port", self.remote_port_var, width=12)
        self._add_file_row(form, 10, "SSH key", self.ssh_key_var, self._choose_ssh_key, optional=True)
        self._add_entry_row(form, 11, "SSH password", self.ssh_password_var, show="*")
        self._add_entry_row(form, 12, "Public base URL", self.public_base_url_var)
        self._add_file_row(form, 13, "Style config", self.style_config_var, self._choose_style_config, optional=True)

        options_row = ttk.Frame(form)
        options_row.grid(row=14, column=0, columnspan=3, sticky="w", pady=(10, 0))
        ttk.Checkbutton(
            options_row,
            text="Сохранять обработанные картинки рядом с HTML",
            variable=self.keep_temp_var,
        ).pack(side="left")
        ttk.Checkbutton(
            options_row,
            text="Сохранять full HTML page",
            variable=self.save_full_page_var,
        ).pack(side="left", padx=(18, 0))
        ttk.Label(
            options_row,
            text="Пароль сохраняется локально в ~/.news_builder_gui.json",
        ).pack(side="left", padx=(18, 0))

        actions = ttk.Frame(container)
        actions.grid(row=3, column=0, sticky="ew", pady=(12, 10))

        self.run_button = ttk.Button(actions, text="Собрать новость", command=self._start_build)
        self.run_button.pack(side="left")
        ttk.Button(actions, text="Обновить превью", command=self._refresh_preview).pack(side="left", padx=(8, 0))
        ttk.Button(actions, text="Загрузить из файла", command=self._load_input_into_editor).pack(side="left", padx=(8, 0))
        ttk.Button(actions, text="Открыть HTML", command=self._open_output).pack(side="left", padx=(8, 0))
        ttk.Button(actions, text="Открыть папку", command=self._open_output_folder).pack(side="left", padx=(8, 0))
        ttk.Button(actions, text="Сохранить настройки", command=self._save_settings).pack(side="left", padx=(8, 0))

        notebook = ttk.Notebook(container)
        notebook.grid(row=4, column=0, sticky="nsew")

        editor_tab = ttk.Frame(notebook, padding=10)
        images_tab = ttk.Frame(notebook, padding=10)
        preview_tab = ttk.Frame(notebook, padding=10)
        html_tab = ttk.Frame(notebook, padding=10)
        log_tab = ttk.Frame(notebook, padding=10)
        notebook.add(editor_tab, text="Редактор")
        notebook.add(images_tab, text="Фото")
        notebook.add(preview_tab, text="Превью")
        notebook.add(html_tab, text="HTML")
        notebook.add(log_tab, text="Лог")

        editor_tab.columnconfigure(0, weight=1)
        editor_tab.rowconfigure(1, weight=1)
        marker_bar = ttk.Frame(editor_tab)
        marker_bar.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        ttk.Button(marker_bar, text="[image]", command=lambda: self._insert_marker("[image:1]")).pack(side="left")
        ttk.Button(marker_bar, text="[images]", command=lambda: self._insert_marker("[images:1,2]")).pack(side="left", padx=(8, 0))
        ttk.Button(marker_bar, text="[left]", command=lambda: self._insert_marker("[image-left:1]")).pack(side="left", padx=(8, 0))
        ttk.Button(marker_bar, text="[right]", command=lambda: self._insert_marker("[image-right:1]")).pack(side="left", padx=(8, 0))
        ttk.Button(marker_bar, text="Очистить текст", command=self._clear_editor).pack(side="left", padx=(18, 0))

        self.editor_text = tk.Text(editor_tab, wrap="word", undo=True)
        self.editor_text.grid(row=1, column=0, sticky="nsew")
        editor_scroll = ttk.Scrollbar(editor_tab, orient="vertical", command=self.editor_text.yview)
        editor_scroll.grid(row=1, column=1, sticky="ns")
        self.editor_text.configure(yscrollcommand=editor_scroll.set)

        images_tab.columnconfigure(0, weight=1)
        images_tab.rowconfigure(1, weight=1)
        image_bar = ttk.Frame(images_tab)
        image_bar.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        ttk.Button(image_bar, text="Обновить список", command=self._refresh_image_index_list).pack(side="left")
        ttk.Button(image_bar, text="Вставить [image]", command=lambda: self._insert_selected_marker("image")).pack(side="left", padx=(8, 0))
        ttk.Button(image_bar, text="Вставить [images]", command=lambda: self._insert_selected_marker("images")).pack(side="left", padx=(8, 0))
        ttk.Button(image_bar, text="Вставить [left]", command=lambda: self._insert_selected_marker("image-left")).pack(side="left", padx=(8, 0))
        ttk.Button(image_bar, text="Вставить [right]", command=lambda: self._insert_selected_marker("image-right")).pack(side="left", padx=(8, 0))

        self.images_tree = ttk.Treeview(
            images_tab,
            columns=("index", "name"),
            show="headings",
            selectmode="extended",
        )
        self.images_tree.heading("index", text="#")
        self.images_tree.heading("name", text="Файл")
        self.images_tree.column("index", width=60, anchor="center")
        self.images_tree.column("name", width=700, anchor="w")
        self.images_tree.grid(row=1, column=0, sticky="nsew")
        images_scroll = ttk.Scrollbar(images_tab, orient="vertical", command=self.images_tree.yview)
        images_scroll.grid(row=1, column=1, sticky="ns")
        self.images_tree.configure(yscrollcommand=images_scroll.set)

        preview_tab.columnconfigure(0, weight=1)
        preview_tab.rowconfigure(0, weight=1)
        if HTMLScrolledText is not None:
            self.preview_widget = HTMLScrolledText(preview_tab, html="", background="#f4ede2")
        else:
            self.preview_widget = tk.Text(preview_tab, wrap="word", state="disabled")
        self.preview_widget.grid(row=0, column=0, sticky="nsew")

        html_tab.columnconfigure(0, weight=1)
        html_tab.rowconfigure(0, weight=1)
        self.html_text = tk.Text(html_tab, wrap="word", state="disabled")
        self.html_text.grid(row=0, column=0, sticky="nsew")
        html_scroll = ttk.Scrollbar(html_tab, orient="vertical", command=self.html_text.yview)
        html_scroll.grid(row=0, column=1, sticky="ns")
        self.html_text.configure(yscrollcommand=html_scroll.set)

        log_tab.columnconfigure(0, weight=1)
        log_tab.rowconfigure(0, weight=1)
        self.log_text = tk.Text(log_tab, wrap="word", state="disabled")
        self.log_text.grid(row=0, column=0, sticky="nsew")
        log_scroll = ttk.Scrollbar(log_tab, orient="vertical", command=self.log_text.yview)
        log_scroll.grid(row=0, column=1, sticky="ns")
        self.log_text.configure(yscrollcommand=log_scroll.set)

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
                self.output_var.set(str(Path(path).with_suffix(".html")))
            if not self.full_output_var.get():
                self.full_output_var.set(str(Path(path).with_name(f"{Path(path).stem}_preview.html")))
            self._load_input_into_editor()

    def _choose_images_dir(self) -> None:
        path = filedialog.askdirectory(title="Выбери папку с фотографиями")
        if path:
            self.images_dir_var.set(path)
            self._refresh_image_index_list()

    def _choose_output_file(self) -> None:
        path = filedialog.asksaveasfilename(
            title="Куда сохранить HTML fragment",
            defaultextension=".html",
            filetypes=[("HTML files", "*.html"), ("All files", "*.*")],
        )
        if path:
            self.output_var.set(path)
            if not self.full_output_var.get():
                self.full_output_var.set(str(Path(path).with_name(f"{Path(path).stem}_preview.html")))

    def _choose_full_output_file(self) -> None:
        path = filedialog.asksaveasfilename(
            title="Куда сохранить full HTML page",
            defaultextension=".html",
            filetypes=[("HTML files", "*.html"), ("All files", "*.*")],
        )
        if path:
            self.full_output_var.set(path)

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

    def _load_input_into_editor(self) -> None:
        path_value = self.input_var.get().strip()
        if not path_value:
            messagebox.showinfo("News Builder", "Сначала выбери файл текста.")
            return
        path = Path(path_value).expanduser()
        if not path.is_file():
            messagebox.showerror("News Builder", f"Файл не найден:\n{path}")
            return
        try:
            title, body = news_builder.read_input_document(path)
        except Exception as exc:
            messagebox.showerror("News Builder", str(exc))
            return

        if title:
            self.title_var.set(title)
        self.editor_text.delete("1.0", "end")
        self.editor_text.insert("1.0", body)
        self._append_log(f"Loaded document into editor: {path}")

    def _insert_marker(self, marker: str) -> None:
        self.editor_text.insert("insert", f"\n\n{marker}\n\n")
        self.editor_text.focus_set()

    def _selected_image_indices(self) -> tuple[int, ...]:
        values: list[int] = []
        for item_id in self.images_tree.selection():
            item = self.images_tree.item(item_id)
            index_value = item.get("values", [None])[0]
            if index_value is not None:
                values.append(int(index_value))
        return tuple(values)

    def _insert_selected_marker(self, marker_type: str) -> None:
        indices = self._selected_image_indices()
        if not indices:
            messagebox.showinfo("News Builder", "Сначала выбери фото в списке.")
            return

        if marker_type == "image" and len(indices) != 1:
            messagebox.showinfo("News Builder", "Для [image] выбери ровно одно фото.")
            return
        if marker_type in {"image-left", "image-right"} and len(indices) != 1:
            messagebox.showinfo("News Builder", f"Для [{marker_type}] выбери ровно одно фото.")
            return

        payload = ",".join(str(index) for index in indices)
        self._insert_marker(f"[{marker_type}:{payload}]")

    def _refresh_image_index_list(self) -> None:
        for item_id in self.images_tree.get_children():
            self.images_tree.delete(item_id)

        images_dir_value = self.images_dir_var.get().strip()
        if not images_dir_value:
            return

        images_dir = Path(images_dir_value).expanduser()
        if not images_dir.is_dir():
            return

        images = news_builder.discover_images(images_dir)
        for index, image_path in enumerate(images, start=1):
            self.images_tree.insert("", "end", values=(index, image_path.name))
        self._append_log(f"Indexed {len(images)} image(s) from {images_dir}")

    def _clear_editor(self) -> None:
        self.editor_text.delete("1.0", "end")

    def _append_log(self, message: str) -> None:
        self.log_text.configure(state="normal")
        self.log_text.insert("end", message.rstrip() + "\n")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def _set_html_source(self, content: str) -> None:
        self.html_text.configure(state="normal")
        self.html_text.delete("1.0", "end")
        self.html_text.insert("1.0", content)
        self.html_text.configure(state="disabled")

    def _set_preview_html(self, content: str) -> None:
        self.preview_html = content
        if HTMLScrolledText is not None:
            self.preview_widget.set_html(content)
            return

        fallback = (
            "Install `tkhtmlview` to get rendered preview inside the GUI.\n\n"
            "Current fallback shows HTML source.\n\n"
            + content
        )
        self.preview_widget.configure(state="normal")
        self.preview_widget.delete("1.0", "end")
        self.preview_widget.insert("1.0", fallback)
        self.preview_widget.configure(state="disabled")

    def _editor_body(self) -> str:
        return self.editor_text.get("1.0", "end-1c").strip()

    def _build_args(self) -> SimpleNamespace:
        remote_port_text = self.remote_port_var.get().strip() or "22"
        try:
            remote_port = int(remote_port_text)
        except ValueError as exc:
            raise ValueError("SSH port must be an integer.") from exc

        full_output = None
        if self.save_full_page_var.get():
            full_output = self.full_output_var.get().strip()
            if not full_output and self.output_var.get().strip():
                output_path = Path(self.output_var.get().strip())
                full_output = str(output_path.with_name(f"{output_path.stem}_preview.html"))

        return SimpleNamespace(
            input=self.input_var.get().strip() or None,
            images_dir=self.images_dir_var.get().strip(),
            output=self.output_var.get().strip(),
            full_output=full_output,
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

    def _validate_build_fields(self, args: SimpleNamespace, require_auth: bool) -> None:
        required = {
            "Папка с фото": args.images_dir,
            "HTML fragment": args.output,
            "Заголовок": args.title,
            "Базовая удалённая папка": args.remote_path,
            "Public base URL": args.public_base_url,
        }
        if require_auth:
            required["SSH host"] = args.remote_host
            required["SSH user"] = args.remote_user
        missing = [label for label, value in required.items() if not value]
        if missing:
            raise ValueError("Заполни обязательные поля: " + ", ".join(missing))

        if require_auth and not args.ssh_key and not args.ssh_password:
            raise ValueError("Укажи SSH key или SSH password.")
        if not self._editor_body():
            raise ValueError("Редактор пустой. Загрузи файл или вставь текст вручную.")

    def _preview_result_from_editor(self) -> tuple[str, str]:
        title = news_builder.normalize_title(self.title_var.get().strip())
        body = news_builder.normalize_body(self._editor_body())
        images_dir_value = self.images_dir_var.get().strip()
        if not title:
            raise ValueError("Укажи заголовок для превью.")
        if not body:
            raise ValueError("Редактор пустой. Загрузи файл или вставь текст вручную.")
        if not images_dir_value:
            raise ValueError("Укажи папку с фото для превью.")

        images_dir = Path(images_dir_value).expanduser().resolve()
        if not images_dir.is_dir():
            raise FileNotFoundError(f"Images directory not found: {images_dir}")

        styles = news_builder.load_config(self.style_config_var.get().strip() or None)["styles"]
        blocks = news_builder.parse_blocks(body)
        source_images = news_builder.discover_images(images_dir)
        if not source_images:
            raise ValueError(f"No supported image files found in {images_dir}")
        news_builder.ensure_markers_have_images(blocks, len(source_images))
        prepared_images = [
            news_builder.PreparedImage(
                source_path=path,
                processed_path=path,
                remote_name=path.name,
                public_url=path.resolve().as_uri(),
            )
            for path in source_images
        ]
        fragment_html = news_builder.render_html(title, blocks, prepared_images, styles)
        full_html = news_builder.render_full_html_document(title, fragment_html)
        return fragment_html, full_html

    def _refresh_preview(self) -> None:
        try:
            fragment_html, full_html = self._preview_result_from_editor()
        except Exception as exc:
            messagebox.showerror("News Builder", str(exc))
            return
        self._set_preview_html(full_html)
        self._set_html_source(fragment_html)
        self._append_log("Preview updated from editor content.")

    def _current_profile_payload(self) -> dict[str, object]:
        return {
            "remote_host": self.remote_host_var.get().strip(),
            "remote_user": self.remote_user_var.get().strip(),
            "remote_path": self.remote_path_var.get().strip(),
            "remote_port": self.remote_port_var.get().strip() or "22",
            "ssh_key": self.ssh_key_var.get().strip(),
            "ssh_password": self.ssh_password_var.get(),
            "public_base_url": self.public_base_url_var.get().strip(),
            "style_config": self.style_config_var.get().strip(),
        }

    def _refresh_profile_combo(self) -> None:
        self.profile_combo["values"] = sorted(self.profiles.keys())

    def _save_profile(self) -> None:
        profile_name = self.profile_var.get().strip()
        if not profile_name:
            messagebox.showerror("News Builder", "Укажи имя профиля.")
            return
        self.profiles[profile_name] = self._current_profile_payload()
        self._refresh_profile_combo()
        self._save_settings(silent=True)
        self._append_log(f"Saved profile: {profile_name}")

    def _load_selected_profile(self) -> None:
        profile_name = self.profile_var.get().strip()
        payload = self.profiles.get(profile_name)
        if not payload:
            messagebox.showerror("News Builder", f"Профиль не найден: {profile_name}")
            return
        self.remote_host_var.set(str(payload.get("remote_host", "")))
        self.remote_user_var.set(str(payload.get("remote_user", "")))
        self.remote_path_var.set(str(payload.get("remote_path", "")))
        self.remote_port_var.set(str(payload.get("remote_port", "22")))
        self.ssh_key_var.set(str(payload.get("ssh_key", "")))
        self.ssh_password_var.set(str(payload.get("ssh_password", "")))
        self.public_base_url_var.set(str(payload.get("public_base_url", "")))
        self.style_config_var.set(str(payload.get("style_config", "")))
        self._append_log(f"Loaded profile: {profile_name}")

    def _delete_profile(self) -> None:
        profile_name = self.profile_var.get().strip()
        if not profile_name or profile_name not in self.profiles:
            messagebox.showerror("News Builder", "Выбери существующий профиль.")
            return
        del self.profiles[profile_name]
        self._refresh_profile_combo()
        self.profile_var.set("")
        self._save_settings(silent=True)
        self._append_log(f"Deleted profile: {profile_name}")

    def _start_build(self) -> None:
        if self.worker and self.worker.is_alive():
            messagebox.showinfo("News Builder", "Сборка уже выполняется.")
            return

        try:
            args = self._build_args()
            self._validate_build_fields(args, require_auth=True)
        except Exception as exc:
            messagebox.showerror("News Builder", str(exc))
            return

        self._save_settings(silent=True)
        self.run_button.configure(state="disabled")
        self._append_log("Starting build...")

        editor_body = self._editor_body()
        self.worker = threading.Thread(target=self._run_build, args=(args, editor_body), daemon=True)
        self.worker.start()

    def _run_build(self, args: SimpleNamespace, body: str) -> None:
        def logger(message: str) -> None:
            self.message_queue.put(("log", message))

        try:
            result = news_builder.build_with_content(
                args=args,
                title=args.title or "",
                body=body,
                images_dir=Path(args.images_dir).expanduser().resolve(),
                output_path=Path(args.output).expanduser().resolve(),
                logger=logger,
                upload=True,
            )
        except Exception as exc:
            details = "".join(traceback.format_exception_only(type(exc), exc)).strip()
            self.message_queue.put(("error", details))
            return

        self.message_queue.put(("done", result))

    def _poll_queue(self) -> None:
        while True:
            try:
                event_type, payload = self.message_queue.get_nowait()
            except queue.Empty:
                break

            if event_type == "log":
                self._append_log(str(payload))
            elif event_type == "error":
                self._append_log(f"Error: {payload}")
                self.run_button.configure(state="normal")
                messagebox.showerror("News Builder", str(payload))
            elif event_type == "done":
                assert isinstance(payload, news_builder.BuildResult)
                self.last_build_result = payload
                self._append_log(f"Done: {payload.fragment_output_path}")
                if payload.full_output_path:
                    self._append_log(f"Full page: {payload.full_output_path}")
                self._set_html_source(payload.fragment_html)
                self._set_preview_html(payload.full_html or news_builder.render_full_html_document(self.title_var.get(), payload.fragment_html))
                self.run_button.configure(state="normal")
                messagebox.showinfo(
                    "News Builder",
                    f"HTML готов:\n{payload.fragment_output_path}"
                    + (f"\n\nFull page:\n{payload.full_output_path}" if payload.full_output_path else ""),
                )

        self.root.after(100, self._poll_queue)

    def _open_output(self) -> None:
        target = None
        if self.last_build_result and self.last_build_result.full_output_path:
            target = self.last_build_result.full_output_path
        elif self.last_build_result:
            target = self.last_build_result.fragment_output_path
        elif self.output_var.get().strip():
            target = Path(self.output_var.get().strip()).expanduser()

        if not target:
            messagebox.showinfo("News Builder", "Сначала собери новость.")
            return
        if not Path(target).exists():
            messagebox.showinfo("News Builder", "HTML-файл пока не создан.")
            return
        webbrowser.open(Path(target).resolve().as_uri())

    def _open_output_folder(self) -> None:
        output_path = self.output_var.get().strip()
        if not output_path:
            messagebox.showinfo("News Builder", "Сначала укажи путь для HTML.")
            return
        directory = Path(output_path).expanduser().resolve().parent
        if not directory.exists():
            directory.mkdir(parents=True, exist_ok=True)

        if os.name == "nt":
            os.startfile(str(directory))  # type: ignore[attr-defined]
            return
        if sys.platform == "darwin":  # type: ignore[name-defined]
            subprocess.Popen(["open", str(directory)])
            return
        subprocess.Popen(["xdg-open", str(directory)])

    def _load_settings(self) -> None:
        if not SETTINGS_PATH.exists():
            return
        try:
            data = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
        except Exception:
            return

        self.profiles = dict(data.get("profiles", {}))
        self._refresh_profile_combo()
        self.profile_var.set(data.get("selected_profile", ""))
        self.input_var.set(data.get("input", ""))
        self.images_dir_var.set(data.get("images_dir", ""))
        self.output_var.set(data.get("output", ""))
        self.full_output_var.set(data.get("full_output", ""))
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
        self.save_full_page_var.set(bool(data.get("save_full_page", True)))
        self.editor_text.delete("1.0", "end")
        self.editor_text.insert("1.0", data.get("editor_body", ""))
        self._refresh_image_index_list()

    def _save_settings(self, silent: bool = False) -> None:
        data = {
            "profiles": self.profiles,
            "selected_profile": self.profile_var.get().strip(),
            "input": self.input_var.get().strip(),
            "images_dir": self.images_dir_var.get().strip(),
            "output": self.output_var.get().strip(),
            "full_output": self.full_output_var.get().strip(),
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
            "save_full_page": self.save_full_page_var.get(),
            "editor_body": self._editor_body(),
        }
        SETTINGS_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        if not silent:
            messagebox.showinfo("News Builder", f"Настройки сохранены в\n{SETTINGS_PATH}")

    def _on_close(self) -> None:
        try:
            self._save_settings(silent=True)
        finally:
            self.root.destroy()


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
