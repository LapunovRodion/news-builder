#!/usr/bin/env python3
"""Build an HTML news fragment from text and images, then upload images over SSH."""

from __future__ import annotations

import argparse
import html
import json
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime
import unicodedata
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable
from urllib.parse import urljoin
from xml.etree import ElementTree

try:
    import paramiko  # type: ignore
except ImportError:
    paramiko = None

try:
    from docx import Document  # type: ignore
except ImportError:
    Document = None

try:
    from PIL import Image, ImageOps  # type: ignore
except ImportError:
    Image = None
    ImageOps = None


MARKER_PATTERN = re.compile(r"\[(image|images|image-left|image-right):([0-9,\s]+)\]")
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".tif", ".tiff"}
CYRILLIC_TRANSLIT = {
    "а": "a",
    "б": "b",
    "в": "v",
    "г": "g",
    "д": "d",
    "е": "e",
    "ё": "e",
    "ж": "zh",
    "з": "z",
    "и": "i",
    "й": "i",
    "к": "k",
    "л": "l",
    "м": "m",
    "н": "n",
    "о": "o",
    "п": "p",
    "р": "r",
    "с": "s",
    "т": "t",
    "у": "u",
    "ф": "f",
    "х": "h",
    "ц": "ts",
    "ч": "ch",
    "ш": "sh",
    "щ": "sch",
    "ъ": "",
    "ы": "y",
    "ь": "",
    "э": "e",
    "ю": "yu",
    "я": "ya",
    "і": "i",
    "ї": "yi",
    "є": "e",
    "ў": "u",
}
DEFAULT_STYLES = {
    "container": (
        "max-width: 920px; margin: 0 auto; padding: 34px 38px; "
        "font-family: Georgia, 'Times New Roman', serif; color: #1d1d1d; line-height: 1.78; "
        "background: linear-gradient(180deg, #fffdf9 0%, #f7f1e7 100%); "
        "border: 1px solid #e7dcc8; border-radius: 26px; "
        "box-shadow: 0 20px 50px rgba(76, 54, 28, 0.10);"
    ),
    "title": (
        "margin: 0 0 26px; font-size: 40px; line-height: 1.12; font-weight: 700; "
        "letter-spacing: -0.03em; color: #24180d;"
    ),
    "paragraph": "margin: 0 0 18px; font-size: 19px; color: #2c241c;",
    "lead": "margin: 0 0 22px; font-size: 21px; color: #3f2f22; font-weight: 500;",
    "image_wrapper": "margin: 26px 0;",
    "image": (
        "display: block; width: 100%; height: auto; border-radius: 12px; "
        "border: 1px solid rgba(91, 62, 33, 0.10); "
        "box-shadow: 0 12px 28px rgba(53, 34, 16, 0.16);"
    ),
    "row_wrapper": "margin: 28px 0; display: flex; gap: 14px; align-items: stretch;",
    "row_item": "flex: 1 1 0; min-width: 0;",
    "row_image": (
        "display: block; width: 100%; height: auto; border-radius: 12px; "
        "border: 1px solid rgba(91, 62, 33, 0.10); box-shadow: 0 10px 24px rgba(53, 34, 16, 0.14);"
    ),
    "float_left": (
        "float: left; width: 42%; max-width: 360px; margin: 8px 22px 14px 0; display: block; "
        "border-radius: 16px; border: 1px solid rgba(91, 62, 33, 0.10); "
        "box-shadow: 0 12px 28px rgba(53, 34, 16, 0.16);"
    ),
    "float_right": (
        "float: right; width: 42%; max-width: 360px; margin: 8px 0 14px 22px; display: block; "
        "border-radius: 16px; border: 1px solid rgba(91, 62, 33, 0.10); "
        "box-shadow: 0 12px 28px rgba(53, 34, 16, 0.16);"
    ),
    "clear": "clear: both; height: 0; overflow: hidden;",
}
DEFAULT_CONFIG = {
    "image": {
        "max_width": 1600,
        "max_bytes": 500 * 1024,
        "jpeg_quality": 85,
        "jpeg_min_quality": 50,
        "webp_quality": 85,
        "webp_min_quality": 50,
    },
    "styles": DEFAULT_STYLES,
}


