import streamlit as st
import pdfplumber
import re
import pandas as pd
from datetime import datetime
from io import BytesIO
import json
import requests
import os

# --- OCR: pdf2image + pytesseract + PIL ---
try:
    from pdf2image import convert_from_bytes
    import pytesseract
    from PIL import Image, ImageOps

    # Path a tesseract.exe (aggiorna se diverso)
    pytesseract.pytesseract.tesseract_cmd = r"C:\Programmi\Tesseract-OCR\tesseract.exe"
    OCR_AVAILABLE = True
except Exception:
    OCR_AVAILABLE = False

# Path a Poppler (cartella che contiene pdftoppm.exe)
POPPLER_PATH = r"C:\Programmi\Poppler\Library\bin"
POPPLER_AVAILABLE = os.path.exists(POPPLER_PATH)

# --- CONFIGURAZIONE PAGINA ---
st.set_page_config(
    page_title="Agente Locale Fatture PDF (Ollama + Regex + OCR)",
    page_icon="🧾",
    layout="wide"
)

# -------------------------------------------------------------------
# FUNZIONI DI UTILITÀ GENERALI
# -------------------------------------------------------------------

def normalize_date(date_str: str) -> str | None:
    """Normalizza una data in formato YYYY-MM-DD, se possibile."""
    if not date_str:
        return None
    date_str = date_str.strip()
    formats = [
        "%d/%m/%Y",
        "%d-%m-%Y",
        "%Y-%m-%d",
        "%d/%m/%y",
        "%d-%m-%y",
    ]
    for fmt in formats:
        try:
            dt = datetime.strptime(date_str, fmt)
            return dt.strftime("%Y-%m-%d")
        except ValueError:
            continue
    return date_str


def portal_date_format(date_str: str | None) -> str | None:
    """
    Restituisce la data in formato portale gg/mm/aaaa,
    partendo in genere da YYYY-MM-DD.
    """
    if not date_str:
        return None
    s = date_str.strip()
    for fmt in ["%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y"]:
        try:
            dt = datetime.strptime(s, fmt)
            return dt.strftime("%d/%m/%Y")
        except ValueError:
            continue
    return s


def normalize_amount(val) -> str | None:
    """
    Normalizza importo in stringa 'XXXX.YY'.
    - '469,94' -> '469.94'
    - '469.94' -> '469.94'
    - '46994'  -> '469.94' (ultime 2 cifre = centesimi)
    """
    if val is None:
        return None

    s = str(val).strip()
    if not s:
        return None

    s = s.replace(" ", "")

    # ha già separatore
    if "," in s or "." in s:
        s = s.replace(".", "").replace(",", ".")
        try:
            num = float(s)
            return f"{num:.2f}"
        except ValueError:
            return None

    # solo cifre
    if s.isdigit():
        if len(s) <= 2:
            num = float(s)
            return f"{num:.2f}"
        num = float(s[:-2] + "." + s[-2:])
        return f"{num:.2f}"

    return None


def localize_amount_eur(amount, currency) -> str | None:
    """
    Restituisce l'importo in formato display.
    - Se valuta è EUR/Euro/€ → virgola decimale (469,94)
    - Altrimenti lascia il punto (469.94)
    """
    if amount is None:
        return None
    s = str(amount).strip()
    if not s:
        return None
    curr = (currency or "").upper()
    if "EUR" in curr or "EURO" in curr or "€" in curr:
        return s.replace(".", ",")
    return s


def extract_text_from_pdf(uploaded_file) -> str:
    """Estrae il testo 'digitale' dal PDF usando pdfplumber."""
    text = ""
    try:
        with pdfplumber.open(uploaded_file) as pdf:
            for page in pdf.pages:
                page_text = page.extract_text() or ""
                text += page_text + "\n"
    except Exception as e:
        st.error(f"Errore nella lettura del PDF (pdfplumber): {e}")
    return text


