"""Basic tests that do not require Django."""

import os
import re
import unittest

import toml


class TestDocsPackaging(unittest.TestCase):
    """Test Version in doc requirements is the same pyproject."""

    def test_version(self):
        """Verify that pyproject.toml dev dependencies have the same versions as in the docs requirements.txt."""
        parent_path = os.path.dirname(os.path.dirname(os.path.dirname(os.path.realpath(__file__))))
        poetry_path = os.path.join(parent_path, "pyproject.toml")
        poetry_toml = toml.load(poetry_path)
        poetry_groups = poetry_toml["tool"]["poetry"]["group"]
        poetry_details = {
            **poetry_groups.get("dev", {}).get("dependencies", {}),
            **poetry_groups.get("docs", {}).get("dependencies", {}),
        }
        with open(f"{parent_path}/docs/requirements.txt", "r", encoding="utf-8") as file:
            requirements = [line for line in file.read().splitlines() if (len(line) > 0 and not line.startswith("#"))]
        for pkg in requirements:
            package_name = pkg
            if len(pkg.split("==")) == 2:  # noqa: PLR2004
                package_name, version = pkg.split("==")
            else:
                version = "*"
            self.assertEqual(poetry_details[package_name], version)


class TestReadmeLinks(unittest.TestCase):
    """README.md is the PyPI long description, so its links must be absolute."""

    # Inline links and images: [text](target) / ![alt](target), plus reference definitions: [id]: target
    LINK_PATTERN = re.compile(r"\]\(\s*<?([^)\s>]+)|^\s*\[[^\]]+\]:\s*<?(\S+?)>?(?:\s|$)", re.MULTILINE)
    # HTML <img src="..."> / <a href="...">
    HTML_PATTERN = re.compile(r"""(?:src|href)\s*=\s*["']([^"']+)["']""", re.IGNORECASE)
    ALLOWED = re.compile(r"^(?:https?://|mailto:|#)", re.IGNORECASE)

    def test_readme_has_no_relative_links(self):
        """Fail if README.md links or images use relative targets, which break on PyPI."""
        parent_path = os.path.dirname(os.path.dirname(os.path.dirname(os.path.realpath(__file__))))
        with open(os.path.join(parent_path, "README.md"), "r", encoding="utf-8") as file:
            readme = file.read()
        targets = [m.group(1) or m.group(2) for m in self.LINK_PATTERN.finditer(readme)]
        targets += self.HTML_PATTERN.findall(readme)
        relative = [target for target in targets if not self.ALLOWED.match(target)]
        self.assertEqual(relative, [], f"README.md has relative links that break on PyPI: {relative}")
