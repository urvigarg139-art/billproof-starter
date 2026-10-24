from google.auth.transport.requests import Request as GoogleAuthRequest

from google.oauth2 import id_token

import json

from types import SimpleNamespace

import os

import re

import sqlite3

import tempfile

import base64

from datetime import date, datetime

from pathlib import Path

from typing import Optional

from urllib.parse import urlencode

import cloudinary

import cloudinary.uploader

import httpx

from dotenv import load_dotenv

from fastapi import FastAPI, File, Form, HTTPException, UploadFile, Request

from fastapi.middleware.cors import CORSMiddleware

from fastapi.responses import RedirectResponse

from starlette.middleware.sessions import SessionMiddleware

from google.oauth2.credentials import Credentials

from googleapiclient.discovery import build

from googleapiclient.errors import HttpError

from pydantic import BaseModel

from PIL import Image

try:

    import pytesseract

except ImportError:

    pytesseract = None

try:

    from pypdf import PdfReader

except ImportError:

    PdfReader = None

try:

    import fitz

except ImportError:

    fitz = None

# --------------------------------------------------

# CONFIGURATION

# --------------------------------------------------

BASE_DIR = Path(__file__).resolve().parent.parent

load_dotenv(BASE_DIR / ".env")

DB_PATH = BASE_DIR / "billproof.db"

FRONTEND_ORIGIN = os.getenv(

    "FRONTEND_ORIGIN",

    "http://localhost:5173"

)

GOOGLE_SCOPES = [

    "openid",

    "email",

    "profile",

    "https://www.googleapis.com/auth/gmail.readonly",

]

app = FastAPI(title="BillProof API", version="0.3.0")

app.add_middleware(

    SessionMiddleware,

    secret_key=os.getenv(

        "SESSION_SECRET",

        "CHANGE_THIS_LOCAL_DEVELOPMENT_SECRET"

    ),

    same_site="lax",

    https_only=False,

)

app.add_middleware(

    CORSMiddleware,

    allow_origins=[FRONTEND_ORIGIN],

    allow_credentials=True,

    allow_methods=["*"],

    allow_headers=["*"],

)

# --------------------------------------------------

# DATABASE

# --------------------------------------------------

def db():

    conn = sqlite3.connect(DB_PATH)

    conn.row_factory = sqlite3.Row

    return conn

def init_db():

    with db() as conn:

        conn.execute("""

            CREATE TABLE IF NOT EXISTS bills (

                id INTEGER PRIMARY KEY AUTOINCREMENT,

                product TEXT NOT NULL,

                seller TEXT DEFAULT '',

                purchase_date TEXT DEFAULT '',

                amount REAL,

                warranty_months INTEGER,

                warranty_end TEXT DEFAULT '',

                category TEXT DEFAULT 'Other',

                cloudinary_url TEXT DEFAULT '',

                public_id TEXT DEFAULT '',

                original_filename TEXT DEFAULT '',

                extracted_text TEXT DEFAULT '',

                created_at TEXT NOT NULL,

                owner_email TEXT NOT NULL DEFAULT 'local-demo',

                source_key TEXT DEFAULT ''

            )

        """)

        columns = {

            row[1]

            for row in conn.execute(

                "PRAGMA table_info(bills)"

            ).fetchall()

        }

        if "owner_email" not in columns:

            conn.execute("""

                ALTER TABLE bills

                ADD COLUMN owner_email TEXT NOT NULL DEFAULT 'local-demo'

            """)

        if "source_key" not in columns:

            conn.execute("""

                ALTER TABLE bills

                ADD COLUMN source_key TEXT DEFAULT ''

            """)

        conn.execute("""

            CREATE TABLE IF NOT EXISTS gmail_connections (

                email TEXT PRIMARY KEY,

                access_token TEXT NOT NULL,

                refresh_token TEXT DEFAULT '',

                token_uri TEXT NOT NULL,

                scope TEXT DEFAULT '',

                updated_at TEXT NOT NULL

            )

        """)

        # Prevent the same Gmail attachment from being imported twice.

        conn.execute("""

            CREATE UNIQUE INDEX IF NOT EXISTS

            idx_bills_owner_source_key

            ON bills(owner_email, source_key)

            WHERE source_key IS NOT NULL AND source_key != ''

        """)

init_db()

# --------------------------------------------------

# GENERAL HELPERS

# --------------------------------------------------

def current_owner(request: Request) -> str:

    return request.session.get("owner_email") or "local-demo"

def google_oauth_configured() -> bool:

    return bool(

        os.getenv("GOOGLE_CLIENT_ID")

        and os.getenv("GOOGLE_CLIENT_SECRET")

    )