def ocr_full_pdf(uploaded_file) -> str:
    """OCR completo del PDF (tutte le pagine) utilizzando Tesseract + Poppler."""
    if not OCR_AVAILABLE:
        st.warning("OCR non disponibile: mancano librerie Python (pdf2image/pytesseract/PIL).")
        return ""
    if not POPPLER_AVAILABLE:
        st.warning(f"OCR non disponibile: Poppler non trovato in {POPPLER_PATH}")
        return ""

    try:
        pdf_bytes = uploaded_file.getvalue()
        pages = convert_from_bytes(pdf_bytes, dpi=300, poppler_path=POPPLER_PATH)

        full_text = []
        for page in pages:
            gray = page.convert("L")
            gray = ImageOps.autocontrast(gray)
            text = pytesseract.image_to_string(
                gray, lang="ita+eng", config="--psm 6"
            )
            full_text.append(text)

        return "\n\n".join(full_text)
    except Exception as e:
        st.error(f"Errore OCR sul PDF: {e}")
        return ""


def get_invoice_text(uploaded_file, use_ocr_if_empty: bool = True) -> str:
    """
    Prova prima pdfplumber; se il testo è vuoto o molto corto e l'OCR è attivo,
    usa l'OCR completo come fallback.
    """
    text = extract_text_from_pdf(uploaded_file)
    if use_ocr_if_empty and (not text or len(text.strip()) < 50):
        st.info(f"Nessun testo digitale in {uploaded_file.name}. Provo l'OCR completo...")
        text = ocr_full_pdf(uploaded_file)
    return text


# -------------------------------------------------------------------
# OCR DIAGNOSTICO (header + per pagina)
# -------------------------------------------------------------------

def run_ocr_diagnostics(uploaded_file):
    """
    Esegue OCR diagnostico:
    - immagini e OCR per tutte le pagine
    - OCR per l'header (top 15% prima pagina)
    """
    if not OCR_AVAILABLE or not POPPLER_AVAILABLE:
        return None

    try:
        pdf_bytes = uploaded_file.getvalue()
        pages = convert_from_bytes(pdf_bytes, dpi=300, poppler_path=POPPLER_PATH)
        if not pages:
            return None

        diag = {"header_text": None, "header_image": None, "pages": []}

        for idx, page in enumerate(pages):
            gray = page.convert("L")
            gray_small = gray.copy()
            gray_small.thumbnail((900, 900))

            text_page = pytesseract.image_to_string(
                gray, lang="ita+eng", config="--psm 6"
            )

            diag["pages"].append(
                {"index": idx + 1, "image": gray_small, "text": text_page}
            )

        first_page = pages[0]
        w, h = first_page.size
        header_box = (0, 0, w, int(h * 0.15))
        header_img = first_page.crop(header_box)

        header_gray = header_img.convert("L")
        header_gray = ImageOps.autocontrast(header_gray)

        header_text = pytesseract.image_to_string(
            header_gray, lang="ita+eng", config="--psm 6"
        )

        diag["header_text"] = header_text
        diag["header_image"] = header_gray

        return diag
    except Exception as e:
        st.error(f"Errore OCR diagnostico: {e}")
        return None