@dataclass(frozen=True)
class ParagraphBlock:
    text: str


@dataclass(frozen=True)
class ImageLayoutBlock:
    layout: str
    indices: tuple[int, ...]


@dataclass(frozen=True)
class PreparedImage:
    source_path: Path
    processed_path: Path
    remote_name: str
    public_url: str


@dataclass(frozen=True)
class BuildResult:
    fragment_output_path: Path
    full_output_path: Path | None
    fragment_html: str
    full_html: str | None
    news_folder: str
    remote_path: str
    public_base_url: str


LogFn = Callable[[str], None]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate an inline-styled HTML news fragment and upload images over SSH."
    )
    parser.add_argument("--input", required=True, help="Path to .docx, .txt, or .md input.")
    parser.add_argument("--images-dir", required=True, help="Directory with source images.")
    parser.add_argument("--output", required=True, help="Path to generated HTML fragment.")
    parser.add_argument("--full-output", help="Optional path to a full standalone HTML preview page.")
    parser.add_argument("--title", help="Override auto-detected title.")
    parser.add_argument("--remote-host", required=True, help="SSH host.")
    parser.add_argument("--remote-user", required=True, help="SSH username.")
    parser.add_argument("--remote-path", required=True, help="Base remote directory for news image folders.")
    parser.add_argument("--remote-port", type=int, default=22, help="SSH port. Default: 22.")
    parser.add_argument("--ssh-key", help="Path to SSH private key.")
    parser.add_argument("--ssh-password", help="SSH password. Used when no key is provided.")
    parser.add_argument(
        "--public-base-url",
        required=True,
        help="Public base URL that maps to the remote news image folders, e.g. https://site.example/news/2026/03/",
    )
    parser.add_argument(
        "--news-slug",
        help="Optional folder name for this news item. By default it is generated from the title.",
    )
    parser.add_argument(
        "--style-config",
        help="Optional JSON file overriding inline styles and image limits.",
    )
    parser.add_argument(
        "--keep-temp",
        action="store_true",
        help="Keep processed temp images directory for inspection.",
    )
    return parser.parse_args()


def log_message(logger: LogFn | None, message: str) -> None:
    if logger is not None:
        logger(message)


def require_dependency(name: str, module: object) -> None:
    if module is None:
        raise RuntimeError(
            f"Missing dependency: {name}. Install it from requirements-news-builder.txt before running."
        )


def load_config(path: str | None) -> dict:
    config = json.loads(json.dumps(DEFAULT_CONFIG))
    if not path:
        return config

    with open(path, "r", encoding="utf-8") as handle:
        user_config = json.load(handle)

    image_config = user_config.get("image", {})
    style_config = user_config.get("styles", {})
    config["image"].update(image_config)
    config["styles"].update(style_config)
    return config


def read_input_document(path: Path) -> tuple[str | None, str]:
    suffix = path.suffix.lower()
    if suffix in {".txt", ".md"}:
        raw = path.read_text(encoding="utf-8")
        title, body = extract_title_from_plain_text(raw, suffix == ".md")
        return title, body
    if suffix == ".docx":
        if Document is not None:
            return read_docx_with_python_docx(path)
        return read_docx_with_stdlib(path)
    raise ValueError(f"Unsupported input format: {path.suffix}")


def normalize_text_content(value: str) -> str:
    normalized = (
        value.replace("\r\n", "\n")
        .replace("\r", "\n")
        .replace("\u00a0", " ")
        .replace("\u200b", "")
        .replace("\ufeff", "")
    )
    normalized = re.sub(r"[ \t]+", " ", normalized)
    normalized = re.sub(r"\n{3,}", "\n\n", normalized)
    return normalized.strip()