def configure_cloudinary():

    cloud_name = os.getenv("CLOUDINARY_CLOUD_NAME")

    api_key = os.getenv("CLOUDINARY_API_KEY")

    api_secret = os.getenv("CLOUDINARY_API_SECRET")

    if not all([cloud_name, api_key, api_secret]):

        raise HTTPException(

            status_code=503,

            detail="Cloudinary is not configured in backend/.env."

        )

    cloudinary.config(

        cloud_name=cloud_name,

        api_key=api_key,

        api_secret=api_secret,

        secure=True,

    )

def get_gmail_credentials(email: str):

    with db() as conn:

        row = conn.execute(

            "SELECT * FROM gmail_connections WHERE email = ?",

            (email,)

        ).fetchone()

    if not row:

        raise HTTPException(

            status_code=401,

            detail="Connect your Gmail account first."

        )

    return Credentials(

        token=row["access_token"],

        refresh_token=row["refresh_token"] or None,

        token_uri=row["token_uri"],

        client_id=os.getenv("GOOGLE_CLIENT_ID"),

        client_secret=os.getenv("GOOGLE_CLIENT_SECRET"),

        scopes=GOOGLE_SCOPES,

    )



def extract_text(path: str, content_type: str) -> str:
    """Extract selectable PDF text first, then OCR scanned PDFs/images."""
    is_pdf = content_type == "application/pdf" or path.lower().endswith(".pdf")

    if is_pdf:
        text = ""
        if PdfReader:
            try:
                reader = PdfReader(path)
                text = "\\n".join(page.extract_text() or "" for page in reader.pages).strip()
            except Exception as exc:
                print(f"PDF text extraction failed: {exc}")

        if text:
            return text[:20000]

        # A scanned PDF has no selectable text; OCR up to five pages.
        if pytesseract and fitz:
            try:
                extracted_pages = []
                with fitz.open(path) as document:
                    for page in list(document)[:5]:
                        pix = page.get_pixmap(dpi=220, alpha=False)
                        image = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
                        extracted_pages.append(pytesseract.image_to_string(image))
                return "\\n".join(extracted_pages).strip()[:20000]
            except Exception as exc:
                print(f"Scanned PDF OCR failed: {exc}")
        return ""

    if pytesseract:
        try:
            with Image.open(path) as image:
                return pytesseract.image_to_string(image.convert("RGB"))[:20000]
        except Exception as exc:
            print(f"Image OCR failed: {exc}")
    return ""
def parse_invoice_date(value: str) -> str:
    """Parse common invoice date formats into YYYY-MM-DD."""
    value = re.sub(r"\\s+", " ", value.strip()).replace(",", "")
    formats = (
        "%d-%m-%Y", "%d-%m-%y", "%d/%m/%Y", "%d/%m/%y",
        "%Y-%m-%d", "%d %B %Y", "%d %b %Y",
        "%B %d %Y", "%b %d %Y",
    )
    for fmt in formats:
        try:
            return datetime.strptime(value, fmt).date().isoformat()
        except ValueError:
            continue
    return ""


def parse_warranty_months(text: str) -> int:
    """Detect explicit warranty duration; avoid guessing if absent."""
    patterns = (
        (r"\\b(\\d{1,2})\\s*[- ]?\\s*years?\\b.{0,50}\\bwarranty\\b", 12),
        (r"\\bwarranty\\b.{0,50}\\b(\\d{1,2})\\s*years?\\b", 12),
        (r"\\b(\\d{1,3})\\s*months?\\b.{0,50}\\bwarranty\\b", 1),
        (r"\\bwarranty\\b.{0,50}\\b(\\d{1,3})\\s*months?\\b", 1),
        (r"\\bwarranty\\s*[:\\-]?\\s*(\\d{1,3})\\s*(?:month|months)\\b", 1),
    )
    for pattern, multiplier in patterns:
        match = re.search(pattern, text, re.IGNORECASE | re.DOTALL)
        if match:
            months = int(match.group(1)) * multiplier
            if 1 <= months <= 120:
                return months
    return 0


def parse_warranty_end(text: str) -> str:
    """Detect an explicitly labelled warranty expiry/end date."""
    date_pattern = (
        r"(?:warranty\\s*)?(?:end|expiry|expiration|expires|valid until)"
        r"\\s*(?:date)?\\s*[:\\-]?\\s*"
        r"(\\d{1,2}[/-]\\d{1,2}[/-]\\d{2,4}|"
        r"\\d{1,2}\\s+[A-Za-z]+\\s+\\d{4}|"
        r"[A-Za-z]+\\s+\\d{1,2},?\\s+\\d{4})"
    )
    match = re.search(date_pattern, text, re.IGNORECASE)
    return parse_invoice_date(match.group(1)) if match else ""