def extract_supplier_from_header_text(header_text: str | None) -> str | None:
    """
    Estrae il fornitore dall'OCR dell'header, cercando:
    - ragioni sociali complete (es. 'Ostiliomobili S.p.A.')
    - altrimenti nome azienda + sigla societaria rilevata altrove.
    """
    if not header_text:
        return None

    lines = [l.strip() for l in header_text.split("\n") if l.strip()]
    if not lines:
        return None

    full_company_pattern = re.compile(
        r'([A-Z0-9][A-Za-z0-9&\s]{2,80}?\s+'
        r'(s\.?p\.?a|s\.?r\.?l|s\.?n\.?c|s\.?a\.?s|spa|srl|snc|sas))',
        re.IGNORECASE,
    )

    match_full = full_company_pattern.search(header_text)
    if match_full:
        name_part = match_full.group(1)
        sigla_raw = match_full.group(2)

        sigla_map = {
            "spa": "S.p.A.",
            "s.p.a": "S.p.A.",
            "s.p.a.": "S.p.A.",
            "srl": "S.r.l.",
            "s.r.l": "S.r.l.",
            "s.r.l.": "S.r.l.",
            "snc": "S.n.c.",
            "s.n.c": "S.n.c.",
            "sas": "S.a.s.",
            "s.a.s": "S.a.s.",
        }
        key = sigla_raw.lower().replace(" ", "").rstrip(".")
        sigla_norm = sigla_map.get(key, sigla_raw.upper())

        base_name = re.sub(
            r'\b(s\.?p\.?a|s\.?r\.?l|s\.?n\.?c|s\.?a\.?s|spa|srl|snc|sas)\b',
            "",
            name_part,
            flags=re.IGNORECASE,
        ).strip(" ,.-")

        return f"{base_name} {sigla_norm}".strip()

    skip_regex = re.compile(
        r'\b(via|viale|piazza|corso|c\.so|strada|cap\b|\d{5}|tel|cell|destinazione)\b',
        re.IGNORECASE,
    )

    candidate_name = None

    azienda_regex = re.compile(
        r'\b(marchio di proprietà|società soggetta|mobili)\b',
        re.IGNORECASE,
    )
    for line in lines:
        if azienda_regex.search(line) and not skip_regex.search(line):
            candidate_name = line
            break

    if not candidate_name:
        for line in lines:
            if line.isupper() and len(line) > 5 and not skip_regex.search(line):
                candidate_name = line
                break

    if not candidate_name:
        for line in lines:
            if not skip_regex.search(line) and not re.match(r"^\d", line):
                candidate_name = line
                break

    if not candidate_name:
        return None

    base_name = candidate_name.strip()

    sigla_pattern = re.compile(
        r'\b(s\.?p\.?a|spa|s\.?r\.?l|srl|s\.?n\.?c|snc|s\.?a\.?s|sas)\b',
        re.IGNORECASE,
    )
    sigla_match = sigla_pattern.search(header_text)
    if sigla_match and sigla_match.group(1).lower() not in base_name.lower():
        sigla_raw = sigla_match.group(1)
        key = sigla_raw.lower().replace(" ", "").rstrip(".")
        sigla_map = {
            "spa": "S.p.A.",
            "s.p.a": "S.p.A.",
            "s.p.a.": "S.p.A.",
            "srl": "S.r.l.",
            "s.r.l": "S.r.l.",
            "s.r.l.": "S.r.l.",
            "snc": "S.n.c.",
            "s.n.c": "S.n.c.",
            "sas": "S.a.s.",
            "s.a.s": "S.a.s.",
        }
        sigla_norm = sigla_map.get(key, sigla_raw.upper())
        return f"{base_name} {sigla_norm}".strip()

    return base_name


# -------------------------------------------------------------------
# PARSING CLIENTE / REGEX
# -------------------------------------------------------------------

def parse_cliente_from_lines(lines):
    """
    Estrae il CLIENTE dal blocco:
      Destinazione:/Destinatario:/Cliente:
      <codice>
      <nome>
      <indirizzo>
    Restituisce il NOME, non il codice.
    """
    label_pattern = re.compile(
        r'^(destinatario|destinazione|cliente|bill to|bill-to|spett\.le|spett/le|spett\. le|to:|ship to)\b',
        re.IGNORECASE,
    )

    def is_mostly_digits(s: str) -> bool:
        s2 = re.sub(r"\D", "", s)
        return len(s2) >= 5 and len(s2) >= len(s.strip()) * 0.6

    def looks_like_address(s: str) -> bool:
        s_low = s.lower()
        if re.search(r"\b(via|viale|piazza|corso|c\.so|strada|vico|largo)\b", s_low):
            return True
        if re.search(r"\b\d{5}\b", s_low):
            return True
        return False

    for idx, line in enumerate(lines):
        clean = line.strip()
        if not clean:
            continue

        lower = clean.lower()

        if label_pattern.search(lower):
            codice = None
            nome = None

            j = idx + 1
            max_j = min(idx + 7, len(lines))
            while j < max_j:
                candidate = lines[j].strip()
                j += 1
                if not candidate:
                    continue

                if re.search(r"(partita\siva|p\.iva|codice\sfiscale|vat|cf\b|fattura|invoice)", candidate.lower()):
                    continue

                if codice is None and is_mostly_digits(candidate):
                    codice = candidate
                    continue

                if not is_mostly_digits(candidate) and not looks_like_address(candidate) and len(candidate) > 2:
                    nome = candidate
                    break

            if nome:
                return nome

            if codice and not looks_like_address(codice):
                return codice

    return None


