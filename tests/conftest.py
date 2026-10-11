import pymupdf
import pytest

from magscan.config import Settings
from magscan.metadata import MagazineInfo
from magscan.processor import Processor
from magscan.store import Store


def make_pdf(path, text="NATIONAL GEOGRAPHIC\nJuly 2024"):
    path.parent.mkdir(parents=True, exist_ok=True)
    doc = pymupdf.open()
    page = doc.new_page(width=600, height=800)
    page.insert_text((50, 100), text, fontsize=30)
    doc.new_page().insert_text((50, 100), "Contents")
    doc.save(path)
    doc.close()
    return path


class FakeExtractor:
    def __init__(self, info=None):
        self.calls = []
        self.info = info or MagazineInfo(
            magazine="National Geographic", issue="7", year=2024, month=7, publisher="NG Partners",
            headline="Octopuses", cover_lines=["Amazon", "Petra"], language="en", topic="Science", confidence=0.95,
        )

    def extract(self, cover, filename):
        self.calls.append((filename, cover.page_text))
        return self.info


@pytest.fixture
def env(tmp_path):
    lib = tmp_path / "library"
    settings = Settings(library_dir=str(lib), config_dir=str(tmp_path / "config"))
    extractor = FakeExtractor()
    proc = Processor(settings, Store(settings.db_file), extractor=extractor)
    return settings, proc, lib, extractor