def normalize_title(value: str | None) -> str:
    return normalize_text_content(value or "").replace("\n", " ").strip()


def normalize_body(value: str) -> str:
    return normalize_text_content(value)


def build_body_from_paragraphs(paragraphs: Iterable[str]) -> str:
    normalized_paragraphs: list[str] = []
    for paragraph in paragraphs:
        cleaned = normalize_text_content(paragraph)
        if cleaned:
            normalized_paragraphs.append(cleaned)
    return "\n\n".join(normalized_paragraphs)


def extract_title_from_plain_text(raw: str, is_markdown: bool) -> tuple[str | None, str]:
    lines = raw.splitlines()
    title = None
    title_index = None
    for idx, line in enumerate(lines):
        stripped = line.strip()
        if not stripped:
            continue
        if is_markdown and stripped.startswith("# "):
            title = stripped[2:].strip()
            title_index = idx
            break
        title = stripped
        title_index = idx
        break

    if title_index is None:
        return None, ""

    body_lines = lines[:]
    body_lines.pop(title_index)
    body = "\n".join(body_lines).strip()
    return normalize_title(title), normalize_body(body)


def read_docx_with_python_docx(path: Path) -> tuple[str | None, str]:
    document = Document(str(path))
    title = None
    body_lines: list[str] = []
    title_consumed = False

    for paragraph in document.paragraphs:
        text = paragraph.text.strip()
        if not text:
            if body_lines and body_lines[-1] != "":
                body_lines.append("")
            continue

        style_name = getattr(paragraph.style, "name", "") or ""
        if not title and style_name.lower().startswith("heading"):
            title = text
            title_consumed = True
            continue

        if not title:
            title = text
            title_consumed = True
            continue

        if title_consumed and text == title and not body_lines:
            continue
        body_lines.append(text)

    return normalize_title(title), build_body_from_paragraphs(body_lines)


def read_docx_with_stdlib(path: Path) -> tuple[str | None, str]:
    paragraphs: list[str] = []
    with zipfile.ZipFile(path) as archive:
        with archive.open("word/document.xml") as handle:
            tree = ElementTree.parse(handle)

    namespace = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
    for paragraph in tree.findall(".//w:p", namespace):
        chunks = [node.text or "" for node in paragraph.findall(".//w:t", namespace)]
        text = "".join(chunks).strip()
        if text:
            paragraphs.append(text)
        elif paragraphs and paragraphs[-1] != "":
            paragraphs.append("")

    title = next((line for line in paragraphs if line), None)
    body = [line for idx, line in enumerate(paragraphs) if idx != 0]
    return normalize_title(title), build_body_from_paragraphs(body)


def parse_blocks(body: str) -> list[ParagraphBlock | ImageLayoutBlock]:
    if not body.strip():
        return []

    blocks: list[ParagraphBlock | ImageLayoutBlock] = []
    cursor = 0
    for match in MARKER_PATTERN.finditer(body):
        prefix = body[cursor:match.start()]
        blocks.extend(parse_paragraph_blocks(prefix))
        blocks.append(parse_marker_block(match.group(1), match.group(2)))
        cursor = match.end()

    suffix = body[cursor:]
    blocks.extend(parse_paragraph_blocks(suffix))
    return blocks


def extract_referenced_image_indices(body: str) -> set[int]:
    indices: set[int] = set()
    for match in MARKER_PATTERN.finditer(str(body or "")):
        payload = match.group(2)
        for part in payload.split(","):
            part = part.strip()
            if part.isdigit():
                indices.add(int(part))
    return indices


def parse_paragraph_blocks(chunk: str) -> list[ParagraphBlock]:
    return [ParagraphBlock(text=paragraph) for paragraph in split_paragraphs(chunk) if paragraph]


