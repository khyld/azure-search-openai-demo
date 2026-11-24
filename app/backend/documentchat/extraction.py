import io
import re
import zipfile
from collections import Counter
from dataclasses import dataclass
from typing import IO
from xml.etree import ElementTree

from azure.ai.documentintelligence.aio import DocumentIntelligenceClient
from azure.ai.documentintelligence.models import AnalyzeDocumentRequest
from bs4 import BeautifulSoup
from pypdf import PdfReader

from prepdocslib.page import Page
from prepdocslib.pdfparser import DocumentAnalysisParser


@dataclass
class ArtifactPage(Page):
    words: int = 0
    uncertain_words: int = 0
    known_words: int = 0
    complex_table: bool = False


def coverage(original: bytes, extension: str, pages: list[Page]) -> dict:
    reasons = []
    if not pages or not all(isinstance(page, ArtifactPage) for page in pages):
        return {"status": "unverified", "reasons": ["quality_metadata_missing"]}
    if any(not BeautifulSoup(page.text, "html.parser").get_text(" ", strip=True) for page in pages):
        reasons.append("empty_extracted_page")
    if any(isinstance(page, ArtifactPage) and page.complex_table for page in pages):
        reasons.append("table_spans_multiple_pages")
    if extension == ".pdf":
        expected_pages = len(PdfReader(io.BytesIO(original)).pages)
        if [page.page_num for page in pages] != list(range(expected_pages)):
            reasons.append("missing_pages")
        for page in pages:
            if isinstance(page, ArtifactPage) and (
                not page.words or page.known_words / page.words < 0.9 or page.uncertain_words / page.words > 0.05
            ):
                reasons.append("uncertain_ocr")
                break
    else:
        with zipfile.ZipFile(io.BytesIO(original)) as archive:
            root = ElementTree.fromstring(archive.read("word/document.xml"))
            original_text = " ".join(root.itertext())
            if any(name.startswith("word/media/") for name in archive.namelist()):
                reasons.append("embedded_images_not_verified")
        expected = Counter(re.findall(r"\w+", original_text.casefold()))
        actual = Counter(re.findall(r"\w+", BeautifulSoup(" ".join(page.text for page in pages), "html.parser").get_text(" ", strip=True).casefold()))
        if not expected or sum((expected & actual).values()) / sum(expected.values()) < 0.99:
            reasons.append("missing_word_content")
    return {"status": "insufficient" if reasons else "adequate", "reasons": sorted(set(reasons)), "policy_version": 1}


class ArtifactDocumentParser(DocumentAnalysisParser):
    async def parse(self, content: IO):
        content.seek(0)
        async with DocumentIntelligenceClient(endpoint=self.endpoint, credential=self.credential) as client:
            poller = await client.begin_analyze_document(model_id=self.model_id, body=AnalyzeDocumentRequest(bytes_source=content.read()))
            result = await poller.result()
        offset = 0
        for page in result.pages or []:
            spans = page.spans or []
            start = min((span.offset for span in spans), default=0)
            end = max((span.offset + span.length for span in spans), default=start)
            text = (result.content or "")[start:end]
            replacements = []
            complex_table = False
            for table in result.tables or []:
                regions = table.bounding_regions or []
                table_spans = table.spans or []
                overlaps = any(span.offset < end and span.offset + span.length > start for span in table_spans)
                if regions and not any(region.page_number == page.page_number for region in regions):
                    continue
                if not regions and not overlaps:
                    continue
                if len({region.page_number for region in regions}) > 1 or any(
                    (span.offset < start or span.offset + span.length > end)
                    for span in table_spans if span.offset < end and span.offset + span.length > start
                ):
                    complex_table = True
                inserted = False
                for span in table_spans:
                    local_start, local_end = max(span.offset, start), min(span.offset + span.length, end)
                    if local_start < local_end:
                        replacements.append((local_start - start, local_end - start, self.table_to_html(table) if not inserted else ""))
                        inserted = True
            for local_start, local_end, replacement in sorted(replacements, reverse=True):
                text = text[:local_start] + replacement + text[local_end:]
            words = page.words or []
            yield ArtifactPage(page_num=page.page_number - 1, offset=offset, text=text.strip(), words=len(words),
                               known_words=sum(word.confidence is not None for word in words),
                               uncertain_words=sum(word.confidence is None or word.confidence < 0.8 for word in words),
                               complex_table=complex_table)
            offset += len(text.strip())