# News Builder

Standalone Python tool for preparing HTML news pages with inline styles only.

It:
- reads news text from `.docx`, `.txt`, or `.md`
- processes and compresses images
- uploads images over SSH
- creates a separate remote folder for each news item
- generates an HTML fragment ready for CMS insertion
- includes a simple desktop GUI on `tkinter`
- renders local preview inside the GUI
- supports reusable server profiles

## Files

- `news_builder.py` - CLI script
- `news_builder_gui.py` - desktop GUI
- `requirements-news-builder.txt` - Python dependencies
- `news_builder_style.example.json` - style and image-processing overrides
- `style-presets/` - ready-made style configs

## Install

```bash
python -m ensurepip --upgrade
python -m pip install -r requirements-news-builder.txt
```

## Run

GUI:

```bash
python news_builder_gui.py
```

GUI features:
- maximized editor-first layout
- editable text area with extended marker and text tools
- linked photo browser with list, thumbnail grid, and large preview
- used-image highlighting based on markers in the editor
- separate focus window for larger text-and-photo editing
- photo renaming from the GUI with stable index ordering
- local rendered preview tab
- full HTML page export
- saved server profiles
- saved last-used fields between launches

CLI:

```bash
python news_builder.py \
  --input /path/to/news.docx \
  --images-dir /path/to/images \
  --output /path/to/news.html \
  --remote-host example.com \
  --remote-user deploy \
  --remote-path /web/images/news/w \
  --ssh-password your_password \
  --public-base-url https://law.bsu.by/images/news/w/
```

`--remote-path` and `--public-base-url` are base paths. The script creates a per-news subfolder automatically from the title.

## Markers

Use markers inside the text:

```text
Первый абзац.

[image:1]

Второй абзац.

[images:2,3]

[image-left:4]

Текст будет обтекать фото.

[image-right:5]
```

Supported markers:
- `[image:N]` - one full-width image
- `[images:1,2,3]` - several images in one row
- `[image-left:N]` - floating image on the left
- `[image-right:N]` - floating image on the right

## Auth

You can use either:
- `--ssh-key /path/to/private_key`
- `--ssh-password your_password`

At least one must be provided.

The GUI stores entered values, including password, in:

```text
~/.news_builder_gui.json
```

Server profiles are stored in the same file.

## Example Path Mapping

If you set:

- remote base path: `/web/images/news/w`
- public base URL: `https://law.bsu.by/images/news/w/`
- title: `День Конституции`

then the script will create:

```text
/web/images/news/w/den-konstitutsii
```

and generated image URLs will look like:

```text
https://law.bsu.by/images/news/w/den-konstitutsii/filename.jpg
```

You can override the generated folder name with:

```bash
--news-slug custom-folder-name
```

## Style Overrides

```bash
python news_builder.py ... --style-config news_builder_style.example.json
```

All generated HTML uses inline styles only.

Ready-made presets:
- `style-presets/no-style-default.json` - almost no styling, plain inline defaults
- `style-presets/warm-editorial.json` - current warm editorial look
- `style-presets/clean-modern.json` - clean neutral portal style
- `style-presets/newspaper-classic.json` - classic newspaper look
- `style-presets/contrast-magazine.json` - higher-contrast magazine look