def analyze_invoice_regex(text: str, header_supplier: str | None = None) -> dict:
    """
    Estrazione basata su regex:
    - Fornitore (anche da header OCR)
    - Cliente
    - Data
    - Numero Fattura
    - Importo Totale
    - Valuta
    - Imponibile IVA
    - Codice IVA
    - Elenco Articoli (regex fallback)
    """
    data = {
        "Fornitore": None,
        "Cliente": None,
        "Data": None,
        "Numero Fattura": None,
        "Importo Totale": None,
        "Valuta": None,
        "Imponibile IVA": None,
        "Codice IVA": None,
        "Elenco Articoli Regex": [],
    }

    if not text:
        return data

    lines = [line.strip() for line in text.split("\n") if line.strip()]

    # Cliente
    cliente = parse_cliente_from_lines(lines)
    if cliente and not re.search(r"\bfattura\b|\binvoice\b", cliente.lower()):
        data["Cliente"] = cliente

    # Date
    date_pattern = r"\b(\d{1,2}[/-]\d{1,2}[/-]\d{2,4}|\d{4}[/-]\d{1,2}[/-]\d{1,2})\b"
    dates = re.findall(date_pattern, text)
    if dates:
        data["Data"] = normalize_date(dates[0])

    # Importo Totale
    amount_pattern = (
        r"(?:Totale documento|Totale\s+fattura|Totale da pagare|Totale|Importo|Total|Balance)"
        r"[^\d]*?(\d{1,3}(?:[.,]\d{3})*[.,]\d{2})"
    )
    amounts = re.findall(amount_pattern, text, re.IGNORECASE)
    if amounts:
        raw_amount = amounts[-1]
        data["Importo Totale"] = normalize_amount(raw_amount)

    # Numero Fattura – scegliamo la candidata più sensata
    invoice_num_pattern = (
        r"(?:Fattura\s*n\.?|Fattura|Invoice|Num(?:ero)?\s+Doc\.?|N\.|No\.)"
        r"\s*[:#]?\s*([A-Za-z0-9\-/]+)"
    )
    inv_nums = re.findall(invoice_num_pattern, text, re.IGNORECASE)
    chosen_num = None
    if inv_nums:
        for cand in inv_nums:
            c = cand.strip()
            if not any(ch.isdigit() for ch in c):
                continue
            digits = sum(ch.isdigit() for ch in c)
            # scarta codici cliente lunghi solo numerici tipo 000000000075942
            if digits >= 8 and digits == len(c):
                continue
            # preferisci numeri con slash o trattino (es. 3630/8)
            if "/" in c or "-" in c:
                chosen_num = c
                break
            if chosen_num is None:
                chosen_num = c
    if chosen_num:
        data["Numero Fattura"] = chosen_num

    # Valuta
    if re.search(r"\b(EUR|EURO)\b", text, re.IGNORECASE) or "€" in text:
        data["Valuta"] = "EUR"

    # Imponibile IVA
    imponibile_pattern = r"Imponibile[^\d]*?(\d{1,3}(?:[.,]\d{3})*[.,]\d{2})"
    imponibili = re.findall(imponibile_pattern, text, re.IGNORECASE)
    if imponibili:
        data["Imponibile IVA"] = normalize_amount(imponibili[-1])

    # Codice IVA (es. IVA 22%)
    iva_pattern = r"IVA\s*([0-9]{1,2})\s*%"
    iva_matches = re.findall(iva_pattern, text, re.IGNORECASE)
    if iva_matches:
        perc = iva_matches[-1]
        data["Codice IVA"] = f"IVA {perc}%"

    # Elenco articoli (fallback regex: righe con un prezzo ma non Totale/Imponibile/IVA)
    price_pattern = r"\d{1,3}(?:[.,]\d{3})*[.,]\d{2}"
    articolo_lines = []
    for line in lines:
        if re.search(price_pattern, line) and not re.search(
            r"\b(Totale|Imponibile|IVA|Spese|Trasporto|Bolla|Documento)\b",
            line,
            re.IGNORECASE,
        ):
            articolo_lines.append(line)
    data["Elenco Articoli Regex"] = articolo_lines

    # Fornitore con heuristica + header OCR
    blacklist_regex = (
        r"(via\b|viale\b|piazza\b|corso\b|c\.so\b|strada\b|"
        r"spett\.le|gentile|bill\sto|invoice|destinatario|destinazione|cliente|"
        r"partita\siva|p\.iva|codice\sfiscale|vat|cf\b|cap\b|tel\.|fax|www\.|mail|pag\.)"
    )
    societa_regex = r"\b(s\.?r\.?l|s\.?p\.?a|s\.?n\.?c|s\.?a\.?s|s\.?s\.|gmbh|inc\.|ltd)\b"

    candidato_fornitore = None
    for line in lines[:30]:
        testo = line.lower()
        if re.search(blacklist_regex, testo):
            continue
        if re.match(r"^\d", line):
            continue
        if re.search(societa_regex, testo):
            candidato_fornitore = line
            break

    if not candidato_fornitore and header_supplier:
        candidato_fornitore = header_supplier

    if candidato_fornitore and cliente:
        if candidato_fornitore.strip().lower() == cliente.strip().lower():
            candidato_fornitore = None

    data["Fornitore"] = candidato_fornitore

    return data