def parse_marker_block(kind: str, payload: str) -> ImageLayoutBlock:
    indices = tuple(int(part.strip()) for part in payload.split(",") if part.strip())
    if not indices:
        raise ValueError(f"Marker [{kind}:{payload}] does not contain any image numbers.")
    if kind == "image" and len(indices) != 1:
        raise ValueError("Marker [image:N] must contain exactly one image number.")
    if kind in {"image-left", "image-right"} and len(indices) != 1:
        raise ValueError(f"Marker [{kind}:N] must contain exactly one image number.")
    return ImageLayoutBlock(layout=kind, indices=indices)


def split_paragraphs(chunk: str) -> list[str]:
    normalized = chunk.replace("\r\n", "\n")
    groups = re.split(r"\n\s*\n", normalized)
    paragraphs: list[str] = []
    for group in groups:
        lines = [line.strip() for line in group.splitlines() if line.strip()]
        if lines:
            paragraphs.append(" ".join(lines))
    return paragraphs


def natural_sort_key(path: Path) -> list[object]:
    return [int(part) if part.isdigit() else part.lower() for part in re.split(r"(\d+)", path.name)]


def discover_images(images_dir: Path) -> list[Path]:
    images = [path for path in images_dir.iterdir() if path.is_file() and path.suffix.lower() in IMAGE_EXTENSIONS]
    return sorted(images, key=natural_sort_key)


def slugify(value: str) -> str:
    transliterated = "".join(CYRILLIC_TRANSLIT.get(char, char) for char in value.lower())
    transliterated = transliterated.replace("&", " and ")
    normalized = unicodedata.normalize("NFKD", transliterated)
    ascii_value = normalized.encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", ascii_value).strip("-").lower()
    return slug or "news"


def build_news_folder_name(title: str, override: str | None = None) -> str:
    if override and override.strip():
        folder_name = slugify(override.strip())
        if folder_name:
            return folder_name

    base_slug = slugify(title)
    if base_slug != "news":
        return base_slug

    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return f"news-{timestamp}"


def build_news_public_base_url(base_url: str, news_folder: str) -> str:
    return urljoin(base_url.rstrip("/") + "/", news_folder + "/")


def build_news_remote_path(base_path: str, news_folder: str) -> str:
    return posix_join(base_path.rstrip("/"), news_folder)


def normalize_runtime_args(args: argparse.Namespace, title: str) -> tuple[str, str]:
    news_folder = build_news_folder_name(title, getattr(args, "news_slug", None))
    remote_news_path = build_news_remote_path(args.remote_path, news_folder)
    public_news_base_url = build_news_public_base_url(args.public_base_url, news_folder)
    args.remote_path = remote_news_path
    args.public_base_url = public_news_base_url
    return news_folder, remote_news_path


def derive_full_output_path(fragment_output_path: Path, explicit_path: str | None = None) -> Path:
    if explicit_path:
        return Path(explicit_path).expanduser().resolve()
    return fragment_output_path.with_name(f"{fragment_output_path.stem}_preview.html")


def ensure_markers_have_images(
    blocks: Iterable[ParagraphBlock | ImageLayoutBlock],
    total_images: int,
) -> None:
    for block in blocks:
        if isinstance(block, ImageLayoutBlock):
            for index in block.indices:
                if index < 1 or index > total_images:
                    marker = ",".join(str(value) for value in block.indices)
                    raise ValueError(
                        f"Marker [{block.layout}:{marker}] has no matching file. Available images: {total_images}."
                    )


def collect_used_indices(blocks: Iterable[ParagraphBlock | ImageLayoutBlock]) -> set[int]:
    used: set[int] = set()
    for block in blocks:
        if isinstance(block, ImageLayoutBlock):
            used.update(block.indices)
    return used


def processed_extension(source_path: Path, output_format: str | None) -> str:
    if output_format == "JPEG":
        return ".jpg"
    if output_format == "WEBP":
        return ".webp"
    return source_path.suffix.lower()