def parse_fields(text: str, filename: str):
    """Best-effort invoice extraction; always verify OCR results."""
    lines = [
        re.sub(r"\\s+", " ", line).strip()
        for line in text.splitlines()
        if line.strip()
    ]

    # Prefer a plausible product line, skipping common invoice headings.
    product = ""
    ignored = ("invoice", "receipt", "order id", "gstin", "bill to",
               "ship to", "tax invoice", "date:", "amount", "subtotal")
    for line in lines:
        if any(word in line.lower() for word in ignored):
            continue
        if 3 < len(line) < 90:
            product = line
            break
    if not product:
        product = (
            Path(filename).stem.replace("_", " ").replace("-", " ").strip().title()
            or "New bill"
        )

    amount = None
    amount_matches = re.findall(
        r"(?:₹|INR|Rs\\.?)\\s*([0-9,]+(?:\\.[0-9]{1,2})?)",
        text,
        re.IGNORECASE,
    )
    if amount_matches:
        try:
            amount = float(amount_matches[-1].replace(",", ""))
        except ValueError:
            pass
    else:
        # Fallback for invoice totals written without a currency symbol.
        total_match = re.search(
            r"(?:grand total|invoice total|total amount|amount paid|total)\\s*[:\\-]?\\s*"
            r"(?:₹|INR|Rs\\.?)?\\s*([0-9,]+(?:\\.[0-9]{1,2})?)",
            text,
            re.IGNORECASE,
        )
        if total_match:
            try:
                amount = float(total_match.group(1).replace(",", ""))
            except ValueError:
                pass

    purchase_date = ""
    date_patterns = (
        r"\\b\\d{1,2}[/-]\\d{1,2}[/-]\\d{2,4}\\b",
        r"\\b\\d{1,2}\\s+[A-Za-z]+\\s+\\d{4}\\b",
        r"\\b[A-Za-z]+\\s+\\d{1,2},?\\s+\\d{4}\\b",
        r"\\b\\d{4}-\\d{1,2}-\\d{1,2}\\b",
    )
    # Prefer dates near a purchase/invoice label.
    labelled = re.search(
        r"(?:invoice date|purchase date|order date|date of purchase|date)\\s*[:\\-]?\\s*"
        r"(\\d{1,2}[/-]\\d{1,2}[/-]\\d{2,4}|"
        r"\\d{1,2}\\s+[A-Za-z]+\\s+\\d{4}|"
        r"[A-Za-z]+\\s+\\d{1,2},?\\s+\\d{4}|"
        r"\\d{4}-\\d{1,2}-\\d{1,2})",
        text,
        re.IGNORECASE,
    )
    if labelled:
        purchase_date = parse_invoice_date(labelled.group(1))
    if not purchase_date:
        for pattern in date_patterns:
            date_match = re.search(pattern, text)
            if date_match:
                purchase_date = parse_invoice_date(date_match.group(0))
                if purchase_date:
                    break

    seller = ""
    known_sellers = (
        "amazon", "flipkart", "croma", "reliance digital",
        "vijay sales", "ikea", "myntra", "blinkit",
    )
    lower_text = text.lower()
    for candidate in known_sellers:
        if candidate in lower_text:
            seller = candidate.title()
            break

    return {
        "product": product,
        "seller": seller,
        "purchase_date": purchase_date,
        "amount": amount,
        "warranty_months": parse_warranty_months(text),
        "warranty_end": parse_warranty_end(text),
    }

def add_months(original_date: date, months: int) -> date:

    import calendar

    month_index = original_date.month - 1 + months

    year = original_date.year + month_index // 12

    month = month_index % 12 + 1

    day = min(

        original_date.day,

        calendar.monthrange(year, month)[1],

    )

    return date(year, month, day)

def warranty_status(end_text):

    if not end_text:

        return "No Warranty"

    try:

        end_date = date.fromisoformat(end_text)

    except (ValueError, TypeError):

        return "Unknown"

    days_remaining = (end_date - date.today()).days

    if days_remaining < 0:

        return "Expired"

    if days_remaining <= 30:

        return "Expiring Soon"

    return "Active"

def row_to_dict(row):

    result = dict(row)

    result["warranty_status"] = warranty_status(

        result.get("warranty_end", "")

    )

    return result

class BillUpdate(BaseModel):

    product: Optional[str] = None

    seller: Optional[str] = None

    purchase_date: Optional[str] = None

    amount: Optional[float] = None

    warranty_months: Optional[int] = None

    category: Optional[str] = None

# --------------------------------------------------

# GOOGLE LOGIN

# --------------------------------------------------

@app.get("/api/auth/google")