# -------------------------------------------------------------------
# AI LOCALE (OLLAMA)
# -------------------------------------------------------------------

def analyze_invoice_ollama(text: str, model_name: str = "phi3"):
    """
    Chiamata a Ollama (http://localhost:11434).
    Ritorna un dict (JSON) o None. Gestione robusta errori JSON.
    """
    prompt = f"""
Sei un assistente amministrativo. Analizza questa fattura ed estrai i dati in JSON.
ATTENZIONE:
- Il FORNITORE è l'azienda che EMETTE la fattura.
- Il CLIENTE è chi RICEVE la fattura.
- NON usare il nome del Cliente come Fornitore.
- Gli importi non possono essere negativi.

Testo Fattura:
{text[:3000]}

Campi richiesti (usa esattamente queste chiavi):
- fornitore
- cliente
- data_fattura (YYYY-MM-DD se possibile)
- numero_fattura
- importo_totale (numero decimale, punto come separatore)
- valuta
- elenco_articoli (lista con righe/descrizioni)

Rispondi SOLO con JSON valido.
"""

    try:
        response = requests.post(
            "http://localhost:11434/api/generate",
            json={
                "model": model_name,
                "prompt": prompt,
                "stream": False,
                "format": "json",
            },
            timeout=160,
        )

        if response.status_code != 200:
            st.warning(f"Errore HTTP da Ollama ({response.status_code}): {response.text}")
            return None

        result = response.json()
        raw = result.get("response", "")

        try:
            return json.loads(raw)
        except json.JSONDecodeError as e:
            start = raw.find("{")
            end = raw.rfind("}")
            if start != -1 and end != -1 and end > start:
                candidate = raw[start : end + 1]
                try:
                    return json.loads(candidate)
                except json.JSONDecodeError:
                    pass

            st.warning(
                "Errore nel parsing JSON restituito da Ollama.\n\n"
                f"Dettaglio errore: {e}\n\n"
                "Output grezzo del modello:"
            )
            st.code(raw, language="json")
            return None

    except requests.exceptions.RequestException as e:
        st.warning(f"Errore di connessione HTTP verso Ollama: {e}. Uso solo Regex.")
        return None
    except Exception as e:
        st.warning(f"Errore inatteso chiamando Ollama: {e}. Uso solo Regex.")
        return None