def process_single_image(
    source_path: Path,
    destination_dir: Path,
    output_stem: str,
    image_config: dict,
) -> Path:
    require_dependency("Pillow", Image)
    require_dependency("Pillow", ImageOps)

    max_width = int(image_config["max_width"])
    max_bytes = int(image_config["max_bytes"])
    jpeg_quality = int(image_config["jpeg_quality"])
    jpeg_min_quality = int(image_config["jpeg_min_quality"])
    webp_quality = int(image_config["webp_quality"])
    webp_min_quality = int(image_config["webp_min_quality"])

    with Image.open(source_path) as raw_image:
        image = ImageOps.exif_transpose(raw_image)
        if image.width > max_width:
            new_height = max(1, int(image.height * (max_width / image.width)))
            image = image.resize((max_width, new_height), Image.Resampling.LANCZOS)

        source_suffix = source_path.suffix.lower()
        source_format = (image.format or "").upper() or source_suffix.replace(".", "").upper()
        if source_format in {"JPG", "JPEG"}:
            source_format = "JPEG"
        elif source_format == "WEBP":
            source_format = "WEBP"
        elif source_format == "PNG":
            source_format = "PNG"
        else:
            source_format = "JPEG"
            if image.mode not in {"RGB", "L"}:
                image = image.convert("RGB")

        qualities: list[int | None]
        save_options: dict[str, object]
        if source_format == "JPEG":
            if image.mode not in {"RGB", "L"}:
                image = image.convert("RGB")
            qualities = list(range(jpeg_quality, jpeg_min_quality - 1, -5))
            save_options = {"format": "JPEG", "optimize": True, "progressive": True}
        elif source_format == "WEBP":
            if image.mode not in {"RGB", "RGBA", "L"}:
                image = image.convert("RGB")
            qualities = list(range(webp_quality, webp_min_quality - 1, -5))
            save_options = {"format": "WEBP", "method": 6}
        else:
            qualities = [None]
            save_options = {"format": "PNG", "optimize": True, "compress_level": 9}

        last_path = destination_dir / f"{output_stem}{processed_extension(source_path, save_options['format'])}"
        for quality in qualities:
            current_options = dict(save_options)
            if quality is not None:
                current_options["quality"] = quality
            image.save(last_path, **current_options)
            if last_path.stat().st_size <= max_bytes or quality == qualities[-1]:
                return last_path

    return last_path


def prepare_images(
    source_images: list[Path],
    title_slug: str,
    public_base_url: str,
    image_config: dict,
    work_dir: Path,
    logger: LogFn | None = None,
) -> list[PreparedImage]:
    prepared: list[PreparedImage] = []
    for idx, source_path in enumerate(source_images, start=1):
        stem = f"{title_slug}-{idx:02d}"
        log_message(logger, f"Processing image {idx}/{len(source_images)}: {source_path.name}")
        processed_path = process_single_image(source_path, work_dir, stem, image_config)
        prepared.append(
            PreparedImage(
                source_path=source_path,
                processed_path=processed_path,
                remote_name=processed_path.name,
                public_url=urljoin(public_base_url.rstrip("/") + "/", processed_path.name),
            )
        )
    return prepared


def upload_images(
    prepared_images: list[PreparedImage],
    args: argparse.Namespace,
    logger: LogFn | None = None,
) -> None:
    if getattr(args, "ssh_password", None):
        if paramiko is None:
            raise RuntimeError(
                "Password-based SSH upload requires paramiko. Install requirements-news-builder.txt."
            )
        upload_with_paramiko(prepared_images, args, logger)
        return

    if paramiko is not None:
        upload_with_paramiko(prepared_images, args, logger)
        return
    upload_with_system_ssh(prepared_images, args, logger)