async def google_login(request: Request):

    if not google_oauth_configured():

        raise HTTPException(

            status_code=503,

            detail="Add Google OAuth credentials to backend/.env."

        )

    redirect_uri = os.getenv(

        "GOOGLE_REDIRECT_URI",

        "http://localhost:8000/api/auth/google/callback",

    )

    state = os.urandom(24).hex()

    request.session["oauth_state"] = state

    params = {

        "client_id": os.getenv("GOOGLE_CLIENT_ID"),

        "redirect_uri": redirect_uri,

        "response_type": "code",

        "scope": " ".join(GOOGLE_SCOPES),

        "access_type": "offline",

        "include_granted_scopes": "true",

        "prompt": "consent",

        "state": state,

    }

    return RedirectResponse(

        "https://accounts.google.com/o/oauth2/v2/auth?"

        + urlencode(params)

    )

@app.get("/api/auth/google/callback")

async def google_callback(request: Request):

    error = request.query_params.get("error")

    if error:

        return RedirectResponse(

            FRONTEND_ORIGIN + "/?gmail_error=" + error

        )

    code = request.query_params.get("code")

    state = request.query_params.get("state")

    if (

        not code

        or not state

        or state != request.session.get("oauth_state")

    ):

        raise HTTPException(

            status_code=400,

            detail="Google sign-in state check failed. Please try again."

        )

    redirect_uri = os.getenv(

        "GOOGLE_REDIRECT_URI",

        "http://localhost:8000/api/auth/google/callback",

    )

    async with httpx.AsyncClient(timeout=20) as client:

        token_response = await client.post(

            "https://oauth2.googleapis.com/token",

            data={

                "code": code,

                "client_id": os.getenv("GOOGLE_CLIENT_ID"),

                "client_secret": os.getenv("GOOGLE_CLIENT_SECRET"),

                "redirect_uri": redirect_uri,

                "grant_type": "authorization_code",

            },

        )

        if token_response.status_code >= 400:

            raise HTTPException(

                status_code=400,

                detail=(

                    "Google token exchange failed. Check your "

                    "OAuth redirect URI and credentials."

                ),

            )

        token_data = token_response.json()

        user_response = await client.get(

            "https://www.googleapis.com/oauth2/v2/userinfo",

            headers={

                "Authorization":

                f"Bearer {token_data['access_token']}"

            },

        )

        if user_response.status_code >= 400:

            raise HTTPException(

                status_code=400,

                detail="Could not retrieve the Google account profile."

            )

        profile = user_response.json()

    email = (profile.get("email") or "").lower()

    if not email:

        raise HTTPException(

            status_code=400,

            detail="Google did not return an email address."

        )

    with db() as conn:

        existing = conn.execute(

            "SELECT refresh_token FROM gmail_connections WHERE email=?",

            (email,),

        ).fetchone()

        refresh_token = token_data.get("refresh_token")

        if not refresh_token and existing:

            refresh_token = existing["refresh_token"]

        conn.execute(

            """

            INSERT INTO gmail_connections (

                email, access_token, refresh_token,

                token_uri, scope, updated_at

            )

            VALUES (?, ?, ?, ?, ?, ?)

            ON CONFLICT(email) DO UPDATE SET

                access_token=excluded.access_token,

                refresh_token=excluded.refresh_token,

                token_uri=excluded.token_uri,

                scope=excluded.scope,

                updated_at=excluded.updated_at

            """,

            (

                email,

                token_data["access_token"],

                refresh_token or "",

                "https://oauth2.googleapis.com/token",

                token_data.get("scope", ""),

                datetime.utcnow().isoformat(timespec="seconds"),

            ),

        )

    request.session["owner_email"] = email

    request.session.pop("oauth_state", None)

    return RedirectResponse(FRONTEND_ORIGIN)

@app.get("/api/auth/me")

def auth_me(request: Request):

    email = current_owner(request)

    if email == "local-demo":

        return {"connected": False, "email": None}

    return {"connected": True, "email": email}

@app.post("/api/auth/logout")

def auth_logout(request: Request):

    request.session.clear()

    return {"ok": True}

# --------------------------------------------------

# GMAIL BILL SCANNER

# --------------------------------------------------

@app.post("/api/gmail/scan")

