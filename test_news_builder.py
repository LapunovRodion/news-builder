import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))

import news_builder


class NewsBuilderParsingTests(unittest.TestCase):
    def test_markdown_title_is_extracted(self):
        title, body = news_builder.extract_title_from_plain_text(
            "# Hello\n\nParagraph one.\n\n[image:1]\n",
            is_markdown=True,
        )
        self.assertEqual(title, "Hello")
        self.assertEqual(body, "Paragraph one.\n\n[image:1]")

    def test_blocks_keep_marker_positions(self):
        blocks = news_builder.parse_blocks("First.\n\n[image:1]\n\nSecond.")
        self.assertEqual(type(blocks[0]).__name__, "ParagraphBlock")
        self.assertEqual(type(blocks[1]).__name__, "ImageLayoutBlock")
        self.assertEqual(type(blocks[2]).__name__, "ParagraphBlock")
        self.assertEqual(blocks[1].layout, "image")
        self.assertEqual(blocks[1].indices, (1,))

    def test_slugify_falls_back(self):
        self.assertEqual(news_builder.slugify("!!!"), "news")

    def test_slugify_transliterates_cyrillic(self):
        self.assertEqual(news_builder.slugify("День Конституции"), "den-konstitutsii")

    def test_parse_row_and_float_markers(self):
        blocks = news_builder.parse_blocks("[images:1,2]\n\n[image-left:3]\n\nTail.")
        self.assertEqual(blocks[0].layout, "images")
        self.assertEqual(blocks[0].indices, (1, 2))
        self.assertEqual(blocks[1].layout, "image-left")
        self.assertEqual(blocks[1].indices, (3,))
        self.assertEqual(blocks[2].text, "Tail.")

    def test_normalize_text_content_cleans_word_spacing(self):
        value = "Title\u00a0 \n\n\nBody\u200b   text"
        self.assertEqual(news_builder.normalize_text_content(value), "Title \n\nBody text")

    def test_validate_paths_reports_missing_input(self):
        args = SimpleNamespace(
            input="/tmp/definitely-missing-news-builder-input.txt",
            images_dir="/tmp",
            output="/tmp/out.html",
            ssh_key="/tmp/also-missing-key",
            ssh_password="",
        )
        with self.assertRaises(FileNotFoundError):
            news_builder.validate_paths(args)

    def test_collect_used_indices_for_mixed_markers(self):
        blocks = news_builder.parse_blocks("[images:1,2]\n\n[image-right:4]\n\n[image:3]")
        self.assertEqual(news_builder.collect_used_indices(blocks), {1, 2, 3, 4})

    def test_validate_paths_accepts_password_without_key(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            input_file = temp_path / "news.txt"
            input_file.write_text("Title\n\nBody", encoding="utf-8")
            images_dir = temp_path / "images"
            images_dir.mkdir()
            args = SimpleNamespace(
                input=str(input_file),
                images_dir=str(images_dir),
                output=str(temp_path / "out.html"),
                ssh_key="",
                ssh_password="secret",
            )
            input_path, resolved_images_dir, output_path, ssh_key_path = news_builder.validate_paths(args)
            self.assertEqual(input_path, input_file.resolve())
            self.assertEqual(resolved_images_dir, images_dir.resolve())
            self.assertEqual(output_path, (temp_path / "out.html").resolve())
            self.assertIsNone(ssh_key_path)

    def test_validate_paths_requires_auth(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            input_file = temp_path / "news.txt"
            input_file.write_text("Title\n\nBody", encoding="utf-8")
            images_dir = temp_path / "images"
            images_dir.mkdir()
            args = SimpleNamespace(
                input=str(input_file),
                images_dir=str(images_dir),
                output=str(temp_path / "out.html"),
                ssh_key="",
                ssh_password="",
            )
            with self.assertRaises(ValueError):
                news_builder.validate_paths(args)

    def test_normalize_runtime_args_creates_news_subfolder(self):
        args = SimpleNamespace(
            remote_path="/web/images/news/w",
            public_base_url="https://law.bsu.by/images/news/w/",
            news_slug=None,
        )
        news_folder, remote_news_path = news_builder.normalize_runtime_args(args, "День Конституции")
        self.assertEqual(news_folder, "den-konstitutsii")
        self.assertEqual(remote_news_path, "/web/images/news/w/den-konstitutsii")
        self.assertEqual(args.public_base_url, "https://law.bsu.by/images/news/w/den-konstitutsii/")

    def test_build_news_folder_falls_back_to_timestamp(self):
        with patch("news_builder.datetime") as mocked_datetime:
            mocked_datetime.now.return_value.strftime.return_value = "20260313-180000"
            self.assertEqual(news_builder.build_news_folder_name("!!!"), "news-20260313-180000")

    def test_derive_full_output_path(self):
        output = Path("/tmp/news.html")
        self.assertEqual(news_builder.derive_full_output_path(output), Path("/tmp/news_preview.html"))

    def test_render_full_html_document_wraps_fragment(self):
        document = news_builder.render_full_html_document("Title", "<div>Body</div>\n")
        self.assertIn("<!DOCTYPE html>", document)
        self.assertIn("<title>Title</title>", document)
        self.assertIn("<div>Body</div>", document)


if __name__ == "__main__":
    unittest.main()