def upload_with_paramiko(
    prepared_images: list[PreparedImage],
    args: argparse.Namespace,
    logger: LogFn | None = None,
) -> None:
    auth_description = "password" if getattr(args, "ssh_password", None) else "SSH key"
    log_message(
        logger,
        f"Connecting to {args.remote_user}@{args.remote_host}:{args.remote_port} with paramiko via {auth_description}",
    )
    connect_kwargs = {
        "hostname": args.remote_host,
        "port": args.remote_port,
        "username": args.remote_user,
        "look_for_keys": False,
        "allow_agent": False,
    }
    if getattr(args, "ssh_password", None):
        connect_kwargs["password"] = args.ssh_password
    elif getattr(args, "ssh_key", None):
        connect_kwargs["key_filename"] = args.ssh_key
    else:
        raise RuntimeError("SSH authentication is not configured. Provide --ssh-key or --ssh-password.")

    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(**connect_kwargs)
    try:
        sftp = client.open_sftp()
        try:
            ensure_remote_directory_sftp(sftp, args.remote_path)
            for image in prepared_images:
                remote_path = posix_join(args.remote_path, image.remote_name)
                log_message(logger, f"Uploading {image.remote_name} -> {remote_path}")
                sftp.put(str(image.processed_path), remote_path)
        finally:
            sftp.close()
    finally:
        client.close()


def ensure_remote_directory_sftp(sftp, remote_path: str) -> None:
    parts = [part for part in remote_path.split("/") if part]
    current = "/" if remote_path.startswith("/") else ""
    for part in parts:
        current = posix_join(current, part)
        try:
            sftp.stat(current)
        except FileNotFoundError:
            sftp.mkdir(current)


def upload_with_system_ssh(
    prepared_images: list[PreparedImage],
    args: argparse.Namespace,
    logger: LogFn | None = None,
) -> None:
    if not getattr(args, "ssh_key", None):
        raise RuntimeError("System ssh/scp upload requires an SSH key path.")

    ssh_binary = shutil.which("ssh")
    scp_binary = shutil.which("scp")
    if not ssh_binary or not scp_binary:
        raise RuntimeError("paramiko is not installed and ssh/scp are not available in PATH.")

    ssh_target = f"{args.remote_user}@{args.remote_host}"
    log_message(
        logger,
        f"Connecting to {ssh_target}:{args.remote_port} with system ssh/scp",
    )
    mkdir_command = [
        ssh_binary,
        "-i",
        args.ssh_key,
        "-p",
        str(args.remote_port),
        ssh_target,
        f"mkdir -p {shlex.quote(args.remote_path)}",
    ]
    subprocess.run(mkdir_command, check=True)

    for image in prepared_images:
        remote_target = f"{ssh_target}:{posix_join(args.remote_path, image.remote_name)}"
        log_message(logger, f"Uploading {image.remote_name} -> {remote_target}")
        scp_command = [
            scp_binary,
            "-i",
            args.ssh_key,
            "-P",
            str(args.remote_port),
            str(image.processed_path),
            remote_target,
        ]
        subprocess.run(scp_command, check=True)


def posix_join(base: str, name: str) -> str:
    if not base:
        return name
    if base.endswith("/"):
        return f"{base}{name}"
    return f"{base}/{name}"