def scan_gmail(request: Request):

    email = current_owner(request)

    if email == "local-demo":

        raise HTTPException(

            status_code=401,

            detail="Click Connect Gmail and sign in first.",

        )

    credentials = get_gmail_credentials(email)

    # Search only recent purchase-related messages.

    # Gmail sorts results by relevance/date, so sort again below.

    query = (

        'newer_than:365d has:attachment '

        '{invoice receipt bill order purchase warranty}'

    )

    try:

        service = build(

            "gmail",

            "v1",

            credentials=credentials,

            cache_discovery=False,

        )

        result = service.users().messages().list(

            userId="me",

            q=query,

            maxResults=100,

        ).execute()

        messages = result.get("messages", [])

        imported = 0

        skipped = 0

        checked = 0

        errors = []

        purchase_terms = re.compile(

            r"\b(invoice|receipt|purchase|order confirmation|"

            r"order placed|tax invoice|payment confirmation|"

            r"bill|warranty|your order|order delivered)\b",

            re.IGNORECASE,

        )

        for message_stub in messages:

            checked += 1

            message_id = message_stub["id"]

            temp_path = None

            try:

                message = service.users().messages().get(

                    userId="me",

                    id=message_id,

                    format="full",

                ).execute()

                headers = {

                    header["name"].lower(): header["value"]

                    for header in message.get(

                        "payload", {}

                    ).get("headers", [])

                }

                subject = headers.get("subject", "Email invoice")

                sender = headers.get("from", "")

                snippet = message.get("snippet", "")

                payload = message.get("payload", {})

                attachments = []

                def collect_attachments(part):

                    filename = part.get("filename", "")

                    mime_type = part.get("mimeType", "")

                    body = part.get("body", {})

                    if filename and (

                        mime_type == "application/pdf"

                        or mime_type.startswith("image/")

                    ):

                        attachments.append({

                            "filename": filename,

                            "mime_type": mime_type,

                            "attachment_id": body.get("attachmentId"),

                            "inline_data": body.get("data"),

                        })

                    for child in part.get("parts", []) or []:

                        collect_attachments(child)

                collect_attachments(payload)

                if not attachments:

                    skipped += 1

                    continue

                filenames = " ".join(

                    attachment["filename"]

                    for attachment in attachments

                )

                searchable_text = (

                    f"{subject} {sender} {snippet} {filenames}"

                )

                # Allow likely purchase-related emails through using broader keywords.

                broader_purchase_terms = re.compile(

                    r"\b(invoice|receipt|purchase|order|confirmation|"

                    r"tax|payment|bill|warranty|delivered|shipment|"

                    r"transaction|thank you for your order|"

                    r"order number|amount paid|your item)\b",

                    re.IGNORECASE,

                )

                if not broader_purchase_terms.search(searchable_text):

                    skipped += 1

                    continue

                for attachment in attachments:

                    filename = attachment["filename"]

                    mime_type = attachment["mime_type"]

                    attachment_id = attachment["attachment_id"]

                    raw_data = attachment["inline_data"]

                    source_key = (

                        f"gmail:{message_id}:"

                        f"{attachment_id or filename}"

                    )

                    with db() as conn:

                        existing = conn.execute(

                            """

                            SELECT id FROM bills

                            WHERE owner_email=? AND source_key=?

                            """,

                            (email, source_key),

                        ).fetchone()

                    if existing:

                        skipped += 1

                        continue

                    if attachment_id:

                        attachment_result = (

                            service.users()

                            .messages()

                            .attachments()

                            .get(

                                userId="me",

                                messageId=message_id,

                                id=attachment_id,

                            )

                            .execute()

                        )

                        raw_data = attachment_result.get("data", "")

                    if not raw_data:

                        skipped += 1

                        continue

                    try:

                        file_bytes = base64.urlsafe_b64decode(

                            raw_data + "=" * (-len(raw_data) % 4)

                        )

                    except Exception:

                        skipped += 1

                        continue

                    if (

                        not file_bytes

                        or len(file_bytes) > 12 * 1024 * 1024

                    ):

                        skipped += 1

                        continue

                    suffix = Path(filename).suffix or (

                        ".pdf"

                        if mime_type == "application/pdf"

                        else ".jpg"

                    )

                    try:

                        with tempfile.NamedTemporaryFile(

                            delete=False,

                            suffix=suffix,

                        ) as temp_file:

                            temp_file.write(file_bytes)

                            temp_path = temp_file.name

                        extracted = extract_text(temp_path, mime_type)

                        fields = parse_fields(extracted, filename)
                        gmail_warranty_months = fields.get("warranty_months", 0)
                        gmail_purchase_date = fields.get("purchase_date", "")
                        gmail_warranty_end = fields.get("warranty_end", "") or ""
                        if not gmail_warranty_end and gmail_warranty_months and gmail_purchase_date:
                            try:
                                gmail_warranty_end = add_months(
                                    date.fromisoformat(gmail_purchase_date),
                                    gmail_warranty_months,
                                ).isoformat()
                            except (ValueError, TypeError):
                                gmail_warranty_end = ""

                        if not fields.get("seller"):

                            sender_email = (

                                sender.split("<")[-1]

                                .split(">")[0]

                            )

                            seller_guess = (

                                sender_email.split("@")[0]

                                .replace(".", " ")

                                .replace("_", " ")

                                .title()

                            )

                            fields["seller"] = (

                                seller_guess or "Email merchant"

                            )[:80]

                        if (

                            not fields.get("product")

                            or fields["product"].lower()

                            == Path(filename).stem.lower()

                        ):

                            fields["product"] = (

                                subject[:100]

                                or Path(filename).stem

                            )

                        configure_cloudinary()

                        uploaded = cloudinary.uploader.upload(

                            temp_path,

                            folder="billproof/bills",

                            resource_type="auto",

                            use_filename=True,

                            unique_filename=True,

                            overwrite=False,

                        )

                        with db() as conn:

                            conn.execute(

                                """

                                INSERT INTO bills (

                                    product, seller, purchase_date, amount,

                                    warranty_months, warranty_end, category,

                                    cloudinary_url, public_id,

                                    original_filename, extracted_text,

                                    created_at, owner_email, source_key

                                )

                                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)

                                """,

                                (

                                    fields.get("product")

                                    or subject[:100]

                                    or "Email bill",

                                    fields.get("seller", "Email merchant"),

                                    fields.get("purchase_date", ""),

                                    fields.get("amount"),

                                    gmail_warranty_months or None,

                                    gmail_warranty_end,

                                    "Other",

                                    uploaded.get("secure_url", ""),

                                    uploaded.get("public_id", ""),

                                    filename,

                                    extracted,

                                    datetime.utcnow().isoformat(

                                        timespec="seconds"

                                    ),

                                    email,

                                    source_key,

                                ),

                            )

                        imported += 1

                    except sqlite3.IntegrityError:

                        # A concurrent scan may have imported it already.

                        skipped += 1

                    except Exception as exc:

                        skipped += 1

                        errors.append(

                            f"{filename}: {str(exc)[:120]}"

                        )

                    finally:

                        if temp_path and os.path.exists(temp_path):

                            os.unlink(temp_path)

                        temp_path = None

            except Exception as exc:

                skipped += 1

                errors.append(

                    f"Message processing failed: {str(exc)[:120]}"

                )

            finally:

                if temp_path and os.path.exists(temp_path):

                    os.unlink(temp_path)

        return {

            "email": email,

            "messages_checked": checked,

            "imported": imported,

            "skipped": skipped,

            "search_window": "Last 365 days",

            "errors": errors[:5],

            "note": (

                "Recent invoice-related messages were checked. "

                "Review extracted details for accuracy. "

                "This scan runs when you click the button; "

                "real-time background monitoring is not enabled yet."

            ),

        }

    except HTTPException:

        raise

    except HttpError as exc:

        raise HTTPException(

            status_code=502,

            detail=(

                "Gmail could not be scanned. Check Gmail API access "

                "and reconnect your account if needed."

            ),

        ) from exc