def merge_ai_and_regex(ai_data: dict | None, regex_data: dict) -> dict:
    """
    Merge AI + Regex.
    Campi critici (fornitore, cliente, data, numero, importo, imponibile, IVA):
    - ci fidiamo prima delle Regex (più stabili)
    - AI usata per elenco_articoli e, al massimo, piccoli completamenti.
    """
    extracted = {
        "fornitore": None,
        "cliente": None,
        "data_fattura": None,
        "data_documento_portale": None,
        "numero_fattura": None,
        "importo_totale": None,
        "importo_totale_raw": None,
        "valuta": None,
        "imponibile_iva": None,
        "codice_iva": None,
        "elenco_articoli": [],
    }

    # Cliente: regex, altrimenti AI (se non contiene 'fattura')
    regex_cliente = regex_data.get("Cliente")
    ai_cliente = (ai_data or {}).get("cliente") if ai_data else None

    if regex_cliente and not re.search(r"\bfattura\b|\binvoice\b", regex_cliente.lower()):
        extracted["cliente"] = regex_cliente
    elif ai_cliente and not re.search(r"\bfattura\b|\binvoice\b", str(ai_cliente).lower()):
        extracted["cliente"] = ai_cliente

    # Fornitore: solo regex/header OCR
    extracted["fornitore"] = regex_data.get("Fornitore")

    # Data fattura
    if regex_data.get("Data"):
        extracted["data_fattura"] = regex_data["Data"]
    elif ai_data and ai_data.get("data_fattura"):
        extracted["data_fattura"] = normalize_date(str(ai_data["data_fattura"]))

    # Data portale (gg/mm/aaaa) derivata dalla data_fattura
    if extracted["data_fattura"]:
        extracted["data_documento_portale"] = portal_date_format(extracted["data_fattura"])

    # Numero fattura: SOLO regex
    if regex_data.get("Numero Fattura"):
        extracted["numero_fattura"] = regex_data["Numero Fattura"]

    # Importo totale: regex vince, senza rinormalizzare (evita x1000)
    regex_amt = regex_data.get("Importo Totale")  # già normalizzato in analyze_invoice_regex
    ai_amt = normalize_amount(ai_data.get("importo_totale")) if ai_data else None

    if regex_amt:
        extracted["importo_totale_raw"] = regex_amt
    elif ai_amt:
        extracted["importo_totale_raw"] = ai_amt

    # Valuta
    val = None
    if ai_data and ai_data.get("valuta"):
        val = str(ai_data["valuta"]).strip().upper()
    elif regex_data.get("Valuta"):
        val = str(regex_data["Valuta"]).strip().upper()

    if val:
        if "€" in val or "EUR" in val or "EURO" in val:
            extracted["valuta"] = "EUR"
        else:
            extracted["valuta"] = val

    # Importo display (virgola se EUR)
    if extracted["importo_totale_raw"] is not None:
        extracted["importo_totale"] = localize_amount_eur(
            extracted["importo_totale_raw"],
            extracted.get("valuta"),
        )

    # Imponibile IVA e Codice IVA: solo regex, con formattazione locale
    if regex_data.get("Imponibile IVA"):
        extracted["imponibile_iva"] = localize_amount_eur(
            regex_data["Imponibile IVA"], extracted.get("valuta")
        )
    if regex_data.get("Codice IVA"):
        extracted["codice_iva"] = regex_data["Codice IVA"]

    # Elenco articoli:
    # 1) AI se disponibile e non vuoto
    # 2) altrimenti righe trovate via regex (Elenco Articoli Regex)
    line_items = []
    if ai_data and isinstance(ai_data.get("elenco_articoli"), list) and ai_data["elenco_articoli"]:
        line_items = ai_data["elenco_articoli"]
    elif regex_data.get("Elenco Articoli Regex"):
        line_items = regex_data["Elenco Articoli Regex"]

    extracted["elenco_articoli"] = line_items

    return extracted