def render_html(
    title: str,
    blocks: list[ParagraphBlock | ImageLayoutBlock],
    prepared_images: list[PreparedImage],
    styles: dict,
) -> str:
    lines = [f'<div style="{styles["container"]}">', f'  <h1 style="{styles["title"]}">{escape_text(title)}</h1>']
    active_float = False
    first_paragraph = True
    for block in blocks:
        if isinstance(block, ParagraphBlock):
            paragraph_style = styles["lead"] if first_paragraph else styles["paragraph"]
            lines.append(f'  <p style="{paragraph_style}">{escape_text(block.text)}</p>')
            first_paragraph = False
            continue

        if active_float and block.layout not in {"image-left", "image-right"}:
            lines.append(f'  <div style="{styles["clear"]}"></div>')
            active_float = False

        if block.layout == "image":
            image = prepared_images[block.indices[0] - 1]
            alt = f"{title} - image {block.indices[0]}"
            lines.append(f'  <p style="{styles["image_wrapper"]}">')
            lines.append(
                f'    <img src="{html.escape(image.public_url, quote=True)}" '
                f'alt="{html.escape(alt, quote=True)}" style="{styles["image"]}" loading="lazy" />'
            )
            lines.append("  </p>")
            continue

        if block.layout == "images":
            lines.append(f'  <div style="{styles["row_wrapper"]}">')
            for index in block.indices:
                image = prepared_images[index - 1]
                alt = f"{title} - image {index}"
                lines.append(f'    <div style="{styles["row_item"]}">')
                lines.append(
                    f'      <img src="{html.escape(image.public_url, quote=True)}" '
                    f'alt="{html.escape(alt, quote=True)}" style="{styles["row_image"]}" loading="lazy" />'
                )
                lines.append("    </div>")
            lines.append("  </div>")
            continue

        if block.layout in {"image-left", "image-right"}:
            image = prepared_images[block.indices[0] - 1]
            alt = f"{title} - image {block.indices[0]}"
            image_style = styles["float_left"] if block.layout == "image-left" else styles["float_right"]
            lines.append(
                f'  <img src="{html.escape(image.public_url, quote=True)}" '
                f'alt="{html.escape(alt, quote=True)}" style="{image_style}" loading="lazy" />'
            )
            active_float = True
            continue

        raise ValueError(f"Unsupported image layout: {block.layout}")

    if active_float:
        lines.append(f'  <div style="{styles["clear"]}"></div>')
    lines.append("</div>")
    return "\n".join(lines) + "\n"


def render_full_html_document(title: str, fragment_html: str) -> str:
    escaped_title = html.escape(title, quote=False)
    return (
        "<!DOCTYPE html>\n"
        '<html lang="ru">\n'
        "<head>\n"
        '  <meta charset="utf-8" />\n'
        '  <meta name="viewport" content="width=device-width, initial-scale=1" />\n'
        f"  <title>{escaped_title}</title>\n"
        "</head>\n"
        '  <body style="margin: 0; padding: 28px; background: #efe7da;">\n'
        f"{fragment_html}"
        "  </body>\n"
        "</html>\n"
    )


def escape_text(value: str) -> str:
    return html.escape(value, quote=False)


