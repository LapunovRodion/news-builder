# News Builder

Standalone Python script for building an inline-styled HTML news fragment from a text document and a folder of images.

## Features

- Reads `.docx`, `.txt`, and `.md`
- Uses markers like `[image:1]` in the text body
- Resizes and compresses large images
- Uploads images over SSH
- Supports SSH login either by private key or by password
- Generates an HTML fragment with inline styles only
- Includes a simple desktop GUI on `tkinter`

## Install

```bash
python -m ensurepip --upgrade
python -m pip install -r /path/to/python_script/requirements-news-builder.txt
```

If `pip` is not available on the target machine, install the listed dependencies with the package manager or another Python environment first.

## Marker syntax

Use markers in the body text:

```text
News paragraph one.

[image:1]

News paragraph two.

[image:2]
```

Images are mapped after natural sorting of files in `--images-dir`.

Additional layouts:

```text
[images:1,2]
```

Two or more images in one row.

```text
[image-left:3]
```

Floating image on the left with text wrapping around it.

```text
[image-right:4]
```

Floating image on the right with text wrapping around it.

## Example

```bash
python /path/to/python_script/news_builder.py \
  --input /path/to/news.docx \
  --images-dir /path/to/images \
  --output /path/to/news.html \
  --remote-host example.com \
  --remote-user deploy \
  --remote-path /var/www/site/news/2026/03 \
  --ssh-key /home/user/.ssh/id_ed25519 \
  --public-base-url https://example.com/news/2026/03/
```

`--remote-path` and `--public-base-url` are now base paths. The script creates a separate subfolder for each news item automatically and uploads images there.

Example:

- base remote path: `/web/images/news/w`
- base public URL: `https://law.bsu.by/images/news/w/`
- title: `День Конституции`

Generated folder:

```text
den-konstitutsii
```

Final upload target:

```text
/web/images/news/w/den-konstitutsii
```

Final image URLs:

```text
https://law.bsu.by/images/news/w/den-konstitutsii/...
```

If needed, you can override the folder name with:

```bash
--news-slug custom-folder-name
```

Authentication:

- `--ssh-key /path/to/key` for key-based login
- `--ssh-password your_password` for password-based login

You must provide at least one of them.

## GUI

Run:

```bash
python /path/to/python_script/news_builder_gui.py
```

The GUI lets you:

- choose the text file and image folder
- fill SSH fields
- use either `SSH key` or `SSH password`
- set a custom news folder or let it be generated automatically
- save the last-used settings
- run the build in the background
- inspect the build log and open the generated HTML

The GUI stores all entered fields, including the password, in:

```text
~/.news_builder_gui.json
```

## Style overrides

Pass `--style-config /path/to/python_script/news_builder_style.example.json` with your own JSON overrides.