# -------------------------------------------------------------------
# INTERFACCIA UTENTE
# -------------------------------------------------------------------

st.title("🧾 Agente Locale Fatture PDF (Ollama + Regex + OCR, solo locale)")
st.markdown(
    """
Analizza fatture PDF **interamente in locale**:

- Estrazione testo con **pdfplumber**
- Fallback OCR con **Tesseract + Poppler**
- Parsing strutturato con **Regex**
- Arricchimento opzionale con **Ollama** (`phi3`, `llama3`, ecc.) per leggere le righe-articolo

Nessun dato viene inviato all'esterno.
"""
)

with st.sidebar:
    st.header("⚙️ Impostazioni")

    use_ai = st.checkbox(
        "Usa AI Locale (Ollama)",
        value=True,
        help="Richiede Ollama attivo su http://localhost:11434",
    )
    model_name = st.text_input("Modello Ollama", value="phi3")

    use_ocr_fallback = st.checkbox(
        "Usa OCR se il testo PDF è vuoto",
        value=True,
        help="Utile per fatture scansionate (solo immagini).",
    )

    show_ocr_diag = st.checkbox(
        "Mostra pannello OCR avanzato (debug)",
        value=True,
    )

    if OCR_AVAILABLE and POPPLER_AVAILABLE:
        st.success("OCR locale attivo (Tesseract + Poppler).")
    elif OCR_AVAILABLE and not POPPLER_AVAILABLE:
        st.warning("Tesseract ok, ma Poppler non trovato: OCR limitato.")
    else:
        st.warning("OCR non disponibile (mancano librerie e/o Tesseract).")

    if use_ai:
        st.info("Assicurati di avere `ollama serve` in esecuzione e il modello scaricato (es. `ollama pull phi3`).")

uploaded_files = st.file_uploader(
    "Carica le tue fatture (PDF)",
    type=["pdf"],
    accept_multiple_files=True,
)

