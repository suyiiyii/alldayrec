"""Regression checks for the PyPI README build hooks (run with hatchling)."""
from email.parser import Parser
import importlib.util
from pathlib import Path
import tarfile
import unittest
from zipfile import ZipFile


ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("hatch_build", ROOT / "server/hatch_build.py")
hook = importlib.util.module_from_spec(spec)
spec.loader.exec_module(hook)


class PypiReadme(unittest.TestCase):
    def test_relative_markdown_html_and_nested_badges(self):
        source = '''[Guide](docs/guide.md#setup) ![Shot](img/shot.png)
[![Badge](img/badge.svg)](LICENSE)
<a href="docs/guide.md"><img src='img/shot.png'></a>
[English](README.en.md) [Start](#快速开始)
'''
        result = hook.pypi_readme(source)
        self.assertIn(f"[Guide]({hook.REPO}docs/guide.md#setup)", result)
        self.assertIn(f"![Shot]({hook.RAW}img/shot.png)", result)
        self.assertIn(f"[![Badge]({hook.RAW}img/badge.svg)]({hook.REPO}LICENSE)", result)
        self.assertIn(f'href="{hook.REPO}docs/guide.md"', result)
        self.assertIn(f"src='{hook.RAW}img/shot.png'", result)
        self.assertIn(f"[English]({hook.REPO}README.en.md)", result)
        self.assertIn(f"[Start]({hook.REPO}README.md#快速开始)", result)

    def test_picture_fallback_external_assets_and_code(self):
        source = '''<picture>
<source media="(prefers-color-scheme: dark)" srcset="img/dark.svg">
<img alt="Logo" src="img/light.svg">
</picture>
![Asset](https://raw.githubusercontent.com/suyiiyii/alldayrec/assets/img/demo.png)
`[Example](docs/example.md)`
```html
<a href="docs/example.md">Example</a>
```
'''
        result = hook.pypi_readme(source)
        self.assertNotIn("<picture", result)
        self.assertNotIn("<source", result)
        self.assertIn(f'<img alt="Logo" src="{hook.RAW}img/light.svg">', result)
        self.assertIn(source[source.index("![Asset]"):], result)

    def test_built_wheel_and_sdist_descriptions_match_current_readme(self):
        expected = hook.pypi_readme((ROOT / "README.md").read_text())
        dist = ROOT / "server/dist"
        with ZipFile(next(dist.glob("*.whl"))) as wheel:
            metadata = wheel.read(next(n for n in wheel.namelist() if n.endswith("/METADATA"))).decode()
        with tarfile.open(next(dist.glob("*.tar.gz"))) as sdist:
            pkg_info = next(n for n in sdist.getnames() if n.endswith("/PKG-INFO"))
            source_metadata = sdist.extractfile(pkg_info).read().decode()
            snapshot = sdist.extractfile(pkg_info.removesuffix("PKG-INFO") + "README.md").read().decode()
        self.assertEqual(snapshot, (ROOT / "README.md").read_text())
        for raw in (metadata, source_metadata):
            message = Parser().parsestr(raw)
            self.assertEqual(message["Description-Content-Type"], "text/markdown")
            self.assertEqual(message.get_payload().rstrip(), expected.rstrip())


if __name__ == "__main__":
    unittest.main()