# --------------------------------------------------

# HEALTH CHECK

# --------------------------------------------------

@app.get("/api/health")

def health():

    return {

        "ok": True,

        "service": "BillProof API",

    }

# --------------------------------------------------

# LIST BILLS

# --------------------------------------------------

@app.get("/api/bills")

def list_bills(

    request: Request,

    q: str = "",

    status: str = "All Documents",

):

    owner = current_owner(request)

    with db() as conn:

        rows = conn.execute(

            """

            SELECT * FROM bills

            WHERE owner_email=?

            ORDER BY created_at DESC

            """,

            (owner,),

        ).fetchall()

    bills = [row_to_dict(row) for row in rows]

    if q.strip():

        needle = q.strip().lower()

        bills = [

            bill for bill in bills

            if needle in " ".join([

                str(bill.get("product", "")),

                str(bill.get("seller", "")),

                str(bill.get("category", "")),

            ]).lower()

        ]

    if status == "Active Warranties":

        bills = [

            bill for bill in bills

            if bill["warranty_status"] == "Active"

        ]

    elif status == "Expiring Soon":

        bills = [

            bill for bill in bills

            if bill["warranty_status"] == "Expiring Soon"

        ]

    elif status == "No Warranty":

        bills = [

            bill for bill in bills

            if bill["warranty_status"] == "No Warranty"

        ]

    return bills

# --------------------------------------------------

# MANUAL BILL UPLOAD

# --------------------------------------------------

@app.post("/api/bills")