def write_output(path: Path, rendered_html: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(rendered_html, encoding="utf-8")


def print_warnings(
    source_images: list[Path],
    used_indices: set[int],
    logger: LogFn | None = None,
) -> None:
    extra_images = [path for idx, path in enumerate(source_images, start=1) if idx not in used_indices]
    if extra_images:
        names = ", ".join(path.name for path in extra_images)
        message = f"Warning: unused images were not inserted into HTML: {names}"
        if logger is not None:
            logger(message)
        else:
            print(message, file=sys.stderr)


def validate_paths(args: argparse.Namespace) -> tuple[Path, Path, Path, Path | None]:
    input_path = Path(args.input).expanduser().resolve()
    images_dir = Path(args.images_dir).expanduser().resolve()
    output_path = Path(args.output).expanduser().resolve()
    ssh_key_path = None
    ssh_key_value = (getattr(args, "ssh_key", None) or "").strip()
    ssh_password = getattr(args, "ssh_password", None) or ""

    if not input_path.is_file():
        raise FileNotFoundError(f"Input file not found: {input_path}")
    if not images_dir.is_dir():
        raise FileNotFoundError(f"Images directory not found: {images_dir}")
    if not ssh_key_value and not ssh_password:
        raise ValueError("Provide either SSH key or SSH password.")
    if ssh_key_value:
        ssh_key_path = Path(ssh_key_value).expanduser().resolve()
        if not ssh_key_path.is_file():
            raise FileNotFoundError(f"SSH key not found: {ssh_key_path}")

    return input_path, images_dir, output_path, ssh_key_path


def validate_runtime_auth(args: argparse.Namespace) -> None:
    if not getattr(args, "ssh_key", None) and not getattr(args, "ssh_password", None):
        raise ValueError("Provide either SSH key or SSH password.")


def build_with_content(
    *,
    args: argparse.Namespace,
    title: str,
    body: str,
    images_dir: Path,
    output_path: Path,
    logger: LogFn | None = None,
    upload: bool = True,
    public_base_url_override: str | None = None,
) -> BuildResult:
    config = load_config(args.style_config)
    title = normalize_title(title)
    body = normalize_body(body)
    if not title:
        raise ValueError("Could not detect a title. Pass --title explicitly.")

    log_message(logger, f"Using title: {title}")
    args_copy = argparse.Namespace(**vars(args))
    news_folder, remote_news_path = normalize_runtime_args(args_copy, title)
    if public_base_url_override:
        args_copy.public_base_url = public_base_url_override
    log_message(logger, f"News folder: {news_folder}")
    log_message(logger, f"Remote folder: {remote_news_path}")
    log_message(logger, f"Public image URL base: {args_copy.public_base_url}")
    blocks = parse_blocks(body)
    source_images = discover_images(images_dir)
    if not source_images:
        raise ValueError(f"No supported image files found in {images_dir}")

    ensure_markers_have_images(blocks, len(source_images))
    used_indices = collect_used_indices(blocks)
    title_slug = slugify(title)

    with tempfile.TemporaryDirectory(prefix="news-builder-") as temp_dir_name:
        temp_dir = Path(temp_dir_name)
        log_message(logger, f"Preparing {len(source_images)} image(s)")
        prepared_images = prepare_images(
            source_images=source_images,
            title_slug=title_slug,
            public_base_url=args_copy.public_base_url,
            image_config=config["image"],
            work_dir=temp_dir,
            logger=logger,
        )
        if upload:
            validate_runtime_auth(args_copy)
            upload_images(prepared_images, args_copy, logger)
        log_message(logger, f"Rendering HTML fragment: {output_path}")
        rendered_fragment = render_html(title, blocks, prepared_images, config["styles"])
        write_output(output_path, rendered_fragment)
        full_output_path = None
        rendered_full_html = None
        full_output_value = getattr(args_copy, "full_output", None)
        if full_output_value:
            full_output_path = derive_full_output_path(output_path, full_output_value)
            rendered_full_html = render_full_html_document(title, rendered_fragment)
            write_output(full_output_path, rendered_full_html)
            log_message(logger, f"Full HTML page written to {full_output_path}")

        if args.keep_temp:
            preserved = output_path.parent / f"{output_path.stem}_processed_images"
            if preserved.exists():
                shutil.rmtree(preserved)
            shutil.copytree(temp_dir, preserved)
            log_message(logger, f"Saved processed images to {preserved}")

    print_warnings(source_images, used_indices, logger=logger)
    log_message(logger, f"HTML fragment written to {output_path}")
    return BuildResult(
        fragment_output_path=output_path,
        full_output_path=full_output_path,
        fragment_html=rendered_fragment,
        full_html=rendered_full_html,
        news_folder=news_folder,
        remote_path=remote_news_path,
        public_base_url=args_copy.public_base_url,
    )


def run_builder(args: argparse.Namespace, logger: LogFn | None = None) -> BuildResult:
    config = load_config(args.style_config)
    _ = config  # keep config loading validation symmetric with build_with_content
    input_path, images_dir, output_path, _ssh_key_path = validate_paths(args)

    log_message(logger, f"Reading document: {input_path}")
    detected_title, body = read_input_document(input_path)
    title = normalize_title(args.title or detected_title or "")
    return build_with_content(
        args=args,
        title=title,
        body=body,
        images_dir=images_dir,
        output_path=output_path,
        logger=logger,
        upload=True,
    )


def main() -> int:
    args = parse_args()
    run_builder(args, logger=print)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:  # pragma: no cover
        print(f"Error: {exc}", file=sys.stderr)
        raise SystemExit(1)