if uploaded_files:
    st.divider()
    results_list = []
    progress_bar = st.progress(0.0)

    for i, uploaded_file in enumerate(uploaded_files):
        col1, col2 = st.columns([1, 3])

        with col1:
            st.markdown("📄", unsafe_allow_html=True)
            st.caption(uploaded_file.name)

        # Testo principale
        text = get_invoice_text(uploaded_file, use_ocr_if_empty=use_ocr_fallback)

        # OCR diagnostico
        ocr_diag = None
        header_supplier = None
        if OCR_AVAILABLE and POPPLER_AVAILABLE:
            ocr_diag = run_ocr_diagnostics(uploaded_file)
            if ocr_diag and ocr_diag.get("header_text"):
                header_supplier = extract_supplier_from_header_text(
                    ocr_diag["header_text"]
                )

        # Regex
        regex_data = analyze_invoice_regex(text, header_supplier=header_supplier)

        # AI
        ai_data = None
        if use_ai:
            with st.spinner(f"{model_name} (Ollama) sta analizzando {uploaded_file.name}..."):
                ai_data = analyze_invoice_ollama(text, model_name)

        # Merge
        extracted = merge_ai_and_regex(ai_data, regex_data)
        method_used = "Regex + AI" if use_ai and ai_data else (
            "Solo Regex" if not use_ai else "Solo Regex (AI non disponibile)"
        )

        extracted["Nome File"] = uploaded_file.name
        extracted["Metodo"] = method_used
        results_list.append(extracted)

        # UI per singolo file
        with col2:
            st.subheader("Risultato estratto")
            st.json(extracted, expanded=False)

            with st.expander("🔍 Diagnosi dettagliata"):
                tabs = st.tabs(
                    ["Testo PDF", "Regex", "AI (Ollama)", "Merge finale", "OCR avanzato"]
                )

                with tabs[0]:
                    st.write("**Testo PDF (primi 4000 caratteri):**")
                    st.text(text[:4000] if text else "(Nessun testo estratto)")

                with tabs[1]:
                    st.write("**Output Regex (analyze_invoice_regex):**")
                    st.json(regex_data, expanded=True)

                with tabs[2]:
                    st.write("**Output AI Locale (Ollama):**")
                    if ai_data is not None:
                        st.json(ai_data, expanded=True)
                    else:
                        if use_ai:
                            st.write("AI abilitata ma non disponibile / errore.")
                        else:
                            st.write("AI disabilitata (solo Regex).")

                with tabs[3]:
                    st.write("**Dati finali dopo merge AI + Regex:**")
                    st.json(extracted, expanded=True)

                with tabs[4]:
                    st.write("**OCR avanzato (header + pagine)**")
                    if not show_ocr_diag:
                        st.info("Pannello OCR avanzato disattivato dalla sidebar.")
                    elif not (OCR_AVAILABLE and POPPLER_AVAILABLE):
                        st.warning(
                            "OCR avanzato non disponibile (mancano Tesseract o Poppler)."
                        )
                    elif ocr_diag is None:
                        st.warning("Nessun dato OCR disponibile.")
                    else:
                        st.subheader("Header OCR")
                        if ocr_diag.get("header_image") is not None:
                            st.image(
                                ocr_diag["header_image"],
                                caption="Immagine header (top 15%) usata per OCR",
                                use_container_width=True,
                            )
                            buf = BytesIO()
                            ocr_diag["header_image"].save(buf, format="PNG")
                            buf.seek(0)
                            st.download_button(
                                label="📥 Scarica immagine header (PNG)",
                                data=buf,
                                file_name=f"{uploaded_file.name}_header.png",
                                mime="image/png",
                            )

                        st.text_area(
                            "Testo OCR header",
                            value=ocr_diag.get("header_text") or "",
                            height=200,
                        )

                        st.subheader("OCR per pagina")
                        for page_diag in ocr_diag["pages"]:
                            st.markdown(f"**Pagina {page_diag['index']}**")
                            st.image(page_diag["image"], use_container_width=True)
                            st.text_area(
                                f"Testo OCR pagina {page_diag['index']}",
                                value=page_diag["text"],
                                height=150,
                            )

        progress_bar.progress((i + 1) / len(uploaded_files))

    st.success("Analisi completata!")

    # -------------------------------------------------------------------
    # RIEPILOGO + EXPORT
    # -------------------------------------------------------------------
    st.subheader("📊 Riepilogo Dati Estratti")
    df = pd.DataFrame(results_list)

    if "elenco_articoli" in df.columns:
        df["elenco_articoli"] = df["elenco_articoli"].astype(str)

    preferred_order = [
        "Nome File",
        "data_fattura",
        "data_documento_portale",
        "numero_fattura",
        "fornitore",
        "cliente",
        "importo_totale",
        "imponibile_iva",
        "codice_iva",
        "valuta",
        "Metodo",
    ]
    existing_cols = [c for c in preferred_order if c in df.columns]
    remaining_cols = [c for c in df.columns if c not in existing_cols]
    df = df[existing_cols + remaining_cols]

    st.dataframe(df, use_container_width=True)

    csv = df.to_csv(index=False).encode("utf-8")
    st.download_button(
        label="📥 Scarica Report Excel/CSV",
        data=csv,
        file_name=f"export_fatture_{datetime.now().strftime('%Y%m%d')}.csv",
        mime="text/csv",
    )

else:
    st.info("Carica un PDF per iniziare.")
    st.markdown("### Requisiti (solo locale):")
    st.markdown("- `streamlit`, `pdfplumber`, `pandas`, `requests`, `pdf2image`, `pytesseract`, `Pillow`.")
    st.markdown("- **Tesseract** (es. `C:\\Programmi\\Tesseract-OCR\\tesseract.exe`).")
    st.markdown("- **Poppler** (es. `C:\\Programmi\\Poppler\\Library\\bin`).")
    st.markdown("- **Ollama** e modello locale (es. `ollama pull phi3`).")