async def upload_bill(

    request: Request,

    file: UploadFile = File(...),

    warranty_months: int = Form(0),

    category: str = Form("Other"),

    product_override: str = Form(""),

    seller_override: str = Form(""),

    purchase_date_override: str = Form(""),

    amount_override: str = Form(""),

):

    allowed_types = {

        "image/jpeg",

        "image/png",

        "image/webp",

        "application/pdf",

    }

    if file.content_type not in allowed_types:

        raise HTTPException(

            status_code=415,

            detail="Upload a JPG, PNG, WEBP, or PDF invoice.",

        )

    if warranty_months < 0 or warranty_months > 120:

        raise HTTPException(

            status_code=400,

            detail="Warranty must be between 0 and 120 months.",

        )

    data = await file.read()

    if len(data) > 12 * 1024 * 1024:

        raise HTTPException(

            status_code=413,

            detail="Maximum file size is 12 MB.",

        )

    filename = file.filename or "bill"

    suffix = Path(filename).suffix or ".bin"

    temp_path = None

    try:

        with tempfile.NamedTemporaryFile(

            delete=False,

            suffix=suffix,

        ) as temp_file:

            temp_file.write(data)

            temp_path = temp_file.name

        text = extract_text(temp_path, file.content_type or "")

        fields = parse_fields(text, filename)

        # Use an explicitly supplied warranty duration; otherwise use OCR.
        if warranty_months <= 0:
            warranty_months = fields.get("warranty_months", 0)

        product = product_override.strip() or fields["product"]

        seller = seller_override.strip() or fields["seller"]

        purchase_date = (

            purchase_date_override.strip()

            or fields["purchase_date"]

        )

        try:

            amount = (

                float(amount_override)

                if amount_override.strip()

                else fields["amount"]

            )

        except ValueError:

            amount = fields["amount"]

        configure_cloudinary()

        uploaded = cloudinary.uploader.upload(

            temp_path,

            folder="billproof/bills",

            resource_type="auto",

            use_filename=True,

            unique_filename=True,

            overwrite=False,

            context={"original_filename": filename},

        )

        # Prefer an expiry date printed on the invoice, otherwise calculate it.
        warranty_end = fields.get("warranty_end", "") or ""
        if not warranty_end and warranty_months and purchase_date:
            try:
                warranty_end = add_months(
                    date.fromisoformat(purchase_date),
                    warranty_months,
                ).isoformat()
            except (ValueError, TypeError):
                warranty_end = ""

        with db() as conn:

            cursor = conn.execute(

                """

                INSERT INTO bills (

                    product, seller, purchase_date, amount,

                    warranty_months, warranty_end, category,

                    cloudinary_url, public_id, original_filename,

                    extracted_text, created_at, owner_email

                )

                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)

                """,

                (

                    product,

                    seller,

                    purchase_date,

                    amount,

                    warranty_months or None,

                    warranty_end,

                    category,

                    uploaded.get("secure_url", ""),

                    uploaded.get("public_id", ""),

                    filename,

                    text,

                    datetime.utcnow().isoformat(timespec="seconds"),

                    current_owner(request),

                ),

            )

            bill_id = cursor.lastrowid

            row = conn.execute(

                "SELECT * FROM bills WHERE id=?",

                (bill_id,),

            ).fetchone()

        return row_to_dict(row)

    except HTTPException:

        raise

    except Exception as exc:

        raise HTTPException(

            status_code=500,

            detail=f"Could not upload bill: {str(exc)[:240]}",

        ) from exc

    finally:

        if temp_path and os.path.exists(temp_path):

            os.unlink(temp_path)

# --------------------------------------------------

# EDIT BILL

# --------------------------------------------------

@app.patch("/api/bills/{bill_id}")

def update_bill(

    bill_id: int,

    payload: BillUpdate,

    request: Request,

):

    updates = payload.model_dump(exclude_unset=True)

    owner = current_owner(request)

    with db() as conn:

        old = conn.execute(

            """

            SELECT * FROM bills

            WHERE id=? AND owner_email=?

            """,

            (bill_id, owner),

        ).fetchone()

        if not old:

            raise HTTPException(

                status_code=404,

                detail="Bill not found.",

            )

        values = dict(old)

        values.update({

            key: value

            for key, value in updates.items()

            if value is not None

        })

        warranty_end = ""

        if (

            values.get("warranty_months")

            and values.get("purchase_date")

        ):

            try:

                warranty_end = add_months(

                    date.fromisoformat(values["purchase_date"]),

                    int(values["warranty_months"]),

                ).isoformat()

            except (ValueError, TypeError):

                warranty_end = ""

        conn.execute(

            """

            UPDATE bills

            SET product=?, seller=?, purchase_date=?, amount=?,

                warranty_months=?, warranty_end=?, category=?

            WHERE id=?

            """,

            (

                values["product"],

                values["seller"],

                values["purchase_date"],

                values["amount"],

                values["warranty_months"],

                warranty_end,

                values["category"],

                bill_id,

            ),

        )

        row = conn.execute(

            "SELECT * FROM bills WHERE id=?",

            (bill_id,),

        ).fetchone()

    return row_to_dict(row)

# --------------------------------------------------

# DELETE BILL

