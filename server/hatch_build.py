"""Prepare the repository README for PyPI, also when building from an sdist."""
from pathlib import Path
import re
from urllib.parse import urljoin, urlsplit

from hatchling.builders.hooks.plugin.interface import BuildHookInterface
from hatchling.metadata.plugin.interface import MetadataHookInterface


REPO = "https://github.com/suyiiyii/alldayrec/blob/main/"
RAW = "https://raw.githubusercontent.com/suyiiyii/alldayrec/main/"


def absolute_url(url, image=False):
    if urlsplit(url).scheme:
        return url
    if url.startswith("//"):
        return "https:" + url
    if url.startswith("#"):
        return REPO + "README.md" + url
    return urljoin(RAW if image else REPO, url.removeprefix("/"))


def pypi_readme(text):
    # PyPI strips picture/source. Keep the light/default img for both the logo
    # and architecture diagram rather than losing their HTML fallbacks.
    text = re.sub(r"<picture\b[^>]*>.*?(<img\b[^>]*>).*?</picture>",
                  r"\1", text, flags=re.DOTALL | re.IGNORECASE)

    def rewrite(part):
        part = re.sub(r"(!\[[^\]\n]*\]\()([^\s)]+)",
                      lambda m: m[1] + absolute_url(m[2], image=True), part)
        part = re.sub(r"(\]\()([^\s)]+)",
                      lambda m: m[1] + absolute_url(m[2]), part)
        return re.sub(r'''\b(src|href)=(['"])(.*?)\2''',
                      lambda m: m[1] + "=" + m[2] + absolute_url(m[3], image=m[1].lower() == "src") + m[2],
                      part, flags=re.IGNORECASE)

    # Leave command examples and inline code unchanged.
    return "".join(part if part.startswith("`") else rewrite(part)
                   for part in re.split(r"(```.*?```|`[^`\n]+`)", text, flags=re.DOTALL))


def readme_path(root):
    root = Path(root)
    # Source checkout: the root README is authoritative. The sdist carries a
    # snapshot next to pyproject.toml so rebuilding needs neither Git nor a network.
    return root / "README.md" if (root / "README.md").is_file() else root.parent / "README.md"


class ReadmeMetadataHook(MetadataHookInterface):
    def update(self, metadata):
        metadata["readme"] = {
            "content-type": "text/markdown",
            "text": pypi_readme(readme_path(self.root).read_text(encoding="utf-8")),
        }


class ReadmeBuildHook(BuildHookInterface):
    def initialize(self, version, build_data):
        if self.target_name == "sdist":
            build_data["force_include"][str(readme_path(self.root))] = "README.md"
