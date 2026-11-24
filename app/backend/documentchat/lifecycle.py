import hashlib
import io
import uuid
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import PurePath
from xml.etree import ElementTree

from pypdf import PdfReader

MAX_BYTES = 20 * 1024 * 1024
MAX_PAGES = 100
MAX_WORD_CHARACTERS = 200_000
IDLE_TIMEOUT = timedelta(minutes=120)


class DocumentError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def owner_key(tenant: str, oid: str) -> str:
    if not tenant or not oid:
        raise DocumentError("Log ind for at bruge dokumentchatten.", 401)
    return hashlib.sha256(f"{tenant}:{oid}".encode()).hexdigest()


def document_id(value: object) -> str:
    if not isinstance(value, str):
        raise DocumentError("Ugyldigt dokument-id.")
    try:
        return str(uuid.UUID(value))
    except (ValueError, TypeError, AttributeError):
        raise DocumentError("Ugyldigt dokument-id.") from None


def session_active(session: dict, expected_id: str, now: datetime | None = None) -> bool:
    if not session or session.get("document_id") != expected_id:
        return False
    try:
        last_activity = datetime.fromisoformat(session["last_activity"])
        return timedelta(0) <= (now or utc_now()) - last_activity < IDLE_TIMEOUT
    except (KeyError, ValueError, TypeError):
        return False


def require_active(session: dict, expected_id: str, now: datetime | None = None) -> None:
    if not session_active(session, expected_id, now):
        raise DocumentError("Dokumentet er udlobet eller erstattet. Upload et nyt dokument.", 409)


def search_filter(owner: str, active_id: str) -> str:
    if len(owner) != 64 or any(character not in "0123456789abcdef" for character in owner):
        raise DocumentError("Ugyldig brugeridentitet.", 403)
    return f"owner_id eq '{owner}' and document_id eq '{document_id(active_id)}'"


def validate_upload(filename: str, content: bytes) -> str:
    if not content:
        raise DocumentError("Filen er tom.")
    if len(content) > MAX_BYTES:
        raise DocumentError("Dokumentet ma hojst fylde 20 MB.", 413)
    extension = PurePath(filename.replace("\\", "/")).suffix.lower()
    if extension == ".pdf":
        try:
            reader = PdfReader(io.BytesIO(content))
            if reader.is_encrypted:
                raise DocumentError("Adgangskodebeskyttede PDF-filer understottes ikke.")
            if not 0 < len(reader.pages) <= MAX_PAGES:
                raise DocumentError("PDF-dokumentet ma hojst indeholde 100 sider.")
        except DocumentError:
            raise
        except Exception:
            raise DocumentError("PDF-filen kunne ikke laeses.") from None
    elif extension == ".docx":
        try:
            with zipfile.ZipFile(io.BytesIO(content)) as archive:
                entries = archive.infolist()
                if len(entries) > 2000 or sum(entry.file_size for entry in entries) > 100 * 1024 * 1024:
                    raise DocumentError("Word-filens udpakkede indhold er for stort.", 413)
                if "word/document.xml" not in archive.namelist():
                    raise DocumentError("Filen er ikke et gyldigt Word-dokument.")
                if any(entry.flag_bits & 1 for entry in entries):
                    raise DocumentError("Krypterede Word-filer understottes ikke.")
                if archive.getinfo("word/document.xml").file_size > 4 * 1024 * 1024:
                    raise DocumentError("Word-dokumentets tekstindhold er for stort.", 413)
                root = ElementTree.fromstring(archive.read("word/document.xml"))
                text_size = sum(
                    len(element.text or "")
                    for element in root.iter("{http://schemas.openxmlformats.org/wordprocessingml/2006/main}t")
                )
                if text_size > MAX_WORD_CHARACTERS:
                    raise DocumentError("Word-dokumentet ma hojst indeholde 200.000 tegn.", 413)
        except DocumentError:
            raise
        except (zipfile.BadZipFile, OSError, ElementTree.ParseError):
            raise DocumentError("Word-filen kunne ikke laeses.") from None
    else:
        raise DocumentError("Vaelg en PDF- eller DOCX-fil.")
    return extension