# --------------------------------------------------

@app.delete("/api/bills/{bill_id}")

def delete_bill(

    bill_id: int,

    request: Request,

):

    owner = current_owner(request)

    with db() as conn:

        row = conn.execute(

            """

            SELECT * FROM bills

            WHERE id=? AND owner_email=?

            """,

            (bill_id, owner),

        ).fetchone()

        if not row:

            raise HTTPException(

                status_code=404,

                detail="Bill not found.",

            )

        if row["public_id"]:

            try:

                configure_cloudinary()

                resource_type = (

                    "raw"

                    if row["original_filename"].lower().endswith(".pdf")

                    else "image"

                )

                cloudinary.uploader.destroy(

                    row["public_id"],

                    resource_type=resource_type,

                )

            except Exception:

                pass

        conn.execute(

            "DELETE FROM bills WHERE id=?",

            (bill_id,),

        )

    return {"deleted": True}

# --------------------------------------------------

# AUTOMATIC GMAIL MONITORING

# --------------------------------------------------

@app.post("/api/gmail/watch")

def start_gmail_watch(request: Request):

    """

    Enable Gmail notifications for the currently connected account.

    Requires PUBSUB_TOPIC_NAME in backend/.env.

    """

    email = current_owner(request)

    if email == "local-demo":

        raise HTTPException(

            status_code=401,

            detail="Connect Gmail before enabling automatic monitoring.",

        )

    topic_name = os.getenv("PUBSUB_TOPIC_NAME")

    if not topic_name:

        raise HTTPException(

            status_code=503,

            detail="Set PUBSUB_TOPIC_NAME in backend/.env first.",

        )

    credentials = get_gmail_credentials(email)

    try:

        service = build(

            "gmail",

            "v1",

            credentials=credentials,

            cache_discovery=False,

        )

        result = service.users().watch(

            userId="me",

            body={"topicName": topic_name},

        ).execute()

        return {

            "enabled": True,

            "email": email,

            "history_id": result.get("historyId"),

            "expiration": result.get("expiration"),

            "message": "Gmail notifications enabled.",

        }

    except Exception as exc:

        raise HTTPException(

            status_code=502,

            detail=f"Could not enable Gmail notifications: {str(exc)[:200]}",

        ) from exc

@app.post("/api/gmail/push")

async def gmail_push(request: Request):

    """

    Receive a Google Pub/Sub push notification and start

    processing recent purchase-related email attachments.

    Configure the Pub/Sub push endpoint as:

    https://YOUR_PUBLIC_HOST/api/gmail/push

    """

    authorization = request.headers.get("authorization", "")

    expected_audience = os.getenv(

        "PUBSUB_PUSH_AUDIENCE",

        "https://billproof-starter.onrender.com",

    ).strip()

    expected_service_account = os.getenv(

        "PUBSUB_PUSH_SERVICE_ACCOUNT_EMAIL", ""

    ).strip().lower()



    if not authorization.startswith("Bearer ") or not expected_service_account:

        raise HTTPException(

            status_code=401,

            detail="Missing or invalid push authentication.",

        )



    google_token = authorization.split(" ", 1)[1].strip()

    try:

        claims = id_token.verify_oauth2_token(

            google_token,

            GoogleAuthRequest(),

            audience=expected_audience,

        )

    except Exception as exc:

        raise HTTPException(

            status_code=401,

            detail="Google push token verification failed.",

        ) from exc



    if (

        claims.get("email", "").lower() != expected_service_account

        or claims.get("email_verified") is not True

    ):

        raise HTTPException(

            status_code=401,

            detail="Unexpected push service account.",

        )

    try:

        body = await request.json()

        message_data = body.get("message", {}).get("data")

        if not message_data:

            return {"ok": True, "ignored": True}

        decoded = base64.urlsafe_b64decode(

            message_data + "=" * (-len(message_data) % 4)

        )

        notification = json.loads(decoded)

        email = (notification.get("emailAddress") or "").lower()

        if not email:

            return {"ok": True, "ignored": True}

        with db() as conn:

            connected = conn.execute(

                "SELECT email FROM gmail_connections WHERE email = ?",

                (email,),

            ).fetchone()

        if not connected:

            return {"ok": True, "ignored": True}

        # Reuse the existing scanner and its duplicate detection.

        fake_request = SimpleNamespace(

            session={"owner_email": email}

        )

        result = scan_gmail(fake_request)

        return {

            "ok": True,

            "email": email,

            "scan": result,

        }

    except HTTPException:

        raise

    except Exception as exc:

        # A non-2xx response tells Pub/Sub to retry delivery.

        raise HTTPException(

            status_code=500,

            detail=f"Automatic Gmail processing failed: {str(exc)[:200]}",

        ) from exc

