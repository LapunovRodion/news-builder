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
    from PIL import Image, ImageOps, ImageTk  # type: ignore
except ImportError:
    Image = None
    ImageOps = None
    ImageTk = None

try:
    from tkhtmlview import HTMLScrolledText  # type: ignore
except ImportError:
    HTMLScrolledText = None

import news_builder


SETTINGS_PATH = Path.home() / ".news_builder_gui.json"
THUMBNAIL_SIZE = (150, 110)
DETAIL_PREVIEW_SIZE = (420, 320)


class NewsBuilderApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("News Builder")
        self.root.geometry("1380x960")
        self.root.minsize(1180, 820)

        self.message_queue: queue.Queue[tuple[str, object]] = queue.Queue()
        self.worker: threading.Thread | None = None
        self.last_build_result: news_builder.BuildResult | None = None
        self.profiles: dict[str, dict[str, object]] = {}
        self.image_records: list[tuple[int, Path]] = []
        self.selected_image_indices: tuple[int, ...] = tuple()
        self.used_image_indices: set[int] = set()
        self.thumbnail_cards: dict[int, tk.Frame] = {}
        self.thumbnail_photo_refs: dict[int, object] = {}
        self.thumbnail_name_labels: dict[int, tk.Label] = {}
        self.thumbnail_index_labels: dict[int, tk.Label] = {}
        self.detail_photo_ref = None
        self._editor_analysis_job: str | None = None

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
        self.editor_status_var = tk.StringVar(value="Редактор готов.")
        self.used_images_var = tk.StringVar(value="Используемые фото: нет")
        self.detail_caption_var = tk.StringVar(value="Выбери фото в списке или в сетке миниатюр.")
        self.detail_usage_var = tk.StringVar(value="")

        self.preview_html = ""

        self._build_ui()
        self._load_settings()
        self._maximize_window()
        self.root.after(100, self._poll_queue)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    def _maximize_window(self) -> None:
        try:
            self.root.state("zoomed")
            return
        except tk.TclError:
            pass

        try:
            self.root.attributes("-zoomed", True)
        except tk.TclError:
            pass

    def _build_ui(self) -> None:
        container = ttk.Frame(self.root, padding=16)
        container.pack(fill="both", expand=True)
        container.columnconfigure(0, weight=1)
        container.rowconfigure(4, weight=1)

        intro = ttk.Label(
            container,
            text=(
                "Собирает HTML новости, загружает фото по SSH, умеет ряды, обтекание, "
                "профили серверов, локальный preview и связанный браузер изображений."
            ),
        )
        intro.grid(row=0, column=0, sticky="w", pady=(0, 12))

        profile_row = ttk.LabelFrame(container, text="Профили серверов", padding=10)
        profile_row.grid(row=1, column=0, sticky="ew", pady=(0, 10))
        profile_row.columnconfigure(0, weight=1)

        self.profile_combo = ttk.Combobox(profile_row, textvariable=self.profile_var, state="normal")
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

        form = ttk.LabelFrame(container, text="Параметры публикации", padding=12)
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

        self.editor_tab = ttk.Frame(notebook, padding=10)
        self.images_tab = ttk.Frame(notebook, padding=10)
        self.preview_tab = ttk.Frame(notebook, padding=10)
        self.html_tab = ttk.Frame(notebook, padding=10)
        self.log_tab = ttk.Frame(notebook, padding=10)
        notebook.add(self.editor_tab, text="Редактор")
        notebook.add(self.images_tab, text="Фото")
        notebook.add(self.preview_tab, text="Превью")
        notebook.add(self.html_tab, text="HTML")
        notebook.add(self.log_tab, text="Лог")

        self._build_editor_tab()
        self._build_images_tab()
        self._build_preview_tab()
        self._build_html_tab()
        self._build_log_tab()

    def _build_editor_tab(self) -> None:
        self.editor_tab.columnconfigure(0, weight=1)
        self.editor_tab.rowconfigure(1, weight=1)

        status_row = ttk.Frame(self.editor_tab)
        status_row.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        status_row.columnconfigure(0, weight=1)
        ttk.Label(status_row, textvariable=self.editor_status_var).grid(row=0, column=0, sticky="w")
        ttk.Label(status_row, textvariable=self.used_images_var).grid(row=0, column=1, sticky="e")

        paned = ttk.Panedwindow(self.editor_tab, orient="horizontal")
        paned.grid(row=1, column=0, sticky="nsew")

        editor_panel = ttk.Frame(paned, padding=8)
        tools_panel = ttk.Frame(paned, padding=8)
        editor_panel.columnconfigure(0, weight=1)
        editor_panel.rowconfigure(1, weight=1)
        tools_panel.columnconfigure(0, weight=1)
        paned.add(editor_panel, weight=5)
        paned.add(tools_panel, weight=2)

        top_bar = ttk.Frame(editor_panel)
        top_bar.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        ttk.Button(top_bar, text="Загрузить из файла", command=self._load_input_into_editor).pack(side="left")
        ttk.Button(top_bar, text="Обновить превью", command=self._refresh_preview).pack(side="left", padx=(8, 0))
        ttk.Button(top_bar, text="Undo", command=self._editor_undo).pack(side="left", padx=(16, 0))
        ttk.Button(top_bar, text="Redo", command=self._editor_redo).pack(side="left", padx=(8, 0))

        self.editor_text = tk.Text(
            editor_panel,
            wrap="word",
            undo=True,
            font=("DejaVu Sans Mono", 12),
            padx=14,
            pady=14,
            relief="solid",
            borderwidth=1,
        )
        self.editor_text.grid(row=1, column=0, sticky="nsew")
        self.editor_text.bind("<KeyRelease>", self._on_editor_changed)
        self.editor_text.bind("<ButtonRelease-1>", self._on_editor_changed)
        editor_scroll = ttk.Scrollbar(editor_panel, orient="vertical", command=self.editor_text.yview)
        editor_scroll.grid(row=1, column=1, sticky="ns")
        self.editor_text.configure(yscrollcommand=editor_scroll.set)

        marker_frame = ttk.LabelFrame(tools_panel, text="Маркеры", padding=10)
        marker_frame.grid(row=0, column=0, sticky="ew")
        marker_frame.columnconfigure(0, weight=1)
        ttk.Button(marker_frame, text="Одиночное фото", command=lambda: self._insert_marker("[image:1]")).grid(
            row=0, column=0, sticky="ew"
        )
        ttk.Button(marker_frame, text="2 фото в ряд", command=lambda: self._insert_marker("[images:1,2]")).grid(
            row=1, column=0, sticky="ew", pady=(6, 0)
        )
        ttk.Button(marker_frame, text="3 фото в ряд", command=lambda: self._insert_marker("[images:1,2,3]")).grid(
            row=2, column=0, sticky="ew", pady=(6, 0)
        )
        ttk.Button(marker_frame, text="4 фото в ряд", command=lambda: self._insert_marker("[images:1,2,3,4]")).grid(
            row=3, column=0, sticky="ew", pady=(6, 0)
        )
        ttk.Button(marker_frame, text="Фото слева", command=lambda: self._insert_marker("[image-left:1]")).grid(
            row=4, column=0, sticky="ew", pady=(6, 0)
        )
        ttk.Button(marker_frame, text="Фото справа", command=lambda: self._insert_marker("[image-right:1]")).grid(
            row=5, column=0, sticky="ew", pady=(6, 0)
        )

        text_tools = ttk.LabelFrame(tools_panel, text="Текст", padding=10)
        text_tools.grid(row=1, column=0, sticky="ew", pady=(10, 0))
        text_tools.columnconfigure(0, weight=1)
        ttk.Button(text_tools, text="Пустой абзац", command=self._insert_blank_paragraph).grid(
            row=0, column=0, sticky="ew"
        )
        ttk.Button(text_tools, text="Нормализовать текст", command=self._normalize_editor_text).grid(
            row=1, column=0, sticky="ew", pady=(6, 0)
        )
        ttk.Button(text_tools, text="Очистить выделение", command=self._clear_selection).grid(
            row=2, column=0, sticky="ew", pady=(6, 0)
        )
        ttk.Button(text_tools, text="Очистить весь текст", command=self._clear_editor).grid(
            row=3, column=0, sticky="ew", pady=(6, 0)
        )

        selection_tools = ttk.LabelFrame(tools_panel, text="По выбранным фото", padding=10)
        selection_tools.grid(row=2, column=0, sticky="ew", pady=(10, 0))
        selection_tools.columnconfigure(0, weight=1)
        ttk.Button(selection_tools, text="Вставить [image]", command=lambda: self._insert_selected_marker("image")).grid(
            row=0, column=0, sticky="ew"
        )
        ttk.Button(selection_tools, text="Вставить [images]", command=lambda: self._insert_selected_marker("images")).grid(
            row=1, column=0, sticky="ew", pady=(6, 0)
        )
        ttk.Button(selection_tools, text="Вставить [left]", command=lambda: self._insert_selected_marker("image-left")).grid(
            row=2, column=0, sticky="ew", pady=(6, 0)
        )
        ttk.Button(selection_tools, text="Вставить [right]", command=lambda: self._insert_selected_marker("image-right")).grid(
            row=3, column=0, sticky="ew", pady=(6, 0)
        )

        help_frame = ttk.LabelFrame(tools_panel, text="Синтаксис", padding=10)
        help_frame.grid(row=3, column=0, sticky="nsew", pady=(10, 0))
        help_frame.columnconfigure(0, weight=1)
        tools_panel.rowconfigure(3, weight=1)
        help_text = (
            "[image:1] - одно фото\n"
            "[images:1,2] - ряд\n"
            "[image-left:3] - обтекание слева\n"
            "[image-right:4] - обтекание справа\n\n"
            "Выдели фото во вкладке 'Фото' и вставляй маркеры кнопками справа."
        )
        ttk.Label(help_frame, text=help_text, justify="left").grid(row=0, column=0, sticky="nw")

    def _build_images_tab(self) -> None:
        self.images_tab.columnconfigure(0, weight=1)
        self.images_tab.rowconfigure(1, weight=1)

        image_bar = ttk.Frame(self.images_tab)
        image_bar.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        ttk.Button(image_bar, text="Обновить список", command=self._refresh_image_index_list).pack(side="left")
        ttk.Button(image_bar, text="Вставить [image]", command=lambda: self._insert_selected_marker("image")).pack(
            side="left", padx=(8, 0)
        )
        ttk.Button(image_bar, text="Вставить [images]", command=lambda: self._insert_selected_marker("images")).pack(
            side="left", padx=(8, 0)
        )
        ttk.Button(image_bar, text="Вставить [left]", command=lambda: self._insert_selected_marker("image-left")).pack(
            side="left", padx=(8, 0)
        )
        ttk.Button(image_bar, text="Вставить [right]", command=lambda: self._insert_selected_marker("image-right")).pack(
            side="left", padx=(8, 0)
        )

        paned = ttk.Panedwindow(self.images_tab, orient="horizontal")
        paned.grid(row=1, column=0, sticky="nsew")

        list_panel = ttk.Frame(paned, padding=8)
        grid_panel = ttk.Frame(paned, padding=8)
        detail_panel = ttk.Frame(paned, padding=8)
        list_panel.columnconfigure(0, weight=1)
        list_panel.rowconfigure(0, weight=1)
        grid_panel.columnconfigure(0, weight=1)
        grid_panel.rowconfigure(0, weight=1)
        detail_panel.columnconfigure(0, weight=1)
        detail_panel.rowconfigure(1, weight=1)
        paned.add(list_panel, weight=2)
        paned.add(grid_panel, weight=3)
        paned.add(detail_panel, weight=2)

        self.images_tree = ttk.Treeview(
            list_panel,
            columns=("index", "name"),
            show="headings",
            selectmode="extended",
        )
        self.images_tree.heading("index", text="#")
        self.images_tree.heading("name", text="Файл")
        self.images_tree.column("index", width=60, anchor="center")
        self.images_tree.column("name", width=260, anchor="w")
        self.images_tree.grid(row=0, column=0, sticky="nsew")
        self.images_tree.tag_configure("used", background="#e6f4ea")
        self.images_tree.bind("<<TreeviewSelect>>", self._on_tree_select)
        tree_scroll = ttk.Scrollbar(list_panel, orient="vertical", command=self.images_tree.yview)
        tree_scroll.grid(row=0, column=1, sticky="ns")
        self.images_tree.configure(yscrollcommand=tree_scroll.set)

        self.thumbnail_canvas = tk.Canvas(grid_panel, highlightthickness=0, background="#f7f4ef")
        self.thumbnail_canvas.grid(row=0, column=0, sticky="nsew")
        grid_scroll = ttk.Scrollbar(grid_panel, orient="vertical", command=self.thumbnail_canvas.yview)
        grid_scroll.grid(row=0, column=1, sticky="ns")
        self.thumbnail_canvas.configure(yscrollcommand=grid_scroll.set)
        self.thumbnail_inner = ttk.Frame(self.thumbnail_canvas)
        self.thumbnail_window_id = self.thumbnail_canvas.create_window((0, 0), window=self.thumbnail_inner, anchor="nw")
        self.thumbnail_inner.bind("<Configure>", self._on_thumbnail_inner_configure)
        self.thumbnail_canvas.bind("<Configure>", self._on_thumbnail_canvas_configure)

        ttk.Label(detail_panel, text="Предпросмотр фото", font=("TkDefaultFont", 11, "bold")).grid(
            row=0, column=0, sticky="w"
        )
        self.detail_preview_label = tk.Label(
            detail_panel,
            text="Нет выбранного фото",
            relief="solid",
            borderwidth=1,
            width=40,
            height=18,
            bg="#fbfaf7",
            justify="center",
        )
        self.detail_preview_label.grid(row=1, column=0, sticky="nsew", pady=(8, 8))
        ttk.Label(detail_panel, textvariable=self.detail_caption_var, wraplength=320, justify="left").grid(
            row=2, column=0, sticky="ew"
        )
        ttk.Label(detail_panel, textvariable=self.detail_usage_var, wraplength=320, justify="left").grid(
            row=3, column=0, sticky="ew", pady=(6, 0)
        )

    def _build_preview_tab(self) -> None:
        self.preview_tab.columnconfigure(0, weight=1)
        self.preview_tab.rowconfigure(0, weight=1)
        if HTMLScrolledText is not None:
            self.preview_widget = HTMLScrolledText(self.preview_tab, html="", background="#f4ede2")
        else:
            self.preview_widget = tk.Text(self.preview_tab, wrap="word", state="disabled")
        self.preview_widget.grid(row=0, column=0, sticky="nsew")

    def _build_html_tab(self) -> None:
        self.html_tab.columnconfigure(0, weight=1)
        self.html_tab.rowconfigure(0, weight=1)
        self.html_text = tk.Text(self.html_tab, wrap="word", state="disabled")
        self.html_text.grid(row=0, column=0, sticky="nsew")
        html_scroll = ttk.Scrollbar(self.html_tab, orient="vertical", command=self.html_text.yview)
        html_scroll.grid(row=0, column=1, sticky="ns")
        self.html_text.configure(yscrollcommand=html_scroll.set)

    def _build_log_tab(self) -> None:
        self.log_tab.columnconfigure(0, weight=1)
        self.log_tab.rowconfigure(0, weight=1)
        self.log_text = tk.Text(self.log_tab, wrap="word", state="disabled")
        self.log_text.grid(row=0, column=0, sticky="nsew")
        log_scroll = ttk.Scrollbar(self.log_tab, orient="vertical", command=self.log_text.yview)
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

    def _editor_undo(self) -> None:
        try:
            self.editor_text.edit_undo()
        except tk.TclError:
            return
        self._schedule_editor_analysis()

    def _editor_redo(self) -> None:
        try:
            self.editor_text.edit_redo()
        except tk.TclError:
            return
        self._schedule_editor_analysis()

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
        self._schedule_editor_analysis()

    def _insert_marker(self, marker: str) -> None:
        self.editor_text.insert("insert", f"\n\n{marker}\n\n")
        self.editor_text.focus_set()
        self._schedule_editor_analysis()

    def _insert_blank_paragraph(self) -> None:
        self.editor_text.insert("insert", "\n\n")
        self.editor_text.focus_set()
        self._schedule_editor_analysis()

    def _normalize_editor_text(self) -> None:
        normalized = news_builder.normalize_body(self._editor_body())
        self.editor_text.delete("1.0", "end")
        self.editor_text.insert("1.0", normalized)
        self._append_log("Normalized editor text.")
        self._schedule_editor_analysis()

    def _clear_selection(self) -> None:
        try:
            start = self.editor_text.index("sel.first")
            end = self.editor_text.index("sel.last")
        except tk.TclError:
            messagebox.showinfo("News Builder", "Сначала выдели текст в редакторе.")
            return
        self.editor_text.delete(start, end)
        self._schedule_editor_analysis()

    def _clear_editor(self) -> None:
        self.editor_text.delete("1.0", "end")
        self._schedule_editor_analysis()

    def _on_editor_changed(self, _event=None) -> None:
        self._schedule_editor_analysis()

    def _schedule_editor_analysis(self) -> None:
        if self._editor_analysis_job is not None:
            self.root.after_cancel(self._editor_analysis_job)
        self._editor_analysis_job = self.root.after(150, self._analyze_editor_content)

    def _analyze_editor_content(self) -> None:
        self._editor_analysis_job = None
        body = self._editor_body()
        self.used_image_indices = news_builder.extract_referenced_image_indices(body)
        if not body:
            self.editor_status_var.set("Редактор пустой.")
            self.used_images_var.set("Используемые фото: нет")
            self._update_image_highlights()
            return

        try:
            news_builder.parse_blocks(body)
        except Exception as exc:
            self.editor_status_var.set(f"Ошибка маркеров: {exc}")
        else:
            self.editor_status_var.set("Редактор готов. Маркеры корректны.")

        if self.used_image_indices:
            used_summary = ", ".join(str(index) for index in sorted(self.used_image_indices))
            self.used_images_var.set(f"Используемые фото: {used_summary}")
        else:
            self.used_images_var.set("Используемые фото: нет")
        self._update_image_highlights()

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
            messagebox.showinfo("News Builder", "Сначала выбери фото в списке или в сетке.")
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
        for widget in self.thumbnail_inner.winfo_children():
            widget.destroy()
        self.image_records = []
        self.thumbnail_cards.clear()
        self.thumbnail_photo_refs.clear()
        self.thumbnail_name_labels.clear()
        self.thumbnail_index_labels.clear()
        self.detail_photo_ref = None
        self.selected_image_indices = tuple()

        images_dir_value = self.images_dir_var.get().strip()
        if not images_dir_value:
            self.detail_caption_var.set("Укажи папку с фото.")
            self.detail_usage_var.set("")
            self.detail_preview_label.configure(image="", text="Нет папки с фото")
            return

        images_dir = Path(images_dir_value).expanduser()
        if not images_dir.is_dir():
            self.detail_caption_var.set("Папка с фото не найдена.")
            self.detail_usage_var.set("")
            self.detail_preview_label.configure(image="", text="Папка не найдена")
            return

        images = news_builder.discover_images(images_dir)
        self.image_records = [(index, image_path) for index, image_path in enumerate(images, start=1)]
        for index, image_path in self.image_records:
            self.images_tree.insert("", "end", iid=str(index), values=(index, image_path.name))
            self._add_thumbnail_card(index, image_path)

        self._append_log(f"Indexed {len(images)} image(s) from {images_dir}")
        self._update_image_highlights()
        if self.image_records:
            self._set_selected_image_indices((1,))
        else:
            self.detail_caption_var.set("В папке нет поддерживаемых изображений.")
            self.detail_usage_var.set("")
            self.detail_preview_label.configure(image="", text="Нет изображений")

    def _on_thumbnail_inner_configure(self, _event=None) -> None:
        self.thumbnail_canvas.configure(scrollregion=self.thumbnail_canvas.bbox("all"))

    def _on_thumbnail_canvas_configure(self, event) -> None:
        self.thumbnail_canvas.itemconfigure(self.thumbnail_window_id, width=event.width)

    def _create_thumbnail_photo(self, path: Path, size: tuple[int, int]):
        if Image is None or ImageOps is None or ImageTk is None:
            return None
        try:
            with Image.open(path) as raw_image:
                image = ImageOps.exif_transpose(raw_image)
                image.thumbnail(size)
                return ImageTk.PhotoImage(image)
        except Exception:
            return None

    def _add_thumbnail_card(self, index: int, image_path: Path) -> None:
        row = (index - 1) // 3
        column = (index - 1) % 3
        card = tk.Frame(self.thumbnail_inner, bd=1, relief="solid", bg="#f7f4ef", padx=8, pady=8)
        card.grid(row=row, column=column, sticky="nsew", padx=6, pady=6)
        self.thumbnail_inner.grid_columnconfigure(column, weight=1)
        self.thumbnail_cards[index] = card

        index_label = tk.Label(card, text=f"#{index}", bg="#f7f4ef", font=("TkDefaultFont", 10, "bold"))
        index_label.pack(anchor="w")
        self.thumbnail_index_labels[index] = index_label

        photo = self._create_thumbnail_photo(image_path, THUMBNAIL_SIZE)
        image_label = tk.Label(card, bg="#f7f4ef")
        if photo is not None:
            image_label.configure(image=photo)
            self.thumbnail_photo_refs[index] = photo
        else:
            image_label.configure(text=image_path.name[:18], width=20, height=6)
        image_label.pack(fill="both", expand=True, pady=(6, 6))

        name_label = tk.Label(
            card,
            text=image_path.name,
            justify="left",
            anchor="w",
            wraplength=150,
            bg="#f7f4ef",
        )
        name_label.pack(fill="x")
        self.thumbnail_name_labels[index] = name_label

        for widget in (card, index_label, image_label, name_label):
            widget.bind("<Button-1>", lambda _event, idx=index: self._on_thumbnail_click(idx))

    def _on_thumbnail_click(self, index: int) -> None:
        self._set_selected_image_indices((index,))

    def _on_tree_select(self, _event=None) -> None:
        indices = self._selected_image_indices()
        self.selected_image_indices = indices
        self._update_image_highlights()
        self._update_detail_preview(indices[0] if indices else None)

    def _set_selected_image_indices(self, indices: tuple[int, ...]) -> None:
        self.selected_image_indices = indices
        self.images_tree.selection_set([str(index) for index in indices if str(index) in self.images_tree.get_children()])
        if indices:
            self.images_tree.focus(str(indices[0]))
            self.images_tree.see(str(indices[0]))
        self._update_image_highlights()
        self._update_detail_preview(indices[0] if indices else None)

    def _card_style_colors(self, index: int) -> tuple[str, str]:
        if index in self.selected_image_indices:
            return "#dbeafe", "#1d4ed8"
        if index in self.used_image_indices:
            return "#e8f5e9", "#2e7d32"
        return "#f7f4ef", "#cfc7ba"

    def _update_image_highlights(self) -> None:
        for item_id in self.images_tree.get_children():
            index = int(item_id)
            tags: list[str] = []
            if index in self.used_image_indices:
                tags.append("used")
            self.images_tree.item(item_id, tags=tuple(tags))

        for index, card in self.thumbnail_cards.items():
            background, border = self._card_style_colors(index)
            card.configure(bg=background, highlightbackground=border, highlightcolor=border, highlightthickness=2)
            for widget in (
                self.thumbnail_index_labels.get(index),
                self.thumbnail_name_labels.get(index),
            ):
                if widget is not None:
                    widget.configure(bg=background)
            for child in card.winfo_children():
                if isinstance(child, tk.Label):
                    child.configure(bg=background)

        if self.selected_image_indices:
            self._update_detail_preview(self.selected_image_indices[0])

    def _update_detail_preview(self, index: int | None) -> None:
        if index is None:
            self.detail_caption_var.set("Выбери фото в списке или в сетке миниатюр.")
            self.detail_usage_var.set("")
            self.detail_preview_label.configure(image="", text="Нет выбранного фото")
            self.detail_photo_ref = None
            return

        path = next((path for current_index, path in self.image_records if current_index == index), None)
        if path is None:
            return

        photo = self._create_thumbnail_photo(path, DETAIL_PREVIEW_SIZE)
        if photo is not None:
            self.detail_preview_label.configure(image=photo, text="")
            self.detail_photo_ref = photo
        else:
            self.detail_preview_label.configure(image="", text=path.name)
            self.detail_photo_ref = None

        self.detail_caption_var.set(f"Фото #{index}: {path.name}")
        if index in self.used_image_indices:
            self.detail_usage_var.set("Статус: уже используется в тексте.")
        else:
            self.detail_usage_var.set("Статус: пока не используется в тексте.")

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
                self._set_preview_html(
                    payload.full_html
                    or news_builder.render_full_html_document(self.title_var.get(), payload.fragment_html)
                )
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
        if sys.platform == "darwin":
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
        self._schedule_editor_analysis()

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
